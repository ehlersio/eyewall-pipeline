"""
test_nhl_percentile_expansion.py — coverage for Session 81's two additions
to moneypuck.py:

1. 11 new percentile categories (GP, +/-, SHG, GWG, Shots, TOI/G, FO%,
   Hits, Blocks, Takeaways, Giveaways) sourced from player_seasons (via
   load_player_box_stats) rather than the MoneyPuck CSV, which doesn't
   carry these fields at all.
2. Conference/division-scoped percentiles for all 21 categories, using
   team_seasons.conference_abbrev/division_abbrev (already populated,
   Sessions 57-59) joined via each player's resolved current team.

Both resolve_scoping_team() and build_sorted_pool()/percentile_rank()
(already tested elsewhere) are pure/module-level so these are unit-testable
without a full CSV fetch or Supabase mock -- same convention as
test_toi_display_floor.py and test_results_vs_process.py.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import moneypuck


class TestResolveScopingTeam:
    """Confirmed live (Session 81 investigation) against 4 real 2025-26
    trades via player/{id}/landing's currentTeamAbbrev -- the LAST
    comma-separated token is always the player's current team."""

    def test_single_team_returned_as_is(self):
        assert moneypuck.resolve_scoping_team("CAR") == "CAR"

    def test_traded_player_returns_last_team(self):
        assert moneypuck.resolve_scoping_team("STL,DET") == "DET"

    def test_multiple_trades_returns_final_team(self):
        assert moneypuck.resolve_scoping_team("VAN,MIN,BOS") == "BOS"

    def test_none_returns_none(self):
        assert moneypuck.resolve_scoping_team(None) is None

    def test_empty_string_returns_none(self):
        assert moneypuck.resolve_scoping_team("") is None

    def test_whitespace_is_stripped(self):
        assert moneypuck.resolve_scoping_team("STL, DET") == "DET"


class TestGroupByScope:
    def test_groups_players_by_conference(self):
        players = [
            {"playerId": 1, "team": "CAR"},
            {"playerId": 2, "team": "BOS"},
            {"playerId": 3, "team": "COL"},
        ]
        team_scoping = {
            "CAR": {"conference": "E", "division": "M"},
            "BOS": {"conference": "E", "division": "A"},
            "COL": {"conference": "W", "division": "C"},
        }
        groups = moneypuck.group_by_scope(players, team_scoping, "conference")
        assert {p["playerId"] for p in groups["E"]} == {1, 2}
        assert {p["playerId"] for p in groups["W"]} == {3}

    def test_traded_player_scoped_to_current_team(self):
        players = [{"playerId": 1, "team": "STL,DET"}]
        team_scoping = {
            "STL": {"conference": "W", "division": "C"},
            "DET": {"conference": "E", "division": "A"},
        }
        groups = moneypuck.group_by_scope(players, team_scoping, "division")
        assert groups == {"A": players}

    def test_unresolvable_team_is_dropped_not_crashed(self):
        """A team with no team_seasons row yet (e.g. before a season's
        games exist) must not raise -- the player is just absent from
        every scoped pool, same graceful-degradation shape as the rest of
        this module."""
        players = [{"playerId": 1, "team": "UTA"}, {"playerId": 2, "team": "CAR"}]
        team_scoping = {"CAR": {"conference": "E", "division": "M"}}
        groups = moneypuck.group_by_scope(players, team_scoping, "conference")
        assert groups == {"E": [{"playerId": 2, "team": "CAR"}]}

    def test_empty_team_scoping_produces_no_groups(self):
        players = [{"playerId": 1, "team": "CAR"}]
        assert moneypuck.group_by_scope(players, {}, "conference") == {}


class TestBuildScopedPools:
    def test_builds_one_sorted_pool_per_scope_value_per_metric(self):
        groups = {
            "E": [{"v": 3}, {"v": 1}, {"v": 2}],
            "W": [{"v": 10}],
        }
        pools = moneypuck.build_scoped_pools(groups, {"metric": lambda p: p["v"]})
        assert pools == {"E": {"metric": [1, 2, 3]}, "W": {"metric": [10]}}

    def test_none_values_filtered_out(self):
        groups = {"E": [{"v": None}, {"v": 5}]}
        pools = moneypuck.build_scoped_pools(groups, {"metric": lambda p: p["v"]})
        assert pools == {"E": {"metric": [5]}}


class TestLoadPlayerBoxStats:
    def test_paginates_until_empty_page(self):
        """OFFSET pagination, same convention as the rapm_map load in
        run() -- stops on the first EMPTY page (not merely a short one),
        matching the existing rapm_map loader's behavior exactly."""
        pages = [
            [{"player_id": i} for i in range(1000)],
            [{"player_id": 1000}],
            [],
        ]

        class FakeQuery:
            def __init__(self, page):
                self._page = page

            def select(self, *_a, **_kw):
                return self

            def eq(self, *_a, **_kw):
                return self

            def range(self, *_a, **_kw):
                return self

            def execute(self):
                class R:
                    data = self._page

                return R()

        calls = {"n": 0}

        class FakeClient:
            def table(self, _name):
                page = pages[calls["n"]]
                calls["n"] += 1
                return FakeQuery(page)

        result = moneypuck.load_player_box_stats(FakeClient(), season=20252026)
        assert len(result) == 1001
        assert calls["n"] == 3

    def test_empty_result_returns_empty_dict(self):
        class FakeQuery:
            def select(self, *_a, **_kw):
                return self

            def eq(self, *_a, **_kw):
                return self

            def range(self, *_a, **_kw):
                return self

            def execute(self):
                class R:
                    data = None

                r = R()
                r.data = []
                return r

        class FakeClient:
            def table(self, _name):
                return FakeQuery()

        assert moneypuck.load_player_box_stats(FakeClient(), season=20252026) == {}
