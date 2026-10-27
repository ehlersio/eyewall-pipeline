"""milestones.py / pwhl_milestones.py re-scan the last 3 ET dates (2026-10).

A failed night used to lose that date's milestones for good (runner-UTC
"yesterday" only, audit 2026-10-06 F-13). The nightly default now scans the
last 3 ET dates, and threshold milestones are judged on totals as of the
scanned date: season/career totals include every game since, so LaterGames
takes off what the player did after it. Without that, re-scanning a date
would credit a player who reached 50 goals yesterday with it on an earlier
game too.

Supabase is an in-memory fake applying eq/neq/gt/in_/or_ like PostgREST.
"""

import os
import re
from datetime import date

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import milestones as nhl
import pwhl_milestones as pwhl


class FakeQuery:
    def __init__(self, client, name):
        self.client = client
        self.name = name
        self.rows = list(client.tables.get(name, []))
        self._range = None

    def select(self, *_a, **_k):
        return self

    def _f(self, pred):
        self.rows = [r for r in self.rows if pred(r)]
        return self

    def eq(self, col, val):
        return self._f(lambda r: r.get(col) == val)

    def neq(self, col, val):
        return self._f(lambda r: r.get(col) != val)

    def gt(self, col, val):
        return self._f(lambda r: r.get(col) is not None and r.get(col) > val)

    def in_(self, col, vals):
        vals = set(vals)
        return self._f(lambda r: r.get(col) in vals)

    def or_(self, expr):
        terms = [re.fullmatch(r"(\w+)\.eq\.(\d+)", t).groups() for t in expr.split(",")]
        return self._f(lambda r: any(r.get(c) == int(v) for c, v in terms))

    def order(self, col, **_):
        self.rows.sort(key=lambda r: r.get(col))
        return self

    def range(self, lo, hi):
        self._range = (lo, hi)
        return self

    def limit(self, _n):
        return self

    def upsert(self, row, **_):
        self.client.upserts.append((self.name, row))
        return self

    def execute(self):
        rows = self.rows
        if self._range:
            lo, hi = self._range
            rows = rows[lo : hi + 1]

        class R:
            pass

        r = R()
        r.data = rows
        return r


class FakeClient:
    def __init__(self, tables):
        self.tables = tables
        self.upserts = []

    def table(self, name):
        return FakeQuery(self, name)


SEASON = 20262027
D3, D2, D1 = "2026-10-03", "2026-10-04", "2026-10-05"


def _types(ms):
    return sorted((m["game_id"], m["milestone_type"]) for m in ms)


def test_catch_up_dates_are_the_last_three_et_days():
    assert nhl.catch_up_dates(date(2026, 10, 6)) == [D3, D2, D1]
    assert pwhl.catch_up_dates(date(2026, 10, 6)) == [D3, D2, D1]


def test_default_run_scans_three_dates(monkeypatch):
    for mod in (nhl, pwhl):
        seen = []
        monkeypatch.setattr(mod, "get_client", lambda: None)
        monkeypatch.setattr(mod, "today_et", lambda: date(2026, 10, 6))
        monkeypatch.setattr(mod, "run_for_date", lambda _sb, d, seen=seen: seen.append(d))
        monkeypatch.setattr("sys.argv", ["x"])
        mod.main()
        assert seen == [D3, D2, D1]


# ---------------------------------------------------------------------------
# NHL
# ---------------------------------------------------------------------------


def _nhl_game(gid, d, home="CAR", away="BOS", hs=3, as_=1, game_type=2):
    return [
        {
            "game_id": gid,
            "season": SEASON,
            "game_date": d,
            "home_team": home,
            "away_team": away,
            "game_type": game_type,
            "team": home,
            "team_score": hs,
            "opp_score": as_,
        },
        {
            "game_id": gid,
            "season": SEASON,
            "game_date": d,
            "home_team": home,
            "away_team": away,
            "game_type": game_type,
            "team": away,
            "team_score": as_,
            "opp_score": hs,
        },
    ]


