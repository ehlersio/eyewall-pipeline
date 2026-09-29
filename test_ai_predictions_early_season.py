"""
test_ai_predictions_early_season.py -- the AI pre-game prediction context
early in a season (ai_context.build_prediction_context and friends,
ai_persona's prediction/matchup formatters, early_season.py).

Until 2026-09-29 the first week of every season got no predictions:
player_seasons has no current-season rows until a team plays, so the
min_gp=5 player filter left both teams empty and ai_predictions skipped
the game. "Recent form" was last season's playoffs plus this September's
exhibitions, unlabeled, and zone starts/Corsi were preseason numbers.
Now: last season's stats for the current roster, blended team numbers,
this-season-only form, and every number labeled -- a missing stat reads
"not available", never 0 or a default.

Supabase is an in-memory fake (FakeClient below) that applies the filters
the real queries use; the live NHL roster is monkeypatched.
"""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import ai_context
import ai_predictions as ap
import line_combinations
from ai_persona import (
    build_matchup_prompt,
    build_prediction_prompt,
    format_matchup_context,
    format_prediction_context,
)
from early_season import (
    blend_stat,
    describe_stat,
    game_id_range,
    is_early_estimate,
    prior_season,
    season_from_game_id,
    season_label,
)

SEASON = 20262027
PRIOR = 20252026
CAP = 1000


# ---------------------------------------------------------------------------
# In-memory Supabase fake
# ---------------------------------------------------------------------------


