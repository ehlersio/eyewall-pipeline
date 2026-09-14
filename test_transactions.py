"""
test_transactions.py -- coverage for transactions.py (ESPN NHL transactions
feed -> nhl_transactions).

No real network/DB calls -- fetch_teams()/fetch_year() and upsert() are
mocked. Every example description below is verbatim from ESPN's live feed
(2025-26), typos included.
"""

import os
from datetime import date
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from transactions import (
    HISTORICAL_TEAMS,
    build_row,
    build_team_patterns,
    categorize,
    dedupe_key,
    default_years,
    find_counterparties,
    nhl_season_for,
    primary_category,
    run,
)

ESPN_TEAMS = [
    {
        "id": "15",
        "abbreviation": "PHI",
        "location": "Philadelphia",
        "name": "Flyers",
        "displayName": "Philadelphia Flyers",
    },
    {
        "id": "16",
        "abbreviation": "PIT",
        "location": "Pittsburgh",
        "name": "Penguins",
        "displayName": "Pittsburgh Penguins",
    },
    {
        "id": "4",
        "abbreviation": "CHI",
        "location": "Chicago",
        "name": "Blackhawks",
        "displayName": "Chicago Blackhawks",
    },
    {
        "id": "7",
        "abbreviation": "CAR",
        "location": "Carolina",
        "name": "Hurricanes",
        "displayName": "Carolina Hurricanes",
    },
    {
        "id": "13",
        "abbreviation": "NYR",
        "location": "New York",
        "name": "Rangers",
        "displayName": "New York Rangers",
    },
    {
        "id": "12",
        "abbreviation": "NYI",
        "location": "New York",
        "name": "Islanders",
        "displayName": "New York Islanders",
    },
    {
        "id": "20",
        "abbreviation": "TB",
        "location": "Tampa Bay",
        "name": "Lightning",
        "displayName": "Tampa Bay Lightning",
    },
    {
        "id": "30",
        "abbreviation": "MIN",
        "location": "Minnesota",
        "name": "Wild",
        "displayName": "Minnesota Wild",
    },
]
PATTERNS = build_team_patterns(ESPN_TEAMS)


class TestCategorize:
    def test_trade(self):
        cats = categorize(
            "Acquired D Yegor Zamula from Philadelphia in exchange for F Philip Tomasino."
        )
        assert cats == ["trade"]

    def test_multi_move_entry_gets_every_category(self):
        cats = categorize(
            "Acquired F Boris Katchouk from Tampa Bay in exchange for F Michael Milne. Sent D David Jiricek to Iowa (AHL)."
        )
        assert cats == ["trade", "assignment"]

    def test_typos_seen_live_still_match(self):
        assert categorize("Singed G Callum Tung to a three-year, entry-level contract.") == [
            "signing"
        ]
        assert "recall" in categorize("Recaled D Yan Kuznetsov from Calgary (AHL).")
        assert "injury" in categorize("Activated G Thatcher Demko from injure reserve.")

    def test_unconditional_waivers_is_waivers_and_release(self):
        cats = categorize(
            "Placed LW Chris Kreider on unconditional waivers for the purpose of terminating his contract."
        )
        assert "waivers" in cats and "release" in cats

    def test_uncategorized_is_empty_and_primary_other(self):
        assert categorize("Activated C Troy Terry.") == []
        assert primary_category([]) == "other"


class TestPrimaryCategory:
    def test_trade_outranks_routine_roster_moves(self):
        assert primary_category(["recall", "trade", "assignment"]) == "trade"

    def test_release_outranks_waivers(self):
        assert primary_category(["waivers", "release"]) == "release"


class TestCounterparties:
    def test_city_full_name_and_nickname(self):
        assert find_counterparties(
            "Acquired D Yegor Zamula from Philadelphia in exchange for F X.", "PIT", PATTERNS
        ) == ["PHI"]
        assert find_counterparties(
            "Acquired F X from Pittsburgh Penguins for D Y.", "PHI", PATTERNS
        ) == ["PIT"]
        assert find_counterparties(
            "Acquired F X from Tampa Bay in exchange for F Y.", "MIN", PATTERNS
        ) == ["TBL"]

    def test_own_team_is_excluded(self):
        assert (
            find_counterparties("Traded F X to the Minnesota Wild for F Y.", "MIN", PATTERNS) == []
        )

    def test_new_york_never_matches_on_city_alone(self):
        assert (
            find_counterparties("Acquired F X from New York in exchange for F Y.", "CAR", PATTERNS)
            == []
        )
        assert find_counterparties(
            "Acquired F X from the New York Rangers for F Y.", "CAR", PATTERNS
        ) == ["NYR"]
        assert find_counterparties(
            "Claimed F X off waivers from the Islanders.", "CAR", PATTERNS
        ) == ["NYI"]

    def test_ahl_affiliate_city_is_not_a_counterparty(self):
        desc = "Acquired F X from Tampa Bay for F Y. Recalled G Z from Chicago (AHL)."
        assert find_counterparties(desc, "CAR", PATTERNS) == ["TBL"]

    def test_affiliate_strip_handles_espns_curly_apostrophe(self):
        # _AFFILIATE_RE spells the curly apostrophe (U+2019) as an escape
        # inside a raw string -- this only works because `re` expands the
        # escape itself. The possessive "Minnesota" + curly apostrophe + "s"
        # must still be stripped as part of the affiliate mention, or the
        # Wild would be tagged as a counterparty.
        desc = "Acquired F X from Tampa Bay for F Y. Assigned D Z to Minnesota\u2019s Iowa (AHL)."
        assert find_counterparties(desc, "CAR", PATTERNS) == ["TBL"]


