"""
test_pwhl_stats_height.py — unit tests for pwhl_stats.py's
_parse_height_inches() (roster ingestion's HockeyTech "5'11" -> total-inches
parsing, added alongside the height_inches column on pwhl_players).
"""

import pwhl_stats


def test_parses_standard_height():
    assert pwhl_stats._parse_height_inches("5'11") == 71


def test_parses_double_digit_feet_zero_inches():
    assert pwhl_stats._parse_height_inches("6'0") == 72


def test_parses_hyphenated_style_still_matches_quote_form():
    assert pwhl_stats._parse_height_inches("5' 6") == 66


def test_none_input_returns_none():
    assert pwhl_stats._parse_height_inches(None) is None


def test_empty_string_returns_none():
    assert pwhl_stats._parse_height_inches("") is None


def test_weight_style_zero_string_does_not_crash():
    # HockeyTech's PWHL roster feed always returns "0" for weight -- make
    # sure a bare non-height string fed to this parser by mistake degrades
    # to None rather than raising.
    assert pwhl_stats._parse_height_inches("0") is None
