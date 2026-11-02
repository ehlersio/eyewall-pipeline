"""WAR's penalty term is minutes drawn minus minutes taken.

Until 2026-11 it charged the minutes taken and never credited the minutes
drawn, though MoneyPuck's skaters.csv carries both.
"""

import pytest

import moneypuck as mp


def test_drawn_minutes_are_credited_and_taken_minutes_charged():
    row = {"penalityMinutesDrawn": "18.0", "I_F_penalityMinutes": "2.0"}
    assert mp.penalty_goals(row) == pytest.approx(16 * mp.PEN_MIN_VALUE)


def test_a_player_who_takes_more_than_they_draw_loses_goals():
    row = {"penalityMinutesDrawn": "7.0", "I_F_penalityMinutes": "27.0"}
    assert mp.penalty_goals(row) == pytest.approx(-20 * mp.PEN_MIN_VALUE)


def test_a_row_without_the_drawn_column_counts_only_minutes_taken():
    assert mp.penalty_goals({"I_F_penalityMinutes": "4.0"}) == pytest.approx(-4 * mp.PEN_MIN_VALUE)


def test_war_from_rapm_uses_the_net_penalty_term():
    ev = {"icetime": "36000"}  # 10 hours of 5v5
    base = {"I_F_goals": "5", "I_F_xGoals": "5", "I_F_penalityMinutes": "2.0"}
    taken_only = mp.war_from_rapm(base, ev, 0.0, replacement=0.0)
    with_drawn = mp.war_from_rapm(
        {**base, "penalityMinutesDrawn": "18.0"}, ev, 0.0, replacement=0.0
    )
    assert with_drawn - taken_only == pytest.approx(
        18 * mp.PEN_MIN_VALUE * 0.3 / mp.GOALS_PER_WIN, abs=0.001
    )
