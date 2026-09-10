"""Tests for backtest_preseason.py's MoneyPuck Log5/blend helpers -- pure
math only, no network (the cache dict is populated directly rather than
patching the real CSV fetch)."""

import backtest_preseason as bpre


def test_moneypuck_preseason_preds_missing_team_returns_none_none():
    bpre._moneypuck_cache[99999999] = {"CAR": 0.55}  # away team missing entirely
    mp_p, blend_p = bpre.moneypuck_preseason_preds(99999999, "CAR", "BOS", elo_p=0.6)
    assert mp_p is None
    assert blend_p is None


def test_moneypuck_preseason_preds_missing_elo_returns_none_none():
    bpre._moneypuck_cache[99999998] = {"CAR": 0.55, "BOS": 0.45}
    mp_p, blend_p = bpre.moneypuck_preseason_preds(99999998, "CAR", "BOS", elo_p=None)
    assert mp_p is None
    assert blend_p is None


def test_moneypuck_preseason_preds_equal_xgpct_is_a_coin_flip():
    bpre._moneypuck_cache[99999997] = {"CAR": 0.50, "BOS": 0.50}
    mp_p, _ = bpre.moneypuck_preseason_preds(99999997, "CAR", "BOS", elo_p=0.6)
    assert abs(mp_p - 0.5) < 1e-9


def test_moneypuck_preseason_preds_higher_xgpct_favored():
    bpre._moneypuck_cache[99999996] = {"CAR": 0.58, "BOS": 0.42}
    mp_p, _ = bpre.moneypuck_preseason_preds(99999996, "CAR", "BOS", elo_p=0.5)
    assert mp_p > 0.5


def test_moneypuck_preseason_preds_blend_is_unfit_50_50_average():
    bpre._moneypuck_cache[99999995] = {"CAR": 0.55, "BOS": 0.45}
    elo_p = 0.65
    mp_p, blend_p = bpre.moneypuck_preseason_preds(99999995, "CAR", "BOS", elo_p)
    assert abs(blend_p - (0.5 * elo_p + 0.5 * mp_p)) < 1e-9
