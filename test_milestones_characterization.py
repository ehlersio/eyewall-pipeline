"""
test_milestones_characterization.py -- golden master for the HockeyTech
milestone detector.

The PWHL cases were recorded against pwhl_milestones.py BEFORE it became a
wrapper over hockeytech_milestones.py (2026-10): every row upserted into
`milestones` for one scanned date and one --game run, over a fixture with a
natural hat trick, a plain hat trick, short-handed goals both flagged and
unflagged (penalty-window fallback), a shutout, a career-wins crossing,
season goal/point and career point crossings, and a later game that
LaterGames has to take off. The AHL/ECHL cases pin the new league path.
After an intended change:

    UPDATE_GOLDEN=1 pytest test_milestones_characterization.py
"""

import json
import os
from pathlib import Path

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import pwhl_milestones
from test_milestones_catch_up import FakeClient

GOLDEN_DIR = Path(__file__).parent / "tests" / "golden" / "milestones"

DAY, LATER = "2026-01-20", "2026-01-25"
HOME, AWAY = 6, 8  # TOR, SEA


def g(gid, period, t, team, shooter, a1=None, a2=None, sh=False, goalie=None, **extra):
    return {
        "game_id": gid,
        "event_type": "goal",
        "period_id": period,
        "time_seconds": t,
        "team_id": team,
        "shooter_id": shooter,
        "goalie_id": goalie,
        "is_home": team == HOME,
        "assist1_id": a1,
        "assist2_id": a2,
        "is_short_handed": sh,
        **extra,
    }


def s(gid, team, goalie):
    return {"game_id": gid, "event_type": "shot", "team_id": team, "goalie_id": goalie}


def pwhl_tables():
    games = [
        {"game_id": 261, "season_id": 8, "game_date": DAY, "home_team_id": HOME,
         "away_team_id": AWAY, "home_score": 5, "away_score": 2, "game_state": "Final"},
        {"game_id": 262, "season_id": 8, "game_date": DAY, "home_team_id": 1,
         "away_team_id": 2, "home_score": 3, "away_score": 0, "game_state": "Final"},
        {"game_id": 270, "season_id": 8, "game_date": LATER, "home_team_id": HOME,
         "away_team_id": 1, "home_score": 2, "away_score": 1, "game_state": "Final"},
        {"game_id": 271, "season_id": 8, "game_date": DAY, "home_team_id": 3,
         "away_team_id": 4, "home_score": 1, "away_score": 0, "game_state": "Scheduled"},
    ]  # fmt: skip
    events = [
        # 261: natural hat trick for 101 (three straight), then 102 twice and
        # 101 once more; SEA scores twice, once short-handed (flagged) and
        # once with the flag unknown during a TOR penalty (fallback).
        g(261, 1, 78, HOME, 101, a1=102, a2=103, goalie=901),
        g(261, 1, 174, HOME, 101, a1=103, goalie=901),
        g(261, 2, 592, HOME, 101, goalie=901),
        g(261, 2, 829, AWAY, 201, a1=202, sh=True, goalie=701),
        g(261, 3, 100, HOME, 102, a1=101, goalie=901),
        g(261, 3, 500, AWAY, 202, sh=None, goalie=701),
        s(261, HOME, 901),
        s(261, AWAY, 701),
        s(261, AWAY, 701),
        # 262: shutout for 801 (BOS), a plain hat trick (not consecutive).
        g(262, 1, 10, 1, 301, goalie=802),
        g(262, 1, 20, 1, 302, goalie=802),
        g(262, 2, 30, 1, 301, goalie=802),
        g(262, 3, 40, 1, 301, a1=302, goalie=802),
        s(262, 2, 801),
        s(262, 1, 802),
        # 270 (later date): 101 scores again; 701 wins again.
        g(270, 1, 60, HOME, 101, a1=102, goalie=811),
        s(270, 1, 701),
        s(270, HOME, 811),
    ]
    penalties = [
        {"game_id": 261, "event_type": "penalty", "is_power_play": True, "team_id": AWAY,
         "period_id": 3, "time_seconds": 450, "penalty_minutes": 2, "is_bench_penalty": False},
    ]  # fmt: skip
    seasons = [
        {"player_id": 101, "team_id": HOME, "season_id": 8, "season_type": "regular",
         "goals": 16, "points": 31},
        {"player_id": 102, "team_id": HOME, "season_id": 8, "season_type": "regular",
         "goals": 20, "points": 30},
        {"player_id": 103, "team_id": HOME, "season_id": 8, "season_type": "regular",
         "goals": 3, "points": 21},
        {"player_id": 301, "team_id": 1, "season_id": 8, "season_type": "regular",
         "goals": 15, "points": 22},
        {"player_id": 201, "team_id": AWAY, "season_id": 8, "season_type": "regular",
         "goals": 5, "points": 9},
        # earlier seasons: career totals
        {"player_id": 101, "team_id": HOME, "season_id": 5, "season_type": "regular",
         "goals": 10, "points": 25},
        {"player_id": 102, "team_id": HOME, "season_id": 5, "season_type": "regular",
         "goals": 10, "points": 20},
        {"player_id": 102, "team_id": HOME, "season_id": 6, "season_type": "playoffs",
         "goals": 2, "points": 5},
    ]  # fmt: skip
    goalie_seasons = [
        {"player_id": 701, "team_id": HOME, "season_type": "regular", "wins": 26},
        {"player_id": 701, "team_id": HOME, "season_type": "regular", "wins": 0},
        {"player_id": 801, "team_id": 1, "season_type": "regular", "wins": 25},
    ]
    players = [
        {"player_id": pid, "first_name": f"First{pid}", "last_name": f"Last{pid}"}
        for pid in (101, 102, 103, 201, 202, 301, 701)
    ]
    return {
        "pwhl_game_log": games,
        "pwhl_shot_events": events,
        "pwhl_pbp_events": penalties,
        "pwhl_player_seasons": seasons,
        "pwhl_goalie_seasons": goalie_seasons,
        "pwhl_players": players,
    }


