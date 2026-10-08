"""Tests for hockeytech_power_rankings.py -- no network, no Supabase, no AI."""

import os
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import hockeytech_power_rankings as hpr
from hockeytech_leagues import AHL, PWHL

TODAY = date(2026, 11, 2)


def team(tid, gp=10, w=5, lost=3, otl=2, pts=12, gf=30, ga=25, pp=0.2, pk=0.8, **extra):
    return {
        "team_id": tid,
        "gp": gp,
        "wins": w,
        "losses": lost,
        "ot_losses": otl,
        "points": pts,
        "goals_for": gf,
        "goals_against": ga,
        "pp_pct": pp,
        "pk_pct": pk,
        **extra,
    }


def log(gid, day, home, away, hs, aws, state="Final", ended_in=None, ot=False, so=False):
    return {
        "game_id": gid,
        "game_date": day,
        "home_team_id": home,
        "away_team_id": away,
        "home_score": hs,
        "away_score": aws,
        "game_state": state,
        "ended_in": ended_in,
        "ot": ot,
        "shootout": so,
    }


# ── weights ───────────────────────────────────────────────────────────────


def test_weights_are_fixed_and_documented():
    assert hpr.WEIGHTS["pwhl"] == {
        "pts_pct": 35,
        "l10_pts_pct": 20,
        "gd_pg": 20,
        "cf_pct": 15,
        "special_teams": 10,
    }
    # AHL/ECHL: the PWHL weights without Corsi.
    for key in ("ahl", "echl"):
        assert hpr.WEIGHTS[key] == {k: v for k, v in hpr.WEIGHTS["pwhl"].items() if k != "cf_pct"}
    assert set(hpr.COMPONENT_LABELS) >= set(hpr.WEIGHTS["pwhl"])


# ── games and last 10 ─────────────────────────────────────────────────────


def test_finals_read_each_leagues_ot_columns():
    rows = [
        log(1, "2026-10-10", 1, 2, 3, 2, ended_in="OT"),
        log(2, "2026-10-11", 1, 2, 3, 2, ended_in="SO"),
        log(3, "2026-10-12", 1, 2, 3, 2),
        log(4, "2026-10-13", 1, 2, 0, 0, state="7:00 PM"),
        log(5, "2026-10-14", 1, 2, 2, 2),  # a tie is no final result
    ]
    games = hpr.finals("ahl", rows)
    assert [(g["game_id"], g["ot"]) for g in games] == [(1, True), (2, True), (3, False)]
    pwhl = [log(1, "2026-12-05", 1, 2, 3, 2, so=True), log(2, "2026-12-06", 1, 2, 3, 2)]
    assert [g["ot"] for g in hpr.finals("pwhl", pwhl)] == [True, False]


def test_last_ten_uses_the_leagues_points():
    rows = [log(i, f"2026-10-{i:02d}", 1, 2, 3, 2) for i in range(1, 9)]  # 8 regulation wins
    rows += [log(9, "2026-10-09", 1, 2, 2, 3, ended_in="OT", so=True)]  # OT/SO loss
    rows += [log(10, "2026-10-10", 2, 1, 2, 3, ended_in="SO", so=True)]  # OT/SO win (away)
    rows += [log(11, "2026-10-11", 2, 1, 5, 1)]  # regulation loss
    ahl = hpr.last_ten("1", hpr.finals("ahl", rows), AHL.standings_points)
    # Last 10 = games 2..11: 7 regulation wins, OT loss, SO win, regulation loss.
    assert (ahl["wins"], ahl["losses"], ahl["ot_losses"]) == (8, 1, 1)
    assert ahl["pts_pct"] == (8 * 2 + 1) / 20
    pwhl = hpr.last_ten("1", hpr.finals("pwhl", rows), PWHL.standings_points)
    assert pwhl["pts_pct"] == (7 * 3 + 2 + 1) / 30
    assert hpr.last_ten("9", hpr.finals("ahl", rows), (2, 2, 1)) is None


