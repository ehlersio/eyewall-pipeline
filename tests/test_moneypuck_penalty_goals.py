"""WAR's penalty and finishing terms, with the constants measured by
scripts/backtest/measure_war_weights.py (war_weights.json).

Penalties are counts drawn minus taken, at the net goal value of a penalty
and weighted by how much the differential repeats; until 2026-11 they were
minutes taken (drawn never credited), then minutes x 0.11 x 0.3.
Finishing is goals minus xG, shrunk by attempts / (attempts + k); it was a
flat 0.3.
"""

import json
import os

import pytest

import moneypuck as mp


def test_constants_come_from_the_measured_file():
    with open(os.path.join(os.path.dirname(mp.__file__), "war_weights.json")) as fh:
        w = json.load(fh)
    assert w["goals_per_win"] == mp.GOALS_PER_WIN
    assert w["penalty_goals_each"] == mp.PENALTY_GOALS_EACH
    assert w["penalty_weight"] == mp.PENALTY_WEIGHT
    assert w["finishing_k"] == mp.FINISHING_K
    assert w["source"] == "scripts/backtest/measure_war_weights.py"
    # sanity bounds, so a bad re-measure fails loudly
    assert 5 < mp.GOALS_PER_WIN < 7.5
    assert 0.08 < mp.PENALTY_GOALS_EACH < 0.25
    assert 0 < mp.PENALTY_WEIGHT <= 1
    assert 100 < mp.FINISHING_K < 2000


def test_penalties_drawn_are_credited_and_taken_charged_by_count():
    each = mp.PENALTY_GOALS_EACH * mp.PENALTY_WEIGHT
    assert mp.penalty_goals({"penaltiesDrawn": "18", "penalties": "2"}) == pytest.approx(16 * each)
    assert mp.penalty_goals({"penaltiesDrawn": "7", "penalties": "27"}) == pytest.approx(-20 * each)
    assert mp.penalty_goals({"penalties": "3"}) == pytest.approx(-3 * each)


def test_a_misconducts_minutes_dont_count():
    # one misconduct: 10 minutes, one penalty, no power play
    row = {"penalties": "1", "I_F_penalityMinutes": "10.0"}
    assert mp.penalty_goals(row) == pytest.approx(-mp.PENALTY_GOALS_EACH * mp.PENALTY_WEIGHT)


def test_finishing_is_shrunk_by_shot_volume():
    low = {"I_F_goals": "6", "I_F_xGoals": "3", "I_F_unblockedShotAttempts": "40"}
    high = {"I_F_goals": "33", "I_F_xGoals": "30", "I_F_unblockedShotAttempts": "400"}
    assert mp.finishing_goals(low) == pytest.approx(3 * 40 / (40 + mp.FINISHING_K))
    assert mp.finishing_goals(high) == pytest.approx(3 * 400 / (400 + mp.FINISHING_K))
    assert mp.finishing_goals(high) > 3 * mp.finishing_goals(low)  # same +3, ten times the shooting
    assert (
        mp.finishing_goals({"I_F_goals": "1", "I_F_xGoals": "0.2"}) == 0.0
    )  # no attempts recorded


def test_war_from_rapm_adds_the_terms_and_divides_by_goals_per_win():
    ev = {"icetime": "36000"}  # 10 hours of 5v5
    row = {"I_F_goals": "20", "I_F_xGoals": "15", "I_F_unblockedShotAttempts": "200",
           "penaltiesDrawn": "20", "penalties": "10"}  # fmt: skip
    expected = (0.1 * 10 + mp.penalty_goals(row) + mp.finishing_goals(row)) / mp.GOALS_PER_WIN + 0.5
    assert mp.war_from_rapm(row, ev, 0.1, replacement=0.5) == pytest.approx(expected, abs=0.001)
