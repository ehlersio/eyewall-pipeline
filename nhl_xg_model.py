"""
nhl_xg_model.py -- EyeWall's NHL expected-goals model: the chance an
unblocked shot attempt becomes a goal, from where and how it was taken and
the game state around it.

Built 2026-11 to give the public API a per-game xG of EyeWall's own
(eyewall/eyewall-api-band-xg-scope-2026-11-18.md §2, option B): MoneyPuck's
game xG can't be served, and nhl_shot_xg.py's three distance bands only
know distance. Everything here comes from `shot_events` columns the
pipeline already stores, so the output carries no MoneyPuck input.

The model
---------
Logistic regression over unblocked attempts (goals, shots on goal, missed
shots). Features, per attempt:

  where     distance to the attacked net's centre (log, linear, and
            piecewise-linear terms past 10/20/35/60 ft), angle off the net's
            centreline (linear and squared), distance x angle, and "behind
            the net" (angle > 90 degrees)
  how       shot type, with wrist as the baseline. Snap shots are folded
            into wrist: the NHL's scorers have tagged more and more wrist
            shots as snaps (15% of unblocked attempts in 2022-23, 25% in
            2025-26, 28% early in 2026-27), and keeping them apart made the
            model over-predict each newer season (1.053x 2025-26's goals,
            against 1.023x merged). Blank or rare types (between-legs,
            cradle) count as "other".
  state     power play, short-handed, 4v4/3v3, and shooting at an empty
            net, from `situation_code` read from the shooter's side (the
            code is away goalie, away skaters, home skaters, home goalie)
  sequence  rebound: the same team had an attempt (blocked ones included)
            in the previous 3 seconds of the same period, alone and times
            angle

Tried and left out because they added nothing on held-out seasons: score
state (log loss 0.22220 vs 0.22222), and an attempt within 6 seconds of the
other team's (a quick-transition proxy; 0.2305 vs 0.2309 without it on
2022-23, equal elsewhere). A real rush flag needs the non-shot events the
pipeline doesn't store.

Left out entirely, with no xG: blocked attempts (where a block happened says
little about the shot, and none can score), shootout attempts (period 5
and later outside the playoffs) and penalty shots (a side with 0 or 1 skaters in
`situation_code`). shot_events holds about 2,900 shootout attempts and a
few hundred penalty shots over 2022-23..2025-26.

Which net a team attacks isn't stored, so attack_signs() reads it from the
game's own attempts. The first version took the nearer net (|x|), which put
~7,500 attempts from teams' own halves (7 goals against a goalie) next to
the net they were moving away from.

Coefficients are fit by scripts/backtest/fit_nhl_xg.py on the three most
recent complete regular seasons and frozen in MODEL_PATH; backtest numbers
are in docs/nhl_xg_backtest_results.md. Applying them needs only `math`.
"""

import json
import math
import os
from collections import defaultdict

GOAL_X = 89
UNBLOCKED_TYPES = ("goal", "shot-on-goal", "missed-shot")
ATTEMPT_TYPES = (*UNBLOCKED_TYPES, "blocked-shot")

# Shot types with their own coefficient; "wrist" and "snap" are the
# baseline, anything else is "other".
SHOT_TYPES = ("slap", "backhand", "tip-in", "deflected", "wrap-around", "poke", "bat")
BASELINE_TYPES = ("wrist", "snap")

REBOUND_SECONDS = 3

# Distance knots (feet) for the piecewise-linear terms: goal rates fall
# steeply over the first 20 feet and flatten past the blue-line range, which
# log + linear terms alone couldn't follow (they over-predicted the
# low-danger deciles and under-predicted 15-30 ft).
DIST_KNOTS = (10, 20, 35, 60)

FEATURES = (
    "log_dist",
    "dist_10",
    *(f"dist_over_{k}" for k in DIST_KNOTS),
    "angle_45",
    "angle_45_sq",
    "dist_x_angle",
    "behind_net",
    *(f"type_{t}" for t in SHOT_TYPES),
    "type_other",
    "power_play",
    "short_handed",
    "even_not_5v5",
    "empty_net",
    "rebound",
    "rebound_x_angle",
)

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nhl_xg_model.json")


def clock_seconds(time_in_period) -> int:
    """'MM:SS' elapsed in the period -> seconds."""
    m, s = str(time_in_period).split(":")
    return int(m) * 60 + int(s)


def _skaters(situation_code, is_home):
    """(own skaters, opposing skaters, own goalie in, opposing goalie in),
    or None for a code that isn't four digits."""
    code = str(situation_code or "")
    if len(code) != 4 or not code.isdigit():
        return None
    away_g, away_sk, home_sk, home_g = (int(c) for c in code)
    if is_home:
        return home_sk, away_sk, home_g, away_g
    return away_sk, home_sk, away_g, home_g


def is_excluded(row, home_team, game_type) -> bool:
    """A shootout attempt or penalty shot: never scored, never part of a
    sequence."""
    if game_type != 3 and int(row["period"]) >= 5:  # preseason has shootouts too
        return True
    state = _skaters(row.get("situation_code"), row.get("team") == home_team)
    if state is None:
        return True
    own_sk, opp_sk, _, _ = state
    return own_sk <= 1 or opp_sk <= 1