def _nhl_client(player_goals):
    """Player 8 (CAR) is at 48 goals / 98 points before D2, scores one goal
    on D2 (game 1) and one on D1 (game 2): 50 goals / 100 points now."""
    return FakeClient(
        {
            "game_log": _nhl_game(1, D2) + _nhl_game(2, D1),
            "game_scoring": [
                {"game_id": 1, "period": 1, "scorer_id": 8, "assist1_id": 9, "assist2_id": None},
                {"game_id": 2, "period": 2, "scorer_id": 8, "assist1_id": None, "assist2_id": 9},
            ],
            "player_seasons": [
                {
                    "player_id": 8,
                    "team": "CAR",
                    "season": SEASON,
                    "goals": player_goals,
                    "points": 100,
                    "game_type": 2,
                }
            ],
        }
    )


def _nhl_detect(sb, gid, d, later):
    game = {"game_id": gid, "season": SEASON, "game_date": d, "game_type": 2}
    rows = sb.table("game_scoring").eq("game_id", gid).execute().data
    ms, _, _ = nhl.detect_season_milestones(sb, game, rows, later)
    return ms


def test_nhl_rescanned_date_uses_totals_as_of_that_date():
    sb = _nhl_client(50)
    # D2 re-scanned after D1's game: 49 goals / 99 points as of D2 -- nothing.
    assert _nhl_detect(sb, 1, D2, nhl.LaterGames(sb, D2)) == []
    # D1: the 50th goal and 100th point.
    ms = _nhl_detect(sb, 2, D1, nhl.LaterGames(sb, D1))
    assert _types(ms) == [(2, "season_goals_50"), (2, "season_points_100")]
    assert ms[0]["detail"] in ({"season_goals": 50}, {"season_points": 100})


def test_nhl_without_later_games_the_old_logic_misfires():
    """Documents why LaterGames exists: today's totals on an older date."""
    sb = _nhl_client(50)
    assert _types(_nhl_detect(sb, 1, D2, None)) == [
        (1, "season_goals_50"),
        (1, "season_points_100"),
    ]


def test_nhl_career_points_take_off_later_regular_season_points(monkeypatch):
    sb = _nhl_client(50)
    monkeypatch.setattr(nhl, "get_career_totals", lambda _pid: {"points": 1000})
    game1 = {"game_id": 1, "season": SEASON, "game_date": D2, "game_type": 2}
    game2 = {"game_id": 2, "season": SEASON, "game_date": D1, "game_type": 2}
    assert nhl.detect_career_milestones(game1, 8, "CAR", 1, 1, nhl.LaterGames(sb, D2)) == []
    ms = nhl.detect_career_milestones(game2, 8, "CAR", 1, 1, nhl.LaterGames(sb, D1))
    assert [m["milestone_type"] for m in ms] == ["career_points_1000"]


def test_nhl_goalie_career_wins_take_off_later_wins(monkeypatch):
    # Goalie 31 (CAR) faces every BOS shot in both games; CAR wins both.
    sb = _nhl_client(50)
    sb.tables["shot_events"] = [
        {"game_id": 1, "team": "BOS", "goalie_id": 31, "event_type": "shot"},
        {"game_id": 2, "team": "BOS", "goalie_id": 31, "event_type": "shot"},
    ]
    monkeypatch.setattr(nhl, "get_career_totals", lambda _pid: {"wins": 200})
    for gid, d, expected in ((1, D2, []), (2, D1, ["career_wins_200"])):
        game = {
            "game_id": gid,
            "season": SEASON,
            "game_date": d,
            "home_team": "CAR",
            "away_team": "BOS",
        }
        apps = nhl.get_goalie_appearances(sb, game)
        ms = nhl.detect_goalie_win_milestones(sb, apps, game, nhl.LaterGames(sb, d))
        assert [m["milestone_type"] for m in ms] == expected


