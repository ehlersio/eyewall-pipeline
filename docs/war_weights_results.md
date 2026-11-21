# WAR weights, measured

Written by `scripts/backtest/measure_war_weights.py` on 2026-11-21 from 2022-23, 2023-24, 2024-25, 2025-26 regular seasons (MoneyPuck skater files, EyeWall's game_log). `moneypuck.py` reads them from `war_weights.json`. Why each one: `eyewall/war-weights-review-2026-11-21.md`.

| Constant | Value | From |
|---|---|---|
| goals_per_win | 6.208 | slope of wins on goal differential, 128 team-seasons |
| penalty_goals_each | 0.1573 | (6,509 PP goals - 768 SH goals) / 36,486 skater penalties (30,631 PP opportunities) |
| penalty_weight | 0.578 | season-to-season correlation of (drawn - taken) per 60, 1549 skater pairs with 500+ min |
| finishing_k | 566.4 | finishing talent SD 0.0105 goals per attempt over 2970 player-seasons; weight at the median 133 attempts 0.19 |

WAR = (EV RAPM x 5v5 hours + penalty_weight x penalty_goals_each x (drawn - taken) + attempts / (attempts + finishing_k) x (goals - xG)) / goals_per_win + replacement.
