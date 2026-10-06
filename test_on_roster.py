"""{league}_players.on_roster from the nightly roster ingest (2026-10).

Each team whose roster came back gets on_roster = true on those players and
false on the team's other rows (one update per team). Only the nightly,
current-season run marks; a backfill of another season doesn't. Until the
owner runs docs/2026-10-06_on_roster.sql the column is missing: the first
write's error is logged once and the rosters are written as before.
"""

import logging
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import hockeytech_stats as hs
import pwhl_stats
from hockeytech_leagues import AHL
from pipeline_common import OnRosterMarker


class FakeQuery:
    def __init__(self, sb, table):
        self.sb, self.table, self.ops = sb, table, []

    def __getattr__(self, name):
        def op(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self

        return op

    def execute(self):
        self.sb.calls.append((self.table, self.ops))
        if self.ops and self.ops[0][0] == "upsert" and self.sb.missing_column:
            rows = self.ops[0][1][0]
            if any("on_roster" in r for r in rows):
                raise Exception(
                    "{'code': 'PGRST204', 'message': \"Could not find the 'on_roster' "
                    f"column of '{self.table}' in the schema cache\"}}"
                )

        return SimpleNamespace(data=[])


class FakeSB:
    def __init__(self, missing_column=False):
        self.missing_column = missing_column
        self.calls = []

    def table(self, name):
        return FakeQuery(self, name)

    def upserts(self):
        return [ops[0][1][0] for _t, ops in self.calls if ops[0][0] == "upsert"]

    def updates(self):
        return [ops for _t, ops in self.calls if ops[0][0] == "update"]


def _upsert(sb, table):
    def go(rows):
        if rows:
            sb.table(table).upsert(rows, on_conflict="player_id").execute()
        return len(rows)

    return go


ROWS = [{"player_id": 1, "team_id": 5}, {"player_id": 2, "team_id": 5}]


def test_marks_the_roster_true_and_the_teams_other_rows_false():
    sb = FakeSB()
    m = OnRosterMarker(sb, "ahl_players")
    assert m.write(5, ROWS, _upsert(sb, "ahl_players")) == 2
    assert [r["on_roster"] for r in sb.upserts()[0]] == [True, True]
    assert sb.updates() == [
        [
            ("update", ({"on_roster": False},), {}),
            ("eq", ("team_id", 5), {}),
            ("filter", ("player_id", "not.in", "(1,2)"), {}),
        ]
    ]


def test_disabled_writes_rows_unmarked_and_updates_nothing():
    sb = FakeSB()
    OnRosterMarker(sb, "ahl_players", enabled=False).write(5, ROWS, _upsert(sb, "ahl_players"))
    assert sb.upserts() == [ROWS]
    assert sb.updates() == []


def test_empty_roster_touches_nothing():
    sb = FakeSB()
    assert OnRosterMarker(sb, "ahl_players").write(5, [], _upsert(sb, "ahl_players")) == 0
    assert sb.calls == []


def test_missing_column_is_logged_once_and_rosters_still_written(caplog):
    sb = FakeSB(missing_column=True)
    m = OnRosterMarker(sb, "pwhl_players")
    with caplog.at_level(logging.WARNING):
        m.write(5, ROWS, _upsert(sb, "pwhl_players"))
        m.write(6, [{"player_id": 3, "team_id": 6}], _upsert(sb, "pwhl_players"))
    warnings = [r for r in caplog.records if "on_roster is missing" in r.getMessage()]
    assert len(warnings) == 1
    # first attempt (with on_roster) failed; then plain rows, team 5 and team 6
    assert sb.upserts()[1:] == [ROWS, [{"player_id": 3, "team_id": 6}]]
    assert sb.updates() == []


def test_other_write_errors_still_raise():
    class Boom(FakeSB):
        def table(self, name):
            raise RuntimeError("connection reset")

    with pytest.raises(RuntimeError):
        OnRosterMarker(Boom(), "ahl_players").write(5, ROWS, _upsert(Boom(), "ahl_players"))


def test_hockeytech_nightly_marks_backfill_does_not(monkeypatch):
    seen = []
    monkeypatch.setattr(hs, "create_client", lambda *a, **k: FakeSB())
    monkeypatch.setattr(
        hs, "resolve_current_season", lambda lg: {"season_id": 93, "season_type": "regular"}
    )
    monkeypatch.setattr(hs, "resolve_season_type", lambda lg, sid: "regular")
    monkeypatch.setattr(
        hs, "fetch_roster", lambda *a, mark_on_roster=False, **k: seen.append(mark_on_roster)
    )
    for name in (
        "fetch_skater_stats",
        "fetch_goalie_stats",
        "fetch_team_stats",
        "fetch_game_log",
        "run_upcoming_game_logs",
    ):
        monkeypatch.setattr(hs, name, lambda *a, **k: None)
    hs.run(AHL)
    hs.run(AHL, "90")
    assert seen == [True, False]


def test_hockeytech_fetch_roster_marks_each_team(monkeypatch):
    sb = FakeSB()
    monkeypatch.setattr(hs.time, "sleep", lambda _s: None)
    roster = {"Roster": [{"player_id": "11", "first_name": "A", "last_name": "B"}]}
    monkeypatch.setattr(hs, "_modulekit_get", lambda *a, **k: roster)
    hs.fetch_roster(AHL, sb, "93", "regular", mark_on_roster=True)
    assert len(sb.updates()) == len(AHL.team_id_map)
    assert all(r[0]["on_roster"] is True for r in sb.upserts())


def test_pwhl_nightly_marks_backfill_does_not(monkeypatch):
    seen = []
    monkeypatch.setattr(pwhl_stats, "create_client", lambda *a, **k: FakeSB())
    monkeypatch.setattr(pwhl_stats, "_resolve_season_type", lambda sid: "regular")
    monkeypatch.setattr(
        pwhl_stats,
        "fetch_roster",
        lambda sb, sid, mark_on_roster=False: seen.append((sid, mark_on_roster)),
    )
    for name in (
        "ensure_season_row",
        "fetch_skater_stats",
        "fetch_goalie_stats",
        "fetch_team_stats",
        "fetch_game_log",
    ):
        monkeypatch.setattr(pwhl_stats, name, lambda *a, **k: None)
    pwhl_stats.run()
    pwhl_stats.run("")
    pwhl_stats.run("9")
    assert seen == [
        (pwhl_stats.PWHL_SEASON, True),
        (pwhl_stats.PWHL_SEASON, True),
        ("9", False),
    ]
