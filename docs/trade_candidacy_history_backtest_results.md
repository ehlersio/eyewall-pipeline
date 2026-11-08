# Trade candidacy backtest with deadline rosters (contract-gaps Phase 3b)

Generated 2026-11-08 by `python -m scripts.backtest.backtest_trade_candidacy_history`. Scope: `eyewall/contract-gaps-scope-2026-11-07.md` §3. Same labels, population, as-of dates, model and ship rule as `docs/trade_candidacy_backtest_results.md` (#258); contracts from the deadline rosters (29,817 rows, CapFriendly team pages captured before each as-of date, tier E) where they have the player, the nhl-contracts release otherwise; clause four-way. Held-out seasons (leave one out): 2019-20, 2020-21, 2021-22, 2022-23, 2023-24, 2024-25, 2025-26.

## Verdict

**Passes the ship rule (§4.4).** The model beats both baselines on pooled held-out Brier and every decile with 30+ players is within ±5 points. Switching candidacy to a percentage is §3c, a separate go/no-go; production is unchanged.

Pooled held-out Brier: model 0.0630, base rate 0.0690, two-cell rule 0.0656 (3.8% better than the two-cell rule). The model wins on Brier in 7 of 7 held-out seasons.

## Caveats (read with the verdict)

- **Largest decile miss: 9.6% predicted vs 6.9% observed** (2.7 points). The bar is absolute: the three lowest deciles predict 1.4% and 1.7% were traded: inside the bar, but 1.3x off in relative terms.
- **The pass comes from the extra seasons, not the clause change:** the three-way clause run below also passes.
- **Two contract eras.** 2019-20, 2020-21, 2021-22, 2022-23, 2023-24 take contracts from the deadline rosters (as of the capture, extensions already signed included, almost no unknown clause); 2024-25, 2025-26 from the release (no extension known, as in #258; clause unknown for 2024-25 96, 2025-26 562 rows). `clause_unknown` is mostly an era marker; its weight is small.
- **Roster staleness.** A capture is the last one before the as-of date, not one on it. Most 2019-20 captures are from late 2019 (median 94 days before the deadline); later seasons lag 1-37 days at the median (nhl-contracts README, deadline rosters).
- **2020-21** was a 56-game season with four divisions: no Jan 1 rows (no games yet), few Feb 1 rows (10 GP minimum), and the playoff line is the 4th team in the division.
- **Not a production test.** The live job scores with `trade_candidacy_model.json` (three seasons, three-way clause). Showing a percentage would need that refit, its clause input in `trade_candidacy.py`, and the Worker/app wording (scope §3c).

## Data

| Season | Rows | Traded | Rate | Contract from rosters | from release | none | Clause full | modified | none | unknown |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2019-20 | 2253 | 163 | 7.2% | 2242 | 7 | 4 | 266 | 226 | 1758 | 3 |
| 2020-21 | 895 | 54 | 6.0% | 893 | 1 | 1 | 102 | 105 | 687 | 1 |
| 2021-22 | 2356 | 179 | 7.6% | 2318 | 24 | 14 | 265 | 245 | 1834 | 12 |
| 2022-23 | 2340 | 217 | 9.3% | 2331 | 4 | 5 | 301 | 292 | 1741 | 6 |
| 2023-24 | 2315 | 129 | 5.6% | 2260 | 50 | 5 | 349 | 347 | 1616 | 3 |
| 2024-25 | 2274 | 201 | 8.8% | 0 | 2221 | 53 | 383 | 333 | 1462 | 96 |
| 2025-26 | 2372 | 159 | 6.7% | 0 | 2244 | 128 | 371 | 346 | 1093 | 562 |

Roster rows matched to NHL ids: id 12,721, last_birth 506, name 65, name_birth 12,171, unmatched 4,354.

## Held-out seasons (four-way clause)

| Test season | Rows | Traded | Brier model | Brier base | Brier two-cell | Log loss model | Log loss two-cell |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2019-20 | 2253 | 163 | 0.0595 | 0.0671 | 0.0630 | 0.2203 | 0.2356 |
| 2020-21 | 895 | 54 | 0.0509 | 0.0569 | 0.0531 | 0.1882 | 0.2040 |
| 2021-22 | 2356 | 179 | 0.0649 | 0.0702 | 0.0666 | 0.2356 | 0.2474 |
| 2022-23 | 2340 | 217 | 0.0750 | 0.0846 | 0.0805 | 0.2599 | 0.2927 |
| 2023-24 | 2315 | 129 | 0.0470 | 0.0531 | 0.0490 | 0.1783 | 0.1916 |
| 2024-25 | 2274 | 201 | 0.0779 | 0.0808 | 0.0789 | 0.2937 | 0.2921 |
| 2025-26 | 2372 | 159 | 0.0588 | 0.0626 | 0.0603 | 0.2196 | 0.2315 |

## Reliability (pooled held-out, deciles by predicted probability)

| Players | Predicted | Observed | Within ±5 pts |
|---:|---:|---:|---|
| 1481 | 0.5% | 1.1% | yes |
| 1481 | 1.4% | 1.4% | yes |
| 1481 | 2.2% | 2.7% | yes |
| 1481 | 3.0% | 3.6% | yes |
| 1481 | 3.9% | 4.9% | yes |
| 1480 | 5.0% | 6.6% | yes |
| 1480 | 6.7% | 5.5% | yes |
| 1480 | 9.6% | 6.9% | yes |
| 1480 | 14.4% | 13.7% | yes |
| 1480 | 28.5% | 28.0% | yes |

## Top 50 by predicted probability: share traded

| Season | As of | Traded in top 50 |
|---|---|---:|
| 2019-20 | 2020-01-01 | 38.0% |
| 2019-20 | 2020-02-01 | 34.0% |
| 2019-20 | 2020-02-10 | 32.0% |
| 2020-21 | 2021-02-01 | 20.0% |
| 2020-21 | 2021-03-29 | 24.0% |
| 2021-22 | 2022-01-01 | 34.0% |
| 2021-22 | 2022-02-01 | 34.0% |
| 2021-22 | 2022-03-07 | 38.0% |
| 2022-23 | 2023-01-01 | 38.0% |
| 2022-23 | 2023-02-01 | 44.0% |
| 2022-23 | 2023-02-17 | 36.0% |
| 2023-24 | 2024-01-01 | 28.0% |
| 2023-24 | 2024-02-01 | 32.0% |
| 2023-24 | 2024-02-23 | 30.0% |
| 2024-25 | 2025-01-01 | 32.0% |
| 2024-25 | 2025-02-01 | 26.0% |
| 2024-25 | 2025-02-21 | 28.0% |
| 2025-26 | 2026-01-01 | 30.0% |
| 2025-26 | 2026-02-01 | 22.0% |
| 2025-26 | 2026-02-20 | 22.0% |

## Same seasons, #258's three-way clause

None and unknown combined again, everything else equal: isolates the clause change.

Pooled held-out Brier: model 0.0630, base 0.0690, two-cell 0.0656. Deciles off the bar: 0. Would pass the ship rule: yes.

| Players | Predicted | Observed | Within ±5 pts |
|---:|---:|---:|---|
| 1481 | 0.5% | 1.1% | yes |
| 1481 | 1.4% | 1.1% | yes |
| 1481 | 2.2% | 2.7% | yes |
| 1481 | 3.0% | 3.6% | yes |
| 1481 | 3.9% | 5.2% | yes |
| 1480 | 5.0% | 6.4% | yes |
| 1480 | 6.7% | 5.6% | yes |
| 1480 | 9.6% | 6.9% | yes |
| 1480 | 14.5% | 13.7% | yes |
| 1480 | 28.6% | 28.0% | yes |

## Coefficients (fit on every season above, standardized features)

| Feature | Coefficient |
|---|---:|
| `years_left` | -0.743 |
| `log_cap` | +0.663 |
| `goalie` | -0.454 |
| `ppg` | -0.407 |
| `clause_full` | -0.348 |
| `ufa_expiring` | +0.313 |
| `expiring_x_seller` | +0.240 |
| `gap` | -0.220 |
| `cap_unknown` | +0.175 |
| `defense` | -0.158 |
| `clause_modified` | -0.153 |
| `toi_trend` | -0.125 |
| `sv_pct` | -0.089 |
| `days_to_deadline` | +0.076 |
| `contract_unknown` | +0.053 |
| `age` | +0.038 |
| `clause_unknown` | +0.032 |
| `expiring` | -0.011 |
| `seller` | +0.007 |

## If production were refit (not done)

`trade_candidacy_model.json` is unchanged. A refit on these seasons and features would cut tiers at: several ≥ 18.1% (held-out traded 28.0%), some ≥ 7.9% (10.3%).

2026-27 dry run as of 2027-01-01 (games through 2026-11-07), 619 players: frozen model 56 several / 86 some; refit 48 several / 83 some. **71 players change tier.**

| From | To | Players |
|---|---|---:|
| some | none | 30 |
| none | some | 19 |
| several | some | 15 |
| some | several | 7 |

Examples: Artyom Levshunov (several → some); Brad Lambert (several → some); Cole Smith (several → some); Egor Chinakhov (several → some); Evgeni Malkin (several → some); Jamie Oleksiak (several → some); Liam Ohgren (several → some); Michael Rasmussen (several → some); Noah Cates (several → some); Patrick Kane (several → some); Ryan Donato (several → some); Trevor Moore (several → some); Tyler Bertuzzi (several → some); Will Cuylle (several → some); Wyatt Kaiser (several → some).
