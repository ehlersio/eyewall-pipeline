"""
test_pwhl_percentiles.py — unit tests for pwhl_percentiles.py's pure
functions: percentile_rank() edge cases (ported from moneypuck.py) and
per60()'s zero/None-TOI guard.

No network calls -- these are all pure math.
"""

import pwhl_percentiles


class _ChainQuery:
    """Generic chainable fake -- every method except execute()/data returns
    self; .execute().data returns the given rows unconditionally. Good
    enough for tables this test doesn't need to paginate or branch on."""

    def __init__(self, data):
        self._data = data

    def __getattr__(self, _name):
        def method(*_a, **_k):
            return self

        return method

    def execute(self):
        return self

    @property
    def data(self):
        return self._data


class _PlayerSeasonsQuery(_ChainQuery):
    def __init__(self, rows, upserted):
        super().__init__(rows)
        self._upserted = upserted

    def upsert(self, rows, **_k):
        self._upserted.extend(rows)
        return self


class _PercentilesSb:
    """Fake Supabase client for compute_percentiles(). pwhl_shot_events
    always reports no data (has_shot_data=False), so a1_60 stays None and
    the test doesn't need to model that table's separate pagination."""

    def __init__(self, player_seasons_rows, positions_rows):
        self.upserted = []
        self._player_seasons_rows = player_seasons_rows
        self._positions_rows = positions_rows

    def table(self, name):
        if name == "pwhl_player_seasons":
            return _PlayerSeasonsQuery(self._player_seasons_rows, self.upserted)
        if name == "pwhl_players":
            return _ChainQuery(self._positions_rows)
        if name == "pwhl_shot_events":
            return _ChainQuery([])
        raise AssertionError(f"unexpected table {name!r}")


class TestComputePercentilesStringTypedColumns:
    """Regression test for the 2026-07-20 bug: toi_per_game (bigint) and
    finishing (numeric) come back from PostgREST as JSON strings, unlike the
    plain `integer` columns (gp, goals, ...) read alongside them. Multiplying
    an un-cast string TOI by an int GP is Python string-repetition, not
    arithmetic, and blew up downstream in per60() with a TypeError -- masked
    until compute_toi_per_game()'s own pagination bug was fixed and this
    path finally ran against a non-null toi_per_game for the first time."""

    def test_string_typed_toi_and_finishing_do_not_raise(self):
        rows = [
            {
                "player_id": 1,
                "team_id": 10,
                "gp": 10,
                "goals": 5,
                "assists": 0,
                "pim": 0,
                "toi_per_game": "1200",  # string, as PostgREST returns bigint
                "finishing": "1.5",  # string, as PostgREST returns numeric
            },
            {
                "player_id": 2,
                "team_id": 10,
                "gp": 10,
                "goals": 10,
                "assists": 0,
                "pim": 0,
                "toi_per_game": "1200",
                "finishing": "3.0",
            },
        ]
        positions = [{"player_id": 1, "position": "F"}, {"player_id": 2, "position": "F"}]
        sb = _PercentilesSb(rows, positions)

        pwhl_percentiles.compute_percentiles(sb, "8", "regular")

        assert len(sb.upserted) == 2
        by_player = {u["player_id"]: u for u in sb.upserted}
        # Player 2 scored more goals and finished better -> ranks higher.
        assert by_player[2]["pct_goals"] > by_player[1]["pct_goals"]
        assert by_player[2]["pct_finishing"] > by_player[1]["pct_finishing"]


class TestPercentileRank:
    def test_none_value_returns_none(self):
        assert pwhl_percentiles.percentile_rank(None, [1, 2, 3]) is None

    def test_empty_pool_returns_none(self):
        assert pwhl_percentiles.percentile_rank(5, []) is None

    def test_value_below_pool_is_zeroth_percentile(self):
        assert pwhl_percentiles.percentile_rank(0, [1, 2, 3, 4, 5]) == 0

    def test_value_above_pool_is_100th_percentile(self):
        assert pwhl_percentiles.percentile_rank(100, [1, 2, 3, 4, 5]) == 100

    def test_value_matching_pool_minimum(self):
        # lo finds the first index >= value; value equal to the smallest
        # pool element still ranks at 0 (nothing in the pool is smaller).
        assert pwhl_percentiles.percentile_rank(1, [1, 2, 3, 4, 5]) == 0

    def test_value_matching_pool_maximum(self):
        # 4 of 5 elements are strictly smaller -> 80th percentile.
        assert pwhl_percentiles.percentile_rank(5, [1, 2, 3, 4, 5]) == 80

    def test_median_value_is_50th_percentile_ish(self):
        pool = list(range(1, 101))  # 1..100
        assert pwhl_percentiles.percentile_rank(50, pool) == 49

    def test_single_element_pool(self):
        assert pwhl_percentiles.percentile_rank(5, [5]) == 0
        assert pwhl_percentiles.percentile_rank(10, [5]) == 100


class TestBuildSortedPool:
    def test_filters_none_values(self):
        players = [{"v": 1}, {"v": None}, {"v": 3}]
        pool = pwhl_percentiles.build_sorted_pool(players, lambda p: p["v"])
        assert pool == [1, 3]

    def test_sorts_ascending(self):
        players = [{"v": 3}, {"v": 1}, {"v": 2}]
        pool = pwhl_percentiles.build_sorted_pool(players, lambda p: p["v"])
        assert pool == [1, 2, 3]

    def test_empty_input(self):
        assert pwhl_percentiles.build_sorted_pool([], lambda p: p["v"]) == []


class TestPer60:
    def test_zero_toi_returns_none(self):
        assert pwhl_percentiles.per60(10, 0) is None

    def test_none_toi_returns_none(self):
        assert pwhl_percentiles.per60(10, None) is None

    def test_normal_rate_computation(self):
        # 10 goals over 3600 seconds (1 hour) of TOI -> 10 per 60.
        assert pwhl_percentiles.per60(10, 3600) == 10.0

    def test_negative_value_for_penalties_style_metrics(self):
        assert pwhl_percentiles.per60(-4, 3600) == -4.0
