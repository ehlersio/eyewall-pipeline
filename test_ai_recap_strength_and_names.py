"""
test_ai_recap_strength_and_names.py -- two things the EyeWall AI game recap
prompt used to get wrong, checked on real api-web play-by-play
(tests/fixtures/nhl_pbp_<id>.json, run through the real ingest parsers by
test_ai_period_labels._context):

1. A goal by a team with its own goalie pulled was tagged "[en]" (empty
   net). 2026020037 FLA @ ANA, P3 18:10: Brad Marchand tied it 6-on-5 with
   FLA's net empty and ANA's goalie in -- an extra-attacker goal (code
   0651). EN is now read from the scoring side, as the Worker's
   goalStrengthTags does.
2. A player the context couldn't name was listed as a made-up
   "Player 8484762" in ZONE STARTS / SEASON STATS. Names now come from the
   game's own roster (play-by-play rosterSpots), then the players table; a
   player found in neither is left out.
"""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import ai_context
from ai_persona import build_game_summary_prompt, format_game_context
from test_ai_period_labels import _context, _Query, _section

FIXTURES = Path(__file__).parent / "tests" / "fixtures"
MADE_UP_NAME = re.compile(r"\b(Player|Goalie) #?\d+")


def _pbp(game_id: int) -> dict:
    return json.loads((FIXTURES / f"nhl_pbp_{game_id}.json").read_text())


# ── 1. empty net vs extra attacker ───────────────────────────────────────────


def test_extra_attacker_goal_is_not_empty_net_2026020037(monkeypatch):
    ctx = _context(2026020037, "ANA", monkeypatch)
    text = format_game_context(ctx)
    tying = next(ln for ln in text.split("\n") if ln.startswith("  P3 18:10 — FLA:"))
    assert "Brad Marchand" in tying
    assert tying.endswith("[extra attacker] (2-2)"), tying
    assert "[en]" not in text
    # FLA's two 6-on-5 shots (the 18:08 shot and the goal): no EN bucket.
    assert "extra attacker: 1 goals, 1 shots on goal" in _section(text, "SHOTS BY SITUATION")
    assert "en" not in ctx["shots"]["by_situation"]


def test_power_play_with_the_goalie_pulled_2025030413(monkeypatch):
    # CAR (away) 6-on-4 at P3 18:18 (code 0641): a power-play goal with the
    # goalie pulled, not an empty-netter -- VGK's goalie was in.
    ctx = _context(2025030413, "CAR", monkeypatch)
    goal = next(g for g in ctx["goals"] if g["period"] == 3 and g["time"] == "18:18")
    assert goal["team"] == "CAR" and goal["situation"] == "away_pp"
    assert "[en]" not in format_game_context(ctx)


# ── 2. no made-up player names ───────────────────────────────────────────────

GAME = 2026020037
SENNECKE = 8484762  # ANA, on the game's roster, not in the players table
CARLSSON = 8484153  # ANA, on the game's roster and in the players table
NOT_ANYWHERE = 8499999  # on neither: must be left out
TRADED_IN = 8471214  # not on this game's roster, but in the players table


