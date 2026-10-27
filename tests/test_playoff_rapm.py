"""rapm.playoff_rapm(): playoff RAPM shrunk toward regular-season RAPM.

A ridge fit on the residual y - X @ prior is a ridge regression whose prior
mean is each player's regular-season value: with no playoff evidence a
player stays at it, and playoff shots move them only as far as they support.
"""

import numpy as np
from scipy.sparse import csr_matrix

import rapm


def fit(rows, y, regular):
    X = csr_matrix(np.array(rows, dtype=np.float32))
    player_idx = {pid: i for i, pid in enumerate(sorted(regular))}
    return rapm.playoff_rapm(X, np.array(y, dtype=np.float32), player_idx, regular)


def test_no_playoff_evidence_keeps_the_regular_season_value():
    # Player 2 is never on the ice for a shot here.
    regular = {1: 0.02, 2: 0.08}
    out = fit([[1, 0]] * 50 + [[-1, 0]] * 50, [0.0] * 100, regular)
    assert out[2] == 0.08


def test_a_large_sample_moves_a_player_toward_their_playoff_play():
    regular = {1: 0.0, 2: 0.0}
    # Player 1 on ice for many high-xG shots for, none against.
    rows = [[1, 0]] * 20000 + [[0, 1]] * 20000
    y = [0.2] * 20000 + [0.0] * 20000
    out = fit(rows, y, regular)
    assert out[1] > 0.5
    small = fit(rows[:200] + rows[20000:20200], y[:200] + y[20000:20200], regular)
    assert 0 < small[1] < out[1]  # a short run moves the estimate less


def test_stays_on_the_regular_seasons_per_60_scale():
    regular = {1: 0.05}
    out = fit([[0]] * 10, [0.0] * 10, regular)
    assert out[1] == 0.05
