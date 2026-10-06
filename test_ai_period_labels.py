"""
test_ai_period_labels.py -- EyeWall AI game recaps name every period the way
the Worker's alerts do (eyewall-poller pushPeriodLabel): P1-P3, then OT, and
2OT/3OT in the playoffs; SO only for a regular-season shootout.

Until 2026-10 the recap prompt's period-by-period shots read "period_4 ANA:
1 goals" (a playoff double overtime "period_5"), the overtime line read
"Went to overtime (ended period 5)", and a shootout's attempts were counted
as period-5 shots and goals in "0v1"/"1v0" situations. A missing stat could
print "None" (or crash the formatter).

The games are real, trimmed from api-web.nhle.com's play-by-play (only the
shot/goal plays and roster names; tests/fixtures/nhl_pbp_<id>.json) and run
through the real ingest parsers (shot_events.process_game,
game_scoring.parse_goals_from_pbp, nhl_stats.period_end_of):
  2026020037  FLA 2 @ ANA 3   regular season, OT (Mikael Granlund 4:14)
  2025030413  CAR 4 @ VGK 5   2026 Cup Final Game 3, 2OT (Shea Theodore 5:38)
  2025020121  CAR 5 @ COL 4   regular season, shootout (Seth Jarvis)
"""

import json
import os
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import ai_context
import game_scoring
import shot_events
from ai_persona import _format_form, build_game_summary_prompt, format_game_context
from early_season import ending_label, period_label
from nhl_stats import period_end_of

FIXTURES = Path(__file__).parent / "tests" / "fixtures"

# A raw period, a stringified missing value, or a shootout read as a manpower
# state must never reach the prompt.
RAW = re.compile(r"\bP[4-9]\b|period_\d|ended period|\bNone\b|\bnan\b|\b[01]v[01]\b")


def _pbp(game_id: int) -> dict:
    return json.loads((FIXTURES / f"nhl_pbp_{game_id}.json").read_text())


class _Query:
    """Supabase query stand-in: every filter chains, execute() returns the
    table's rows (one dict after .single())."""

    def __init__(self, rows):
        self._rows, self._single = rows, False

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def single(self):
        self._single = True
        return self

    def execute(self):
        return MagicMock(data=self._rows[0] if self._single else self._rows)


def _context(game_id: int, team: str, monkeypatch) -> dict:
    """The recap context ai_context builds for `team`, from the tables the
    real ingest would have written for this game."""
    pbp = _pbp(game_id)
    monkeypatch.setattr(shot_events, "nhl_get", lambda url: pbp)
    shots = shot_events.process_game(pbp, pbp["season"])
    goals, seen = [], set()
    for r in game_scoring.parse_goals_from_pbp(pbp, game_id, pbp["season"]):
        key = (r["period"], r["time_in_period"], r["team"])  # upsert_goals' dedupe
        if key not in seen:
            seen.add(key)
            goals.append(r)
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    mine, theirs = (home, away) if team == home["abbrev"] else (away, home)
    game_log = {
        "game_id": game_id, "season": pbp["season"], "game_date": pbp["gameDate"],
        "game_type": pbp["gameType"], "home_team": home["abbrev"], "away_team": away["abbrev"],
        "team": team, "opponent": theirs["abbrev"], "team_score": mine["score"],
        "opp_score": theirs["score"], "period_end": period_end_of(pbp),
    }  # fmt: skip
    players = [
        {"id": s["playerId"], "name": f"{s['firstName']['default']} {s['lastName']['default']}"}
        for s in pbp["rosterSpots"]
    ]
    tables = {"shot_events": shots, "game_log": [game_log], "game_scoring": goals,
              "players": players}  # fmt: skip
    client = MagicMock()
    client.table.side_effect = lambda name: _Query(tables[name])
    monkeypatch.setattr(ai_context, "supabase", client)
    return {
        "game": ai_context.get_game_context(game_id, team=team),
        "shots": ai_context.get_shot_context(game_id, team=team),
        "goals": ai_context.get_goal_scorers(game_id),
    }


def _section(text: str, title: str) -> list:
    lines = text.split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.startswith(title))
    end = next((i for i in range(start + 1, len(lines)) if not lines[i].strip()), len(lines))
    return lines[start + 1 : end]


def test_period_label_matches_the_worker():
    assert [period_label(n, 2) for n in (1, 2, 3, 4, 5)] == ["P1", "P2", "P3", "OT", "SO"]
    assert [period_label(n, 3) for n in (3, 4, 5, 6, 7)] == ["P3", "OT", "2OT", "3OT", "4OT"]
    assert period_label(5, "playoff") == "2OT" and period_label(5, "regular") == "SO"
    assert period_label(5, 1) == "SO"  # preseason games can go to a shootout too
    assert period_label("4", 2) == "OT"
    for missing in (None, 0, "", "x", float("nan")):
        assert period_label(missing, 2) is None


def test_ending_label():
    assert ending_label(3, 2) is None
    assert ending_label(4, 2) == "OT"
    assert ending_label(5, 2) == "SO"
    assert ending_label(5, 3) == "2OT"
    assert ending_label(6, "playoff") == "3OT"
    assert ending_label(None, 2) is None


def test_regular_season_overtime_2026020037(monkeypatch):
    ctx = _context(2026020037, "ANA", monkeypatch)
    text = format_game_context(ctx)
    assert "Went to overtime: decided in OT" in text
    assert "  OT 04:14 — ANA: Mikael Granlund" in text
    assert "  P3 18:10 — FLA:" in text
    by_period = _section(text, "SHOTS BY PERIOD")
    assert [ln.split()[0] for ln in by_period] == ["P1"] * 2 + ["P2"] * 2 + ["P3"] * 2 + ["OT"] * 2
    assert "OT ANA: 1 goals" in text
    assert "SHOOTOUT" not in text and "shootout" not in text
    assert not RAW.search(text), RAW.search(text)


