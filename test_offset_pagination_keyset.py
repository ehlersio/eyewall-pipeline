"""
test_offset_pagination_keyset.py — regression coverage for Session 47's
Item 3: converting the remaining OFFSET-pagination sites confirmed at
real risk (audit #10) to keyset pagination.

Background: line_combinations.py's fetch_all() proved keyset pagination
fixes the Postgres 57014 statement-timeout risk that OFFSET pagination
has on shot_events/shift_events as they grow (confirmed in production,
2026-07-04 incident). This applies the same fix to shot_events.py's
get_already_processed(), moneypuck.py's run_goalie_qs() shot_events load,
zone_starts.py's get_processed_games(), and special_teams.py's
fetch_shifts_for_team() -- all season/team-scoped queries against the
same two tables, using .gt("id", last_id).order("id").limit(999) instead
of .range(offset, offset + 999).
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import moneypuck
import shot_events
import special_teams
import zone_starts


def _paged_client(rows_by_table):
    """Fake Supabase client: .table(name) returns a query builder that
    honors .gt("id", last_val).order("id").limit(n) keyset pagination
    against a flat, id-sorted row list."""
    client = MagicMock()

    def table_side_effect(name):
        all_rows = sorted(rows_by_table.get(name, []), key=lambda r: r["id"])
        q = MagicMock()
        state = {"last_val": 0, "limit": None}

        def eq(*_a, **_k):
            return q

        def neq(*_a, **_k):
            return q

        def in_(*_a, **_k):
            return q

        def not_is(*_a, **_k):
            return q

        def gt(_col, val):
            state["last_val"] = val
            return q

        def order(_col):
            return q

        def limit(n):
            state["limit"] = n
            return q

        def execute():
            page = [r for r in all_rows if r["id"] > state["last_val"]][: state["limit"]]
            return MagicMock(data=page)

        for method in ("select",):
            getattr(q, method).return_value = q
        q.eq.side_effect = eq
        q.neq.side_effect = neq
        q.in_.side_effect = in_
        q.not_ = MagicMock()
        q.not_.is_.side_effect = not_is
        q.gt.side_effect = gt
        q.order.side_effect = order
        q.limit.side_effect = limit
        q.execute.side_effect = execute
        return q

    client.table.side_effect = table_side_effect
    return client


class TestShotEventsGetAlreadyProcessed:
    def test_pages_past_the_page_size_boundary(self):
        rows = [{"id": i, "game_id": 1000 + i} for i in range(1, 1500)]
        client = _paged_client({"shot_events": rows})

        result = shot_events.get_already_processed(client, season=20252026)

        assert result == {1000 + i for i in range(1, 1500)}

    def test_empty_table_returns_empty_set(self):
        client = _paged_client({"shot_events": []})
        assert shot_events.get_already_processed(client, season=20252026) == set()


class TestMoneypuckGoalieQS:
    def test_paginates_shot_events_past_page_boundary(self):
        # 1200 shot-on-goal rows across 2 goalies -- exceeds one 999-row page
        rows = [
            {"id": i, "goalie_id": 1 if i % 2 == 0 else 2, "game_id": 500, "event_type": "shot-on-goal"}
            for i in range(1, 1201)
        ]
        client = _paged_client({"shot_events": rows})

        # run_goalie_qs prints and upserts -- just confirm it doesn't crash
        # and processes every row (visible via the printed row count).
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            moneypuck.run_goalie_qs(client, season=20252026)

        assert "Processed 1200 shot events" in buf.getvalue()


class TestZoneStartsGetProcessedGames:
    def test_pages_past_the_page_size_boundary(self):
        rows = [{"id": i, "game_id": 2000 + i} for i in range(1, 1200)]
        client = _paged_client({"zone_starts": rows})

        result = zone_starts.get_processed_games(client, season=20252026)

        assert result == {2000 + i for i in range(1, 1200)}


class TestSpecialTeamsFetchShiftsForTeam:
    def test_pages_past_the_page_size_boundary(self, monkeypatch):
        rows = [
            {"id": i, "game_id": 1, "player_id": i, "period": 1, "start_secs": 0, "end_secs": 30}
            for i in range(1, 1100)
        ]
        client = _paged_client({"shift_events": rows})
        monkeypatch.setattr(special_teams, "supabase", client)

        result = special_teams.fetch_shifts_for_team("CAR", 20252026)

        assert len(result) == 1099
