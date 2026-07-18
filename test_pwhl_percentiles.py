"""
test_pwhl_percentiles.py — unit tests for pwhl_percentiles.py's pure
functions: percentile_rank() edge cases (ported from moneypuck.py) and
per60()'s zero/None-TOI guard.

No network calls -- these are all pure math.
"""

import pwhl_percentiles


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
