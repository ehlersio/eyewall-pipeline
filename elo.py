"""
elo.py — Pure Elo rating math for NHL win-probability backtesting.

Purpose-built alternative to two things this pipeline already tried and
backtested worse than the plain standings scorecard: RAPM/Log5 (see
docs/prediction_model_backtest_results.md, docs/true_preseason_backtest_results.md)
and the scorecard's own hand-set weights (0.6/0.6/0.4/0.5/0.3, never fit --
see nhl.js:2552-2567). Elo is the standard tool for exactly this job across
sports analytics (this follows FiveThirtyEight's published NHL Elo
methodology) -- it needs only game results (date, home/away, score,
whether it went to overtime), which is all already in `game_log`, and its
season-to-season regression-to-mean is a principled, outcome-driven
version of the continuity-dampening hack this pipeline built by hand for
the preseason fallback.

No writes anywhere -- pure functions only, consumed by backtest_elo.py and
backtest_preseason.py. Not wired into production; this is the validation
step before any of that.

Constants below are FiveThirtyEight's published NHL Elo values, not tuned
against this pipeline's own data -- a reasonable, literature-grounded
starting point, not a claim of optimality. If Elo backtests well, tuning
K/HOME_ADVANTAGE/REGRESS_FRACTION against this pipeline's own 3 seasons of
game_log would be a natural follow-up, same posture as
prediction_model_backtest_results.md took toward RAPM's untuned constants.
"""

INITIAL_RATING = 1500.0
K = 6.0  # low relative to chess (32) -- a single NHL game is noisy; 538 uses a similarly low K for NHL
HOME_ADVANTAGE = 35.0  # Elo points added to the home team's rating before computing win probability
REGRESS_FRACTION = 1.0 / 3.0  # fraction of the gap to the mean erased between seasons

# Overtime/shootout results get a damped rating swing -- going to extra
# time is itself evidence the two teams were closely matched, so treating
# an OT win identically to a regulation blowout would over-credit it.
OT_MOV_MULT = 0.5


def expected_prob(rating_a: float, rating_b: float) -> float:
    """Standard Elo win probability for A given both ratings (apply any
    home-ice adjustment to rating_a/rating_b before calling this)."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def mov_multiplier(margin: int, went_to_overtime: bool) -> float:
    """Margin-of-victory scaling for the rating update. Regulation results
    get a mild boost for larger margins (capped, so a 7-0 blowout doesn't
    swing a rating 3x harder than a 2-1 win); OT/SO results are damped
    instead, since the extra period signals a near-even game regardless of
    the eventual (often single-goal) final margin."""
    if went_to_overtime:
        return OT_MOV_MULT
    return min(1.0 + 0.15 * max(margin - 1, 0), 1.75)


def update_ratings(
    rating_home: float,
    rating_away: float,
    home_won: bool,
    margin: int,
    went_to_overtime: bool,
) -> tuple[float, float]:
    """One game's rating update. Returns (new_rating_home, new_rating_away).
    margin is abs(home_score - away_score); pass 1 if unknown/degenerate."""
    exp_home = expected_prob(rating_home + HOME_ADVANTAGE, rating_away)
    actual_home = 1.0 if home_won else 0.0
    delta = K * mov_multiplier(margin, went_to_overtime) * (actual_home - exp_home)
    return rating_home + delta, rating_away - delta


def regress_to_mean(rating: float, mean: float = INITIAL_RATING) -> float:
    """Applied once per team at the start of a new season -- the
    outcome-driven, team-level analog of the hand-set continuity-dampening
    fraction the preseason fallback currently uses. A team with no prior
    rating (true expansion team) should just start at `mean` directly
    rather than calling this."""
    return mean + (rating - mean) * (1.0 - REGRESS_FRACTION)
