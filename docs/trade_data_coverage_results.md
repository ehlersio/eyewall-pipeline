# Trade data coverage (Phase 0)

Generated 2026-11-08 by `python -m scripts.backtest.trade_data_coverage`. Contracts: nhl-contracts release updated 2026-11-08T12:07:59+00:00 (7,033 contracts, 3,294 without a player_id). Scope: `eyewall/trade-likelihood-scope-2026-11-06.md` §6 Phase 0.

Deadline = busiest trade day dated Jan 1 - Apr 30 (inferred from `trades`, not a published date; trades take wiki_trades.py's corrected dates where ESPN's ran early). Label window = Jan 1 → deadline. Usable trade assets: player rows with a `player_id`, not rights-only, trade not `pairing_uncertain`. Population: skaters ≥10 GP, goalies ≥5 GP (season totals). Training bar: population contract coverage ≥90%.

## 0. Label source check: stored trades vs the NHL's own multi-team players

Players the NHL season summary lists with two or more teams (`teamAbbrevs`) vs those with any player row in `trade_assets` that season. Multi-team players also include waiver claims, so this recall is a floor for trades. `trade_assets` holds ESPN's trades plus those `wiki_trades.py` adds from Wikipedia's cited lists (from 2026-11-07; ESPN alone covered 21-82%).

| Season | Multi-team players (NHL) | With a trade row | Recall |
|---|---:|---:|---:|
| 2015-16 | 73 | 61 | 84% |
| 2016-17 | 66 | 50 | 76% |
| 2017-18 | 76 | 59 | 78% |
| 2018-19 | 88 | 75 | 85% |
| 2019-20 | 70 | 61 | 87% |
| 2020-21 | 61 | 50 | 82% |
| 2021-22 | 84 | 63 | 75% |
| 2022-23 | 99 | 84 | 85% |
| 2023-24 | 72 | 53 | 74% |
| 2024-25 | 111 | 92 | 83% |
| 2025-26 | 83 | 67 | 81% |
| 2026-27 | 8 | 6 | 75% |

## 1. Trades and labels

| Season | Deadline (inferred) | Trades | In window | Player assets in window | Rights only | No player_id | Pairing uncertain | Usable |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2015-16 | 2016-02-29 | 102 | 50 | 115 | 0 | 21 | 0 | 94 |
| 2016-17 | 2017-03-01 | 106 | 51 | 94 | 0 | 17 | 0 | 77 |
| 2017-18 | 2018-02-26 | 114 | 48 | 94 | 0 | 17 | 0 | 77 |
| 2018-19 | 2019-02-25 | 139 | 64 | 112 | 0 | 13 | 0 | 99 |
| 2019-20 | 2020-02-24 | 90 | 57 | 103 | 0 | 16 | 0 | 87 |
| 2020-21 | 2021-04-12 | 103 | 52 | 91 | 0 | 12 | 0 | 79 |
| 2021-22 | 2022-03-21 | 142 | 62 | 105 | 0 | 17 | 0 | 88 |
| 2022-23 | 2023-03-03 | 167 | 76 | 145 | 1 | 24 | 0 | 120 |
| 2023-24 | 2024-03-08 | 134 | 58 | 93 | 1 | 20 | 0 | 72 |
| 2024-25 | 2025-03-07 | 148 | 63 | 133 | 0 | 21 | 0 | 112 |
| 2025-26 | 2026-03-06 | 158 | 55 | 93 | 1 | 8 | 1 | 83 |
| 2026-27 | - | 27 | 0 | 0 | 0 | 0 | 0 | 0 |

## 2. Contract coverage: usable traded players (label window)

| Season | Players | Contract found | by id | by name | Cap hit known | From archive (tier E) | Missing, played 2023-24+ | Missing, older |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2015-16 | 94 | 11 (12%) | 6 | 5 | 12% | 100% | 3 | 80 |
| 2016-17 | 77 | 16 (21%) | 3 | 13 | 6% | 25% | 3 | 58 |
| 2017-18 | 77 | 33 (43%) | 15 | 18 | 30% | 48% | 1 | 43 |
| 2018-19 | 99 | 51 (52%) | 18 | 33 | 30% | 37% | 6 | 42 |
| 2019-20 | 87 | 53 (61%) | 22 | 31 | 39% | 40% | 6 | 28 |
| 2020-21 | 79 | 53 (67%) | 32 | 21 | 56% | 36% | 4 | 22 |
| 2021-22 | 88 | 61 (69%) | 37 | 24 | 52% | 36% | 9 | 18 |
| 2022-23 | 120 | 94 (78%) | 54 | 40 | 68% | 43% | 11 | 15 |
| 2023-24 | 72 | 52 (72%) | 35 | 17 | 68% | 48% | 8 | 12 |
| 2024-25 | 112 | 105 (94%) | 72 | 33 | 77% | 38% | 4 | 3 |
| 2025-26 | 83 | 65 (78%) | 54 | 11 | 63% | 28% | 7 | 11 |
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
| 2022-23 | 860 | 769 (89%) | 562 | 207 | 83% | 42% | 51 | 40 |
| 2023-24 | 855 | 805 (94%) | 627 | 178 | 90% | 37% | 50 | 0 |
| 2024-25 | 842 | 820 (97%) | 688 | 132 | 87% | 40% | 22 | 0 |
| 2025-26 | 855 | 806 (94%) | 748 | 58 | 70% | 23% | 49 | 0 |
| 2026-27 | 619 | 616 (100%) | 616 | 0 | 70% | 13% | 3 | 0 |

