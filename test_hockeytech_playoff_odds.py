"""Tests for hockeytech_playoff_odds.py and the playoff formats in
hockeytech_leagues.py -- no network, no Supabase.

The 2025-26 qualification tests replay each league's real final standings
(tests/fixtures/hockeytech_final_standings_2025_26.json, trimmed from
HockeyTech's view=teams on 2026-10-07) through the encoded format: the
qualifiers must be exactly the teams the feed marks clinched (x/y/z).
"""

import json
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import hockeytech_playoff_odds as hpo
from hockeytech_leagues import AHL, ECHL, PWHL

FIXTURE = json.loads(
    (Path(__file__).parent / "tests/fixtures/hockeytech_final_standings_2025_26.json").read_text()
)


def clinched(key):
    """team_ids the feed marked as in the playoffs (an x/y/z clinch prefix)."""
    league = {"ahl": AHL, "echl": ECHL, "pwhl": PWHL}[key.split("_")[0]]
    out = set()
    for section in FIXTURE[key][0]["sections"]:
        for item in section["data"]:
            code = item["row"]["team_code"]
            prefix = code.split(" - ")[0] if " - " in code else ""
            if set(prefix) & set("xyz"):
                out.add(item["prop"]["team_code"]["teamLink"])
    assert out, league.key
    return out


def season(sid, stype, start):
    return {"seasonId": sid, "seasonType": stype, "startDate": start}


def log_row(gid, sid, day, home, away, state="7:00 pm"):
    return {
        "game_id": gid,
        "season_id": sid,
        "game_date": day,
        "home_team_id": home,
        "away_team_id": away,
        "game_state": state,
    }


TODAY = date(2026, 10, 20)


# ── formats ───────────────────────────────────────────────────────────────


class TestFormats:
    def test_verified_seasons_only(self):
        assert sorted(AHL.playoff_formats) == [90]  # 2026-27 (94) not published yet
        assert sorted(ECHL.playoff_formats) == [73]  # 2026-27 (78) not published yet
        assert sorted(PWHL.playoff_formats) == [8, 11]

    def test_berth_counts(self):
        assert sum(AHL.playoff_formats[90].berths.values()) == 23
        assert sum(ECHL.playoff_formats[73].berths.values()) == 16
        assert sum(PWHL.playoff_formats[8].berths.values()) == 4
        assert sum(PWHL.playoff_formats[11].berths.values()) == 8

    def test_every_format_cites_the_leagues_own_site(self):
        sites = {"ahl": "https://theahl.com/", "echl": "https://echl.com/"}
        sites["pwhl"] = "https://www.thepwhl.com/"
        for league in (AHL, ECHL, PWHL):
            for fmt in league.playoff_formats.values():
                assert fmt.source.startswith(sites[league.key])
                assert fmt.description

    def test_pwhl_conferences_cover_all_twelve_teams_six_a_side(self):
        alignment = PWHL.playoff_formats[11].alignment
        assert set(alignment) == set(PWHL.team_id_map)
        assert sorted(alignment.values()).count("East") == 6
        assert {PWHL.team_id_map[t] for t, c in alignment.items() if c == "East"} == {
            "BOS",
            "HAM",
            "MTL",
            "NY",
            "OTT",
            "TOR",
        }

    def test_standings_points(self):
        assert AHL.standings_points == ECHL.standings_points == (2, 2, 1)
        assert PWHL.standings_points == (3, 2, 1)


# ── 2025-26 replays ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "league,key,season_id",
    [(AHL, "ahl_90", 90), (ECHL, "echl_73", 73), (PWHL, "pwhl_8", 8)],
)
def test_final_2025_26_standings_reproduce_the_real_qualifiers(league, key, season_id):
    standings = hpo.parse_standings(league, FIXTURE[key])
    fmt = league.playoff_formats[season_id]
    groups = hpo.assign_groups(fmt, standings)
    assert groups is not None
    rng = np.random.default_rng(0)
    names, pts, gp, rw = hpo.simulate(standings, [], {}, league.standings_points, 50, rng, 0.2)
    made, _ = hpo.qualify(
        names, groups, fmt, pts, gp, rw, max(league.standings_points), np.random.default_rng(1)
    )
    # No games left: every simulated season is the real final table.
    assert made.all(axis=0).sum() == made.any(axis=0).sum()
    qualified = {t for i, t in enumerate(names) if made[0, i]}
    assert qualified == clinched(key)


def test_ahl_regulation_wins_break_the_78_point_tie():
    standings = hpo.parse_standings(AHL, FIXTURE["ahl_90"])
    sd, tuc = "404", "412"
    assert standings[sd]["points"] == standings[tuc]["points"] == 78
    assert standings[sd]["rw"] > standings[tuc]["rw"]
    assert standings[sd]["group"] == standings[tuc]["group"] == "Pacific"


