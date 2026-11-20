# RAPM design backtest: per-attempt (production) vs stints

Written by `scripts/backtest/backtest_rapm_design.py` (step 2 of `rapm-rework-scope-2026-11-20.md`). The ship rule is in the script's docstring, set before the first run.

## Held-out stints

Each season: games in five folds by game id; four fit (alpha chosen on an inner split of those four), the fifth is scored. Skill = 1 - error / error of the training mean: weighted squared error of xG/60, Poisson deviance of goals. Higher is better; 0 = no better than the league average.

| Predictor | 2023-24 | 2024-25 | 2025-26 | Pooled |
|---|---|---|---|---|
| stints (model) | 0.0018 / 0.0037 | 0.0017 / 0.0052 | 0.0011 / 0.0016 | 0.0015 / 0.0035 |
| stints (bands) | 0.0020 / 0.0036 | 0.0015 / 0.0035 | 0.0012 / 0.0015 | 0.0016 / 0.0029 |
| production | 0.0015 / 0.0015 | 0.0013 / 0.0028 | 0.0009 / 0.0021 | 0.0012 / 0.0022 |
| raw on-ice | 0.0019 / 0.0003 | 0.0013 / 0.0042 | 0.0010 / 0.0019 | 0.0014 / 0.0022 |
| teams | 0.0006 / -0.0002 | 0.0005 / 0.0006 | 0.0007 / 0.0011 | 0.0006 / 0.0005 |

Each cell: xG skill / goals skill. Chosen alpha by season: 2023-24 300000, 2024-25 1e+06, 2025-26 300000.

## Next season

Single-season fits on every game. Correlation of net RAPM in N with on-ice 5v5 GD/60 in N+1 (300+ 5v5 minutes in both), and season-to-season repeatability of net RAPM.

| Design | Changed teams | Stayed | Repeatability |
|---|---|---|---|
| stints (model) | 0.207 (n=164, n=136) | 0.337 (n=407, n=426) | 0.516 (n=571, n=562) |
| production | 0.188 (n=164, n=136) | 0.323 (n=407, n=426) | 0.523 (n=571, n=562) |

## Ship rule

- 1. held-out goals: **met**
- 2. held-out xG: **met**
- 3. movers, next season: **met**

**Switch: yes.**