def test_season_in_progress_needs_a_recent_final():
    games = hpr.finals("ahl", [log(1, "2026-10-30", 1, 2, 3, 2)])
    assert not hpr.season_in_progress(games, TODAY)
    games = hpr.finals("ahl", [log(1, "2026-10-31", 1, 2, 3, 2)])
    assert hpr.season_in_progress(games, TODAY)


def test_ready_to_rank_waits_for_every_team():
    assert not hpr.ready_to_rank([])
    assert not hpr.ready_to_rank([team(1, gp=5), team(2, gp=2)])
    assert hpr.ready_to_rank([team(1, gp=5), team(2, gp=3)])


def test_pick_season_is_the_latest_started_regular_season():
    seasons = [
        {"seasonId": 90, "seasonType": "regular", "startDate": "2025-10-10"},
        {"seasonId": 92, "seasonType": "playoffs", "startDate": "2026-04-20"},
        {"seasonId": 94, "seasonType": "regular", "startDate": "2026-10-09"},
        {"seasonId": 96, "seasonType": "regular", "startDate": "2027-10-08"},
    ]
    assert hpr.pick_season(seasons, TODAY) == 94
    assert hpr.pick_season(seasons, date(2026, 5, 1)) == 90
    assert hpr.pick_season(seasons, date(2025, 1, 1)) is None


# ── ranking ───────────────────────────────────────────────────────────────


def test_better_team_on_every_component_ranks_first_with_movement_data():
    rows = [
        team(1, pts=18, gf=40, ga=20, pp=0.25, pk=0.85),
        team(2, pts=10, gf=25, ga=30, pp=0.15, pk=0.75),
        team(3, pts=12, gf=30, ga=30, pp=None, pk=0.8),
    ]
    games = hpr.finals("ahl", [log(1, "2026-11-01", 1, 2, 4, 1), log(2, "2026-11-01", 3, 2, 2, 1)])
    ranked = hpr.compute_rankings("ahl", rows, games)
    assert [t["team_id"] for t in ranked] == [1, 3, 2]
    assert [t["rank"] for t in ranked] == [1, 2, 3]
    c = ranked[0]["components"]
    assert c["pts_pct"] == 0.9 and c["gd_pg"] == 2.0 and c["l10"] == "1-0-0"
    assert c["record"] == "5-3-2" and c["gp"] == 10 and c["points"] == 18
    assert c["ranks"]["pts_pct"] == 1
    # No PP% -> no special-teams number or rank, and a neutral score term.
    third = next(t for t in ranked if t["team_id"] == 3)["components"]
    assert third["special_teams"] is None and "special_teams" not in third["ranks"]
    assert 0 <= ranked[-1]["score"] <= ranked[0]["score"] <= 1


def test_identical_teams_tie_break_deterministically():
    ranked = hpr.compute_rankings("echl", [team(5), team(2)], [])
    assert [t["team_id"] for t in ranked] == [2, 5]
    assert ranked[0]["score"] == ranked[1]["score"] == 0.5


def test_shootout_losses_count_in_the_record():
    ranked = hpr.compute_rankings("ahl", [team(1, otl=1, shootout_losses=2)], [])
    assert ranked[0]["components"]["record"] == "5-3-3"


def test_pwhl_points_pct_and_corsi():
    ranked = hpr.compute_rankings(
        "pwhl",
        [team(1, pts=24, corsi_for_pct=55.0), team(2, pts=12, corsi_for_pct=45.0)],
        [],
    )
    c = ranked[0]["components"]
    assert c["pts_pct"] == 0.8  # 24 / (10 x 3)
    assert c["cf_pct"] == 0.55 and c["ranks"]["cf_pct"] == 1


def test_points_system_is_the_league_configs(monkeypatch):
    # hockeytech_leagues.League.standings_points is the one source: change it
    # there and the rankings follow (no copy of the table in this module).
    assert not hasattr(hpr, "STANDINGS_POINTS")
    monkeypatch.setitem(hpr.LEAGUES, "ahl", replace(AHL, standings_points=(3, 2, 1)))
    ranked = hpr.compute_rankings("ahl", [team(1, pts=24), team(2, pts=12)], [])
    assert ranked[0]["components"]["pts_pct"] == 0.8  # 24 / (10 x 3)


