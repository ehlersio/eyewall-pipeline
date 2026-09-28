"""moneypuck.py fetches the season it's run for, not the live one."""

from moneypuck import mp_season_url


def test_uses_the_seasons_start_year():
    assert mp_season_url(20252026, "skaters").endswith("/seasonSummary/2025/regular/skaters.csv")
    assert mp_season_url(20242025, "goalies").endswith("/seasonSummary/2024/regular/goalies.csv")
