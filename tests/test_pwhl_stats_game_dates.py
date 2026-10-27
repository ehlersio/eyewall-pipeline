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

Since 2026-10 a season the Worker's /config/seasons describes is dated from
its real start_date instead of the map, and a date whose weekday doesn't
match what HockeyTech printed raises GameDateError instead of being
returned. The map cases below run with no Worker start date.
"""

from datetime import date

import pytest

import pwhl_stats
from pwhl_stats import GameDateError, _parse_game_date


@pytest.fixture(autouse=True)
def no_worker_start_dates(monkeypatch):
    """No network: the map cases run as if the Worker described no season.
    Tests that need a Worker start date set their own."""
    monkeypatch.setattr(pwhl_stats, "get_pwhl_season_start_date", lambda season_id: None)


def _worker_start_dates(monkeypatch, dates):
    monkeypatch.setattr(
        pwhl_stats, "get_pwhl_season_start_date", lambda season_id: dates.get(str(season_id))
    )


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


def test_leap_day():
    # Parsing "Feb 29" without a year used to fail (strptime assumes 1900,
    # not a leap year), so a Feb 29 game got no date. Season 1 is 2023-24,
    # and 2024-02-29 was a Thursday.
    assert _parse_game_date("Thu, Feb 29", "1") == "2024-02-29"


# Real 2026-27 schedule dates (HockeyTech view=schedule, 2026-10-01).
@pytest.mark.parametrize(
    "season_id, printed, expected",
    [
        ("11", "Sat, Dec 5", "2026-12-05"),  # 2026-27 opener, SEA @ VAN
        ("11", "Sun, Dec 20", "2026-12-20"),
        ("11", "Sat, Jan 2", "2027-01-02"),  # January belongs to the next year
        ("10", "Sun, Nov 22", "2026-11-22"),  # 2026-27 preseason
        ("10", "Mon, Nov 30", "2026-11-30"),
    ],
)
def test_worker_start_date_dates_a_season_the_map_does_not_list(
    monkeypatch, season_id, printed, expected
):
    _worker_start_dates(monkeypatch, {"10": "2026-10-01", "11": "2026-12-04"})
    assert "11" not in pwhl_stats.SEASON_YEAR_MAP
    assert _parse_game_date(printed, season_id) == expected


def test_worker_start_date_wins_over_the_map(monkeypatch):
    # Season 9 (2026 Playoffs) starts 2026-04-28; its games are in spring
    # 2026, the same answer the map's start year 2025 gives.
    _worker_start_dates(monkeypatch, {"9": "2026-04-28"})
    assert _parse_game_date("Thu, Apr 30", "9") == "2026-04-30"


def test_a_wrong_year_raises_instead_of_returning_a_date(monkeypatch):
    # A start date a year off puts the opener on 2025-12-05, a Friday;
    # HockeyTech printed Saturday.
    _worker_start_dates(monkeypatch, {"11": "2025-12-04"})
    with pytest.raises(GameDateError, match="year is wrong"):
        _parse_game_date("Sat, Dec 5", "11")


def test_an_unknown_season_raises_instead_of_guessing_2025():
    # Before 2026-10, an unmapped season fell back to 2025 silently.
    with pytest.raises(GameDateError, match="no start date"):
        _parse_game_date("Sat, Dec 5", "12")


def test_weekday_check_applies_to_map_seasons_too(monkeypatch):
    monkeypatch.setitem(pwhl_stats.SEASON_YEAR_MAP, "8", 2024)  # a year off
    with pytest.raises(GameDateError):
        _parse_game_date("Fri, Nov 21", "8")
