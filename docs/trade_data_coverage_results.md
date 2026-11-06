# Trade data coverage (Phase 0)

Generated 2026-11-06 by `python -m scripts.backtest.trade_data_coverage`. Contracts: nhl-contracts release updated 2026-11-06T22:08:07+00:00 (7,033 contracts, 3,300 without a player_id). Scope: `eyewall/trade-likelihood-scope-2026-11-06.md` §6 Phase 0.

Deadline = busiest trade day dated Jan 1 - Apr 30 (inferred from `trades`, not a published date; see §0 for ESPN's date shift). Label window = Jan 1 → deadline. Usable trade assets: player rows with a `player_id`, not rights-only, trade not `pairing_uncertain`. Population: skaters ≥10 GP, goalies ≥5 GP (season totals). Training bar: population contract coverage ≥90%.

## 0. Label source check: ESPN trades vs the NHL's own multi-team players

Players the NHL season summary lists with two or more teams (`teamAbbrevs`) vs those with any player row in `trade_assets` that season. Multi-team players also include waiver claims, so this recall is a floor for trades.

| Season | Multi-team players (NHL) | With an ESPN trade row | Recall |
|---|---:|---:|---:|
| 2015-16 | 73 | 60 | 82% |
| 2016-17 | 66 | 49 | 74% |
| 2017-18 | 76 | 16 | 21% |
| 2018-19 | 88 | 54 | 61% |
| 2019-20 | 70 | 31 | 44% |
| 2020-21 | 61 | 35 | 57% |
| 2021-22 | 84 | 50 | 60% |
| 2022-23 | 99 | 57 | 58% |
| 2023-24 | 72 | 40 | 56% |
| 2024-25 | 111 | 78 | 70% |
| 2025-26 | 83 | 43 | 52% |
| 2026-27 | 8 | 6 | 75% |

## 1. Trades and labels

| Season | Deadline (inferred) | Trades | In window | Player assets in window | Rights only | No player_id | Pairing uncertain | Usable |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2015-16 | 2016-02-29 | 84 | 48 | 108 | 0 | 19 | 0 | 89 |
| 2016-17 | 2017-03-01 | 75 | 45 | 86 | 0 | 14 | 0 | 72 |
| 2017-18 | 2018-02-26 | 43 | 12 | 28 | 0 | 7 | 0 | 21 |
| 2018-19 | 2019-02-25 | 78 | 41 | 76 | 1 | 12 | 0 | 63 |
| 2019-20 | 2020-02-24 | 50 | 26 | 46 | 0 | 7 | 0 | 39 |
| 2020-21 | 2021-04-07 | 38 | 32 | 58 | 0 | 9 | 0 | 49 |
| 2021-22 | 2022-03-16 | 90 | 52 | 89 | 0 | 19 | 0 | 70 |
| 2022-23 | 2023-02-26 | 113 | 45 | 87 | 2 | 12 | 0 | 73 |
| 2023-24 | 2024-03-03 | 89 | 40 | 58 | 1 | 11 | 0 | 46 |
| 2024-25 | 2025-03-07 | 108 | 51 | 105 | 0 | 17 | 0 | 88 |
| 2025-26 | 2026-03-06 | 98 | 39 | 60 | 1 | 5 | 1 | 53 |
| 2026-27 | - | 13 | 0 | 0 | 0 | 0 | 0 | 0 |

## 2. Contract coverage: usable traded players (label window)

| Season | Players | Contract found | by id | by name | Cap hit known | From archive (tier E) | Missing, played 2023-24+ | Missing, older |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2015-16 | 89 | 11 (12%) | 6 | 5 | 12% | 100% | 3 | 75 |
| 2016-17 | 72 | 16 (22%) | 3 | 13 | 7% | 25% | 3 | 53 |
| 2017-18 | 21 | 8 (38%) | 4 | 4 | 29% | 50% | 0 | 13 |
| 2018-19 | 63 | 37 (59%) | 10 | 27 | 32% | 30% | 2 | 24 |
| 2019-20 | 39 | 22 (56%) | 10 | 12 | 38% | 36% | 2 | 15 |
| 2020-21 | 49 | 37 (76%) | 21 | 16 | 59% | 38% | 1 | 11 |
| 2021-22 | 70 | 47 (67%) | 28 | 19 | 51% | 40% | 6 | 17 |
| 2022-23 | 73 | 61 (84%) | 30 | 31 | 70% | 38% | 5 | 7 |
| 2023-24 | 46 | 34 (74%) | 20 | 14 | 67% | 44% | 5 | 7 |
| 2024-25 | 88 | 82 (93%) | 53 | 29 | 78% | 46% | 4 | 2 |
| 2025-26 | 53 | 36 (68%) | 28 | 8 | 55% | 28% | 7 | 10 |
| 2026-27 | 0 | 0 (-) | 0 | 0 | - | - | 0 | 0 |

## 3. Contract coverage: season population

| Season | Players | Contract found | by id | by name | Cap hit known | From archive (tier E) | Missing, played 2023-24+ | Missing, older |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2015-16 | 809 | 266 (33%) | 181 | 85 | 33% | 100% | 41 | 502 |
| 2016-17 | 808 | 387 (48%) | 223 | 164 | 39% | 73% | 42 | 379 |
| 2017-18 | 838 | 505 (60%) | 281 | 224 | 47% | 58% | 37 | 296 |
| 2018-19 | 842 | 582 (69%) | 342 | 240 | 53% | 52% | 47 | 213 |
| 2019-20 | 808 | 611 (76%) | 386 | 225 | 61% | 49% | 37 | 160 |
| 2020-21 | 807 | 641 (79%) | 435 | 206 | 70% | 49% | 48 | 118 |
| 2021-22 | 894 | 750 (84%) | 533 | 217 | 75% | 46% | 58 | 86 |
| 2022-23 | 860 | 769 (89%) | 561 | 208 | 83% | 42% | 51 | 40 |
| 2023-24 | 855 | 805 (94%) | 626 | 179 | 90% | 37% | 50 | 0 |
| 2024-25 | 842 | 820 (97%) | 687 | 133 | 87% | 40% | 22 | 0 |
| 2025-26 | 855 | 806 (94%) | 748 | 58 | 70% | 23% | 49 | 0 |
| 2026-27 | 605 | 602 (100%) | 602 | 0 | 70% | 13% | 3 | 0 |

## 4. Expiring contracts and clauses (usable traded players)

| Season | Expiring that season | UFA/RFA stated | Cap hit ≥ $3M | With a clause row |
|---|---:|---:|---:|---:|
| 2015-16 | 7 | 100% | 5 | 20% |
| 2016-17 | 15 | 27% | 0 | - |
| 2017-18 | 8 | 75% | 1 | 0% |
| 2018-19 | 22 | 50% | 9 | 22% |
| 2019-20 | 15 | 60% | 6 | 0% |
| 2020-21 | 28 | 79% | 8 | 38% |
| 2021-22 | 35 | 80% | 9 | 44% |
| 2022-23 | 42 | 81% | 19 | 5% |
| 2023-24 | 26 | 88% | 17 | 47% |
| 2024-25 | 52 | 75% | 29 | 55% |
| 2025-26 | 22 | 73% | 13 | 62% |
| 2026-27 | 0 | - | 0 | - |

## 5. Age rule vs stated UFA/RFA

Contracts that state UFA or RFA and whose player has a birth date in the bios index: 2,405. The age rule (`nhl_contracts.derive_expiry_type`: 27+ on June 30 of the expiry year = UFA) agrees on 2,261 (94%).

| Stated | Age rule says | Contracts |
|---|---|---:|
| RFA | RFA | 1,259 |
| RFA | UFA | 1 |
| UFA | RFA | 143 |
| UFA | UFA | 1,002 |

## 6. Seasons that pass the training bar

2023-24, 2024-25, 2025-26