def test_weights_add_to_one():
    best = [team(1, pts=20, gf=40, ga=10, pp=0.3, pk=0.9, corsi_for_pct=60)]
    worst = [team(2, pts=2, gf=10, ga=40, pp=0.1, pk=0.6, corsi_for_pct=40)]
    rows = [log(i, "2026-11-01", 1, 2, 3, 1) for i in range(10)]
    for key in ("ahl", "pwhl"):
        ranked = hpr.compute_rankings(key, best + worst, hpr.finals(key, rows))
        assert ranked[0]["score"] == 1.0 and ranked[1]["score"] == 0.0


# ── prompt ────────────────────────────────────────────────────────────────


def test_prompt_is_neutral_names_no_players_and_states_the_league():
    ranked = hpr.compute_rankings("ahl", [team(307, pts=18), team(309)], [])
    prompt = hpr.build_prompt(AHL, ranked, ranked[0], 2, TODAY)
    assert "HFD" in prompt and "the AHL (not the NHL), 2 teams" in prompt
    assert "CURRENT RANK: 1/2 -- up 1 from 2" in prompt
    assert "Do not name any players" in prompt
    assert "No betting" in prompt
    first = hpr.build_prompt(AHL, ranked, ranked[1], None, TODAY)
    assert "do not mention movement" in first


def test_pwhl_prompt_has_corsi():
    ranked = hpr.compute_rankings("pwhl", [team(1, corsi_for_pct=52.0), team(2)], [])
    assert "Corsi for %" in hpr.build_prompt(PWHL, ranked, ranked[0], None, TODAY)


@pytest.mark.parametrize(
    "rank,prior,text",
    [(3, None, "first ranking"), (3, 3, "unchanged"), (3, 5, "up 2"), (5, 3, "down 2")],
)
def test_movement(rank, prior, text):
    assert text in hpr.movement(rank, prior)


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
        if self.table in self.db.missing:
            raise RuntimeError(f'relation "public.{self.table}" does not exist')
        for name, a, k in self.ops:
            if name == "upsert":
                self.db.writes.append((self.table, k.get("on_conflict"), a[0]))
                return SimpleNamespace(data=a[0])
        eq = {a[0]: a[1] for name, a, _ in self.ops if name == "eq"}
        rows = [r for r in self.db.tables.get(self.table, []) if self._match(r, eq)]
        lt = {a[0]: a[1] for name, a, _ in self.ops if name == "lt"}
        rows = [r for r in rows if all(r[k] < v for k, v in lt.items())]
        if any(name == "order" and k.get("desc") for name, a, k in self.ops):
            rows = sorted(rows, key=lambda r: r["run_date"], reverse=True)
        rng = next((a for name, a, _ in self.ops if name == "range"), None)
        if rng:
            rows = rows[rng[0] : rng[1] + 1]
        lim = next((a[0] for name, a, _ in self.ops if name == "limit"), None)
        return SimpleNamespace(data=rows[:lim] if lim else rows)

    @staticmethod
    def _match(row, eq):
        return all(k not in row or row[k] == v for k, v in eq.items())


class FakeDB:
    def __init__(self, tables, missing=()):
        self.tables, self.missing, self.writes = tables, set(missing), []

    def table(self, name):
        return FakeQuery(self, name)


SEASONS = [{"seasonId": 94, "seasonType": "regular", "startDate": "2026-10-09"}]


def ahl_db(**extra):
    return FakeDB(
        {
            "ahl_team_seasons": [team(307, pts=16), team(309, pts=8), team(None)],
            "ahl_game_log": [log(1, "2026-11-01", 307, 309, 4, 2)],
            "ahl_power_rankings": [
                {"season_id": 94, "run_date": "2026-10-30", "team_id": 307, "rank": 2},
                {"season_id": 94, "run_date": "2026-10-30", "team_id": 309, "rank": 1},
                {"season_id": 94, "run_date": "2026-10-20", "team_id": 307, "rank": 1},
            ],
            "ahl_power_rankings_narratives": [
                {"season_id": 94, "run_date": TODAY.isoformat(), "team_id": 307, "locale": "en"}
            ],
        },
        **extra,
    )