def test_nhl_later_lookups_only_for_players_at_a_threshold():
    sb = _nhl_client(12)  # nowhere near 50 goals; 100 points still triggers
    later = nhl.LaterGames(sb, D2)
    _nhl_detect(sb, 1, D2, later)
    assert list(later._scoring) == [(8, SEASON, 2)]
    sb.tables["player_seasons"][0]["points"] = 40
    later = nhl.LaterGames(sb, D2)
    assert _nhl_detect(sb, 1, D2, later) == []
    assert later._scoring == {}


# ---------------------------------------------------------------------------
# PWHL
# ---------------------------------------------------------------------------


def _pwhl_game(gid, d, hs=2, as_=0):
    return {
        "game_id": gid,
        "season_id": 8,
        "game_date": d,
        "home_team_id": 1,
        "away_team_id": 2,
        "home_score": hs,
        "away_score": as_,
        "game_state": "Final",
        "season_type": "regular",
    }


def _pwhl_client():
    """Player 50 is at 14 goals / 49 career points before D2, scores on D2
    (game 11) and D1 (game 12). Goalie 70 (team 1) has 25 career wins now,
    the last two in those games."""
    goals = [
        {
            "game_id": gid,
            "event_type": "goal",
            "team_id": 1,
            "shooter_id": 50,
            "goalie_id": 80,
            "assist1_id": None,
            "assist2_id": None,
            "period_id": 1,
            "time_seconds": 100,
        }
        for gid in (11, 12)
    ]
    shots_on_70 = [
        {"game_id": gid, "event_type": "shot", "team_id": 2, "goalie_id": 70} for gid in (11, 12)
    ]
    return FakeClient(
        {
            "pwhl_game_log": [_pwhl_game(11, D2), _pwhl_game(12, D1)],
            "pwhl_shot_events": goals + shots_on_70,
            "pwhl_player_seasons": [
                {
                    "player_id": 50,
                    "team_id": 1,
                    "season_id": 8,
                    "season_type": "regular",
                    "goals": 16,
                    "points": 51,
                }
            ],
            "pwhl_goalie_seasons": [
                {"player_id": 70, "season_type": "regular", "wins": 25},
            ],
        }
    )


def test_pwhl_rescanned_date_uses_totals_as_of_that_date():
    sb = _pwhl_client()
    g11, g12 = _pwhl_game(11, D2), _pwhl_game(12, D1)
    goals11 = [
        r for r in sb.tables["pwhl_shot_events"] if r["game_id"] == 11 and r["event_type"] == "goal"
    ]
    goals12 = [
        r for r in sb.tables["pwhl_shot_events"] if r["game_id"] == 12 and r["event_type"] == "goal"
    ]

    later2, later1 = pwhl.LaterGames(sb, D2), pwhl.LaterGames(sb, D1)
    # As of D2: 15 goals (crossed 15 on D2), 50 season points, 50 career points.
    assert _types(pwhl.detect_season_goal_milestones(sb, g11, goals11, later2)) == [
        (11, "season_goals_15")
    ]
    assert pwhl.detect_season_goal_milestones(sb, g12, goals12, later1) == []
    assert _types(pwhl.detect_career_points_milestones(sb, g11, goals11, later2)) == [
        (11, "career_points_50")
    ]
    assert pwhl.detect_career_points_milestones(sb, g12, goals12, later1) == []


def test_pwhl_goalie_career_wins_take_off_later_wins():
    sb = _pwhl_client()
    for gid, d, expected in ((11, D2, []), (12, D1, ["career_wins_25"])):
        game = _pwhl_game(gid, d)
        apps = pwhl.get_goalie_appearances(sb, game)
        ms = pwhl.detect_goalie_win_milestones(sb, apps, game, pwhl.LaterGames(sb, d))
        assert [m["milestone_type"] for m in ms] == expected
