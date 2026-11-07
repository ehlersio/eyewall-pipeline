# Trade candidacy backtest (Phase 3)

Generated 2026-11-07 by `python -m scripts.backtest.backtest_trade_candidacy`. Scope: `eyewall/trade-likelihood-scope-2026-11-06.md` §4, §9. Labels from the NHL's per-game rows (team switches) plus `trade_assets`; waiver claims dropped. Population at each as-of date (Jan 1, Feb 1, deadline - 14 days): skaters with 10+ GP and goalies with 5+ GP before it.

## Verdict

**Tiers, not a percentage** (§4.4): 1 decile(s) with 30+ players miss the ±5-point bar.

Pooled held-out Brier: model 0.0622, base rate 0.0655, two-cell rule 0.0629.

## Caveats (read with the verdict)

- **Margin over the two-cell rule is small.** Pooled Brier is 1.1% better than "expiring and below the playoff line". Per held-out season the model wins on Brier in 3 of 3 and on log loss in 2 of 3.
- **The ±5-point bar is loose at low probabilities.** The three lowest deciles predict 1.3% on average and 2.7% were traded: inside the bar, but off by 2.0x relative. The top decile predicts 28.0% and 22.6% were traded.
- **Labels are mostly real trade rows.** 825 of 885 traded labels have a `trade_assets` row in the window; 60 come from a team switch alone (a waiver claim the description match missed is one way that happens).
- **`cap_unknown` carries weight** (see the coefficients): a player with no public cap hit in the dataset is traded more often. It is the same signal in production (today's dataset has the same gaps), but it is a data-coverage signal, not a hockey one.

## Data

| Season | Rows | Traded | Rate | Dropped (waiver) | Dropped (moved before as-of) |
|---|---:|---:|---:|---:|---:|
| 2021-22 | 2356 | 179 | 7.6% | 8 | 1 |
| 2022-23 | 2340 | 217 | 9.3% | 7 | 7 |
| 2023-24 | 2315 | 129 | 5.6% | 20 | 3 |
| 2024-25 | 2274 | 201 | 8.8% | 18 | 12 |
| 2025-26 | 2372 | 159 | 6.7% | 11 | 3 |

## Held-out seasons (leave one season out)

| Test season | Rows | Traded | Brier model | Brier base | Brier two-cell | Log loss model | Log loss base | Log loss two-cell |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2023-24 | 2315 | 129 | 0.0494 | 0.0531 | 0.0498 | 0.1872 | 0.2187 | 0.1973 |
| 2024-25 | 2274 | 201 | 0.0781 | 0.0813 | 0.0791 | 0.2964 | 0.3044 | 0.2950 |
| 2025-26 | 2372 | 159 | 0.0595 | 0.0626 | 0.0602 | 0.2233 | 0.2461 | 0.2313 |

## Reliability (pooled held-out, deciles by predicted probability)

| Players | Predicted | Observed | Within ±5 pts |
|---:|---:|---:|---|
| 697 | 0.6% | 1.1% | yes |
| 696 | 1.3% | 2.7% | yes |
| 696 | 2.1% | 4.3% | yes |
| 696 | 2.9% | 4.5% | yes |
| 696 | 3.8% | 3.9% | yes |
| 696 | 4.9% | 4.5% | yes |
| 696 | 6.5% | 6.5% | yes |
| 696 | 8.9% | 8.0% | yes |
| 696 | 13.3% | 12.2% | yes |
| 696 | 28.0% | 22.6% | **no** |

## Top 50 by predicted probability: share traded

| Season | As of | Traded in top 50 |
|---|---|---:|
| 2023-24 | 2024-01-01 | 20.0% |
| 2023-24 | 2024-02-01 | 20.0% |
| 2023-24 | 2024-02-23 | 18.0% |
| 2024-25 | 2025-01-01 | 32.0% |
| 2024-25 | 2025-02-01 | 28.0% |
| 2024-25 | 2025-02-21 | 28.0% |
| 2025-26 | 2026-01-01 | 26.0% |
| 2025-26 | 2026-02-01 | 20.0% |
| 2025-26 | 2026-02-20 | 22.0% |

## Sensitivity: 2021-22 and 2022-23 added to training

Pooled held-out Brier with the extra seasons: model 0.0608 (vs 0.0622 without), base 0.0655, two-cell 0.0628. Would show a percentage: yes.

## Coefficients (fit on all three test seasons, standardized features)

| Feature | Coefficient |
|---|---:|
| `log_cap` | +0.880 |
| `goalie` | -0.587 |
| `ppg` | -0.543 |
| `years_left` | -0.504 |
| `cap_unknown` | +0.419 |
| `expiring_x_seller` | +0.367 |
| `clause_full` | -0.364 |
| `gap` | -0.327 |
| `ufa_expiring` | +0.239 |
| `defense` | -0.222 |
| `toi_trend` | -0.139 |
| `expiring` | -0.135 |
| `days_to_deadline` | +0.120 |
| `age` | +0.109 |
| `sv_pct` | -0.095 |
| `clause_modified` | -0.078 |
| `seller` | -0.074 |
| `contract_unknown` | +0.050 |

## Production tiers (`trade_candidacy_model.json`)

`trade_candidacy.py` scores today's players with the fit above and shows a tier, never the number. Cuts are the fitted model's top tenth and next two tenths of the training population; the traded rate is the matching held-out deciles'.

| Tier | Model probability at least | Share of population | Traded (held out) |
|---|---:|---:|---:|
| several | 16.2% | 10.0% | 22.6% |
| some | 7.4% | 20.0% | 10.1% |
