"""
test_draft_ingest_pick_order.py — regression coverage for Session 51's
draft_ingest.py --sync-pick-order (draft_pick_order_2026's NHL-API-backed
replacement for Tankathon, now that the 2026 draft is over).

Session 49 found Tankathon rolling over to next year's *projected* order
right after each draft concludes, silently corrupting draft_pick_order_2026
(30/32 Round 1 picks held 2027 data at the time). PR #20 added a loud
year-guard to tankathon_ingest.py for that (see test_tankathon_year_guard.py)
-- untouched here. This test file covers the new NHL-API sync path instead:
parse_pick_order_row()'s schema match with tankathon_ingest.py's
draft_pick_order_2026 columns, and _original_team_from_history()'s parsing
of the NHL API's teamPickHistory field (single team = never traded = None,
multi-team = first segment is the original owner).
"""

from unittest.mock import MagicMock, patch

import draft_ingest as di

# Real shapes pulled live from /draft/picks/2026/all (2026-07-10).
PICK_NO_TRADE = {
    "round": 1,
    "pickInRound": 1,
    "overallPick": 1,
    "teamAbbrev": "TOR",
    "teamPickHistory": "TOR",
}

PICK_ONE_TRADE = {
    "round": 1,
    "pickInRound": 15,
    "overallPick": 15,
    "teamAbbrev": "ANA",
    "teamPickHistory": "DET-STL-ANA",
}

PICK_MULTI_HOP = {
    "round": 1,
    "pickInRound": 26,
    "overallPick": 26,
    "teamAbbrev": "MTL",
    "teamPickHistory": "DAL-CAR-NYR-VGK-MTL",
}


class TestOriginalTeamFromHistory:
    def test_no_trade_returns_none(self):
        assert di._original_team_from_history(PICK_NO_TRADE) is None

    def test_single_trade_returns_first_segment(self):
        assert di._original_team_from_history(PICK_ONE_TRADE) == "DET"

    def test_multi_hop_returns_original_not_intermediate(self):
        assert di._original_team_from_history(PICK_MULTI_HOP) == "DAL"

    def test_missing_history_field_returns_none(self):
        assert di._original_team_from_history({"teamAbbrev": "TOR"}) is None

    def test_empty_history_string_returns_none(self):
        assert di._original_team_from_history({"teamPickHistory": ""}) is None


class TestParsePickOrderRow:
    def test_no_trade_row_matches_tankathon_schema(self):
        row = di.parse_pick_order_row(PICK_NO_TRADE)
        assert row == {
            "pick_overall": 1,
            "round": 1,
            "pick_in_round": 1,
            "team_abbrev": "TOR",
            "original_team": None,
        }

    def test_traded_pick_row_sets_original_team(self):
        row = di.parse_pick_order_row(PICK_ONE_TRADE)
        assert row == {
            "pick_overall": 15,
            "round": 1,
            "pick_in_round": 15,
            "team_abbrev": "ANA",
            "original_team": "DET",
        }

    def test_row_keys_match_tankathon_ingest_columns(self):
        # tankathon_ingest.py's upsert_rows() strips 'forfeited' and writes
        # exactly these 5 columns (see upsert_rows() docstring/on_conflict).
        # A schema drift here would silently break the on_conflict="pick_overall"
        # upsert compatibility this swap depends on.
        row = di.parse_pick_order_row(PICK_MULTI_HOP)
        assert set(row.keys()) == {
            "pick_overall",
            "round",
            "pick_in_round",
            "team_abbrev",
            "original_team",
        }


class TestSyncPickOrder:
    @patch("draft_ingest.upsert")
    @patch("draft_ingest.get_client")
    @patch("draft_ingest.nhl_get")
    def test_upserts_all_picks_on_pick_overall_conflict(
        self, mock_nhl_get, mock_get_client, mock_upsert
    ):
        mock_nhl_get.return_value = {"picks": [PICK_NO_TRADE, PICK_ONE_TRADE, PICK_MULTI_HOP]}
        mock_sb = MagicMock()
        mock_get_client.return_value = mock_sb

        di.sync_pick_order()

        mock_nhl_get.assert_called_once_with(f"/draft/picks/{di.DRAFT_YEAR}/all")
        mock_upsert.assert_called_once()
        args, kwargs = mock_upsert.call_args
        assert args[0] is mock_sb
        assert args[1] == "draft_pick_order_2026"
        assert len(args[2]) == 3
        assert kwargs["conflict"] == "pick_overall"

    @patch("draft_ingest.upsert")
    @patch("draft_ingest.get_client")
    @patch("draft_ingest.nhl_get")
    def test_fewer_than_224_picks_warns_but_still_upserts(
        self, mock_nhl_get, mock_get_client, mock_upsert
    ):
        mock_nhl_get.return_value = {"picks": [PICK_NO_TRADE]}
        mock_get_client.return_value = MagicMock()

        di.sync_pick_order()

        mock_upsert.assert_called_once()
