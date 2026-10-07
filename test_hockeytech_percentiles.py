"""
test_hockeytech_percentiles.py -- the AHL/ECHL (per game played) side of
hockeytech_shot_xg.py, hockeytech_percentiles.py and
hockeytech_goalie_percentiles.py. The PWHL side is pinned separately by
test_pwhl_percentiles_characterization.py, whose in-memory Supabase fake
these tests reuse.
"""

import logging

import pytest

import hockeytech_goalie_percentiles as gp_mod
import hockeytech_percentiles as pct_mod
import hockeytech_shot_xg as xg_mod
from hockeytech_leagues import AHL, ECHL
from test_pwhl_percentiles_characterization import FakeSupabase

SEASON = "90"


def shot(eid, game, team, shooter, goalie, x, y=0.0, period=1, t=100, **extra):
    return {
        "id": eid,
        "game_id": game,
        "season_id": int(SEASON),
        "season_type": "regular",
        "event_type": "shot",
        "team_id": team,
        "shooter_id": shooter,
        "goalie_id": goalie,
        "assist1_id": None,
        "x_norm": None if x is None else str(x),  # numeric -> string from PostgREST
        "y_norm": None if y is None else str(y),
        "period_id": period,
        "time_seconds": t,
        "is_empty_net": None,
        "is_penalty_shot": None,
        **extra,
    }


def goal(eid, game, team, shooter, x, a1=None, period=1, t=100, **extra):
    return shot(
        eid,
        game,
        team,
        shooter,
        None,  # AHL/ECHL goal rows carry no goalie
        x,
        period=period,
        t=t,
        **{"event_type": "goal", "assist1_id": a1, "is_empty_net": False, **extra},
    )


def ahl_tables(lg=AHL):
    """Two teams (10 home, 20 away), two games. Goalies: 501 (team 10),
    601 and 602 (team 20; 602 relieved 601 in game 2). Skater 1 (team 10)
    was traded to team 20 for game 2."""
    k = lg.key
    events = [
        # Game 1: team 10 shoots on 601.
        shot(1, 1, 10, 1, 601, 85),  # high (dist 4)
        shot(2, 1, 10, 2, 601, 65),  # medium (dist 24)
        goal(3, 1, 10, 1, 88, a1=2, t=200),  # high
        shot(4, 1, 10, 3, 601, 20),  # low
        # team 20 shoots on 501.
        shot(5, 1, 20, 11, 501, 80),
        goal(6, 1, 20, 11, 60, a1=12, t=300),  # medium
        goal(7, 1, 20, 12, 30, t=1190, is_empty_net=True),  # EN: no goalie faced it
        # Game 2: team 10 shoots on 601, then 602 after a change in period 2.
        shot(8, 2, 10, 2, 601, 70, period=1, t=50),
        goal(9, 2, 10, 2, 84, period=1, t=60),  # 601 (nearest shot before, period 1)
        shot(10, 2, 10, 3, 602, 70, period=2, t=10),
        goal(11, 2, 10, 3, 86, period=2, t=5),  # 602 (only shot in period 2 is after)
        # Traded skater 1 now shooting for team 20 on 501.
        goal(12, 2, 20, 1, 80, a1=11),
        shot(13, 2, 20, 1, 501, 40),
        # Never shots in the run of play: shootout, penalty shot, no location.
        goal(14, 2, 20, 11, 85, period=7, t=0),
        goal(15, 2, 20, 12, 85, t=900, is_penalty_shot=True),
        shot(16, 2, 10, 3, 601, None, y=None),
    ]
    other_season = {**shot(17, 3, 10, 1, 601, 85), "season_id": 91, "season_type": "playoffs"}
    events.append(other_season)
    return {
        f"{k}_shot_events": events,
        f"{k}_game_log": [
            {"game_id": 1, "season_id": 90, "home_team_id": 10, "away_team_id": 20},
            {"game_id": 2, "season_id": 90, "home_team_id": 10, "away_team_id": 20},
        ],
        f"{k}_goalie_game_box": [
            {"game_id": 1, "season_id": 90, "player_id": 601, "team_id": 20, "goals_against": 1},
            {"game_id": 1, "season_id": 90, "player_id": 501, "team_id": 10, "goals_against": 1},
            {"game_id": 2, "season_id": 90, "player_id": 601, "team_id": 20, "goals_against": 1},
            {"game_id": 2, "season_id": 90, "player_id": 602, "team_id": 20, "goals_against": 1},
            {"game_id": 2, "season_id": 90, "player_id": 501, "team_id": 10, "goals_against": 1},
        ],
        f"{k}_players": [
            {"player_id": 1, "position": "LW"},
            {"player_id": 2, "position": "C"},
            {"player_id": 3, "position": "RD"},
            {"player_id": 11, "position": "RW"},
            {"player_id": 12, "position": "D"},
            {"player_id": 13, "position": None},
            {"player_id": 501, "position": "G"},
        ],
        f"{k}_player_seasons": [
            {"player_id": 1, "team_id": 10, "gp": 20, "goals": 4, "pim": 10},
            {"player_id": 1, "team_id": 20, "gp": 30, "goals": 9, "pim": 6},
            {"player_id": 2, "team_id": 10, "gp": 40, "goals": 6, "pim": 20},
            {"player_id": 3, "team_id": 10, "gp": 12, "goals": 1, "pim": 30},
            {"player_id": 11, "team_id": 20, "gp": 50, "goals": 20, "pim": 0},
            {"player_id": 12, "team_id": 20, "gp": 5, "goals": 2, "pim": 2},
            {"player_id": 13, "team_id": 20, "gp": 30, "goals": 2, "pim": 2},
        ],
        f"{k}_goalie_seasons": [
            {"player_id": 501, "team_id": 10, "gp": 40},
            {"player_id": 601, "team_id": 20, "gp": 25},
            {"player_id": 602, "team_id": 20, "gp": 4},
        ],
    }