def attack_signs(shots) -> dict:
    """{team: +1 or -1}: the sign of x at the net the team attacks in
    period 1. Teams change ends every period (overtime included, which
    plays at the second period's ends), so period p's net is
    sign * (-1)**(p - 1). Read from where the team's unblocked attempts
    were taken: over 2022-23..2026-27 the rule matched the majority side
    in all but 12 of ~44,000 team-periods. A team with no unblocked
    attempts gets no entry and falls back to the nearer net."""
    total = defaultdict(float)
    for r in shots:
        if r.get("event_type") in UNBLOCKED_TYPES:
            total[r.get("team")] += float(r["x"]) * (-1) ** (int(r["period"]) - 1)
    return {t: (1 if v > 0 else -1) for t, v in total.items() if v != 0}


def net_relative_x(row, sign) -> float:
    """x measured toward the attacked net: GOAL_X at its goal line,
    negative from the team's own half. With no side known, |x|."""
    x = float(row["x"])
    if sign is None:
        return abs(x)
    return x * sign * (-1) ** (int(row["period"]) - 1)


def features_for(row, state, rebound, sign=None) -> dict:
    """One unblocked attempt's feature values, keyed by FEATURES."""
    x = net_relative_x(row, sign)
    y = float(row["y"] or 0)
    dx = GOAL_X - x
    dist = math.hypot(dx, y)
    angle = math.degrees(math.atan2(abs(y), dx))
    shot_type = row.get("shot_type") or ""
    own_sk, opp_sk, _own_g, opp_g = state
    f = {
        "log_dist": math.log1p(dist),
        "dist_10": dist / 10,
        **{f"dist_over_{k}": max(0.0, dist - k) / 10 for k in DIST_KNOTS},
        "angle_45": angle / 45,
        "angle_45_sq": (angle / 45) ** 2,
        "dist_x_angle": (dist / 10) * (angle / 45),
        "behind_net": 1.0 if angle > 90 else 0.0,
    }
    for t in SHOT_TYPES:
        f[f"type_{t}"] = 1.0 if shot_type == t else 0.0
    f["type_other"] = 0.0 if shot_type in SHOT_TYPES or shot_type in BASELINE_TYPES else 1.0
    goalie_in = opp_g == 1
    f["power_play"] = 1.0 if goalie_in and own_sk > opp_sk else 0.0
    f["short_handed"] = 1.0 if goalie_in and own_sk < opp_sk else 0.0
    f["even_not_5v5"] = 1.0 if goalie_in and own_sk == opp_sk and own_sk != 5 else 0.0
    f["empty_net"] = 0.0 if goalie_in else 1.0
    f["rebound"] = 1.0 if rebound else 0.0
    f["rebound_x_angle"] = angle / 45 if rebound else 0.0
    return f


def game_attempts(shots, home_team, game_type):
    """Every unblocked, non-excluded attempt of one game with its features.

    shots: the game's shot_events rows (any order) with period,
    time_in_period, event_id, event_type, team, x, y, shot_type,
    situation_code. Returns [(row, features)] in game order."""
    ordered = sorted(
        (r for r in shots if r.get("event_type") in ATTEMPT_TYPES),
        key=lambda r: (
            int(r["period"]),
            clock_seconds(r["time_in_period"]),
            int(r.get("event_id") or 0),
        ),
    )
    signs = attack_signs(r for r in ordered if not is_excluded(r, home_team, game_type))
    last = {}  # team -> (period, seconds) of its latest attempt
    out = []
    for r in ordered:
        if is_excluded(r, home_team, game_type):
            continue
        team = r.get("team")
        period = int(r["period"])
        t = clock_seconds(r["time_in_period"])

        if r["event_type"] in UNBLOCKED_TYPES:
            state = _skaters(r.get("situation_code"), team == home_team)
            prev = last.get(team)
            rebound = prev is not None and prev[0] == period and 0 <= t - prev[1] <= REBOUND_SECONDS
            out.append((r, features_for(r, state, rebound, signs.get(team))))
        last[team] = (period, t)
    return out


def load_model(path=MODEL_PATH) -> dict:
    with open(path) as f:
        model = json.load(f)
    missing = set(FEATURES) - set(model["coefficients"])
    if missing:
        raise ValueError(f"{path} lacks coefficients for {sorted(missing)}")
    return model


def probability(features: dict, model: dict) -> float:
    z = model["intercept"] + sum(model["coefficients"][k] * features[k] for k in FEATURES)
    return 1 / (1 + math.exp(-z))


def game_xg(shots, home_team, game_type, model=None):
    """[(row, xg)] for one game's scored attempts, in game order."""
    model = model or load_model()
    return [(r, probability(f, model)) for r, f in game_attempts(shots, home_team, game_type)]


def team_totals(shots, home_team, game_type, model=None) -> dict:
    """{team: {"attempts", "goals", "xg"}} over one game's scored attempts."""
    totals = defaultdict(lambda: {"attempts": 0, "goals": 0, "xg": 0.0})
    for r, p in game_xg(shots, home_team, game_type, model):
        t = totals[r["team"]]
        t["attempts"] += 1
        t["goals"] += 1 if r["event_type"] == "goal" else 0
        t["xg"] += p
    return dict(totals)
