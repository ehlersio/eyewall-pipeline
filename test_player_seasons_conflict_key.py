"""
test_player_seasons_conflict_key.py — regression coverage for Session 81's
duplicate-row fix.

nhl_stats.py and moneypuck.py both used to upsert to player_seasons/
goalie_seasons on a conflict key that included `team`. nhl_stats.py's
`team` (NHL API's teamAbbrevs, possibly comma-joined trade history like
"VAN,SJS") and moneypuck.py's `team` (MoneyPuck's CSV, current team only,
e.g. "SJS") don't reliably match for a traded player -- so the upsert
couldn't find the existing row and created a second one instead. Found in
production during a live backfill: 338 duplicate (player_id, season,
game_type) pairs in player_seasons, 2 in goalie_seasons -- one row per
pair had real box-score stats and no analytics, the other had WAR/
percentiles and no box-score stats. All were merged and deleted in
production before this fix landed (see docs/session81_dedupe_constraint_fix.sql
for the matching unique-constraint migration).

The fix: drop `team` from the conflict key entirely (player_id,season,
game_type is the real identity), and stop moneypuck.py from writing
`team` in its payload at all -- it's nhl_stats.py's column to own.

These tests assert both properties directly against the actual upsert
calls moneypuck.py's goalie-writing functions make, using fake Supabase
clients (same convention as test_nhl_hits_penalties.py's
TestRunTeamHitsPenaltiesRollup) -- not just checking source text.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import moneypuck


class _FakeQuery:
    """Minimal chainable query builder — every filter/order/limit method
    returns self; execute() returns whatever page is next (or empty)."""

    def __init__(self, pages=None):
        self._pages = list(pages) if pages else [[]]
        self.upserts = []

    def select(self, *_a, **_kw):
        return self

    def eq(self, *_a, **_kw):
        return self

    def in_(self, *_a, **_kw):
        return self

    def gt(self, *_a, **_kw):
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *_a, **_kw):
        return self

    def upsert(self, batch, on_conflict=None):
        self.upserts.append({"batch": batch, "on_conflict": on_conflict})
        return self

    def execute(self):
        class R:
            pass

        r = R()
        r.data = self._pages.pop(0) if self._pages else []
        return r


class _FakeClient:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return self._tables[name]


class TestRunGoalieQSConflictKey:
    def test_upserts_without_team_in_conflict_key_or_payload(self):
        # sa (shots-against) must clear the sa>=5 "real workload" floor in
        # run_goalie_qs -- fewer than that is treated as garbage-time/
        # backup and skipped entirely, which would leave nothing to
        # upsert and make this fixture prove nothing.
        shot_events_pages = [
            [
                {"id": i, "goalie_id": 555, "game_id": 1, "event_type": "shot-on-goal"}
                for i in range(1, 6)
            ]
            + [{"id": 6, "goalie_id": 555, "game_id": 1, "event_type": "goal"}],
            [],
        ]
        goalie_seasons = _FakeQuery()
        client = _FakeClient(
            {"shot_events": _FakeQuery(shot_events_pages), "goalie_seasons": goalie_seasons}
        )

        moneypuck.run_goalie_qs(client, season=20252026)

        assert len(goalie_seasons.upserts) == 1
        call = goalie_seasons.upserts[0]
        assert call["on_conflict"] == "player_id,season,game_type"
        for row in call["batch"]:
            assert "team" not in row


class TestRunGoaliesConflictKey:
    def test_upserts_without_team_in_conflict_key_or_payload(self, monkeypatch):
        rows = [
            {
                "playerId": "555",
                "situation": "all",
                "team": "SJS",
                "games_played": "20",
                "icetime": "72000",
                "flurryAdjustedxGoals": "50",
                "goals": "45",
            },
            {
                "playerId": "555",
                "situation": "5on5",
                "ongoal": "400",
                "goals": "35",
            },
            {
                "playerId": "555",
                "situation": "4on5",
                "ongoal": "50",
                "goals": "8",
            },
        ]
        monkeypatch.setattr(moneypuck, "fetch_csv", lambda *_a, **_kw: rows)

        goalie_seasons = _FakeQuery()
        client = _FakeClient({"goalie_seasons": goalie_seasons})

        moneypuck.run_goalies(client, season=20252026)

        assert len(goalie_seasons.upserts) == 1
        call = goalie_seasons.upserts[0]
        assert call["on_conflict"] == "player_id,season,game_type"
        for row in call["batch"]:
            assert "team" not in row