def test_parse_standings_groups_and_records():
    standings = hpo.parse_standings(AHL, FIXTURE["ahl_90"])
    assert len(standings) == 32
    assert standings["309"] == {"group": "Atlantic", "points": 110, "gp": 72, "rw": 41}
    # The relocated franchise's 2025-26 row stays on its own id.
    assert standings["317"]["group"] == "Atlantic"


def test_parse_standings_falls_back_to_the_code_and_skips_unknowns():
    data = [
        {
            "sections": [
                {
                    "headers": {"team_code": {"properties": {"label": "PWHL"}}},
                    "data": [
                        {"row": {"team_code": "x - VGS", "points": "9", "games_played": "4"}},
                        {"row": {"team_code": "ZZZ", "points": "1", "games_played": "4"}},
                    ],
                }
            ]
        }
    ]
    assert hpo.parse_standings(PWHL, data) == {
        "12": {"group": "PWHL", "points": 9, "gp": 4, "rw": 0}
    }


# ── groups ────────────────────────────────────────────────────────────────


def test_unverified_season_has_no_groups():
    assert hpo.assign_groups(None, {"1": {"group": "X"}}) is None


def test_changed_alignment_makes_the_format_unverified():
    standings = hpo.parse_standings(AHL, FIXTURE["ahl_90"])
    standings["999"] = {"group": "Northeast", "points": 0, "gp": 0, "rw": 0}
    assert hpo.assign_groups(AHL.playoff_formats[90], standings) is None


def test_pwhl_conferences_come_from_the_format_not_the_feed():
    standings = {t: {"group": "PWHL", "points": 0, "gp": 0, "rw": 0} for t in PWHL.team_id_map}
    groups = hpo.assign_groups(PWHL.playoff_formats[11], standings)
    assert groups["1"] == "East" and groups["2"] == "West"


def test_pwhl_team_missing_from_the_alignment_is_unverified():
    standings = {t: {"group": "PWHL", "points": 0, "gp": 0, "rw": 0} for t in PWHL.team_id_map}
    standings["14"] = {"group": "PWHL", "points": 0, "gp": 0, "rw": 0}
    assert hpo.assign_groups(PWHL.playoff_formats[11], standings) is None


# ── season selection and remaining games ──────────────────────────────────


SEASONS = [
    season(73, "regular", "2025-10-17"),
    season(76, "playoffs", "2026-04-14"),
    season(77, "preseason", "2026-10-01"),
    season(78, "regular", "2026-10-17"),
]


def test_remaining_games_skip_finals_other_seasons_and_stale_dates():
    rows = [
        log_row(1, 78, "2026-10-20", 74, 66),
        log_row(2, 78, "2026-10-19", 74, 66, state="Final"),
        log_row(3, 78, "2026-10-18", 74, 66),  # past-dated, never final
        log_row(4, 73, "2026-10-21", 74, 66),
        log_row(5, 78, "2026-10-22", 66, 74, state="Final OT"),
        log_row(6, 78, "2026-10-23", None, 74),
    ]
    games, stale = hpo.remaining_games(rows, 78, TODAY)
    assert [g["game_id"] for g in games] == [1]
    assert games[0] == {"game_id": 1, "game_date": "2026-10-20", "home": "74", "away": "66"}
    assert stale == 1


def test_pick_season_prefers_the_earliest_regular_season_with_games_left():
    rows = [log_row(1, 73, "2026-10-21", 74, 66), log_row(2, 78, "2026-10-21", 74, 66)]
    assert hpo.pick_season(SEASONS, rows, TODAY) == 73
    assert hpo.pick_season(SEASONS, rows[1:], TODAY) == 78
    assert hpo.pick_season(SEASONS, [], TODAY) is None
    # A playoff season's games never make it the odds season.
    assert hpo.pick_season(SEASONS, [log_row(3, 76, "2026-10-21", 74, 66)], TODAY) is None


def test_previous_regular_season():
    assert hpo.previous_regular_season(SEASONS, 78) == 73
    assert hpo.previous_regular_season(SEASONS, 73) is None
    assert hpo.previous_regular_season(SEASONS, 76) is None


def test_ot_rate():
    assert hpo.ot_rate([]) is None
    assert hpo.ot_rate([{"ot": True}, {"ot": False}, {"ot": False}, {"ot": False}]) == 0.25


# ── simulation ────────────────────────────────────────────────────────────


def two_teams(points=0, gp=0):
    return {
        "1": {"points": points, "gp": gp, "rw": 0},
        "2": {"points": points, "gp": gp, "rw": 0},
    }