class TestSeasonAndKeys:
    def test_july_first_boundary(self):
        assert nhl_season_for(date(2026, 7, 1)) == 20262027
        assert nhl_season_for(date(2026, 6, 30)) == 20252026
        assert nhl_season_for(date(2027, 1, 15)) == 20262027

    def test_dedupe_key_is_stable_and_text_sensitive(self):
        a = dedupe_key("2026-09-10", 7, "Signed D Mike Reilly to a one-year contract.")
        assert a == dedupe_key("2026-09-10", 7, "Signed D Mike Reilly to a one-year contract.")
        assert a != dedupe_key("2026-09-10", 7, "Signed D Mike Reilly to a two-year contract.")

    def test_default_years_adds_last_year_only_in_january(self):
        assert default_years(date(2026, 1, 10)) == [2025, 2026]
        assert default_years(date(2026, 9, 12)) == [2026]


class TestBuildRow:
    def test_maps_espn_abbrev_to_app_abbrev_and_sets_counterparties_for_trades_only(self):
        row = build_row(
            {
                "date": "2025-12-28T08:00Z",
                "team": {"id": "30"},
                "description": "Acquired F Boris Katchouk from Tampa Bay in exchange for F Michael Milne.",
            },
            PATTERNS,
        )
        assert row["team"] == "MIN"
        assert row["tx_date"] == "2025-12-28"
        assert row["season"] == 20252026
        assert row["primary_category"] == "trade"
        assert row["counterparties"] == ["TBL"]

    def test_non_trade_entries_get_no_counterparties(self):
        row = build_row(
            {
                "date": "2026-09-09T07:00Z",
                "team": {"id": "7"},
                "description": "Recalled G X from Chicago (AHL).",
            },
            PATTERNS,
        )
        assert row["counterparties"] == []

    def test_arizona_history_is_kept_as_ari(self):
        # ESPN id 24 is gone from /teams (the franchise moved to Utah) but its
        # 2015-24 entries are still in the feed
        patterns = build_team_patterns(ESPN_TEAMS + HISTORICAL_TEAMS)
        own = build_row(
            {
                "date": "2019-02-20T08:00Z",
                "team": {"id": "24"},
                "description": "Acquired D Stefan Elliott from Chicago for D Victor Bartley.",
            },
            patterns,
        )
        assert own["team"] == "ARI" and own["counterparties"] == ["CHI"]
        other = build_row(
            {
                "date": "2019-02-20T08:00Z",
                "team": {"id": "4"},
                "description": "Acquired D Victor Bartley from the Arizona Coyotes for D Stefan Elliott.",
            },
            patterns,
        )
        assert other["counterparties"] == ["ARI"]

    def test_unrecognized_team_or_empty_text_is_skipped(self):
        assert (
            build_row(
                {"date": "2026-09-09T07:00Z", "team": {"id": "999999"}, "description": "Signed X."},
                PATTERNS,
            )
            is None
        )
        assert (
            build_row(
                {"date": "2026-09-09T07:00Z", "team": {"id": "7"}, "description": "  "}, PATTERNS
            )
            is None
        )


ENTRIES = [
    {
        "date": "2026-09-10T07:00Z",
        "team": {"id": "7"},
        "description": "Signed D Mike Reilly to a one-year contract.",
    },
    # Same entry repeated across a page boundary -> one row
    {
        "date": "2026-09-10T07:00Z",
        "team": {"id": "7"},
        "description": "Signed D Mike Reilly to a one-year contract.",
    },
    {"date": "2026-09-09T07:00Z", "team": {"id": "999999"}, "description": "Not an NHL team."},
]


class TestRun:
    @patch("transactions.fetch_year", return_value=ENTRIES)
    @patch("transactions.fetch_teams", return_value=ESPN_TEAMS)
    def test_dedupes_skips_and_upserts_on_dedupe_key(self, _teams, _year):
        with patch("transactions.get_client"), patch("transactions.upsert") as mock_upsert:
            n = run(years=[2026])
        assert n == 1
        _client, table, rows, conflict = mock_upsert.call_args.args
        assert table == "nhl_transactions"
        assert conflict == "dedupe_key"
        assert rows[0]["team"] == "CAR"
        assert rows[0]["primary_category"] == "signing"

    @patch("transactions.fetch_year", return_value=ENTRIES)
    @patch("transactions.fetch_teams", return_value=ESPN_TEAMS)
    def test_dry_run_writes_nothing(self, _teams, _year):
        with patch("transactions.upsert") as mock_upsert:
            run(years=[2026], dry_run=True)
        mock_upsert.assert_not_called()

    @patch("transactions.fetch_teams", side_effect=Exception("403"))
    def test_fetch_failure_returns_none(self, _teams):
        with patch("transactions.upsert") as mock_upsert:
            assert run(years=[2026]) is None
        mock_upsert.assert_not_called()
