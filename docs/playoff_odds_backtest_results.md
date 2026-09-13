# Playoff odds backtest

`backtest_playoff_odds.py`, 5,000 simulations per snapshot. Seasons 2023-24 through 2025-26;
snapshots preseason (day before opening night, all teams at 0 points), Nov 15, Jan 1, Mar 1.
Standings from the NHL's standings-by-date endpoint, Elo ratings replayed from game_log to each date,
remaining games from game_log. Scored against the 16 teams that played in that season's playoffs.
Lower Brier / log loss is better. Baseline: "currently holds a playoff spot" (1 or 0; 0.5 preseason).

**Chosen rating uncertainty:** 50 Elo points preseason, shrinking over n0 = 40 games (`RATING_SD_PRESEASON = 50.0`, `RATING_SD_HALF_GAMES = 40.0`).

**Overall** (384 team-snapshots): Brier **0.161** with uncertainty vs **0.165** with fixed ratings vs **0.224** baseline; log loss **0.471** vs **0.483** fixed.

| Season | Snapshot | Games left | Rating sd | Brier (uncertainty) | Brier (fixed) | Brier (baseline) |
|---|---|---|---|---|---|---|
| 20232024 | preseason | 1312 | 50.0 | 0.252 | 0.249 | 0.250 |
| 20232024 | 2023-11-15 | 1071 | 42.6 | 0.186 | 0.188 | 0.250 |
| 20232024 | 2024-01-01 | 739 | 36.3 | 0.103 | 0.101 | 0.125 |
| 20232024 | 2024-03-01 | 357 | 31.7 | 0.084 | 0.086 | 0.125 |
| 20242025 | preseason | 1312 | 50.0 | 0.213 | 0.218 | 0.250 |
| 20242025 | 2024-11-15 | 1042 | 41.9 | 0.165 | 0.163 | 0.312 |
| 20242025 | 2025-01-01 | 711 | 35.9 | 0.089 | 0.090 | 0.125 |
| 20242025 | 2025-03-01 | 353 | 31.6 | 0.080 | 0.082 | 0.188 |
| 20252026 | preseason | 1312 | 50.0 | 0.257 | 0.283 | 0.250 |
| 20252026 | 2025-11-15 | 1017 | 41.4 | 0.217 | 0.226 | 0.375 |
| 20252026 | 2026-01-01 | 673 | 35.4 | 0.167 | 0.169 | 0.250 |
| 20252026 | 2026-03-01 | 361 | 31.7 | 0.124 | 0.127 | 0.188 |

## Calibration

| Predicted | Teams | Avg predicted | Actually made it | (fixed ratings: avg predicted / made it) |
|---|---|---|---|---|
| 0-10% | 47 | 3% | 2% | 2% / 3% |
| 10-20% | 29 | 15% | 17% | 15% / 36% |
| 20-30% | 36 | 25% | 36% | 25% / 23% |
| 30-40% | 32 | 35% | 31% | 34% / 31% |
| 40-50% | 61 | 47% | 52% | 46% / 50% |
| 50-60% | 43 | 54% | 40% | 53% / 47% |
| 60-70% | 26 | 65% | 62% | 66% / 60% |
| 70-80% | 25 | 73% | 72% | 75% / 63% |
| 80-90% | 29 | 86% | 83% | 85% / 79% |
| 90-100% | 56 | 97% | 100% | 97% / 96% |

## Tuning grid (2,000 simulations per snapshot, same seed)

| sd0 | n0 | Brier | Log loss |
|---|---|---|---|
| 0 | 20 | 0.1652 | 0.4829 |
| 25 | 20 | 0.1637 | 0.4780 |
| 25 | 40 | 0.1635 | 0.4767 |
| 25 | 80 | 0.1635 | 0.4767 |
| 50 | 20 | 0.1621 | 0.4724 |
| 50 | 40 | 0.1617 | 0.4718 |
| 50 | 80 | 0.1620 | 0.4732 |
| 75 | 20 | 0.1617 | 0.4726 |
| 75 | 40 | 0.1622 | 0.4750 |
| 75 | 80 | 0.1625 | 0.4769 |
| 100 | 20 | 0.1626 | 0.4769 |
| 100 | 40 | 0.1633 | 0.4803 |
| 100 | 80 | 0.1640 | 0.4836 |

Chosen by lowest Brier. With 3 seasons x 4 snapshots x 32 teams, adjacent settings differ by
little -- the point is a reasonable amount of uncertainty, not a precise optimum.

Overtime/shootout rate: 22.1% of games (pooled 2023-24..2025-26 game_log).
Caveats: neutral-site games are treated as home games; head-to-head and later NHL tiebreakers
aren't modeled.