def assert_golden(name, data):
    path = GOLDEN_DIR / f"{name}.json"
    actual = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    assert path.exists(), f"missing {path} -- create it with UPDATE_GOLDEN=1"
    assert json.loads(actual) == json.loads(path.read_text(encoding="utf-8")), (
        f"{name} differs from {path.name}: regenerate with UPDATE_GOLDEN=1 only for an "
        "intended change, and explain it in the PR"
    )


def upserts(sb):
    rows = [row for table, row in sb.upserts if table == "milestones"]
    assert all(table == "milestones" for table, _ in sb.upserts)
    return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))


def test_pwhl_run_for_date():
    sb = FakeClient(pwhl_tables())
    pwhl_milestones.run_for_date(sb, DAY)
    rows = upserts(sb)
    assert rows
    assert_golden("pwhl_run_for_date", rows)


def test_pwhl_run_for_game():
    sb = FakeClient(pwhl_tables())
    pwhl_milestones.run_for_game(sb, 270)
    assert_golden("pwhl_run_for_game", upserts(sb))


# ── AHL/ECHL ─────────────────────────────────────────────────────────

from datetime import date  # noqa: E402

import pytest  # noqa: E402

import hockeytech_milestones  # noqa: E402
from hockeytech_leagues import AHL, ECHL  # noqa: E402

TOR, ROC = 335, 323  # AHL ids; ECHL fixtures reuse them (codes resolve to None)