@pytest.fixture
def tables(monkeypatch):
    """Supabase tables plus the game's real play-by-play (its rosterSpots)
    behind nhl_get."""
    pbp = _pbp(GAME)
    monkeypatch.setattr(ai_context, "_game_roster_cache", {})
    monkeypatch.setattr(ai_context, "nhl_get", lambda path: pbp)
    data = {
        "players": [
            {"id": CARLSSON, "name": "Leo Carlsson", "position": "C"},
            {"id": TRADED_IN, "name": "Alex Ovechkin", "position": "L"},
            {"id": 8400000, "name": None, "position": "C"},  # no name: no use
        ],
        "zone_starts": [
            {"player_id": SENNECKE, "team": "ANA", "oz_starts": 6, "dz_starts": 2, "nz_starts": 1},
            {"player_id": CARLSSON, "team": "ANA", "oz_starts": 4, "dz_starts": 4, "nz_starts": 0},
            {"player_id": NOT_ANYWHERE, "team": "ANA", "oz_starts": 9, "dz_starts": 1,
             "nz_starts": 1},
            {"player_id": 8400000, "team": "ANA", "oz_starts": 1, "dz_starts": 1, "nz_starts": 0},
        ],
        "player_seasons": [
            {"player_id": NOT_ANYWHERE, "team": "ANA", "games_played": 40, "goals": 20,
             "assists": 20, "points": 40},
            {"player_id": SENNECKE, "team": "ANA", "games_played": 40, "goals": 12,
             "assists": 18, "points": 30},
            {"player_id": TRADED_IN, "team": "ANA", "games_played": 40, "goals": 15,
             "assists": 10, "points": 25},
        ],
        "goalie_seasons": [
            {"player_id": 8480843, "team": "ANA", "games_played": 30},
            {"player_id": NOT_ANYWHERE, "team": "ANA", "games_played": 10},
        ],
    }  # fmt: skip
    client = MagicMock()
    client.table.side_effect = lambda name: _Query(data[name])
    monkeypatch.setattr(ai_context, "supabase", client)
    return data


def test_zone_starts_are_named_from_the_games_roster(tables):
    zones = ai_context.get_zone_starts_context(game_id=GAME, team="ANA")
    assert [z["name"] for z in zones] == ["Beckett Sennecke", "Leo Carlsson"]


def test_season_stats_name_from_roster_then_players_table(tables):
    players = ai_context.get_player_context(team="ANA", game_id=GAME)
    assert [p["name"] for p in players] == ["Beckett Sennecke", "Alex Ovechkin"]
    assert players[0]["position"] == "R"  # from rosterSpots' positionCode


def test_recap_prompt_never_makes_up_a_name(tables):
    ctx = {
        "game": {"home_team": "ANA", "away_team": "FLA", "primary_team": "ANA",
                 "game_type": "regular"},
        "players": ai_context.get_player_context(team="ANA", game_id=GAME),
        "zones": ai_context.get_zone_starts_context(game_id=GAME, team="ANA"),
    }  # fmt: skip
    text = format_game_context(ctx)
    assert "Beckett Sennecke (R): 12G 18A 30PTS in 40 GP" in text
    assert "Beckett Sennecke: OZ 66.7% | DZ 22.2% | NZ starts 1" in text
    assert str(NOT_ANYWHERE) not in text
    assert not MADE_UP_NAME.search(text), MADE_UP_NAME.search(text)
    assert not MADE_UP_NAME.search(build_game_summary_prompt(ctx))


def test_unknown_goalie_is_left_out(tables):
    tables["players"].append({"id": 8480843, "name": "Lukas Dostal", "position": "G"})
    assert [g["name"] for g in ai_context.get_goalie_context(team="ANA", min_gp=1)] == [
        "Lukas Dostal"
    ]


def test_unfetchable_game_roster_falls_back_to_players_table(tables, monkeypatch):
    def down(path):
        raise RuntimeError("api-web down")

    monkeypatch.setattr(ai_context, "nhl_get", down)
    zones = ai_context.get_zone_starts_context(game_id=GAME, team="ANA")
    assert [z["name"] for z in zones] == ["Leo Carlsson"]


def test_prediction_lists_leave_out_unnamed_players(tables, monkeypatch):
    roster = {SENNECKE: {"name": "Beckett Sennecke", "position": "R"}}
    early = ai_context.get_prediction_players("ANA", 20262027, 0, roster)
    names = [p["name"] for p in early["players"] + early["newcomers"]]
    assert names and "Beckett Sennecke" in names
    assert not any(MADE_UP_NAME.search(n) for n in names)
    starts = {"oz": 5, "dz": 3, "nz": 2, "games": {1}}
    monkeypatch.setattr(
        ai_context,
        "_zone_totals",
        lambda season, team=None, player_ids=None: {CARLSSON: starts, NOT_ANYWHERE: starts},
    )
    zones = ai_context.get_prediction_zones("ANA", 20262027, 0, None)["zones"]
    assert [z["name"] for z in zones] == ["Leo Carlsson"]
