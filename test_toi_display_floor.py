"""
test_toi_display_floor.py — coverage for the TOI-based display floor on NHL
skater percentiles (moneypuck.py's MIN_TOI_MINUTES / meets_toi_floor()).

Bug this closes: MIN_GP=10 only ever gated POOL membership (who counts as
reference data for percentile calculation) -- nothing gated DISPLAY of an
individual player's own pct_* values. A player clearing 10 GP but with only
70-170 minutes of season TOI (a black-ace cameo) could still get a computed,
misleading percentile (e.g. a 95th-percentile badge off a handful of shifts).

meets_toi_floor() is pure/module-level (same convention as
compute_on_ice_gf_pct and apply_results_vs_process_guardrail in
test_results_vs_process.py) specifically so the boundary case is
unit-testable without a full CSV fetch or Supabase mock.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import moneypuck


class TestMeetsToiFloor:
    """MIN_TOI_MINUTES = {"F": 250, "D": 330} -- derived from p10 of the
    GP-qualified pool, season 20252026, live-queried."""

    def test_forward_just_below_floor_fails(self):
        # 249 minutes = 14940 seconds
        assert moneypuck.meets_toi_floor(True, 249 * 60) is False

    def test_forward_at_floor_passes(self):
        # 250 minutes = 15000 seconds
        assert moneypuck.meets_toi_floor(True, 250 * 60) is True

    def test_forward_well_above_floor_passes(self):
        assert moneypuck.meets_toi_floor(True, 1200 * 60) is True

    def test_defenseman_just_below_floor_fails(self):
        # 329 minutes = 19740 seconds
        assert moneypuck.meets_toi_floor(False, 329 * 60) is False

    def test_defenseman_at_floor_passes(self):
        # 330 minutes = 19800 seconds
        assert moneypuck.meets_toi_floor(False, 330 * 60) is True

    def test_defenseman_floor_is_higher_than_forward_floor(self):
        """Same TOI (300 minutes) clears the forward floor but not the
        defenseman floor -- position must actually change the outcome, not
        just be accepted as a no-op parameter."""
        toi_seconds = 300 * 60
        assert moneypuck.meets_toi_floor(True, toi_seconds) is True
        assert moneypuck.meets_toi_floor(False, toi_seconds) is False

    def test_black_ace_cameo_fails_despite_clearing_min_gp(self):
        """The exact scenario this feature closes: a player with enough GP
        to clear MIN_GP=10 for pool membership, but with only ~150 minutes
        of season TOI (a handful of shifts per game) -- must fail the
        display floor regardless of GP."""
        assert moneypuck.meets_toi_floor(True, 150 * 60) is False
        assert moneypuck.meets_toi_floor(False, 150 * 60) is False

    def test_zero_and_missing_icetime_fail(self):
        assert moneypuck.meets_toi_floor(True, 0) is False
        assert moneypuck.meets_toi_floor(True, None) is False

    def test_string_icetime_from_csv_is_coerced(self):
        """MoneyPuck's CSV rows come in as strings -- meets_toi_floor should
        go through the same n() coercion as every other stat reader in this
        module rather than raising a TypeError on a str/int comparison."""
        assert moneypuck.meets_toi_floor(True, "15000") is True
        assert moneypuck.meets_toi_floor(True, "14940") is False
