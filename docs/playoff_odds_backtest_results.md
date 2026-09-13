# Playoff odds backtest

`backtest_playoff_odds.py`, 5,000 simulations per snapshot. Standings from the NHL's standings-by-date
endpoint, Elo ratings replayed from game_log to each date, remaining games from game_log.
Scored against the 16 teams that played in that season's playoffs. Lower Brier / log loss is better;
the baseline is "currently holds a playoff spot" (probability 1 or 0).

| Season | Snapshot | Games left | Brier (model) | Brier (baseline) |
|---|---|---|---|---|
| 20232024 | 2023-11-15 | 1071 | 0.192 | 0.250 |
| 20232024 | 2024-01-01 | 739 | 0.102 | 0.125 |
| 20232024 | 2024-03-01 | 357 | 0.089 | 0.125 |
| 20242025 | 2024-11-15 | 1042 | 0.159 | 0.312 |
| 20242025 | 2025-01-01 | 711 | 0.084 | 0.125 |
| 20242025 | 2025-03-01 | 353 | 0.079 | 0.188 |
| 20252026 | 2025-11-15 | 1017 | 0.226 | 0.375 |
| 20252026 | 2026-01-01 | 673 | 0.169 | 0.250 |
| 20252026 | 2026-03-01 | 361 | 0.127 | 0.188 |

**Overall** (288 team-snapshots): Brier model **0.136** vs baseline **0.215**; log loss model **0.413**.

## Calibration

| Predicted | Teams | Avg predicted | Actually made it |
|---|---|---|---|
| 0-10% | 56 | 3% | 4% |
| 10-20% | 31 | 15% | 29% |
| 20-30% | 22 | 25% | 27% |
| 30-40% | 20 | 34% | 30% |
| 40-50% | 20 | 45% | 45% |
| 50-60% | 22 | 56% | 45% |
| 60-70% | 17 | 66% | 65% |
| 70-80% | 8 | 74% | 75% |
| 80-90% | 18 | 85% | 72% |
| 90-100% | 74 | 97% | 97% |

Caveats: game_log's period_end is only populated for 2025-26 (2023-24/2024-25 games all read as
regulation), so replayed ratings for those seasons skip Elo's overtime damping; neutral-site games
are treated as home games. Snapshot dates falling before a season's first game are skipped by construction
(none do for these seasons).