def ahl_tables(lg):
    k = lg.key
    games = [
        {"game_id": 1001, "season_id": 90, "game_date": DAY, "home_team_id": TOR,
         "away_team_id": ROC, "home_score": 4, "away_score": 0, "game_state": "Final"},
        {"game_id": 1002, "season_id": 90, "game_date": DAY, "home_team_id": ROC,
         "away_team_id": TOR, "home_score": 1, "away_score": 2, "game_state": "Final"},
        {"game_id": 1003, "season_id": 90, "game_date": LATER, "home_team_id": TOR,
         "away_team_id": ROC, "home_score": 1, "away_score": 0, "game_state": "Final"},
    ]  # fmt: skip
    events = [
        # 1001: natural hat trick for 11, SH goal by 12; shootout row ignored.
        g(1001, 1, 100, TOR, 11, a1=12),
        g(1001, 1, 200, TOR, 11, a1=13),
        g(1001, 2, 300, TOR, 11),
        g(1001, 3, 400, TOR, 12, a1=11, sh=True),
        g(1001, 7, 0, ROC, 21),  # shootout attempt: not a goal against
        s(1001, ROC, 901),
        # 1002: ROC's only goal is into an empty net, so TOR's goalie 901
        # doesn't get a shutout even with 0 goals against in the box score.
        g(1002, 1, 50, TOR, 13),
        g(1002, 2, 60, TOR, 13),
        g(1002, 3, 1190, ROC, 21),
        # 1003 (later): 11 scores again.
        g(1003, 1, 10, TOR, 11),
    ]
    box = [
        {"game_id": 1001, "player_id": 901, "team_id": TOR, "toi_seconds": 3600},
        {"game_id": 1001, "player_id": 951, "team_id": ROC, "toi_seconds": 2400},
        {"game_id": 1001, "player_id": 952, "team_id": ROC, "toi_seconds": 1200},
        {"game_id": 1001, "player_id": 999, "team_id": TOR, "toi_seconds": 0},  # backup
        {"game_id": 1002, "player_id": 901, "team_id": TOR, "toi_seconds": 3540},
        {"game_id": 1002, "player_id": 951, "team_id": ROC, "toi_seconds": 3600},
    ]
    seasons = [
        # 11 was traded: 12 goals for ROC earlier, 9 for TOR -> 21 total, so
        # the hat trick tonight (+1 later) crosses 20 on this date.
        {"player_id": 11, "team_id": ROC, "season_id": 90, "season_type": "regular",
         "goals": 12, "points": 30},
        {"player_id": 11, "team_id": TOR, "season_id": 90, "season_type": "regular",
         "goals": 10, "points": 21},
        {"player_id": 12, "team_id": TOR, "season_id": 90, "season_type": "regular",
         "goals": 30, "points": 49},
        # Earlier seasons don't make a career: no career milestones here.
        {"player_id": 12, "team_id": TOR, "season_id": 86, "season_type": "regular",
         "goals": 40, "points": 400},
    ]  # fmt: skip
    goalie_seasons = [{"player_id": 901, "team_id": TOR, "season_type": "regular", "wins": 300}]
    players = [
        {"player_id": pid, "first_name": f"First{pid}", "last_name": f"Last{pid}"}
        for pid in (11, 12, 901)
    ]
    return {
        f"{k}_game_log": games,
        f"{k}_shot_events": events,
        f"{k}_goalie_game_box": box,
        f"{k}_player_seasons": seasons,
        f"{k}_goalie_seasons": goalie_seasons,
        f"{k}_players": players,
    }


@pytest.fixture(autouse=False)
def regular(monkeypatch):
    monkeypatch.setattr(
        hockeytech_milestones.hockeytech_stats, "resolve_season_type", lambda lg, sid: "regular"
    )


@pytest.mark.parametrize("lg", [AHL, ECHL], ids=["ahl", "echl"])
def test_league_run_for_date(lg, regular):
    sb = FakeClient(ahl_tables(lg))
    hockeytech_milestones.run_for_date(lg, sb, DAY)
    rows = upserts(sb)
    assert_golden(f"{lg.key}_run_for_date", rows)

    got = sorted((r["game_id"], r["milestone_type"], r["player_id"]) for r in rows)
    assert got == [
        (1001, "natural_hat_trick", 11),
        (1001, "season_goals_20", 11),
        (1001, "season_goals_30", 12),
        (1001, "season_points_50", 11),
        (1001, "sh_goal", 12),
        (1001, "shutout", 901),
    ]
    for r in rows:
        assert r["is_pwhl"] is False and r["sport"] == lg.key and r["season"] == 90
    # Traded player's season total sums both team rows; tonight's team named.
    goals20 = next(r for r in rows if r["milestone_type"] == "season_goals_20")
    assert goals20["detail"] == {"season_goals": 21}  # 22 minus the later goal
    if lg is AHL:
        assert goals20["team"] == "TOR"


def test_league_upsert_failure_is_logged_once(regular, caplog):
    sb = FakeClient(ahl_tables(AHL))
    calls = []

    class Failing:
        def upsert(self, row, **_):
            calls.append(row)
            return self

        def execute(self):
            raise RuntimeError("column milestones.sport does not exist")

    real = sb.table
    sb.table = lambda name: Failing() if name == "milestones" else real(name)
    hockeytech_milestones.run_for_date(AHL, sb, DAY)
    assert len(calls) == 1
    assert any("2026-10-07_milestones_sport.sql" in r.message for r in caplog.records)


def test_league_cli_scans_three_dates(monkeypatch):
    seen = []
    monkeypatch.setattr(hockeytech_milestones, "get_client", lambda: None)
    monkeypatch.setattr(hockeytech_milestones, "today_et", lambda: date(2026, 10, 6))
    monkeypatch.setattr(
        hockeytech_milestones, "run_for_date", lambda lg, _sb, d: seen.append((lg.key, d))
    )
    monkeypatch.setattr("sys.argv", ["x", "echl"])
    hockeytech_milestones.main()
    assert seen == [("echl", "2026-10-03"), ("echl", "2026-10-04"), ("echl", "2026-10-05")]
