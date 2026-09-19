"""hockeytech_stats._goalie_saves: ECHL's goalie rows have no "saves" field,
which stored 0 for every ECHL goalie until 2026-09."""

from hockeytech_stats import _goalie_saves


def test_uses_the_feeds_saves_when_present():  # AHL
    assert _goalie_saves({"saves": "1118", "shots": "1202", "goals_against": "84"}) == 1118


def test_derives_saves_when_the_feed_has_none():  # ECHL
    row = {"shots": "1175", "goals_against": "91", "save_percentage": "0.923"}
    assert _goalie_saves(row) == 1084
    assert round(1084 / 1175, 3) == 0.923


def test_never_negative_or_crashing_on_blanks():
    assert _goalie_saves({"shots": "", "goals_against": "3"}) == 0
    assert _goalie_saves({"saves": "", "shots": "10", "goals_against": "1"}) == 9
