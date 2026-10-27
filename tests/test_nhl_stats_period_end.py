"""
test_nhl_stats_period_end.py -- nhl_stats.period_end_of(): the period a
game ended in (3 = regulation, 4 = OT, 5 = shootout), from a club-schedule
game entry.

game_log.period_end was stuck at 3 for every 2023-24 and 2024-25 game
(found 2026-09-13), so Elo never applied its overtime damping to those
seasons. The schedule carries the value two ways -- periodDescriptor.number
and gameOutcome.lastPeriodType -- so the writer now falls back to the
second before defaulting to regulation. No network calls.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from nhl_stats import period_end_of


def test_uses_period_descriptor_number():
    assert period_end_of({"periodDescriptor": {"number": 4}}) == 4
    assert period_end_of({"periodDescriptor": {"number": 3}}) == 3


def test_falls_back_to_last_period_type():
    assert period_end_of({"gameOutcome": {"lastPeriodType": "OT"}}) == 4
    assert period_end_of({"gameOutcome": {"lastPeriodType": "SO"}}) == 5
    assert period_end_of({"gameOutcome": {"lastPeriodType": "REG"}}) == 3


def test_period_descriptor_wins_when_both_are_present():
    game = {"periodDescriptor": {"number": 5}, "gameOutcome": {"lastPeriodType": "OT"}}
    assert period_end_of(game) == 5


def test_defaults_to_regulation_only_when_both_are_missing():
    assert period_end_of({}) == 3
    assert period_end_of({"periodDescriptor": {}, "gameOutcome": {}}) == 3
    assert period_end_of({"periodDescriptor": None, "gameOutcome": {"lastPeriodType": "??"}}) == 3