## 4. Expiring contracts and clauses (usable traded players)

| Season | Expiring that season | UFA/RFA stated | Cap hit ≥ $3M | With a clause row |
|---|---:|---:|---:|---:|
| 2015-16 | 7 | 100% | 5 | 20% |
| 2016-17 | 15 | 27% | 0 | - |
| 2017-18 | 23 | 65% | 4 | 25% |
| 2018-19 | 33 | 58% | 12 | 25% |
| 2019-20 | 33 | 58% | 13 | 15% |
| 2020-21 | 44 | 84% | 13 | 31% |
| 2021-22 | 46 | 78% | 11 | 45% |
| 2022-23 | 63 | 86% | 29 | 21% |
| 2023-24 | 37 | 92% | 23 | 52% |
| 2024-25 | 67 | 76% | 37 | 59% |
| 2025-26 | 41 | 80% | 26 | 54% |
| 2026-27 | 0 | - | 0 | - |

## 5. Age rule vs stated UFA/RFA

Contracts that state UFA or RFA and whose player has a birth date in the bios index: 2,410. The age rule (`nhl_contracts.derive_expiry_type`: 27+ on June 30 of the expiry year = UFA) agrees on 2,264 (94%).

| Stated | Age rule says | Contracts |
|---|---|---:|
| RFA | RFA | 1,260 |
| RFA | UFA | 1 |
| UFA | RFA | 145 |
| UFA | UFA | 1,004 |

## 6. Seasons that pass the training bar

2023-24, 2024-25, 2025-26

## 7. With the deadline rosters (contract-gaps scope §3, Phase 3b)

`deadline_rosters.csv` from nhl-contracts' `run deadline`: 29,817 rows, CapFriendly team pages archived before each 2019-20..2023-24 deadline and as-of date (tier E, captured 2019-11-15 to 2024-03-07). Rows matched to NHL ids: id 12,721, last_birth 514, name 67, name_birth 12,698, unmatched 3,817. A player with no contract in the release but a roster row that season counts as covered.

| Season | Players | Covered before | With rosters | Filled by rosters | Cap hit known | Traded players covered before → with rosters | Passes the bar |
|---|---:|---:|---:|---:|---:|---:|---|
| 2015-16 | 809 | 33% | 33% | 0 | 33% → 33% | 12% → 12% | no |
| 2016-17 | 808 | 48% | 48% | 0 | 39% → 39% | 21% → 21% | no |
| 2017-18 | 838 | 60% | 60% | 0 | 47% → 47% | 43% → 43% | no |
| 2018-19 | 842 | 69% | 69% | 0 | 53% → 53% | 52% → 52% | no |
| 2019-20 | 808 | 76% | 100% | 195 | 61% → 100% | 61% → 100% | yes |
| 2020-21 | 807 | 79% | 100% | 164 | 70% → 100% | 67% → 96% | yes |
| 2021-22 | 894 | 84% | 99% | 139 | 75% → 99% | 69% → 95% | yes |
| 2022-23 | 860 | 89% | 100% | 87 | 83% → 99% | 78% → 100% | yes |
| 2023-24 | 855 | 94% | 100% | 47 | 90% → 100% | 72% → 89% | yes |
| 2024-25 | 842 | 97% | 97% | 0 | 87% → 87% | 94% → 94% | yes |
| 2025-26 | 855 | 94% | 94% | 0 | 70% → 70% | 78% → 78% | yes |
| 2026-27 | 619 | 100% | 100% | 0 | 70% → 70% | - → - | no |

Seasons at or above the bar with the rosters: 2019-20, 2020-21, 2021-22, 2022-23, 2023-24, 2024-25, 2025-26.
