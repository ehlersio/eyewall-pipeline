"""rapm.py's regression rows: one sign convention for X and y, so a
team's RAPM doesn't depend on where its name sorts."""

import numpy as np
from sklearn.linear_model import Ridge

from rapm import design_row

IDX = {pid: i for i, pid in enumerate([1, 2, 3, 4, 5, 6])}
ANA = [1, 2, 3]  # the reference team in an ANA-WSH game (sorts first)
WSH = [4, 5, 6]


def test_reference_team_shooting_is_plus_one_for_its_skaters():
    assert design_row(ANA, WSH, IDX, 1) == {0: 1, 1: 1, 2: 1, 3: -1, 4: -1, 5: -1}


def test_other_team_shooting_is_still_from_the_reference_teams_side():
    # WSH shoots: sign -1, so WSH skaters -1 and ANA skaters +1 -- matching
    # y = -xG. Before the fix WSH got +1 here with a negative y.
    assert design_row(WSH, ANA, IDX, -1) == {3: -1, 4: -1, 5: -1, 0: 1, 1: 1, 2: 1}


def test_players_off_the_index_are_left_out():
    assert design_row([1, 99], [4], IDX, 1) == {0: 1, 3: -1}


def test_evenly_matched_teams_get_equal_rapm_whatever_their_names():
    # Equal shots of equal xG each way: both teams' players should come out
    # the same. Under the old rows ANA came out well above WSH.
    X, y = [], []
    for _ in range(200):
        for shooters, defenders, sign in ((ANA, WSH, 1), (WSH, ANA, -1)):
            row = design_row(shooters, defenders, IDX, sign)
            X.append([row.get(i, 0) for i in range(6)])
            y.append(sign * 0.08)
    coef = Ridge(alpha=1.0).fit(np.array(X, float), np.array(y)).coef_
    assert abs(coef[:3].mean() - coef[3:].mean()) < 1e-6
