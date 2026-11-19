# NHL xG model: backtest

Written by `scripts/backtest/fit_nhl_xg.py` on 2026-11-18. Method and features: `nhl_xg_model.py`'s docstring. Regular season only; blocked attempts, shootout attempts and penalty shots are left out.

## Leave one season out

Each season scored by a model fit on the other complete seasons. Each cell: log loss / Brier / AUC / predicted ÷ actual goals. Lower log loss and Brier are better; higher AUC is better.

| Season | Attempts | Goals | Model | Bands, refit | Bands, as shipped |
|---|---|---|---|---|---|
| 2022-23 | 113,788 | 8,219 | 0.2305 / 0.0623 / 0.745 / 1.025 | 0.2472 / 0.0654 / 0.659 / 1.000 | 0.2512 / 0.0660 / 0.659 / 0.727 |
| 2023-24 | 114,636 | 8,063 | 0.2213 / 0.0600 / 0.768 / 0.984 | 0.2415 / 0.0637 / 0.666 / 1.013 | 0.2452 / 0.0643 / 0.666 / 0.738 |
| 2024-25 | 112,207 | 7,894 | 0.2204 / 0.0596 / 0.769 / 0.976 | 0.2428 / 0.0638 / 0.657 / 0.991 | 0.2467 / 0.0644 / 0.657 / 0.727 |
| 2025-26 | 112,058 | 8,080 | 0.2280 / 0.0618 / 0.755 / 1.024 | 0.2485 / 0.0655 / 0.648 / 0.998 | 0.2522 / 0.0660 / 0.648 / 0.729 |

The shipped band values were calibrated with blocked attempts in, so on unblocked attempts alone they under-predict (about 0.73x); the refit bands are the fair baseline.

## Forward check: 2026-27 so far

Fit on every complete season, scored on the in-progress one.

| Attempts | Goals | Model | Bands, refit |
|---|---|---|---|
| 28,724 | 2,152 | 0.2341 / 0.0638 / 0.754 / 0.992 | 0.2561 / 0.0679 / 0.642 / 0.972 |

## Reliability (pooled held-out predictions)

| Predicted xG decile | Attempts | Predicted rate | Actual rate |
|---|---|---|---|
| 0.000-0.009 | 45,263 | 0.0064 | 0.0062 |
| 0.009-0.016 | 45,262 | 0.0122 | 0.0118 |
| 0.016-0.026 | 45,265 | 0.0208 | 0.0192 |
| 0.026-0.039 | 45,285 | 0.0324 | 0.0289 |
| 0.039-0.054 | 45,230 | 0.0463 | 0.0438 |
| 0.054-0.071 | 45,290 | 0.0623 | 0.0616 |
| 0.071-0.088 | 45,281 | 0.0789 | 0.0817 |
| 0.088-0.112 | 45,208 | 0.0993 | 0.1072 |
| 0.112-0.153 | 45,334 | 0.1309 | 0.1405 |
| 0.153-0.996 | 45,271 | 0.2247 | 0.2114 |

| Group | Attempts | Model predicted | Bands predicted | Actual |
|---|---|---|---|---|
| 5v5 (no other state flag) | 354,485 | 0.0604 | 0.0701 | 0.0602 |
| Power play | 74,471 | 0.0963 | 0.0763 | 0.0962 |
| Short-handed | 10,533 | 0.0733 | 0.0737 | 0.0732 |
| Empty net | 3,439 | 0.5586 | 0.0574 | 0.5571 |
| Rebound | 47,377 | 0.1099 | 0.1050 | 0.1095 |
| Tip-in | 40,229 | 0.0793 | 0.1110 | 0.0792 |
| Slap shot | 53,634 | 0.0495 | 0.0421 | 0.0494 |
| Within 15 ft | 103,558 | 0.1300 | 0.1295 | 0.1300 |
| 15-30 ft | 104,825 | 0.0951 | 0.0950 | 0.0955 |
| 30-60 ft | 190,953 | 0.0398 | 0.0357 | 0.0390 |
| Beyond 60 ft | 53,353 | 0.0243 | 0.0389 | 0.0249 |

## Against MoneyPuck's 5v5 game xG, 2025-26

2,624 team-games, 5v5 only. A sanity check: MoneyPuck's numbers are read, never served.

| | This model | Bands |
|---|---|---|
| Correlation per team-game | 0.920 | 0.756 |
| Correlation per team-season | 0.971 | 0.925 |
| Mean xG per team-game (MoneyPuck 2.049, goals 2.043) | 2.058 | 2.459 |

## Production coefficients (`nhl_xg_model.json`, fit on 2023-24, 2024-25, 2025-26)

338,901 attempts, 24,037 goals. Raw features (the scaler is folded in).

| Feature | Coefficient |
|---|---|
| intercept | +1.7304 |
| log_dist | -1.4889 |
| dist_10 | +0.3183 |
| dist_over_10 | +0.6869 |
| dist_over_20 | -1.0441 |
| dist_over_35 | -0.2875 |
| dist_over_60 | +0.3615 |
| angle_45 | +0.4499 |
| angle_45_sq | -0.5463 |
| dist_x_angle | -0.2150 |
| behind_net | +0.7355 |
| type_slap | +0.3846 |
| type_backhand | -0.5074 |
| type_tip-in | -0.9213 |
| type_deflected | -0.6620 |
| type_wrap-around | -1.1681 |
| type_poke | -0.5726 |
| type_bat | -0.4329 |
| type_other | +0.5899 |
| power_play | +0.2403 |
| short_handed | +0.2069 |
| even_not_5v5 | +0.2661 |
| empty_net | +4.8928 |
| rebound | -0.4289 |
| rebound_x_angle | +0.2521 |