class FakeQuery:
    """Applies eq/in_/gte/lt/gt/order/limit/range like PostgREST, returns at
    most CAP rows per request, and counts with count="exact"."""

    def __init__(self, client, name):
        self._client = client
        self._name = name
        self._rows = list(client.tables.get(name, []))
        self._order = []
        self._range = None
        self._limit = None
        self._count = False
        self._upsert = None

    def select(self, _cols="*", count=None):
        self._count = count == "exact"
        return self

    def _filter(self, pred):
        self._rows = [r for r in self._rows if pred(r)]
        return self

    def eq(self, col, val):
        return self._filter(lambda r: r.get(col) == val)

    def in_(self, col, vals):
        vals = set(vals)
        return self._filter(lambda r: r.get(col) in vals)

    def gte(self, col, val):
        return self._filter(lambda r: r.get(col) is not None and r[col] >= val)

    def gt(self, col, val):
        return self._filter(lambda r: r.get(col) is not None and r[col] > val)

    def lt(self, col, val):
        return self._filter(lambda r: r.get(col) is not None and r[col] < val)

    def order(self, col, desc=False):
        self._order.append((col, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def upsert(self, rows, on_conflict=None):
        self._upsert = rows if isinstance(rows, list) else [rows]
        return self

    def execute(self):
        if self._upsert is not None:
            self._client.upserts.setdefault(self._name, []).extend(self._upsert)
            return SimpleNamespace(data=self._upsert, count=None)
        rows = self._rows
        for col, desc in reversed(self._order):
            rows = sorted(rows, key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        count = len(rows)
        if self._range:
            start, end = self._range
            rows = rows[start : end + 1]
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows[:CAP], count=count if self._count else None)


class FakeClient:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.upserts = {}

    def table(self, name):
        return FakeQuery(self, name)


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------


def skater(pid, team, season, gp, g, a, rapm=0.05, xgf60=1.0):
    return {
        "player_id": pid,
        "team": team,
        "season": season,
        "game_type": 2,
        "games_played": gp,
        "goals": g,
        "assists": a,
        "points": g + a,
        "rapm": rapm,
        "xgf_per60": xgf60,
    }


def game(team, season, game_id, date, gf, ga, game_type=2, opp="OPP"):
    return {
        "game_id": game_id,
        "team": team,
        "season": season,
        "game_type": game_type,
        "game_date": date,
        "opponent": opp,
        "team_score": gf,
        "opp_score": ga,
        "period_end": 3,
    }


def zone(pid, team, game_id, oz, dz, nz, row_id):
    return {
        "id": row_id,
        "game_id": game_id,
        "season": season_from_game_id(game_id),
        "player_id": pid,
        "team": team,
        "oz_starts": oz,
        "dz_starts": dz,
        "nz_starts": nz,
    }


def team_season(team, season, gp, corsi=None, corsi_5v5=None, record=(0, 0, 0)):
    w, losses, otl = record
    return {
        "team": team,
        "season": season,
        "game_type": 2,
        "games_played": gp,
        "wins": w,
        "losses": losses,
        "ot_losses": otl,
        "points": 2 * w + otl,
        "corsi_for_pct": corsi,
        "corsi_for_pct_5v5": corsi_5v5,
    }


PLAYERS = [
    {"id": 1, "name": "Carl Center", "position": "C"},
    {"id": 2, "name": "Wes Wing", "position": "R"},
    {"id": 3, "name": "Dee Fence", "position": "D"},
    {"id": 4, "name": "Gone Guy", "position": "L"},
    {"id": 5, "name": "Rookie Rick", "position": "C"},
    {"id": 6, "name": "Other Olaf", "position": "C"},
]

# CAR's live roster: 1, 2, 3 stayed; 4 left; 5 is a rookie; 3 came from BOS.
CAR_ROSTER = {
    1: {"name": "Carl Center", "position": "C"},
    2: {"name": "Wes Wing", "position": "R"},
    3: {"name": "Dee Fence", "position": "D"},
    5: {"name": "Rookie Rick", "position": "C"},
}
BOS_ROSTER = {6: {"name": "Other Olaf", "position": "C"}}


def base_tables():
    return {
        "players": list(PLAYERS),
        "player_seasons": [
            skater(1, "CAR", PRIOR, 82, 30, 50, rapm=0.06, xgf60=1.3),
            skater(2, "CAR", PRIOR, 80, 20, 20, rapm=None, xgf60=None),
            skater(3, "BOS,CAR", PRIOR, 70, 5, 25),
            skater(4, "CAR", PRIOR, 82, 40, 40),  # left in the summer
            skater(6, "BOS", PRIOR, 82, 25, 25),
        ],
        "game_log": [
            # last season's playoffs and this September's exhibitions -- neither is form
            game("CAR", PRIOR, 2025030416, "2026-06-14", 3, 0, game_type=3),
            game("CAR", SEASON, 2026010053, "2026-09-26", 6, 0, game_type=1),
            game("BOS", SEASON, 2026010050, "2026-09-25", 1, 2, game_type=1),
        ],
        "zone_starts": [
            # player 1 last season: 40 OZ / 20 DZ / 40 NZ over 2 regular-season games
            zone(1, "CAR", 2025020001, 20, 10, 20, 1),
            zone(1, "CAR", 2025020002, 20, 10, 20, 2),
            # a playoff game and a preseason game -- never counted
            zone(1, "CAR", 2025030001, 50, 0, 0, 3),
            zone(1, "CAR", 2026010001, 50, 0, 0, 4),
        ],
        "team_seasons": [
            team_season("CAR", PRIOR, 82, 0.5876, 0.5916, (53, 22, 7)),
            # 0-GP row whose Corsi is September exhibition data
            team_season("CAR", SEASON, 0, 0.4605, 0.484),
            team_season("BOS", PRIOR, 82, None, None, (40, 30, 12)),
        ],
        "line_combinations": [],
        "player_scouting": [],
    }


@pytest.fixture
def db(monkeypatch):
    client = FakeClient(base_tables())
    monkeypatch.setattr(ai_context, "supabase", client)
    rosters = {"CAR": CAR_ROSTER, "BOS": BOS_ROSTER}
    monkeypatch.setattr(ai_context, "fetch_current_roster", lambda team: rosters.get(team))
    return client


def add_current_games(client, team, n, start_id=2026020001):
    for i in range(n):
        client.tables["game_log"].append(
            game(team, SEASON, start_id + i, f"2026-10-{1 + i:02d}", 3, 2 if i % 2 else 4)
        )


# ---------------------------------------------------------------------------
# early_season.py helpers
# ---------------------------------------------------------------------------


class TestSeasonArithmetic:
    def test_labels_and_ids(self):
        assert prior_season(SEASON) == PRIOR
        assert season_label(SEASON) == "2026-27"
        assert season_label(None) == "this season"
        assert season_from_game_id(2026020001) == SEASON
        assert game_id_range(SEASON, 2) == (2026020000, 2026030000)


class TestBlendStat:
    def test_neither_season_is_none_not_zero(self):
        assert blend_stat(None, 0, None, 10) is None
        assert describe_stat("PK%", None, "2025-26") == "PK%: not available"

    def test_zero_gp_ignores_current_value(self):
        """A 0-GP row can hold preseason numbers -- weight 0 ignores them."""
        s = blend_stat(48.4, 0, 59.2, 10)
        assert s["value"] == 59.2 and s["cur"] is None
        assert describe_stat("CF%", s, "2025-26") == "CF%: 59.2% (2025-26; none this season yet)"

    def test_blend_formula(self):
        s = blend_stat(40.0, 5, 60.0, 10)
        assert s["value"] == pytest.approx((5 * 40 + 10 * 60) / 15)
        assert is_early_estimate(s)
        text = describe_stat("CF%", s, "2025-26")
        assert "early-season estimate" in text and "40.0% in 5 GP" in text

    def test_k_games_on_this_season_stands_alone(self):
        s = blend_stat(40.0, 10, 60.0, 10)
        assert s["value"] == 40.0 and not is_early_estimate(s)
        assert describe_stat("CF%", s, "2025-26") == "CF%: 40.0%"

    def test_no_prior_is_small_sample(self):
        s = blend_stat(40.0, 3, None, 10)
        assert s["value"] == 40.0
        assert "small sample" in describe_stat("CF%", s, "2025-26")


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


class TestPredictionPlayers:
    def test_before_first_game_uses_last_season_for_current_roster(self, db):
        res = ai_context.get_prediction_players("CAR", SEASON, 0, CAR_ROSTER)
        assert res["mode"] == "early" and res["stats_season"] == PRIOR
        names = [p["name"] for p in res["players"]]
        assert names == ["Carl Center", "Wes Wing", "Dee Fence"]  # by last season's points
        assert "Gone Guy" not in names  # left the team
        assert all(p["this_season"] is None for p in res["players"])
        dee = res["players"][2]
        assert dee["last_season_teams"] == ["BOS", "CAR"]
        assert res["players"][0]["last_season_teams"] is None

    def test_this_season_line_and_newcomers_after_a_few_games(self, db):
        db.tables["player_seasons"] += [
            skater(1, "CAR", SEASON, 3, 2, 1),
            skater(5, "CAR", SEASON, 3, 1, 2),  # rookie, no NHL stats last season
        ]
        res = ai_context.get_prediction_players("CAR", SEASON, 3, CAR_ROSTER)
        carl = res["players"][0]
        assert carl["this_season"] == {"games_played": 3, "goals": 2, "assists": 1, "points": 3}
        assert carl["goals"] == 30  # the listed stats are still last season's
        assert [p["name"] for p in res["newcomers"]] == ["Rookie Rick"]

    def test_without_roster_falls_back_to_last_seasons_team_unconfirmed(self, db):
        res = ai_context.get_prediction_players("CAR", SEASON, 0, None)
        assert res["roster_confirmed"] is False
        names = [p["name"] for p in res["players"]]
        assert "Gone Guy" in names and "Dee Fence" not in names
        out = format_prediction_context(
            {
                "home_team": "CAR",
                "season": SEASON,
                "home_players": res.pop("players"),
                "home_players_info": res,
            }
        )
        assert "Current roster couldn't be confirmed" in out

    def test_from_20_games_on_it_is_this_season_only(self, db):
        db.tables["player_seasons"] += [
            skater(1, "CAR", SEASON, 20, 10, 10),
            skater(2, "CAR", SEASON, 3, 1, 0),  # under min_gp 5
        ]
        res = ai_context.get_prediction_players("CAR", SEASON, 20, CAR_ROSTER)
        assert res["mode"] == "current"
        assert [(p["name"], p["goals"]) for p in res["players"]] == [("Carl Center", 10)]


# ---------------------------------------------------------------------------
# Recent form, zones, team stats
# ---------------------------------------------------------------------------


class TestRecentForm:
    def test_excludes_preseason_and_other_seasons(self, db):
        assert ai_context.get_recent_form("CAR", season=SEASON) == []
        add_current_games(db, "CAR", 2)
        form = ai_context.get_recent_form("CAR", season=SEASON)
        assert [g["game_date"] for g in form] == ["2026-10-02", "2026-10-01"]
        assert {g["season"] for g in form} == {SEASON}

    def test_unscoped_still_drops_preseason(self, db):
        form = ai_context.get_recent_form("CAR")
        assert [g["season"] for g in form] == [PRIOR]
        assert form[0]["game_type"] == "playoff"


class TestZones:
    def test_early_blends_regular_season_starts_only(self, db):
        # 2 regular-season games this season: 10 OZ / 30 DZ / 10 NZ -> OZ 20%
        db.tables["zone_starts"] += [
            zone(1, "CAR", 2026020001, 5, 15, 5, 10),
            zone(1, "CAR", 2026020002, 5, 15, 5, 11),
        ]
        res = ai_context.get_prediction_zones("CAR", SEASON, 2, CAR_ROSTER)
        assert res["mode"] == "early"
        (z,) = res["zones"]
        assert z["name"] == "Carl Center" and z["games_this_season"] == 2
        # last season 40% OZ (playoff/preseason rows excluded), this season 20% in 2 GP, k=10
        assert z["oz_pct"]["prior"] == pytest.approx(40.0)
        assert z["oz_pct"]["cur"] == pytest.approx(20.0)
        assert z["oz_pct"]["value"] == pytest.approx((2 * 20 + 10 * 40) / 12)

    def test_before_first_game_is_last_season_labeled(self, db):
        res = ai_context.get_prediction_zones("CAR", SEASON, 0, CAR_ROSTER)
        out = format_prediction_context(
            {
                "home_team": "CAR",
                "season": SEASON,
                "home_zones": res.pop("zones"),
                "home_zones_info": res,
            }
        )
        assert "Carl Center: OZ 40.0% | DZ 20.0% (2025-26; none this season yet)" in out

    def test_season_zone_starts_page_past_the_row_cap(self, db):
        db.tables["zone_starts"] = [
            zone(1, "CAR", 2025020001 + i // 2, 1, 0, 0, i) for i in range(2500)
        ]
        (z,) = ai_context.get_zone_starts_context(team="CAR", season=PRIOR)
        assert z["oz_starts"] == 2500


class TestTeamSeasonStats:
    def test_zero_gp_preseason_corsi_is_ignored(self, db):
        stats = ai_context.get_team_season_stats("CAR", SEASON)
        assert stats["corsi_for_pct_5v5"]["value"] == 59.2
        assert stats["corsi_for_pct_5v5"]["cur"] is None
        assert stats["prior_record"]["wins"] == 53

    def test_blends_by_games_played(self, db):
        db.tables["team_seasons"][1].update(games_played=5, corsi_for_pct_5v5=0.50)
        s = ai_context.get_team_season_stats("CAR", SEASON)["corsi_for_pct_5v5"]
        assert s["value"] == pytest.approx((5 * 50.0 + 10 * 59.2) / 15)

    def test_missing_everywhere_is_not_available(self, db):
        stats = ai_context.get_team_season_stats("BOS", SEASON)
        assert stats["corsi_for_pct_5v5"] is None and stats["corsi_for_pct"] is None
        out = format_prediction_context({"home_team": "BOS", "home_team_stats": stats})
        assert "Corsi For%: not available" in out


# ---------------------------------------------------------------------------
# End to end: context -> prompt
# ---------------------------------------------------------------------------


class TestPredictionPrompt:
    def test_opening_night_prompt(self, db):
        ctx = ai_context.build_prediction_context("CAR", "BOS", season=SEASON)
        assert ctx["home_players"] and ctx["away_players"]  # ai_predictions won't skip
        prompt = build_prediction_prompt(ctx)
        assert "ranked on LAST season's (2025-26)" in prompt
        assert "Dee Fence (D) [on CAR's current roster; played for BOS/CAR in 2025-26]" in prompt
        assert "Wes Wing (R): 2025-26: 20G 20A in 80 GP | RAPM not available" in prompt
        assert "Recent form: no 2026-27 regular-season games played yet." in prompt
        assert "CAR and BOS haven't played a 2026-27 regular-season game yet" in prompt
        assert "2025-26 regular-season record: 53-22-7 (113 pts in 82 GP)" in prompt
        assert "it's early in the 2026-27 season" in prompt
        assert 'Don\'t cite any stat marked "not available".' in prompt
        assert "48.4" not in prompt  # the preseason Corsi
        assert "6-0" not in prompt  # the preseason game

    def test_after_a_few_games(self, db):
        add_current_games(db, "CAR", 3)
        db.tables["player_seasons"].append(skater(1, "CAR", SEASON, 3, 2, 1))
        prompt = build_prediction_prompt(
            ai_context.build_prediction_context("CAR", "BOS", season=SEASON)
        )
        assert "CAR has played 3 regular-season games in 2026-27" in prompt
        assert "| 2026-27: 2G 1A in 3 GP" in prompt
        assert "Recent form (2026-27, last 3 games, preseason excluded): 1W-2L" in prompt
        assert "BOS hasn't played a 2026-27 regular-season game yet" in prompt

    def test_mid_season_has_no_early_note(self, db):
        add_current_games(db, "CAR", 20)
        add_current_games(db, "BOS", 20, start_id=2026020100)
        db.tables["player_seasons"] += [
            skater(1, "CAR", SEASON, 20, 10, 10),
            skater(6, "BOS", SEASON, 20, 5, 5),
        ]
        db.tables["team_seasons"][1].update(games_played=20, corsi_for_pct_5v5=0.55)
        db.tables["team_seasons"].append(team_season("BOS", SEASON, 20, 0.49, 0.48))
        prompt = build_prediction_prompt(
            ai_context.build_prediction_context("CAR", "BOS", season=SEASON)
        )
        assert "Top players (2026-27 regular season):" in prompt
        assert "early in the" not in prompt
        assert "Corsi For% (5-on-5 shot-attempt share): 55.0%" in prompt


class TestMatchupPrompt:
    def test_carried_over_units_and_xgf_scale(self, db):
        db.tables["line_combinations"] = [
            {
                "team": "CAR",
                "season": SEASON,
                "unit_type": "F",
                "rank": 1,
                "name_a": "Carl Center",
                "name_b": "Wes Wing",
                "name_c": "Rookie Rick",
                "pos_a": "C",
                "pos_b": "R",
                "pos_c": "C",
                "toi_secs": 6000,
                "xgf_pct": 0.5616,
                "source": "prior_season",
            }
        ]
        ctx = ai_context.build_matchup_context("CAR", "BOS", season=SEASON)
        out = format_matchup_context(ctx)
        assert "Line 1: Carl Center, Wes Wing, Rookie Rick | xGF% 56.2" in out
        assert "carried over from 2025-26" in out
        assert "Defence pairs: not available" in out
        assert "it's early in the 2026-27 season" in build_matchup_prompt(ctx)


# ---------------------------------------------------------------------------
# ai_predictions wiring
# ---------------------------------------------------------------------------


def test_upcoming_games_skip_preseason(monkeypatch):
    sched = {
        "gameWeek": [
            {
                "date": "2026-09-29",
                "games": [
                    {"id": 2026010099, "gameState": "FUT", "gameType": 1},
                    {
                        "id": 2026020001,
                        "gameState": "FUT",
                        "gameType": 2,
                        "homeTeam": {"abbrev": "CAR"},
                        "awayTeam": {"abbrev": "FLA"},
                    },
                ],
            }
        ]
    }
    monkeypatch.setattr(ap, "nhl_get", lambda path: sched)
    assert [g["game_id"] for g in ap.get_upcoming_games()] == [2026020001]


def test_process_game_builds_context_for_the_games_own_season(monkeypatch):
    seen = {}

    def fake_ctx(home, away, season=None):
        seen["prediction"] = season
        return {"home_players": [{"name": "x"}], "away_players": []}

    def fake_matchup(home, away, season=None):
        seen["matchup"] = season
        return {}

    monkeypatch.setattr(ap, "already_generated", lambda *a: False)
    monkeypatch.setattr(ap, "build_prediction_context", fake_ctx)
    monkeypatch.setattr(ap, "build_prediction_prompt", lambda ctx: "p")
    monkeypatch.setattr(ap, "build_matchup_context", fake_matchup)
    monkeypatch.setattr(ap, "build_matchup_prompt", lambda ctx: "m")
    monkeypatch.setattr(ap, "generate", lambda *a, **k: "text")
    saved = {}
    monkeypatch.setattr(ap, "save_prediction", lambda *a, **k: saved.update(season=a[1]))
    game_ = {"game_id": 2026020001, "home_team": "CAR", "away_team": "FLA", "game_date": "x"}
    assert ap.process_game(game_)
    assert seen == {"prediction": SEASON, "matchup": SEASON}
    assert saved["season"] == SEASON


# ---------------------------------------------------------------------------
# line_combinations: preseason games no longer build a season's units
# ---------------------------------------------------------------------------


def test_line_combinations_ignore_preseason_games(monkeypatch):
    def fake_fetch_all(client, table, select, filters, **_):
        if table == "shift_events":
            return [{"game_id": 2026010001, "player_id": 1, "team": "CAR"}]
        if table == "game_log":
            return [{"game_id": 2026010001, "game_type": 1}]
        raise AssertionError(f"unexpected fetch of {table}")

    monkeypatch.setattr(line_combinations, "fetch_all", fake_fetch_all)
    assert line_combinations.compute_current_season_rows(None, "CAR", SEASON) == []
