"""Tests for elo.py's pure rating math and elo_ratings.py's season arithmetic."""

import elo
from elo_ratings import season_after, seasons_in_order


def test_season_after():
    assert season_after(20232024) == 20242025
    assert season_after(20242025) == 20252026
    assert season_after(20252026) == 20262027


def test_seasons_in_order():
    assert seasons_in_order(20232024, 20252026) == [20232024, 20242025, 20252026]
    assert seasons_in_order(20232024, 20232024) == [20232024]


def test_expected_prob_symmetry():
    # Equal ratings -> coin flip
    assert elo.expected_prob(1500, 1500) == 0.5
    # Higher rating -> higher win probability
    assert elo.expected_prob(1600, 1500) > 0.5
    assert elo.expected_prob(1400, 1500) < 0.5
    # Probabilities for the two sides of one matchup sum to 1
    p = elo.expected_prob(1550, 1480)
    q = elo.expected_prob(1480, 1550)
    assert abs((p + q) - 1.0) < 1e-9


def test_update_ratings_winner_gains_loser_loses():
    r_home, r_away = elo.update_ratings(1500, 1500, home_won=True, margin=2, went_to_overtime=False)
    assert r_home > 1500
    assert r_away < 1500
    # Zero-sum: what the winner gains, the loser loses
    assert abs((r_home - 1500) + (r_away - 1500)) < 1e-9


def test_update_ratings_upset_moves_more_than_expected_win():
    # A big underdog winning should move ratings more than a heavy favorite winning
    _, underdog_upset_delta = elo.update_ratings(
        1650, 1350, home_won=False, margin=1, went_to_overtime=False
    )
    _, favorite_win_delta = elo.update_ratings(
        1650, 1350, home_won=True, margin=1, went_to_overtime=False
    )
    assert abs(underdog_upset_delta - 1350) > abs(favorite_win_delta - 1350)


def test_mov_multiplier_ot_damped_vs_regulation():
    assert elo.mov_multiplier(margin=1, went_to_overtime=True) < elo.mov_multiplier(
        margin=1, went_to_overtime=False
    )


def test_mov_multiplier_capped():
    assert elo.mov_multiplier(margin=1, went_to_overtime=False) == 1.0
    assert elo.mov_multiplier(margin=50, went_to_overtime=False) <= 1.75


def test_regress_to_mean_moves_toward_mean_not_past_it():
    regressed_high = elo.regress_to_mean(1700)
    assert 1500 < regressed_high < 1700
    regressed_low = elo.regress_to_mean(1300)
    assert 1300 < regressed_low < 1500


def test_regress_to_mean_leaves_mean_unchanged():
    assert elo.regress_to_mean(1500) == 1500