def games(n):
    return [{"home": "1", "away": "2"} if i % 2 else {"home": "2", "away": "1"} for i in range(n)]


@pytest.mark.parametrize("system,ot_share", [((2, 2, 1), 0.0), ((3, 2, 1), 0.0), ((3, 2, 1), 1.0)])
def test_points_handed_out_per_game_follow_the_league_system(system, ot_share):
    rng = np.random.default_rng(3)
    _, pts, gp, _ = hpo.simulate(two_teams(), games(10), {}, system, 200, rng, ot_share)
    per_game = system[0] if ot_share == 0 else system[1] + system[2]
    assert np.all(pts.sum(axis=1) == 10 * per_game)
    assert np.all(gp == 10)


def test_regulation_wins_only_count_regulation():
    rng = np.random.default_rng(4)
    *_, rw = hpo.simulate(two_teams(), games(6), {}, (3, 2, 1), 100, rng, 1.0)
    assert not rw.any()


def test_the_stronger_team_finishes_ahead_far_more_often():
    rng = np.random.default_rng(5)
    names, pts, *_ = hpo.simulate(
        two_teams(), games(30), {"1": 1700.0, "2": 1300.0}, (2, 2, 1), 2000, rng, 0.2
    )
    assert names == ["1", "2"]
    assert (pts[:, 0] > pts[:, 1]).mean() > 0.95


def test_unrated_team_is_at_the_mean():
    rng = np.random.default_rng(6)
    _, pts, *_ = hpo.simulate(two_teams(), games(40), {}, (2, 2, 1), 4000, rng, 0.0)
    assert abs((pts[:, 0] > pts[:, 1]).mean() - (pts[:, 1] > pts[:, 0]).mean()) < 0.05


def test_qualify_gives_each_group_exactly_its_berths():
    fmt = AHL.playoff_formats[90]
    standings = hpo.parse_standings(AHL, FIXTURE["ahl_90"])
    for s in standings.values():
        s.update(points=0, gp=0, rw=0)
    groups = hpo.assign_groups(fmt, standings)
    sched = [
        {"home": a, "away": b}
        for a in sorted(standings)
        for b in sorted(standings)
        if a < b and groups[a] == groups[b]
    ]
    rng = np.random.default_rng(7)
    names, pts, gp, rw = hpo.simulate(standings, sched, {}, (2, 2, 1), 300, rng, 0.2)
    made, first = hpo.qualify(names, groups, fmt, pts, gp, rw, 2, rng)
    for group, berths in fmt.berths.items():
        cols = [i for i, t in enumerate(names) if groups[t] == group]
        assert np.all(made[:, cols].sum(axis=1) == berths)
        assert np.all(first[:, cols].sum(axis=1) == 1)


# ── rows ──────────────────────────────────────────────────────────────────


def rows_for(fmt, made, first):
    pts = np.array([[60.0, 40.0], [62.0, 38.0], [64.0, 36.0]])
    return hpo.odds_rows(
        78,
        "2026-10-20",
        two_teams(points=4, gp=3),
        ["1", "2"],
        pts,
        made,
        first,
        fmt,
        [{"home": "1", "away": "2"}],
        3,
    )


def test_verified_rows():
    made = np.array([[True, False], [True, True], [True, False]])
    rows = rows_for(ECHL.playoff_formats[73], made, made)
    assert rows[0] == {
        "season_id": 78,
        "team_id": 1,
        "run_date": "2026-10-20",
        "make_playoffs_pct": 1.0,
        "win_division_pct": 1.0,
        "proj_points_p10": 60,
        "proj_points_p50": 62,
        "proj_points_p90": 64,
        "current_points": 4,
        "games_remaining": 1,
        "sims": 3,
        "format": "16 of 30: top 4 in each division (points %)",
    }
    assert rows[1]["make_playoffs_pct"] == round(1 / 3, 4)


def test_unverified_rows_have_points_but_no_probabilities():
    rows = rows_for(None, None, None)
    assert {r["make_playoffs_pct"] for r in rows} == {None}
    assert {r["win_division_pct"] for r in rows} == {None}
    assert {r["format"] for r in rows} == {"unverified"}
    assert rows[1]["proj_points_p50"] == 38


def test_conference_format_has_no_division_pct():
    made = np.ones((3, 2), dtype=bool)
    rows = rows_for(PWHL.playoff_formats[11], made, made)
    assert {r["win_division_pct"] for r in rows} == {None}
    assert {r["make_playoffs_pct"] for r in rows} == {1.0}


