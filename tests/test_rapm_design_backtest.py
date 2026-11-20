"""Scoring helpers of scripts/backtest/backtest_rapm_design.py: the
held-out skill measures and the per-row features every predictor is put
through. No network."""

import numpy as np
import pytest

from scripts.backtest import backtest_rapm_design as bd


def stint(h, a, dur=60, hx=0.0, ax=0.0, hg=0, ag=0, lead=0, game=1):
    return (game, "AAA", "BBB", (h, a, dur, hx, ax, hg, ag, lead))


def test_poisson_deviance_is_zero_for_a_perfect_fit():
    g = np.array([0.0, 1.0, 2.0])
    assert bd.poisson_dev(g, g + 1e-12) == pytest.approx(0.0, abs=1e-6)
    assert bd.poisson_dev(g, np.full(3, 1.0)) > 0


def test_features_two_rows_per_stint_from_each_side():
    rows = [stint((1, 2, 3, 4, 5), (6, 7, 8, 9, 10), dur=120, hx=0.2, ax=0.1, hg=1, lead=1)]
    strengths = {1: (0.5, 0.0), 6: (0.0, 0.3)}
    F, xg, goals, secs = bd.features(rows, strengths)
    assert F.tolist() == [[0.5, 0.3, 1.0, 1.0, 0.0], [0.0, 0.0, 0.0, 0.0, 1.0]]
    assert xg.tolist() == pytest.approx([6.0, 3.0])
    assert goals.tolist() == [1, 0] and secs.tolist() == [120, 120]


def test_team_level_features_use_team_codes():
    rows = [stint((1, 2, 3, 4, 5), (6, 7, 8, 9, 10))]
    F, *_ = bd.features(rows, {"AAA": (0.4, 0.1), "BBB": (0.2, 0.3)}, team_level=True)
    assert F[0, :2].tolist() == [0.4, 0.3] and F[1, :2].tolist() == [0.2, 0.1]


def test_a_real_signal_scores_above_the_league_mean():
    rng = np.random.default_rng(0)
    rows = []
    for i in range(3000):
        star = i % 2 == 0
        h = (1, 2, 3, 4, 5) if star else (11, 12, 13, 14, 15)
        rate = 4.0 if star else 2.0
        rows.append(
            stint(h, (6, 7, 8, 9, 10), dur=60, hx=rng.gamma(2, rate / 120), ax=0.03, game=i)
        )
    strengths = dict.fromkeys((1, 2, 3, 4, 5), (1.0, 0.0))
    r = bd.score_holdout(rows[:2000], rows[2000:], strengths)
    assert r["xg"] > 0.0
    useless = bd.score_holdout(rows[:2000], rows[2000:], {})
    assert useless["xg"] < r["xg"]


def test_folds_are_stable_and_cover_every_game():
    folds = {bd.fold_of(g) for g in range(2023020001, 2023020500)}
    assert folds == set(range(bd.FOLDS))
    assert bd.fold_of(2023020001) == bd.fold_of(2023020001)
    assert bd.fold_of(2023020001, salt=1) in range(bd.FOLDS)
