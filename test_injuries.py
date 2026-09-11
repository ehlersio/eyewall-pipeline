"""
test_injuries.py -- coverage for injuries.py (ESPN injuries feed ->
player_injuries).

No real network/DB calls -- fetch_espn_injuries() and get_client() are
mocked. normalize_status()/normalize_name() are pure and tested directly;
run() is tested end-to-end against a fake Supabase client + fake ESPN
payload, mirroring test_keyset_pagination.py's MagicMock-based client
fake (this module's query shapes are simpler -- no pagination needed).
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from injuries import normalize_name, normalize_status, run


def _fake_client(players_by_team):
    """Fake Supabase client: players_by_team = {"CAR": [{"id":1,"name":"Eric Robinson"}, ...]}.
    Tracks delete/insert calls on player_injuries for assertions."""
    client = MagicMock()
    inserted = []
    state = {"deleted": False}

    def table_side_effect(name):
        q = MagicMock()
        if name == "players":

            def select(cols):
                return q

            def eq(col, val):
                q.execute.return_value.data = players_by_team.get(val, [])
                return q

            q.select.side_effect = select
            q.eq.side_effect = eq
        elif name == "player_injuries":

            def delete():
                state["deleted"] = True
                return q

            def gte(col, val):
                return q

            def insert(rows):
                inserted.extend(rows)
                return q

            q.delete.side_effect = delete
            q.gte.side_effect = gte
            q.insert.side_effect = insert
            q.execute.return_value = MagicMock(data=None)
        return q

    client.table.side_effect = table_side_effect
    client._inserted = inserted
    client._state = state
    return client


class TestNormalizeStatus:
    def test_maps_known_espn_statuses(self):
        assert normalize_status("Day-To-Day") == "day-to-day"
        assert normalize_status("Out") == "out"
        assert normalize_status("Injured Reserve") == "injured-reserve"
        assert normalize_status("Suspension") == "suspension"

    def test_is_case_insensitive(self):
        assert normalize_status("OUT") == "out"
        assert normalize_status("day-to-day") == "day-to-day"

    def test_slugifies_an_unrecognized_status_instead_of_dropping_it(self):
        assert normalize_status("Season-Ending Injury") == "season-ending-injury"

    def test_handles_none_and_empty(self):
        assert normalize_status(None) == "unknown"
        assert normalize_status("") == "unknown"


class TestNormalizeName:
    def test_lowercases(self):
        assert normalize_name("Eric Robinson") == normalize_name("eric robinson")

    def test_strips_apostrophes(self):
        assert normalize_name("Skyler Brind'Amour") == normalize_name("Skyler BrindAmour")

    def test_strips_accents(self):
        assert normalize_name("Björn Björnsson") == normalize_name("Bjorn Bjornsson")

    def test_handles_none(self):
        assert normalize_name(None) == ""


ESPN_PAYLOAD_TWO_TEAMS = [
    {
        "id": 7,  # CAR
        "displayName": "Carolina Hurricanes",
        "injuries": [
            {
                "status": "Day-To-Day",
                "shortComment": "day-to-day",
                "date": "2026-09-10T14:06Z",
                "athlete": {"displayName": "Eric Robinson"},
            },
            {
                "status": "Out",
                "shortComment": "out",
                "date": "2026-06-27T03:22Z",
                "athlete": {"displayName": "Someone Unrostered"},
            },
        ],
    },
    {
        "id": 999999,  # not a real NHL team id
        "displayName": "Unrecognized Team",
        "injuries": [{"status": "Out", "athlete": {"displayName": "Nobody"}}],
    },
]


class TestRun:
    @patch("injuries.fetch_espn_injuries", return_value=ESPN_PAYLOAD_TWO_TEAMS)
    def test_matches_a_real_player_and_logs_an_unmatched_one_without_dropping_it(self, _mock_fetch):
        client = _fake_client({"CAR": [{"id": 8480762, "name": "Eric Robinson"}]})
        with patch("injuries.get_client", return_value=client):
            result = run(dry_run=True)

        assert result == 2  # both rows counted, matched or not
        assert client._state["deleted"] is False  # dry-run never touches the DB

    @patch("injuries.fetch_espn_injuries", return_value=ESPN_PAYLOAD_TWO_TEAMS)
    def test_writes_matched_and_unmatched_rows_with_correct_shape(self, _mock_fetch):
        client = _fake_client({"CAR": [{"id": 8480762, "name": "Eric Robinson"}]})
        with patch("injuries.get_client", return_value=client):
            run(dry_run=False)

        assert client._state["deleted"] is True
        by_name = {r["player_name"]: r for r in client._inserted}
        assert by_name["Eric Robinson"]["player_id"] == 8480762
        assert by_name["Eric Robinson"]["team"] == "CAR"
        assert by_name["Eric Robinson"]["status"] == "day-to-day"
        assert by_name["Someone Unrostered"]["player_id"] is None
        assert by_name["Someone Unrostered"]["team"] == "CAR"  # kept, not dropped

    @patch("injuries.fetch_espn_injuries", return_value=ESPN_PAYLOAD_TWO_TEAMS)
    def test_skips_an_unrecognized_espn_team_id_without_crashing(self, _mock_fetch):
        client = _fake_client({"CAR": [{"id": 8480762, "name": "Eric Robinson"}]})
        with patch("injuries.get_client", return_value=client):
            run(dry_run=True)
        # No exception raised is the assertion here -- team id 999999 has
        # no entry in ESPN_TEAM_ID_TO_ABBR and must be skipped, not crash
        # the whole run.

    @patch("injuries.fetch_espn_injuries", side_effect=Exception("network down"))
    def test_fetch_failure_returns_none_gracefully(self, _mock_fetch):
        client = _fake_client({})
        with patch("injuries.get_client", return_value=client):
            result = run(dry_run=True)
        assert result is None
        assert client._state["deleted"] is False
