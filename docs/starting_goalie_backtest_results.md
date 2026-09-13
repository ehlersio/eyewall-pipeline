# Starting goalie backtest

`backtest_starting_goalie.py` (read-only), from `goalie_game_starts` (the NHL boxscore's own
`starter` flag). One choice per team per regular-season game with 2+ dressed goalies: which
one starts. 7,837 choices across 2023-24 to 2025-26. Forward-chaining folds -- each
test season is scored by a model fit only on earlier seasons.

Accuracy = the top pick started. Log loss / Brier: lower is better.

## Train 20232024 -> test 20242025

2,623 choices, 398 of them back-to-backs.

| Method | Accuracy | Log loss | Brier | B2B accuracy | B2B log loss | B2B Brier |
|---|---|---|---|---|---|---|
| Model | 74.9% | 0.527 | 0.353 | 92.7% | 0.234 | 0.129 |
| Last starter | 56.7% | 0.686 | 0.493 | 92.0% | 0.605 | 0.412 |
| Share of last 10 | 64.8% | 0.675 | 0.466 | 38.9% | 0.996 | 0.728 |

Last-starter baseline rate (training): 44.3%.

## Train 20232024, 20242025 -> test 20252026

2,623 choices, 429 of them back-to-backs.

| Method | Accuracy | Log loss | Brier | B2B accuracy | B2B log loss | B2B Brier |
|---|---|---|---|---|---|---|
| Model | 72.8% | 0.544 | 0.368 | 90.7% | 0.264 | 0.155 |
| Last starter | 57.6% | 0.684 | 0.491 | 89.5% | 0.606 | 0.413 |
| Share of last 10 | 62.9% | 0.684 | 0.478 | 42.0% | 0.963 | 0.703 |

Last-starter baseline rate (training): 44.1%.

## Pooled test seasons

| Method | Accuracy | Log loss | Brier |
|---|---|---|---|
| Model | 73.8% | 0.535 | 0.361 |
| Last starter | 57.1% | 0.685 | 0.492 |
| Share of last 10 | 63.9% | 0.680 | 0.472 |

## Calibration (model, pooled test seasons, every candidate)

| Predicted | Candidates | Avg predicted | Actually started |
|---|---|---|---|
| 0-10% | 582 | 4% | 4% |
| 10-20% | 895 | 16% | 15% |
| 20-30% | 893 | 26% | 22% |
| 30-40% | 1321 | 34% | 30% |
| 40-50% | 1547 | 44% | 40% |
| 50-60% | 1563 | 56% | 60% |
| 60-70% | 1321 | 66% | 70% |
| 70-80% | 893 | 74% | 78% |
| 80-90% | 895 | 84% | 85% |
| 90-100% | 582 | 96% | 96% |

## Production weights (fit on all three seasons)

| Feature | Weight |
|---|---|
| `share_last10` | +2.212 |
| `started_last` | -0.545 |
| `b2b_started` | -1.331 |
| `b2b_other` | +1.331 |
| `log_days_rest` | +0.003 |
| `no_recent` | -0.696 |

Caveats: candidates in history are the goalies who dressed; in production they're the team's
roster goalies (the same two in nearly every game). Injuries aren't in the backtest --
player_injury_history starts 2026-09-12 -- so production excludes goalies listed out / IR as a
rule on top of the model rather than as a fitted feature.
