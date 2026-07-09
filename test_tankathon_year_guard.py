"""
test_tankathon_year_guard.py — regression coverage for Session 49's
tankathon_ingest.py fix.

TANKATHON_URL has no year in it -- the page always serves "whichever draft
is next" and rolls over to the following year's order right after the
current draft concludes. Before this fix, scrape_draft_order() had no way
to notice that and would happily parse + upsert whatever year the page
showed into the hardcoded draft_pick_order_2026 table. Confirmed live
(Session 49): this already silently overwrote 30/32 Round 1 picks with
2027 team assignments via the scheduled weekly sync.

_extract_draft_year() + the check in scrape_draft_order() make a year
mismatch a loud RuntimeError instead of a silent bad write.
"""

from unittest.mock import MagicMock, patch

import pytest

import tankathon_ingest as ti

VALID_TITLE_HTML = "<html><head><title>2026 NHL Draft Order | Tankathon</title></head></html>"
ROLLED_OVER_TITLE_HTML = "<html><head><title>2027 NHL Draft Order | Tankathon</title></head></html>"
MALFORMED_TITLE_HTML = "<html><head><title>Tankathon</title></head></html>"

MINIMAL_VALID_PAGE = (
    "<html><head><title>2026 NHL Draft Order | Tankathon</title></head><body>"
    '<div class="round-title">1st Round</div>'
    "<table><tr>"
    '<td class="pick-number">1</td>'
    '<td><img class="logo-thumb" src="/logos/nhl/tor.svg"></td>'
    "</tr></table>"
    "</body></html>"
)


class TestExtractDraftYear:
    def test_extracts_year_from_valid_title(self):
        assert ti._extract_draft_year(VALID_TITLE_HTML) == 2026

    def test_extracts_rolled_over_year(self):
        assert ti._extract_draft_year(ROLLED_OVER_TITLE_HTML) == 2027

    def test_returns_none_for_malformed_title(self):
        assert ti._extract_draft_year(MALFORMED_TITLE_HTML) is None

    def test_returns_none_for_empty_html(self):
        assert ti._extract_draft_year("") is None


class TestScrapeDraftOrderYearGuard:
    @patch("tankathon_ingest.requests.get")
    def test_raises_when_page_year_does_not_match_draft_year(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = ROLLED_OVER_TITLE_HTML
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with pytest.raises(RuntimeError, match="2027"):
            ti.scrape_draft_order()

    @patch("tankathon_ingest.requests.get")
    def test_raises_when_title_year_missing_entirely(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = MALFORMED_TITLE_HTML
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with pytest.raises(RuntimeError, match="None"):
            ti.scrape_draft_order()

    @patch("tankathon_ingest.requests.get")
    def test_proceeds_and_parses_when_year_matches(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = MINIMAL_VALID_PAGE
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        rows = ti.scrape_draft_order()

        assert rows == [
            {
                "pick_overall": 1,
                "round": 1,
                "pick_in_round": 1,
                "team_abbrev": "TOR",
                "original_team": None,
                "forfeited": False,
            }
        ]
