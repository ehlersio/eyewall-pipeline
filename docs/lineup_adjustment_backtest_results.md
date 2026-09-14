# Lineup adjustment backtest

`backtest_lineup_adjustment.py` (read-only), run 2026-09-14. Question: would knowing each
night's lineup make the Elo game-winner probabilities (the ones the public scorecard grades)
better? **Answer: not enough to adopt.** Missing skaters add nothing out of sample. The starting
goalie adds a small, consistent improvement, but only with the actual starter known in advance.

## Method

An **upper bound**: the actual lineups from the NHL's own boxscores (~3,900 regular-season
games, 2023-24 to 2025-26) -- who dressed and who started in goal -- which is more than any
injury report says before a game. Elo is replayed exactly as `elo_ratings.py` does. Two
adjustments are added to the Elo logit, `logit P(home) = Elo + a*(loss_away - loss_home) +
b*(gdev_home - gdev_away)`, and every row below is scored on games the weights were **not**
fit on:

- **Missing regular skaters** (`loss`): sum of max(prior-season WAR per game, 0) over a team's
  regulars (dressed for 10+ of its last 20 games) who didn't dress but played for it again
  later -- a real absence, not a trade or demotion. `player_seasons.war` exists only for
  2024-25 and 2025-26, so only 2025-26 games have a clean prior season: fit on one half of
  2025-26, scored on the other.
- **Starting goalie** (`gdev`): the starter's prior-season goals saved above league-average
  save % per game (NHL stats API season totals; `goalie_seasons.gsax` is 2025-26 only) minus
  the start-weighted average of the team's last 20 starters. Fit on one season, scored on
  the other.

## Results

Lower Brier and log loss are better.

| Test | Games | Brier (Elo → adjusted) | Log loss | Picked right | Fitted weight(s) |
|---|---|---|---|---|---|
| Goalie: fit 2024-25, score 2025-26 | 1,312 | 0.2484 → 0.2481 | 0.6900 → 0.6894 | 53.4% → 52.5% | 0.311 |
| Goalie: fit 2025-26, score 2024-25 | 1,312 | 0.2373 → 0.2368 | 0.6672 → 0.6661 | 58.5% → 58.5% | 0.23 |
| Skaters: fit 2025-26 1st half, score 2nd | 656 | 0.2438 → 0.2445 | 0.6807 → 0.6821 | 55.5% → 56.9% | 2.66 |
| Skaters: fit 2nd half, score 1st | 656 | 0.2530 → 0.2532 | 0.6993 → 0.6998 | 51.4% → 51.5% | -0.554 |
| Both: fit 1st half, score 2nd | 656 | 0.2438 → 0.2444 | 0.6807 → 0.6819 | 55.5% → 56.1% | 2.534, 0.291 |
| Both: fit 2nd half, score 1st | 656 | 0.2530 → 0.2528 | 0.6993 → 0.6988 | 51.4% → 51.2% | -0.563, 0.153 |

Subsets (same fits):

| Test | Games | Brier (Elo → adjusted) | Log loss | Picked right | Fitted weight(s) |
|---|---|---|---|---|---|
| Goalie, games with a non-usual starter (2025-26) | 890 | 0.2493 → 0.2482 | 0.6919 → 0.6897 | 53.1% → 52.5% | 0.311 |
| Goalie, games with a non-usual starter (2024-25) | 847 | 0.2394 → 0.2387 | 0.6713 → 0.6698 | 56.7% → 56.4% | 0.23 |
| Skaters, games with a notable absence (2nd half) | 268 | 0.2456 → 0.2473 | 0.6847 → 0.6880 | 53.4% → 57.1% | 2.66 |
| Skaters, games with a notable absence (1st half) | 284 | 0.2483 → 0.2488 | 0.6896 → 0.6906 | 51.8% → 52.5% | -0.554 |

## Reading it

- **Skaters:** worse out of sample in both directions, and the weight flips sign (+2.66 / -0.55)
  -- noise. The effect per game is small (typical gap between the teams ~0.014 WAR per game), and
  there's only half a season to fit on. Accuracy moved up in one half (55.5% -> 56.9%) while Brier
  got worse; Brier is the scorecard's measure.
- **Goalie:** Brier better in both directions (by 0.0003 and 0.0005) with a stable weight (0.31 /
  0.23 per goal saved per game, on the logit scale). That's roughly 5-8% of Elo's edge over "the
  home team wins", and it assumes the starter is known. In production it would have to use
  `starting_goalie.py`'s probabilities (74% top-pick accuracy), which shrinks it further.
- **Decision:** the game-winner model stays plain Elo for 2026-27's opening. The public methodology
  page (eyewallanalytics `/methodology`) says the adjustment was tested and not adopted. Revisit
  the goalie adjustment during 2026-27 once `goalie_start_probs` history exists, weighting by the
  probable-starter probabilities; any change gets a dated changelog entry there.
