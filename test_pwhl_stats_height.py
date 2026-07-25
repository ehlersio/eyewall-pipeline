"""
test_pwhl_stats_height.py — unit tests for pwhl_stats.py's
_parse_height_inches() (HockeyTech "5'11" -> total-inches parsing) and
_fetch_player_height_inches() (the per-player view=player call that's the
only HockeyTech source that actually carries height -- Session 85 found
the bulk view=roster fetch_roster() already makes does NOT carry it,
correcting an earlier assumption).
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


def test_fetch_player_height_inches_parses_info_height(monkeypatch):
    def fake_ht_get(params):
        assert params == {"view": "player", "player_id": 21}
        return {"info": {"height": "5'10"}}

    monkeypatch.setattr(pwhl_stats, "ht_get", fake_ht_get)
    assert pwhl_stats._fetch_player_height_inches(21) == 70


def test_fetch_player_height_inches_missing_info_returns_none(monkeypatch):
    monkeypatch.setattr(pwhl_stats, "ht_get", lambda params: {"info": {}})
    assert pwhl_stats._fetch_player_height_inches(21) is None


def test_fetch_player_height_inches_swallows_fetch_error(monkeypatch):
    def raise_fetch_error(params):
        raise pwhl_stats.FetchError("boom")

    monkeypatch.setattr(pwhl_stats, "ht_get", raise_fetch_error)
    assert pwhl_stats._fetch_player_height_inches(21) is None
