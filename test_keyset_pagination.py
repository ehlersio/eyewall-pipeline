"""
test_keyset_pagination.py — regression coverage for Session 47's keyset
pagination fix in rapm.py / score_state.py.

Background: rapm.py and score_state.py page through shift_events and
shot_events league-wide (all 32 teams) across a 3-season pool using OFFSET
pagination. On 2026-07-04, the same table (shift_events), scoped to a
single team/season, already hit a Postgres `57014` statement timeout via
OFFSET pagination in line_combinations.py -- fixed there with keyset
(cursor-based) pagination. This applies the same fix to rapm.py's and
score_state.py's league-wide pool loads, which run at a larger scope than
the query that originally failed.

fetch_all_keyset() is NOT used for every fetch_all() call in either module
-- game_log has no `id` column (composite game_id+team rows) and
player_score_state_dist has no surrogate key, so those stay on offset
pagination.
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import rapm
import score_state


def _paged_client(pages_by_table):
    """Build a fake Supabase client whose .table(name) returns rows from
    `pages_by_table[name]`, a flat list of row dicts (each with an "id"),
    paginated as fetch_all_keyset would request them (id > last_val,
    ordered, limited to page_size)."""
    client = MagicMock()

    def table_side_effect(name):
        all_rows = sorted(pages_by_table.get(name, []), key=lambda r: r["id"])
        q = MagicMock()
        state = {"last_val": 0, "limit": None}

        def gt(col, val):
            state["last_val"] = val
            return q

        def order(col):
            return q

        def limit(n):
            state["limit"] = n
            return q

        def execute():
            page = [r for r in all_rows if r["id"] > state["last_val"]][: state["limit"]]
            return MagicMock(data=page)

        for method in ("select", "eq", "in_"):
            getattr(q, method).return_value = q
        q.gt.side_effect = gt
        q.order.side_effect = order
        q.limit.side_effect = limit
        q.execute.side_effect = execute
        return q

    client.table.side_effect = table_side_effect
    return client


class TestFetchAllKeyset:
    def test_pages_past_the_page_size_boundary(self):
        rows = [{"id": i, "season": 20252026} for i in range(1, 2501)]
        client = _paged_client({"shot_events": rows})

        result = rapm.fetch_all_keyset(
            client, "shot_events", "id,season", {"season": 20252026}, page_size=999
        )

        assert len(result) == 2500
        assert [r["id"] for r in result] == list(range(1, 2501))

    def test_empty_table_returns_empty_list(self):
        client = _paged_client({"shift_events": []})

        result = score_state.fetch_all_keyset(
            client, "shift_events", "id,season", {"season": 20252026}
        )

        assert result == []

    def test_stops_exactly_on_a_full_final_page(self):
        """A final page that happens to be exactly page_size rows must not
        trigger an extra, unnecessary round-trip that returns empty."""
        rows = [{"id": i, "season": 20252026} for i in range(1, 4)]
        client = _paged_client({"shot_events": rows})

        result = rapm.fetch_all_keyset(
            client, "shot_events", "id,season", {"season": 20252026}, page_size=3
        )

        assert len(result) == 3
        assert [r["id"] for r in result] == [1, 2, 3]
