"""
test_pwhl_stats_game_dates.py — unit tests for pwhl_stats.py's
_parse_game_date() / SEASON_YEAR_MAP.

HockeyTech's view=schedule gives dates without a year ("Wed, May 8"), so
_parse_game_date() infers it from SEASON_YEAR_MAP (the year each season
STARTS). Confirmed live 2026-09-13 by checking every schedule date's printed
weekday against the computed date's weekday: seasons 3 and 6 (2023-24 and
2024-25 playoffs) had been mapped to their playoff year, dating every one of
those playoff games a year late (0 of 13 and 0 of 12 weekdays matched), and
season 10 (2026-27 preseason) was missing, falling through to 2025 (0 of
12). Each case below is a real schedule date from HockeyTech, checked the
same way: the computed date must fall on the weekday HockeyTech printed.
"""

from datetime import date

import pytest

from pwhl_stats import _parse_game_date

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@pytest.mark.parametrize(
    "season_id, printed, expected",
    [
        ("1", "Mon, Jan 1", "2024-01-01"),  # 2023-24 regular season opener
        ("3", "Wed, May 8", "2024-05-08"),  # 2023-24 playoffs (was 2025-05-08)
        ("5", "Sat, Nov 30", "2024-11-30"),  # 2024-25 regular season
        ("6", "Wed, May 7", "2025-05-07"),  # 2024-25 playoffs (was 2026-05-07)
        ("8", "Fri, Nov 21", "2025-11-21"),  # 2025-26 regular season
        ("9", "Thu, Apr 30", "2026-04-30"),  # 2025-26 playoffs
        ("10", "Sun, Nov 22", "2026-11-22"),  # 2026-27 preseason (was 2025-11-22)
    ],
)
def test_game_date_year_matches_the_printed_weekday(season_id, printed, expected):
    iso = _parse_game_date(printed, season_id)
    assert iso == expected
    assert WEEKDAYS[date.fromisoformat(iso).weekday()] == printed[:3]


def test_unparseable_dates_return_none():
    assert _parse_game_date("", "8") is None
    assert _parse_game_date("TBD", "8") is None
