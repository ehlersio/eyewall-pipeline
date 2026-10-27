"""Tests for nhl_shot_xg.py -- the NHL shot-quality proxy behind rapm.py's
regression outcome and line_combinations.py's line xGF%. No network.

The bug these pin: a goal used to score 1.0 (its outcome, not its
location), and the band values were roughly double the real per-band
scoring rates. Together that put league xG at 2.41x actual goals. Same
mistake the PWHL model had (#150)."""

import math
from typing import ClassVar

import pytest

import line_combinations
import rapm
from nhl_shot_xg import DANGER_XG, GOAL_X, REAL_SHOT_TYPES, danger_bucket, shot_xg


def at_distance(d, attacking_right=True):
    """A shot d feet straight out from the net."""
    x = GOAL_X - d if attacking_right else -(GOAL_X - d)
    return x, 0


class TestBuckets:
    @pytest.mark.parametrize(
        ("dist", "band"),
        [(0, "high"), (15, "high"), (15.1, "medium"), (30, "medium"), (30.1, "low"), (75, "low")],
    )
    def test_bands_by_distance_from_the_net(self, dist, band):
        assert danger_bucket(*at_distance(dist)) == band

    def test_both_ends_of_the_rink_are_the_same_shot(self):
        assert danger_bucket(*at_distance(10)) == danger_bucket(
            *at_distance(10, attacking_right=False)
        )

    def test_distance_is_measured_to_the_net_not_the_goal_line(self):
        # 10 ft out and 10 ft wide is ~14.1 ft away, still high danger
        assert danger_bucket(GOAL_X - 10, 10) == "high"
        assert math.hypot(10, 10) == pytest.approx(14.14, abs=0.01)

    def test_missing_coordinates_do_not_raise(self):
        assert danger_bucket(None, None) == "low"  # treated as centre ice


class TestShotXg:
    def test_a_goal_scores_its_location_not_its_outcome(self):
        """The bug: a goal used to return 1.0 wherever it was taken."""
        for dist in (5, 20, 60):
            spot = at_distance(dist)
            assert shot_xg("goal", *spot) == shot_xg("shot-on-goal", *spot)
        assert shot_xg("goal", *at_distance(60)) == DANGER_XG["low"]

    @pytest.mark.parametrize("event_type", REAL_SHOT_TYPES)
    def test_every_attempt_type_carries_xg(self, event_type):
        assert shot_xg(event_type, *at_distance(10)) == DANGER_XG["high"]

    @pytest.mark.parametrize("event_type", ["faceoff", "hit", "giveaway", "penalty", ""])
    def test_events_that_are_not_attempts_carry_none(self, event_type):
        assert shot_xg(event_type, *at_distance(10)) == 0.0

    def test_closer_is_never_worth_less(self):
        xgs = [shot_xg("shot-on-goal", *at_distance(d)) for d in (5, 20, 40, 70)]
        assert xgs == sorted(xgs, reverse=True)


class TestCalibration:
    """A league's xG should land on its goals. These are the real 2025-26
    regular-season band counts (163,908 attempts, 8,915 goals) -- see
    nhl_shot_xg.py, where the values were measured and checked against
    2024-25."""

    SEASON_2025_26: ClassVar[dict] = {"high": 41015, "medium": 43774, "low": 79119}
    GOALS_2025_26 = 8915

    def test_a_seasons_attempts_add_up_to_its_goals(self):
        total = sum(DANGER_XG[band] * n for band, n in self.SEASON_2025_26.items())
        assert total == pytest.approx(self.GOALS_2025_26, rel=0.03)

    def test_the_old_values_did_not(self):
        """What this replaced: goals at 1.0 plus double-rate bands."""
        old = {"high": 0.20, "medium": 0.07, "low": 0.03}
        goals_as_one = self.GOALS_2025_26  # each goal contributed 1.0
        non_goals = sum(self.SEASON_2025_26.values()) - self.GOALS_2025_26
        old_total = goals_as_one + old["medium"] * non_goals  # rough, band-agnostic
        assert old_total / self.GOALS_2025_26 > 1.5


class TestOneSharedModel:
    def test_rapm_and_line_combinations_use_this_one(self):
        assert rapm.shot_xg is shot_xg
        assert line_combinations.shot_xg is shot_xg
        assert rapm.DANGER_XG is DANGER_XG

    def test_rapm_still_exports_what_backtest_predictions_calls(self):
        assert rapm.shot_xg("goal", *at_distance(10)) == DANGER_XG["high"]
        assert set(REAL_SHOT_TYPES) == set(rapm.REAL_SHOT_TYPES)
