"""
test_draft_history.py -- coverage for draft_history.py (NHL records API
draft data -> draft_pick_history).

No real network/DB calls -- fetch_picks() and upsert() are mocked. Example
teamPickHistory strings are verbatim from the live records API.
"""

import os
from datetime import date
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from draft_history import build_row, default_since, parse_pick_chain, run


class TestParsePickChain:
    def test_dash_chain_original_owner_first(self):
        assert parse_pick_chain("NYR-VAN-PIT-PHI", "PHI") == (["NYR", "VAN", "PIT", "PHI"], True)

    def test_single_team_is_an_untraded_pick(self):
        assert parse_pick_chain("NYI", "NYI") == (["NYI"], True)

    def test_repeat_owner_is_kept_in_order(self):
        chain, ok = parse_pick_chain("BOS-EDM-BOS-NYI-SJS", "SJS")
        assert ok and chain == ["BOS", "EDM", "BOS", "NYI", "SJS"]

    def test_older_from_format(self):
        assert parse_pick_chain("NJD (from ATL)", "NJD") == (["ATL", "NJD"], True)

    def test_from_own_team_means_untraded(self):
        assert parse_pick_chain("NYI (from NYI)", "NYI") == (["NYI"], True)

    def test_unparseable_keeps_drafting_team_and_flags_it(self):
        assert parse_pick_chain("NJD (from ATL via LAK)", "NJD") == (["NJD"], False)
        assert parse_pick_chain(None, "CAR") == (["CAR"], False)


PICK_2025_12 = {
    "id": 19580,
    "draftYear": 2025,
    "overallPickNumber": 12,
    "roundNumber": 1,
    "pickInRound": 12,
    "triCode": "PHI",
    "teamPickHistory": "NYR-VAN-PIT-PHI",
    "playerId": 8485380,
    "playerName": "Jack Nesbitt",
    "position": "C",
    "amateurClubName": "Windsor",
    "amateurLeague": "OHL",
    "countryCode": "CAN",
    "birthDate": "2007-01-11",
    "draftDate": "2025-06-27",
    "removedOutright": "N",
}


class TestBuildRow:
    def test_maps_fields_and_derives_chain_columns(self):
        row, ok = build_row(PICK_2025_12)
        assert ok
        assert row["team"] == "PHI"
        assert row["original_team"] == "NYR"
        assert row["pick_chain"] == ["NYR", "VAN", "PIT", "PHI"]
        assert row["times_traded"] == 3
        assert row["history_raw"] == "NYR-VAN-PIT-PHI"
        assert row["player_id"] == 8485380
        assert row["birth_date"] == "2007-01-11"
        assert row["removed_outright"] is False

    def test_missing_player_id_and_dates_stay_null(self):
        row, _ = build_row(
            {**PICK_2025_12, "playerId": None, "birthDate": None, "removedOutright": None}
        )
        assert row["player_id"] is None
        assert row["birth_date"] is None
        assert row["removed_outright"] is None

    def test_untraded_pick(self):
        row, _ = build_row({**PICK_2025_12, "teamPickHistory": "PHI"})
        assert row["original_team"] == "PHI"
        assert row["times_traded"] == 0


class TestDefaultSince:
    def test_covers_the_last_five_drafts(self):
        assert default_since(date(2026, 9, 13)) == 2022


PICKS = [
    PICK_2025_12,
    {
        **PICK_2025_12,
        "id": 2,
        "overallPickNumber": 13,
        "triCode": "NYI",
        "teamPickHistory": "NYI (from NYI)",
    },
    {
        **PICK_2025_12,
        "id": 3,
        "overallPickNumber": 14,
        "triCode": "CAR",
        "teamPickHistory": "CAR (from ??)",
    },
    {**PICK_2025_12, "id": 4, "overallPickNumber": 12},  # same (year, pick) again -> one row
    {**PICK_2025_12, "id": 5, "overallPickNumber": None},  # no pick number -> skipped
]


class TestRun:
    @patch("draft_history.fetch_picks", return_value=PICKS)
    def test_upserts_one_row_per_year_and_pick_on_the_natural_key(self, mock_fetch):
        with patch("draft_history.get_client"), patch("draft_history.upsert") as mock_upsert:
            n = run(since_year=2025)
        mock_fetch.assert_called_once_with(2025)
        assert n == 3
        _client, table, rows, conflict = mock_upsert.call_args.args
        assert table == "draft_pick_history"
        assert conflict == "draft_year,overall_pick"
        by_pick = {r["overall_pick"]: r for r in rows}
        assert by_pick[13]["pick_chain"] == ["NYI"]
        assert by_pick[14]["pick_chain"] == ["CAR"]  # unparsed -> drafting team only

    @patch("draft_history.fetch_picks", return_value=PICKS)
    def test_all_years_fetches_without_a_year_filter(self, mock_fetch):
        with patch("draft_history.upsert"):
            run(all_years=True, dry_run=True)
        mock_fetch.assert_called_once_with(None)

    @patch("draft_history.fetch_picks", return_value=PICKS)
    def test_dry_run_writes_nothing(self, _fetch):
        with patch("draft_history.upsert") as mock_upsert:
            run(since_year=2025, dry_run=True)
        mock_upsert.assert_not_called()

    @patch("draft_history.fetch_picks", side_effect=Exception("timeout"))
    def test_fetch_failure_returns_none(self, _fetch):
        with patch("draft_history.upsert") as mock_upsert:
            assert run(since_year=2025) is None
        mock_upsert.assert_not_called()
