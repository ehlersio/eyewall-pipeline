"""line_combinations.py builds lines per game type, never from preseason.

Until 2026-09 it built a season's lines from every game_log game, so on
2026-09-29 all 224 of 2026-27's rows were preseason groupings. Now: the
regular season and the playoffs separately; nothing (and old rows
cleared) before a team's first regular-season game; playoff gaps filled
from the same season's regular-season units.
"""

from types import SimpleNamespace

import line_combinations as lc

SEASON = 20262027


class RecordingClient:
    def __init__(self):
        self.ops = []

    def table(self, name):
        client = self

        class Q:
            def __init__(self):
                self.filters = []
                self.kind = None
                self.payload = None

            def delete(self):
                self.kind = "delete"
                return self

            def insert(self, rows):
                self.kind, self.payload = "insert", rows
                return self

            def eq(self, col, val):
                self.filters.append((col, val))
                return self

            def execute(self):
                client.ops.append((name, self.kind, tuple(self.filters), self.payload))
                return SimpleNamespace(data=[])

        return Q()


def unit(unit_type, rank, *players):
    return {
        "season": SEASON,
        "team": "CAR",
        "unit_type": unit_type,
        "rank": rank,
        "player_a": players[0],
        "player_b": players[1],
        "player_c": players[2] if len(players) > 2 else None,
    }


def full_set():
    return [unit("F", i, 10 * i, 10 * i + 1, 10 * i + 2) for i in range(1, 5)] + [
        unit("D", i, 100 * i, 100 * i + 1) for i in range(1, 4)
    ]


def patch(monkeypatch, game_ids, computed, roster=None, regular_units=()):
    calls = {}

    def compute(_c, _team, _season, game_type, ids):
        calls.setdefault("computed", []).append((game_type, sorted(ids)))
        return [dict(r) for r in computed.get(game_type, [])]

    def prior(_c, _team, season, game_type):
        calls.setdefault("prior", []).append((season, game_type))
        return [dict(r) for r in regular_units]

    monkeypatch.setattr(lc, "fetch_team_game_ids", lambda *_a: game_ids)
    monkeypatch.setattr(lc, "compute_current_season_rows", compute)
    monkeypatch.setattr(lc, "fetch_current_roster_ids", lambda _t: roster)
    monkeypatch.setattr(lc, "fetch_prior_units", prior)
    return calls


def test_before_the_opener_clears_regular_season_rows_and_builds_nothing(monkeypatch):
    calls = patch(monkeypatch, {1: [2026010001, 2026010002]}, {})
    client = RecordingClient()

    assert lc.run_team(client, "CAR", SEASON) is None

    assert "computed" not in calls  # preseason games are never built into lines
    assert client.ops == [
        (
            "line_combinations",
            "delete",
            (("season", SEASON), ("team", "CAR"), ("game_type", 2)),
            None,
        )
    ]


def test_regular_season_lines_come_from_regular_season_games_only(monkeypatch):
    calls = patch(monkeypatch, {1: [2026010001], 2: [2026020005, 2026020001]}, {2: full_set()})
    client = RecordingClient()

    assert lc.run_team(client, "CAR", SEASON) == 7

    assert calls["computed"] == [(2, [2026020001, 2026020005])]
    (rows,) = [op[3] for op in client.ops if op[1] == "insert"]
    assert {r["game_type"] for r in rows} == {2}
    assert {r["source"] for r in rows} == {"current"}


def test_playoff_gaps_fill_from_the_same_seasons_regular_season_units(monkeypatch):
    regular = full_set()
    playoff = [unit("F", 1, 10, 11, 12), unit("D", 1, 100, 101)]
    roster = {p for r in regular for p in (r["player_a"], r["player_b"], r["player_c"]) if p}
    calls = patch(
        monkeypatch,
        {2: [2026020001], 3: [2026030111]},
        {2: regular, 3: playoff},
        roster=roster,
        regular_units=regular,
    )
    client = RecordingClient()

    lc.run_team(client, "CAR", SEASON)

    assert calls["prior"] == [(SEASON, 2)]  # this season's regular season, not last season's
    inserts = {op[3][0]["game_type"]: op[3] for op in client.ops if op[1] == "insert"}
    playoff_rows = inserts[3]
    assert {r["game_type"] for r in playoff_rows} == {3}
    by_source = {}
    for r in playoff_rows:
        by_source.setdefault(r["source"], []).append((r["unit_type"], r["rank"]))
    assert by_source["current"] == [("F", 1), ("D", 1)]
    assert sorted(by_source["regular_season"]) == [("D", 2), ("D", 3), ("F", 2), ("F", 3), ("F", 4)]
    deletes = [op[2] for op in client.ops if op[1] == "delete"]
    assert (("season", SEASON), ("team", "CAR"), ("game_type", 3)) in deletes


def test_regular_season_gaps_fill_from_last_seasons_regular_season_units(monkeypatch):
    calls = patch(
        monkeypatch,
        {2: [2026020001]},
        {2: [unit("F", 1, 10, 11, 12)]},
        roster={10, 11, 12, 20, 21, 22},
        regular_units=[unit("F", 1, 20, 21, 22)],
    )
    client = RecordingClient()

    lc.run_team(client, "CAR", SEASON)

    assert calls["prior"] == [(SEASON - 10001, 2)]
    (rows,) = [op[3] for op in client.ops if op[1] == "insert"]
    assert [(r["rank"], r["source"]) for r in rows] == [(1, "current"), (2, "prior_season")]