def with_season(rows):
    return [{**r, "season_id": 90, "season_type": "regular"} for r in rows]


@pytest.fixture
def sb():
    tables = ahl_tables()
    for t in ("ahl_player_seasons", "ahl_goalie_seasons"):
        tables[t] = with_season(tables[t])
    return FakeSupabase(tables)


def rows_for(sb, table):
    return [r for w in sb.writes if w["table"] == table for r in w["rows"]]


# ── xG ────────────────────────────────────────────────────────────────


def test_bucket_rates_reproduce_goal_count():
    events = [
        {"event_type": t, "x_norm": x, "y_norm": 0}
        for t, x in [("goal", 85), ("shot", 85), ("shot", 85), ("goal", 60), ("shot", 20)]
    ]
    rates = xg_mod.bucket_rates(events)
    assert rates == {"high": 1 / 3, "medium": 1.0, "low": 0.0}
    xg = sum(rates[xg_mod.danger_bucket(e["x_norm"], 0)] for e in events)
    assert xg == pytest.approx(2)


@pytest.mark.parametrize("lg", [AHL, ECHL])
def test_xg_rows_per_player_and_team(lg):
    tables = ahl_tables(lg)
    sb = FakeSupabase(tables)
    xg_mod.compute_shooter_xg(lg, sb, SEASON, "regular")

    assert [w["table"] for w in sb.writes] == [f"{lg.key}_player_xg"]
    assert sb.writes[0]["on_conflict"] == "player_id,team_id,season_id,season_type"
    rows = {(r["player_id"], r["team_id"]): r for r in sb.writes[0]["rows"]}
    # The traded skater has one row per team.
    assert rows[(1, 10)]["attempts"] == 2 and rows[(1, 10)]["goals"] == 1
    assert rows[(1, 20)]["attempts"] == 2 and rows[(1, 20)]["goals"] == 1
    # Shootout, penalty-shot and unlocated events are not attempts.
    assert rows[(11, 20)]["attempts"] == 2  # shot 5 + goal 6, not the SO goal
    assert rows[(12, 20)]["attempts"] == 1  # the EN goal only
    assert rows[(3, 10)]["attempts"] == 3  # shots 4, 10 and goal 11, not shot 16
    # Rates come from this season's own attempts, so xG sums to goals.
    assert sum(r["xg_for"] for r in rows.values()) == pytest.approx(
        sum(r["goals"] for r in rows.values()), abs=0.01
    )
    for r in rows.values():
        assert r["finishing"] == round(r["goals"] - r["xg_for"], 3)
        assert r["season_id"] == 90 and r["season_type"] == "regular"


def test_xg_without_located_events_writes_nothing():
    tables = ahl_tables()
    tables["ahl_shot_events"] = []
    sb = FakeSupabase(tables)
    xg_mod.compute_shooter_xg(AHL, sb, SEASON, "regular")
    assert sb.writes == []


# ── Skater percentiles ────────────────────────────────────────────────


def test_skater_percentiles_per_game_played(sb):
    xg_mod.compute_shooter_xg(AHL, sb, SEASON, "regular")
    sb.writes.clear()
    pct_mod.compute_percentiles(AHL, sb, SEASON, "regular")

    assert [w["table"] for w in sb.writes] == ["ahl_player_percentiles"]
    rows = {(r["player_id"], r["team_id"]): r for r in sb.writes[0]["rows"]}
    # Every F/D row; 13 has no position, goalies aren't skaters.
    assert set(rows) == {(1, 10), (1, 20), (2, 10), (3, 10), (11, 20), (12, 20)}
    for r in rows.values():
        assert r["rate_basis_per_gp"] is True
        assert "toi_per_game" not in r  # no TOI in these leagues
    # Forwards with >= 10 GP: (1,10) 0.2 g/gp, (1,20) 0.3, (2,10) 0.15, (11,20) 0.4.
    assert rows[(11, 20)]["pct_goals"] == 75
    assert rows[(2, 10)]["pct_goals"] == 0
    # Primary assists count per team: 11's only A1 came for team 20, 2's for team 10.
    assert rows[(2, 10)]["pct_a1"] > rows[(1, 10)]["pct_a1"]
    # finishing comes from ahl_player_xg; xg_for alongside it.
    assert rows[(1, 20)]["finishing"] is not None and rows[(1, 20)]["xg_for"] is not None
    # Below MIN_GP still gets a row, ranked against the qualified pool.
    assert rows[(12, 20)]["pct_goals"] is not None
    assert rows[(12, 20)]["gp"] == 5