@pytest.fixture
def calls(monkeypatch):
    sent = []

    def fake_generate(prompt, system=None, max_tokens=1024):
        sent.append((prompt, system))
        return "Narrative." if "HFD" in prompt.split("\n")[0] else None

    monkeypatch.setattr(hpr, "generate", fake_generate)
    monkeypatch.setattr(hpr, "league_seasons", lambda key: SEASONS)
    monkeypatch.setattr(hpr.time, "sleep", lambda s: None)
    return sent


def test_run_writes_rankings_with_prior_ranks(monkeypatch, calls):
    db = ahl_db()
    monkeypatch.setattr(hpr, "get_client", lambda: db)
    assert hpr.run("ahl", today=TODAY) == 0
    [(table, conflict, rows)] = db.writes
    assert table == "ahl_power_rankings" and conflict == "season_id,run_date,team_id"
    assert [(r["team_id"], r["rank"], r["prior_rank"]) for r in rows] == [(307, 1, 2), (309, 2, 1)]
    assert {r["run_date"] for r in rows} == {TODAY.isoformat()} and {
        r["season_id"] for r in rows
    } == {94}
    assert calls == []  # no --narratives, no AI calls


def test_run_narratives_fill_missing_pairs_in_both_locales(monkeypatch, calls):
    db = ahl_db()
    monkeypatch.setattr(hpr, "get_client", lambda: db)
    assert hpr.run("ahl", narratives=True, today=TODAY) == 0
    # 2 teams x 2 locales, minus HFD/en already written = 3 calls.
    assert len(calls) == 3
    assert sum("Réponds ENTIÈREMENT en français" in s for _, s in calls) == 2
    narr = [w for w in db.writes if w[0] == "ahl_power_rankings_narratives"]
    # Only HFD's generation "succeeds" in the fake; PRO's fail and are not written.
    assert [(w[2]["team_id"], w[2]["locale"]) for w in narr] == [(307, "fr")]
    assert narr[0][1] == "season_id,run_date,team_id,locale"


def test_run_waits_for_games_played(monkeypatch, calls):
    db = ahl_db()
    db.tables["ahl_team_seasons"] = [team(307, gp=2), team(309)]
    monkeypatch.setattr(hpr, "get_client", lambda: db)
    assert hpr.run("ahl", narratives=True, today=TODAY) == 0
    assert db.writes == [] and calls == []


def test_run_skips_nights_without_new_results(monkeypatch, calls):
    db = ahl_db()
    db.tables["ahl_game_log"] = [log(1, "2026-10-25", 307, 309, 4, 2)]
    monkeypatch.setattr(hpr, "get_client", lambda: db)
    assert hpr.run("ahl", narratives=True, today=TODAY) == 0
    assert db.writes == [] and calls == []


def test_run_tolerates_missing_tables(monkeypatch, calls):
    db = ahl_db(missing={"ahl_power_rankings", "ahl_power_rankings_narratives"})
    monkeypatch.setattr(hpr, "get_client", lambda: db)
    assert hpr.run("ahl", narratives=True, today=TODAY) == 0
    assert db.writes == [] and calls == []


def test_run_dry_run_makes_no_calls_and_no_writes(monkeypatch, calls):
    db = ahl_db()
    monkeypatch.setattr(hpr, "get_client", lambda: db)
    assert hpr.run("ahl", narratives=True, dry_run=True, today=TODAY) == 0
    assert db.writes == [] and calls == []


def test_run_needs_the_season_list(monkeypatch, calls):
    monkeypatch.setattr(hpr, "league_seasons", lambda key: None)
    assert hpr.run("ahl", today=TODAY) == 1
