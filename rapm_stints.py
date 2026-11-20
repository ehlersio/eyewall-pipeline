"""
rapm_stints.py -- stint-based RAPM with separate offence and defence: the
standard design (Macdonald; Evolving-Hockey), step 1 of
eyewall/rapm-rework-scope-2026-11-20.md. rapm.py runs it with
`--design stints`; its own per-attempt design stays the default until the
step-2 backtest decides.

Rows
    A stint is a stretch of 5v5 with the same ten skaters on: a sweep over
    the game's shifts (goalies left out -- shift_events rows ingested before
    2026-10 still carry goalie shifts). Each stint gives two rows, one per
    team attacking.
Columns
    offence (+1) for each attacking skater with at least `min_secs` of ice
    time in the pool, defence (+1) for each defending one, and three
    covariates: attacking at home, attacking side leading, attacking side
    trailing (score from every non-shootout goal before the stint).
Target and weight
    the attacking team's xG per 60 in the stint (nhl_xg_model.py on
    unblocked attempts, or nhl_shot_xg.py's bands; each game scored as a
    whole), weighted by the stint's seconds. Coefficients are xG/60
    directly: no per-60 constant (the old design's SHOTS_PER_60).
Result
    offence = xG/60 a player adds for his team, defence = xG/60 he allows
    (lower is better), net = offence - defence. The ridge pulls each toward
    0, league average.

Prototype on 2023-24..2025-26 (single seasons, alpha 3e5): next-season
on-ice GD/60 prediction 0.317 vs 0.299 for the per-attempt design, 0.201 vs
0.188 for players who changed teams. STINT_ALPHA: cross-validation over
games in scripts/backtest/backtest_rapm_design.py (2026-11-20) chose 3e5 for
2023-24 and 2025-26 and 1e6 for 2024-25 (inner skill at the two within
1%); docs/rapm_design_backtest_results.md.
"""

from collections import defaultdict

import numpy as np

STINT_ALPHA = 3e5
N_COVARIATES = 3  # home, leading, trailing


def game_stints(shifts, home, xg_events, goal_secs, goalie_ids=frozenset()):
    """One game's 5v5 stints.

    shifts: the game's shift_events rows; home: the home team code;
    xg_events: [(abs secs, team, xg, is_goal)] for the 5v5 attempts the
    target scores; goal_secs: [(abs secs, team)] for every non-shootout goal.
    Returns [(home_ids, away_ids, secs, home_xg, away_xg, home_goals,
    away_goals, home_lead)] -- an attempt at time t belongs to the stint
    (start, end] it falls in, and home_lead is the score before the stint."""
    skaters = [
        s for s in shifts if s["player_id"] not in goalie_ids and s["end_secs"] > s["start_secs"]
    ]
    teams = {s["team"] for s in skaters}
    if len(teams) != 2 or home not in teams:
        return []
    events = []
    for s in skaters:
        events.append((s["start_secs"], 1, s))
        events.append((s["end_secs"], -1, s))
    events.sort(key=lambda e: (e[0], e[1]))  # ends before starts at the same second
    attempts = sorted(xg_events, key=lambda e: e[0])
    goals = sorted(goal_secs)
    on = defaultdict(set)
    out, last, i = [], None, 0
    for t, delta, s in events:
        if last is not None and t > last:
            away_on = [v for k, v in on.items() if k != home]
            if len(on[home]) == 5 and away_on and len(away_on[0]) == 5:
                while i < len(attempts) and attempts[i][0] <= last:
                    i += 1
                hx = ax = 0.0
                hg = ag = 0
                j = i
                while j < len(attempts) and attempts[j][0] <= t:
                    _, team, x, is_goal = attempts[j]
                    if team == home:
                        hx += x
                        hg += bool(is_goal)
                    else:
                        ax += x
                        ag += bool(is_goal)
                    j += 1
                before = [g for g in goals if g[0] < last]
                lead = sum(1 if g[1] == home else -1 for g in before)
                out.append(
                    (
                        tuple(sorted(on[home])),
                        tuple(sorted(away_on[0])),
                        t - last,
                        hx,
                        ax,
                        hg,
                        ag,
                        lead,
                    )
                )
        if delta > 0:
            on[s["team"]].add(s["player_id"])
        else:
            on[s["team"]].discard(s["player_id"])
        last = t
    return out