def test_skater_percentiles_without_xg_table(sb, monkeypatch):
    real_table = sb.table

    def table(name):
        if name == "ahl_player_xg":
            raise RuntimeError('relation "ahl_player_xg" does not exist')
        return real_table(name)

    monkeypatch.setattr(sb, "table", table)
    pct_mod.compute_percentiles(AHL, sb, SEASON, "regular")
    rows = sb.writes[0]["rows"]
    assert all(r["finishing"] is None and r["pct_finishing"] is None for r in rows)
    assert any(r["pct_goals"] is not None for r in rows)


# ── Goalie percentiles ────────────────────────────────────────────────


def test_goals_credited_to_the_goalie_who_allowed_them():
    tables = ahl_tables()
    events = xg_mod.fetch_located_attempts(
        AHL,
        FakeSupabase(tables),
        SEASON,
        "regular",
        "game_id,team_id,goalie_id,time_seconds,is_empty_net",
    )
    faced = gp_mod.attribute_shots_faced(
        events, tables["ahl_game_log"], tables["ahl_goalie_game_box"]
    )
    goals = {e["id"]: (g, team) for g, team, e in faced if e["event_type"] == "goal"}
    assert goals[3] == (601, 20)  # only 20 goalie with a GA in game 1
    assert goals[6] == (501, 10)
    assert 7 not in goals  # empty net
    assert goals[9] == (601, 20)  # two candidates: nearest shot before, same period
    assert goals[11] == (602, 20)  # nearest shot in period 2 comes after the goal
    assert goals[12] == (501, 10)
    assert 14 not in goals and 15 not in goals  # shootout, penalty shot


def test_goal_goalie_fallbacks():
    g = {"period_id": 2, "time_seconds": 100}
    assert gp_mod._goal_goalie(g, [7], []) == 7
    shots = [(1, 50, 7), (3, 10, 8)]
    # No same-period shot: latest before, else first after.
    assert gp_mod._goal_goalie(g, [], shots) == 7
    assert gp_mod._goal_goalie(g, [8, 9], shots) == 8  # limited to candidates
    assert gp_mod._goal_goalie(g, [9, 10], shots) is None


def test_goalie_percentiles_per_game_played(sb):
    gp_mod.compute_goalie_percentiles(AHL, sb, SEASON, "regular")

    assert [w["table"] for w in sb.writes] == ["ahl_goalie_percentiles"]
    rows = {r["player_id"]: r for r in sb.writes[0]["rows"]}
    assert set(rows) == {501, 601}  # 602 has 4 GP
    for r in rows.values():
        assert r["rate_basis_per_gp"] is True
        assert r["gsax_per60"] == round(r["gsax"] / r["gp"], 3)
        # No strength state on AHL/ECHL shots: never a made-up 5v5/PK number.
        assert r["ev_sv_pct"] is r["pk_sv_pct"] is None
        assert r["pct_ev_sv"] is r["pct_pk_sv"] is None
    assert rows[501]["team_id"] == 10 and rows[601]["team_id"] == 20
    # 501 faced: shot 5 (high), goal 6 (medium), goal 12 (high), shot 13 (low).
    assert rows[501]["hd_sv_pct"] == 0.5 and rows[501]["md_sv_pct"] == 0.0


def test_missing_table_is_logged_once(sb, monkeypatch, caplog):
    real_table = sb.table
    calls = []

    class Failing:
        def upsert(self, *a, **k):
            calls.append(1)
            return self

        def execute(self):
            raise RuntimeError('relation "ahl_goalie_percentiles" does not exist')

    def table(name):
        return Failing() if name == "ahl_goalie_percentiles" else real_table(name)

    monkeypatch.setattr(sb, "table", table)
    writer = xg_mod.TableWriter(sb, "ahl_goalie_percentiles", "player_id")
    with caplog.at_level(logging.ERROR):
        assert writer.write([{"player_id": i} for i in range(450)]) == 0
        assert writer.write([{"player_id": 1}]) == 0
    assert len(calls) == 1
    assert sum("ahl_goalie_percentiles" in r.message for r in caplog.records) == 1


def test_pwhl_rows_have_no_rate_basis_column():
    import json
    from pathlib import Path

    golden = Path(__file__).parent / "tests" / "golden" / "pwhl_percentiles"
    for f in golden.glob("*.json"):
        for w in json.loads(f.read_text()):
            assert all("rate_basis_per_gp" not in r for r in w["rows"])
