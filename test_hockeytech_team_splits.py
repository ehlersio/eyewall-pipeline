"""
test_hockeytech_team_splits.py -- AHL/ECHL skater and goalie season rows are
one per (player, team), not the league-wide feed's one row per player.

The league-wide `players` view files a traded player's whole season under
his last team, so earlier teams lost him (AHL 2025-26: Graeme Clarke 65 GP /
43 pts all under BEL; Hershey's own list has him at 50 GP / 24 pts). The
team-filtered view is per team, except that a player's latest team lists
his season total and a team he was only registered with can list him too,
so multi-team players get their lines from their own `player` view.

Fixtures are real HockeyTech responses (2026-10-05), trimmed to the players
under test: tests/fixtures/hockeytech_team_splits.json.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import hockeytech_stats as hs
from hockeytech_leagues import AHL, ECHL

FIXTURES = json.loads(
    (Path(__file__).parent / "tests" / "fixtures" / "hockeytech_team_splits.json").read_text()
)


class FakeQuery:
    def __init__(self, sb, table):
        self.sb, self.table, self.ops = sb, table, []

    def __getattr__(self, name):
        def op(*args, **kwargs):
            self.ops.append((name, args))
            return self

        return op

    @property
    def not_(self):
        return self

    def execute(self):
        names = [n for n, _ in self.ops]
        if "upsert" in names:
            self.sb.upserts.setdefault(self.table, []).extend(self.ops[0][1][0])
            return SimpleNamespace(data=[])
        eq = {a[0]: a[1] for n, a in self.ops if n == "eq"}
        if "delete" in names:
            self.sb.deletes.append((self.table, eq))
            return SimpleNamespace(data=[])
        pids = next(a[1] for n, a in self.ops if n == "in_")
        rows = [r for r in self.sb.existing.get(self.table, []) if r["player_id"] in pids]
        return SimpleNamespace(data=rows)


class FakeSupabase:
    def __init__(self, existing=None):
        self.existing = existing or {}
        self.upserts, self.deletes = {}, []

    def table(self, name):
        return FakeQuery(self, name)


def _run(monkeypatch, lg, season, position, existing=None):
    fx = FIXTURES[f"{lg.key}_{season}_{position}"]
    teams = FIXTURES[f"{lg.key}_{season}_teams"]

    def ht_get(_lg, params):
        if params["view"] == "player":
            return fx["players"][params["player_id"]]
        if params.get("team"):
            return fx["listings"].get(params["team"], [{"sections": []}])
        # league-wide view: the stubs, and the fallback for a player whose
        # player view can't be used
        return fx.get("league_wide", [{"sections": []}])

    monkeypatch.setattr(hs, "ht_get", ht_get)
    monkeypatch.setattr(hs, "_modulekit_get", lambda _lg, view, p: {"Teamsbyseason": teams})
    monkeypatch.setattr(hs.time, "sleep", lambda _s: None)
    sb = FakeSupabase(existing)
    fetch = hs.fetch_skater_stats if position == "skaters" else hs.fetch_goalie_stats
    fetch(lg, sb, season, "regular")
    table = f"{lg.key}_{'player' if position == 'skaters' else 'goalie'}_seasons"
    return sb, {(r["player_id"], r["team_id"]): r for r in sb.upserts.get(table, [])}


def test_traded_skater_gets_a_row_per_team(monkeypatch):
    # Graeme Clarke, AHL 2025-26: HER 50 GP / 15 G / 24 pts, then BEL
    # 15 GP / 5 G / 19 pts. BEL's list shows his 65 GP total.
    _, rows = _run(monkeypatch, AHL, "90", "skaters")
    her, bel = rows[(8598, 319)], rows[(8598, 413)]
    assert (her["gp"], her["goals"], her["points"], her["shots"]) == (50, 15, 24, 126)
    assert (bel["gp"], bel["goals"], bel["points"], bel["shots"]) == (15, 5, 19, 46)
    assert len(rows) == 2


def test_goalie_listed_by_a_team_he_never_played_for(monkeypatch):
    # Jesper Vikman, AHL 2025-26: all 18 GP with Henderson (437); Hershey's
    # list (and the league-wide feed) file him under HER anyway.
    sb, rows = _run(
        monkeypatch,
        AHL,
        "90",
        "goalies",
        existing={"ahl_goalie_seasons": [{"player_id": 9282, "team_id": 319}]},
    )
    [(key, row)] = rows.items()
    assert key == (9282, 437)
    assert (row["gp"], row["wins"], row["saves"], row["goals_against"]) == (18, 8, 402, 62)
    # The player view has no shots column: saves + goals against.
    assert row["shots_against"] == 464
    assert row["sv_pct"] == 0.866
    # Last night's whole-season row under HER is removed.
    assert sb.deletes == [
        (
            "ahl_goalie_seasons",
            {"player_id": 9282, "team_id": 319, "season_id": 90, "season_type": "regular"},
        )
    ]


def test_echl_goalies_listed_under_the_wrong_team(monkeypatch):
    # ECHL 2025-26: Colby Muise played 8 GP for Orlando (61) only, but
    # Atlanta's (10) list carries the same line. Carter McPhail played
    # 1 GP for Greenville (52) only; Atlanta's list carries it under "GVL".
    _, rows = _run(monkeypatch, ECHL, "73", "goalies")
    assert sorted(rows) == [(10659, 61), (10762, 52)]
    assert rows[(10659, 61)]["gp"] == 8
    assert rows[(10762, 52)]["gp"] == 1


def test_single_listing_is_used_as_is_without_a_player_view(monkeypatch):
    calls = []
    fx = FIXTURES["ahl_90_skaters"]
    her_only = {"319": fx["listings"]["319"]}

    def ht_get(_lg, params):
        calls.append(params["view"])
        if params.get("team"):
            return her_only.get(params["team"], [{"sections": []}])
        return [{"sections": []}]

    monkeypatch.setattr(hs, "ht_get", ht_get)
    monkeypatch.setattr(
        hs, "_modulekit_get", lambda *_a: {"Teamsbyseason": FIXTURES["ahl_90_teams"]}
    )
    monkeypatch.setattr(hs.time, "sleep", lambda _s: None)
    sb = FakeSupabase()
    hs.fetch_skater_stats(AHL, sb, "90", "regular")
    [row] = sb.upserts["ahl_player_seasons"]
    assert (row["player_id"], row["team_id"], row["gp"]) == (8598, 319, 50)
    assert "player" not in calls
    assert sb.deletes == []


def test_unusable_player_view_falls_back_to_the_league_wide_row(monkeypatch, caplog):
    # No split we can trust: store what the league-wide feed says (Clarke's
    # whole season under BEL), as before per-team splits -- never a guess.
    fx = FIXTURES["ahl_90_skaters"]

    def ht_get(_lg, params):
        if params["view"] == "player":
            return {"seasons": [], "careerStats": []}
        if params.get("team"):
            return fx["listings"].get(params["team"], [{"sections": []}])
        return fx["league_wide"]

    monkeypatch.setattr(hs, "ht_get", ht_get)
    monkeypatch.setattr(
        hs, "_modulekit_get", lambda *_a: {"Teamsbyseason": FIXTURES["ahl_90_teams"]}
    )
    monkeypatch.setattr(hs.time, "sleep", lambda _s: None)
    sb = FakeSupabase()
    hs.fetch_skater_stats(AHL, sb, "90", "regular")
    [row] = sb.upserts["ahl_player_seasons"]
    assert (row["player_id"], row["team_id"], row["gp"], row["points"]) == (8598, 413, 65, 43)
    assert sb.deletes == []
    assert "no usable player view" in caplog.text


def test_echl_goalie_in_the_skaters_list_keeps_his_points(monkeypatch):
    # David Tendeck, ECHL 2025-26: FLA, ALN and TUL each list him among
    # skaters, but ECHL's player view gives a goalie only his goalie table
    # (no goals or assists), so his 3 assists stay on his league-wide row.
    _, rows = _run(monkeypatch, ECHL, "73", "skaters")
    assert list(rows) == [(8567, 71)]
    assert (rows[(8567, 71)]["gp"], rows[(8567, 71)]["points"]) == (27, 3)


def test_echl_skater_rows_carry_the_feed_team_id():
    rows = hs.extract_rows(FIXTURES["echl_73_skaters"]["league_wide"])
    assert rows[0]["_team_link"] == "71"
    assert hs._row_team_id(ECHL, rows[0]) == "71"


def test_teams_missing_from_the_league_map_are_named(monkeypatch, caplog):
    teams = [*FIXTURES["ahl_90_teams"], {"id": "999", "name": "New Club", "code": "NEW"}]
    monkeypatch.setattr(hs, "_modulekit_get", lambda *_a: {"Teamsbyseason": teams})
    names = hs._season_team_names(AHL, "90")
    assert names["413"] == "Belleville Senators" and "999" not in names
    assert "NEW id 999 (1)" in caplog.text