def pool_stints(pool, xg_of, shot_abs_secs) -> list:
    """Every stint in a rapm.load_pool() pool. xg_of: {id(row): xG} from
    rapm.score_shots(). Shootout goals (period 5+ outside the playoffs)
    don't count toward the score."""
    shifts_by_game = defaultdict(list)
    for s in pool["shifts"]:
        shifts_by_game[s["game_id"]].append(s)
    shots_by_game = defaultdict(list)
    for r in pool["shots"]:
        shots_by_game[r["game_id"]].append(r)
    stints = []
    for gid, shifts in shifts_by_game.items():
        home = pool["game_home"].get(gid)
        if not home:
            continue
        shots = shots_by_game.get(gid, [])
        xg_events = [
            (shot_abs_secs(r), r["team"], xg_of.get(id(r), 0.0), r["event_type"] == "goal")
            for r in shots
            if r.get("situation_code") == "1551" and xg_of.get(id(r), 0.0) > 0
        ]
        goal_secs = [
            (shot_abs_secs(r), r["team"])
            for r in shots
            if r["event_type"] == "goal"
            and not (int(r.get("game_type") or 2) != 3 and int(r.get("period") or 1) >= 5)
        ]
        stints.extend(game_stints(shifts, home, xg_events, goal_secs, pool["goalies"]))
    return stints


def five_on_five_secs(stints) -> dict:
    """{player: seconds of 5v5} over the stints."""
    secs = defaultdict(float)
    for h, a, dur, *_ in stints:
        for p in (*h, *a):
            secs[p] += dur
    return secs


def stint_matrix(stints, min_secs):
    """(X, y, w, player_idx, secs): two rows per stint, columns offence[0:n],
    defence[n:2n], then the covariates. Built straight into arrays: a
    3-season pool is ~1.3M stints."""
    from scipy.sparse import csr_matrix

    secs = five_on_five_secs(stints)
    players = sorted(p for p, v in secs.items() if v >= min_secs)
    idx = {p: i for i, p in enumerate(players)}
    n = len(players)
    n_rows = 2 * len(stints)
    per_row = 10 + N_COVARIATES
    rows = np.empty(n_rows * per_row, dtype=np.int32)
    cols = np.empty(n_rows * per_row, dtype=np.int32)
    vals = np.empty(n_rows * per_row, dtype=np.float32)
    y = np.empty(n_rows, dtype=np.float64)
    w = np.empty(n_rows, dtype=np.float64)
    k = r = 0
    for h, a, dur, hx, ax, _hg, _ag, lead in stints:
        for att, dfn, xg, at_home, att_lead in ((h, a, hx, 1.0, lead), (a, h, ax, 0.0, -lead)):
            for p in att:
                if p in idx:
                    rows[k], cols[k], vals[k] = r, idx[p], 1.0
                    k += 1
            for p in dfn:
                if p in idx:
                    rows[k], cols[k], vals[k] = r, n + idx[p], 1.0
                    k += 1
            for c, v in enumerate(
                (at_home, 1.0 if att_lead > 0 else 0.0, 1.0 if att_lead < 0 else 0.0)
            ):
                rows[k], cols[k], vals[k] = r, 2 * n + c, v
                k += 1
            y[r] = xg / dur * 3600
            w[r] = dur
            r += 1
    X = csr_matrix((vals[:k], (rows[:k], cols[:k])), shape=(n_rows, 2 * n + N_COVARIATES))
    return X, y, w, idx, secs


def fit(X, y, w, idx, alpha=STINT_ALPHA, prior=None, out=None):
    """Ridge on the stint rows. prior: {player: (offence, defence)} to shrink
    toward instead of 0 (playoff RAPM shrinks toward the regular season).
    Returns {player: (offence, defence, net)} in xG/60. `out`, when given,
    gets the intercept and the covariates' coefficients (home, leading,
    trailing), for predicting held-out stints."""
    from sklearn.linear_model import Ridge

    n = len(idx)
    beta0 = np.zeros(X.shape[1])
    if prior:
        for p, i in idx.items():
            off, dfn = prior.get(p, (0.0, 0.0))
            beta0[i], beta0[n + i] = off, dfn
    model = Ridge(alpha=alpha, fit_intercept=True).fit(X, y - X @ beta0, sample_weight=w)
    coef = beta0 + model.coef_
    if out is not None:
        out["intercept"] = float(model.intercept_)
        out["covariates"] = [float(c) for c in coef[2 * n :]]
    return {
        p: (float(coef[i]), float(coef[n + i]), float(coef[i] - coef[n + i]))
        for p, i in idx.items()
    }