def test_playoff_double_overtime_2025030413(monkeypatch):
    ctx = _context(2025030413, "CAR", monkeypatch)
    assert ctx["game"]["ended_in"] == "2OT"
    text = format_game_context(ctx)
    assert "Went to overtime: decided in 2OT (overtime period 2)" in text
    assert "  2OT 05:38 — VGK: Shea Theodore" in text
    by_period = _section(text, "SHOTS BY PERIOD")
    assert [ln.split()[0] for ln in by_period][-4:] == ["OT", "OT", "2OT", "2OT"]
    assert "2OT VGK: 1 goals" in text
    # Playoff period 5 is hockey, not a shootout.
    assert "SO" not in re.findall(r"\b[A-Z0-9]+\b", text) and "shootout" not in text.lower()
    assert ctx["shots"]["shootout"] == {}
    # Mitch Marner's P3 penalty shot (code 1010) isn't a "1v0" manpower state.
    assert "penalty_shot: 0 goals, 1 shots on goal" in text
    assert not RAW.search(text), RAW.search(text)


def test_regular_season_shootout_2025020121(monkeypatch):
    ctx = _context(2025020121, "CAR", monkeypatch)
    text = format_game_context(ctx)
    assert "Decided in a shootout" in text and "Went to overtime" not in text
    assert "  SO — CAR: Seth Jarvis scored in the shootout" in text
    # The shootout is its own section; its attempts are in no other count.
    assert _section(text, "SHOOTOUT") == [
        "CAR: 1 scored on 3 attempts",
        "COL: 0 scored on 3 attempts",
    ]
    assert "CAR: 4 goals" in text and "COL: 4 goals" in text
    by_period = _section(text, "SHOTS BY PERIOD")
    assert [ln.split()[0] for ln in by_period][-2:] == ["OT", "OT"]
    assert not any(ln.startswith("SO ") for ln in by_period)
    assert not RAW.search(text), RAW.search(text)
    prompt = build_game_summary_prompt(ctx)
    assert "  - Seth Jarvis" in prompt and not RAW.search(prompt)


def test_recent_form_names_double_overtime(monkeypatch):
    client = MagicMock()
    client.table.side_effect = lambda name: _Query(
        [
            {"game_date": "2026-06-06", "season": 20252026, "opponent": "VGK", "team_score": 4,
             "opp_score": 5, "game_type": 3, "period_end": 5},  # 2025030413
            {"game_date": "2025-10-23", "season": 20252026, "opponent": "COL", "team_score": 5,
             "opp_score": 4, "game_type": 2, "period_end": 5},  # 2025020121
        ]
    )  # fmt: skip
    monkeypatch.setattr(ai_context, "supabase", client)
    form = ai_context.get_recent_form("CAR", n_games=2)
    assert [g["decided_in"] for g in form] == ["2OT", "SO"]
    assert "  2026-06-06 vs VGK: L 4-5 (2OT) (playoff)" in _format_form(form, "2025-26")


@pytest.mark.parametrize("game_type", ["regular", "playoff"])
def test_missing_stats_are_left_out(game_type):
    """Nothing the context lacks is printed as None/nan or made up."""
    ctx = {
        "game": {
            "home_team": "VGK", "away_team": "CAR", "primary_team": "CAR", "is_home": False,
            "team_score": None, "opp_score": 5, "result": "loss", "game_type": game_type,
            "period_end": 4, "pp_goals": 1, "pp_opps": None, "pk_goals_against": None,
            "pk_opps": 3, "home_cf_pct": float("nan"),
        },
        "shots": {
            "by_team": {}, "by_situation": {"unknown": {"goals": 0, "shots_on_goal": 1}},
            "by_period": {"period_4": {"CAR": {"goals": 0, "shots_on_goal": 2}},
                          None: {"CAR": {"goals": 0, "shots_on_goal": 1}}},
        },
        "goals": [
            {"period": None, "time": None, "team": "VGK", "scorer": None, "situation": None,
             "away_score_after": None, "home_score_after": None},
        ],
        "xg": [{"team": "CAR", "situation": "5v5", "xgf": None, "xga": 1.2, "xgf_pct": None}],
        "players": [
            {"name": "Seth Jarvis", "position": "R", "goals": 1, "assists": None, "points": None,
             "games_played": 3, "rapm": None, "xgf_per60": None, "pct_ev_off": None},
            {"name": "No Stats", "position": "C", "goals": None},
        ],
        "zones": [{"name": "Seth Jarvis", "oz_pct": None, "dz_pct": 40.0, "nz_starts": 2}],
        "form": [],
    }  # fmt: skip
    text = format_game_context(ctx)
    assert not RAW.search(text), RAW.search(text)
    assert "Final score" not in text and "Power play" not in text
    assert "Penalty kill" not in text and "Corsi" not in text
    assert "SHOT SUMMARY" not in text and "SHOTS BY SITUATION" not in text
    assert "RECENT FORM" not in text and "No Stats" not in text
    assert "OT CAR: 0 goals, 2 SOG" in text  # a legacy "period_4" key is named too
    assert "  — VGK: scorer not available (unassisted)" in text
    assert "  CAR 5v5: xGA 1.20" in text
    assert "Seth Jarvis (R): 1G in 3 GP" in text
    assert "Seth Jarvis: DZ 40.0% | NZ starts 2" in text
