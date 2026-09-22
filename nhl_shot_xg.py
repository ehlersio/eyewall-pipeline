"""
nhl_shot_xg.py -- the NHL shot-quality proxy: one shot attempt's expected
goals from where it was taken.

MoneyPuck's own per-shot xG isn't stored in `shot_events`, so rapm.py (as
the outcome it regresses) and line_combinations.py (the Scouting tab's line
xGF%) approximate it from distance to the net. Both had their own copy of
this; it now lives here so the two can't drift apart again.

Calibration (2026-09, regular season, `shot_events`)
----------------------------------------------------
Each value is how often an attempt from that band actually became a goal.
Measured over 2025-26 (163,908 attempts, 8,915 goals) and checked against
2024-25 (166,849 / 8,650), which it had no part in fitting:

    band                 2025-26   2024-25
    high   (<= 15 ft)     0.0939    0.0923
    medium (<= 30 ft)     0.0613    0.0608
    low    (> 30 ft)      0.0301    0.0286

Summed over a season these give 0.998x that season's real goals and 1.022x
the holdout's -- a league's xG should land on its goals.

What this replaced, and why it mattered
---------------------------------------
Two things were wrong. A goal returned 1.0 rather than the value of its
location, so a goal's xG was its outcome; and the band values (0.20 / 0.07
/ 0.03) were roughly double the real rates. Together they put league xG at
21,527 against 8,915 goals -- 2.41x. Fixing only the goal case still left
1.53x. The same goals-as-1.0 mistake was fixed for the PWHL in #150; this
is its NHL twin.

A caveat kept deliberately: a blocked shot's coordinates are where the
block happened, not where the shot was taken, which pushes blocked attempts
into closer bands than they belong in. They stay counted (the same choice
pwhl_shot_xg.py makes, and dropping them would change what a Corsi-style
attempt means here), and the calibration above absorbs the bias, since the
rates were measured with those attempts in.
"""

import math

# Distance from the net, in feet, at each band's edge.
HIGH_FT = 15
MEDIUM_FT = 30

DANGER_XG = {
    "high": 0.094,
    "medium": 0.061,
    "low": 0.030,
}

# Every attempt type that carries xG. A goal is scored by its location like
# any other attempt -- see above.
REAL_SHOT_TYPES = ("goal", "shot-on-goal", "missed-shot", "blocked-shot")

# The net's centre, in the NHL's play-by-play coordinates.
GOAL_X = 89


def danger_bucket(x, y) -> str:
    """Which band a shot at (x, y) falls in. Coordinates are feet from
    centre ice; |x| because a team attacks either end."""
    dist = math.hypot(abs(x or 0) - GOAL_X, y or 0)
    if dist <= HIGH_FT:
        return "high"
    if dist <= MEDIUM_FT:
        return "medium"
    return "low"


def shot_xg(event_type: str, x, y) -> float:
    """One attempt's expected goals, or 0.0 for an event that isn't a shot
    attempt at all."""
    if event_type not in REAL_SHOT_TYPES:
        return 0.0
    return DANGER_XG[danger_bucket(x, y)]
