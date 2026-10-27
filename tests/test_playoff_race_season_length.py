"""
test_playoff_race_season_length.py -- playoff_race.py's games-remaining
math now reads the season length from the live schedule instead of a
hardcoded 82 (the NHL went to 84 games in 2026-27; a hardcoded 82 would
have understated every team's ceiling by 4 points and skewed magic/tragic
numbers from opening night). No network calls -- fetch_schedule is
monkeypatched.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import playoff_race
from playoff_race import _games_remaining, ceiling, season_length


def test_games_remaining_uses_the_teams_season_length():
    assert _games_remaining({"games_played": 10, "games_in_season": 84}) == 74
    assert ceiling({"points": 20, "games_played": 10, "games_in_season": 84}) == 20 + 2 * 74


def test_games_remaining_falls_back_to_82_without_a_season_length():
    assert _games_remaining({"games_played": 10}) == 72


def test_season_length_counts_regular_season_games_only(monkeypatch):
    schedule = [{"gameType": 1}] * 4 + [{"gameType": 2}] * 84 + [{"gameType": 3}] * 2
    monkeypatch.setattr(playoff_race, "fetch_schedule", lambda team, season: schedule)
    assert season_length(20262027) == 84


def test_season_length_falls_back_when_the_schedule_is_empty(monkeypatch):
    monkeypatch.setattr(playoff_race, "fetch_schedule", lambda team, season: [])
    assert season_length(20262027) == 82
