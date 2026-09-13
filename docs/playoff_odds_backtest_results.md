# Playoff odds backtest

`backtest_playoff_odds.py`, 5,000 simulations per snapshot. Seasons 2023-24 through 2025-26;
snapshots preseason (day before opening night, all teams at 0 points), Nov 15, Jan 1, Mar 1.
Standings from the NHL's standings-by-date endpoint, Elo ratings replayed from game_log to each date,
remaining games from game_log. Scored against the 16 teams that played in that season's playoffs.
Lower Brier / log loss is better. Baseline: "currently holds a playoff spot" (1 or 0; 0.5 preseason).

**Chosen rating uncertainty:** 50 Elo points preseason, shrinking over n0 = 40 games (`RATING_SD_PRESEASON = 50.0`, `RATING_SD_HALF_GAMES = 40.0`).

**Overall** (384 team-snapshots): Brier **0.161** with uncertainty vs **0.165** with fixed ratings vs **0.224** baseline; log loss **0.471** vs **0.484** fixed.

| Season | Snapshot | Games left | Rating sd | Brier (uncertainty) | Brier (fixed) | Brier (baseline) |
|---|---|---|---|---|---|---|
| 20232024 | preseason | 1312 | 50.0 | 0.251 | 0.249 | 0.250 |
| 20232024 | 2023-11-15 | 1071 | 42.6 | 0.188 | 0.192 | 0.250 |
| 20232024 | 2024-01-01 | 739 | 36.3 | 0.103 | 0.102 | 0.125 |
| 20232024 | 2024-03-01 | 357 | 31.7 | 0.086 | 0.089 | 0.125 |
| 20242025 | preseason | 1312 | 50.0 | 0.211 | 0.215 | 0.250 |
| 20242025 | 2024-11-15 | 1042 | 41.9 | 0.163 | 0.159 | 0.312 |
| 20242025 | 2025-01-01 | 711 | 35.9 | 0.083 | 0.084 | 0.125 |
| 20242025 | 2025-03-01 | 353 | 31.6 | 0.077 | 0.079 | 0.188 |
| 20252026 | preseason | 1312 | 50.0 | 0.258 | 0.285 | 0.250 |
| 20252026 | 2025-11-15 | 1017 | 41.4 | 0.217 | 0.226 | 0.375 |
| 20252026 | 2026-01-01 | 673 | 35.4 | 0.168 | 0.169 | 0.250 |
| 20252026 | 2026-03-01 | 361 | 31.7 | 0.125 | 0.127 | 0.188 |

## Calibration

| Predicted | Teams | Avg predicted | Actually made it | (fixed ratings: avg predicted / made it) |
|---|---|---|---|---|
| 0-10% | 49 | 3% | 2% | 3% / 3% |
| 10-20% | 28 | 15% | 21% | 15% / 33% |
| 20-30% | 36 | 25% | 33% | 25% / 30% |
| 30-40% | 32 | 36% | 31% | 34% / 38% |
| 40-50% | 55 | 47% | 49% | 46% / 40% |
| 50-60% | 49 | 54% | 45% | 53% / 51% |
| 60-70% | 28 | 66% | 64% | 66% / 62% |
| 70-80% | 22 | 73% | 73% | 76% / 74% |
| 80-90% | 29 | 86% | 83% | 85% / 72% |
| 90-100% | 56 | 97% | 100% | 97% / 96% |

## Tuning grid (2,000 simulations per snapshot, same seed)

| sd0 | n0 | Brier | Log loss |
|---|---|---|---|
| 0 | 20 | 0.1646 | 0.4833 |
| 25 | 20 | 0.1631 | 0.4783 |
| 25 | 40 | 0.1631 | 0.4774 |
| 25 | 80 | 0.1630 | 0.4771 |
| 50 | 20 | 0.1616 | 0.4725 |
| 50 | 40 | 0.1615 | 0.4724 |
| 50 | 80 | 0.1617 | 0.4730 |
| 75 | 20 | 0.1615 | 0.4727 |
| 75 | 40 | 0.1618 | 0.4746 |
| 75 | 80 | 0.1622 | 0.4764 |
| 100 | 20 | 0.1622 | 0.4763 |
| 100 | 40 | 0.1630 | 0.4798 |
| 100 | 80 | 0.1638 | 0.4831 |

Chosen by lowest Brier. With 3 seasons x 4 snapshots x 32 teams, adjacent settings differ by
little -- the point is a reasonable amount of uncertainty, not a precise optimum.

Caveats: game_log's period_end is only populated for 2025-26 (2023-24/2024-25 games all read as
regulation), so replayed ratings for those seasons skip Elo's overtime damping; neutral-site games
are treated as home games.