# ── run() ─────────────────────────────────────────────────────────────────


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.table, self.ops = db, table, []

    def __getattr__(self, name):
        def op(*a, **k):
            self.ops.append((name, a, k))
            return self

        return op

    def execute(self):
        for name, a, k in self.ops:
            if name == "upsert":
                if self.table in self.db.missing:
                    raise RuntimeError(f'relation "public.{self.table}" does not exist')
                self.db.writes.append((self.table, k.get("on_conflict"), a[0]))
                return SimpleNamespace(data=a[0])
        rows = self.db.tables.get(self.table, [])
        rng = next((a for name, a, _ in self.ops if name == "range"), None)
        if rng:
            rows = rows[rng[0] : rng[1] + 1]
        return SimpleNamespace(data=rows)


class FakeDB:
    def __init__(self, tables, missing=()):
        self.tables, self.missing, self.writes = tables, set(missing), []

    def table(self, name):
        return FakeQuery(self, name)


def wire(monkeypatch, db, standings, schedule_rows=()):
    monkeypatch.setattr(hpo, "league_seasons", lambda key: SEASONS)
    monkeypatch.setattr(hpo, "get_client", lambda: db)
    monkeypatch.setattr(hpo, "fetch_standings", lambda league, sid: standings)
    monkeypatch.setattr(hpo, "fetch_schedule", lambda league, sid: list(schedule_rows))


SCHEDULE = [
    {
        "game_id": "9",
        "date_played": "2026-04-01",
        "home_team": "74",
        "visiting_team": "66",
        "game_status": "Final OT",
        "home_goal_count": "3",
        "visiting_goal_count": "2",
    },
    {
        "game_id": "10",
        "date_played": "2026-04-02",
        "home_team": "66",
        "visiting_team": "74",
        "game_status": "Final",
        "home_goal_count": "1",
        "visiting_goal_count": "4",
    },
]


def test_run_writes_projected_points_only_for_an_unverified_season(monkeypatch):
    db = FakeDB(
        {
            "echl_game_log": [log_row(1, 78, "2026-10-20", 74, 66)],
            "echl_team_elo_ratings": [{"team_id": 74, "rating": 1550}],
        }
    )
    wire(
        monkeypatch,
        db,
        {
            "74": {"group": "North", "points": 2, "gp": 1, "rw": 0},
            "66": {"group": "Mountain", "points": 0, "gp": 1, "rw": 0},
        },
        SCHEDULE,
    )
    assert hpo.run("echl", n_sims=10, seed=1, today=TODAY) == 0
    [(table, conflict, rows)] = db.writes
    assert table == "echl_playoff_odds" and conflict == "season_id,team_id,run_date"
    assert {r["season_id"] for r in rows} == {78}
    assert {r["sims"] for r in rows} == {hpo.MIN_SIMS}  # floor
    assert {r["make_playoffs_pct"] for r in rows} == {None}
    assert {r["format"] for r in rows} == {"unverified"}
    by_team = {r["team_id"]: r for r in rows}
    assert by_team[74]["current_points"] == 2 and by_team[74]["games_remaining"] == 1
    assert by_team[74]["proj_points_p10"] >= 2


def test_run_tolerates_a_missing_odds_table(monkeypatch):
    db = FakeDB(
        {"echl_game_log": [log_row(1, 78, "2026-10-20", 74, 66)], "echl_team_elo_ratings": []},
        missing={"echl_playoff_odds"},
    )
    wire(
        monkeypatch,
        db,
        {
            "74": {"group": "North", "points": 0, "gp": 0, "rw": 0},
            "66": {"group": "North", "points": 0, "gp": 0, "rw": 0},
        },
        SCHEDULE,
    )
    assert hpo.run("echl", n_sims=10, seed=1, today=TODAY) == 0
    assert db.writes == []


def test_run_with_nothing_left_to_play_writes_nothing(monkeypatch):
    db = FakeDB({"echl_game_log": [log_row(1, 73, "2026-04-01", 74, 66, state="Final")]})
    wire(monkeypatch, db, {})
    assert hpo.run("echl", today=TODAY) == 0
    assert db.writes == []


def test_run_dry_run_writes_nothing(monkeypatch):
    db = FakeDB({"echl_game_log": [log_row(1, 78, "2026-10-20", 74, 66)]})
    wire(
        monkeypatch,
        db,
        {
            "74": {"group": "North", "points": 0, "gp": 0, "rw": 0},
            "66": {"group": "North", "points": 0, "gp": 0, "rw": 0},
        },
        SCHEDULE,
    )
    assert hpo.run("echl", n_sims=10, dry_run=True, today=TODAY) == 0
    assert db.writes == []


def test_run_needs_the_season_list(monkeypatch):
    monkeypatch.setattr(hpo, "league_seasons", lambda key: None)
    assert hpo.run("ahl", today=TODAY) == 1
