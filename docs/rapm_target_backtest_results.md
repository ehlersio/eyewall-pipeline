# RAPM target backtest: distance bands vs nhl_xg_model.py

Written by `scripts/backtest/backtest_rapm_target.py` (step 2 of `rapm-xg-scope-2026-11-20.md`). Single-season regular-season RAPM, production fit (5v5, ridge alpha 2500, score-state and zone-start weights). Players with 300+ 5v5 minutes in both seasons of a pair.

## Repeatability and prediction

Repeatability: correlation of RAPM in season N with RAPM in N+1. Prediction: correlation of RAPM in N with on-ice 5v5 goal differential per 60 in N+1. Higher is better for both.

| Target | 2023-24 → 2024-25 | 2024-25 → 2025-26 | Mean |
|---|---|---|---|
| bands | 0.541 / 0.331 (n=570) | 0.512 / 0.267 (n=561) | 0.526 / 0.299 |
| bands+score | 0.540 / 0.330 (n=570) | 0.513 / 0.266 (n=561) | 0.527 / 0.298 |
| bands-fenwick | 0.483 / 0.301 (n=570) | 0.466 / 0.265 (n=561) | 0.474 / 0.283 |
| model | 0.465 / 0.325 (n=570) | 0.428 / 0.285 (n=561) | 0.447 / 0.305 |
| on-ice GD/60 in N (context) | — / 0.319 (n=570) | — / 0.332 (n=561) | — / 0.326 |

Each cell: repeatability / prediction.

**Ship rule** (model ≥ bands on both means): **not met**.

## Per-60 scale

Production multiplies each coefficient by a fixed 25. Measured: RAPM rows per 60 minutes of 5v5 play in the same games.

| Season | Target | Rows | 5v5 hours | Measured scale | RAPM spread (SD) at 25 | at measured |
|---|---|---|---|---|---|---|
| 2023-24 | bands | 125,301 | 1,023 | 122.4 | 0.028 | 0.135 |
| 2023-24 | bands+score | 125,301 | 1,023 | 122.4 | 0.028 | 0.136 |
| 2023-24 | bands-fenwick | 89,245 | 1,023 | 87.2 | 0.023 | 0.079 |
| 2023-24 | model | 89,245 | 1,023 | 87.2 | 0.030 | 0.105 |
| 2024-25 | bands | 125,503 | 1,066 | 117.7 | 0.027 | 0.128 |
| 2024-25 | bands+score | 125,503 | 1,066 | 117.7 | 0.027 | 0.128 |
| 2024-25 | bands-fenwick | 89,162 | 1,066 | 83.6 | 0.022 | 0.074 |
| 2024-25 | model | 89,162 | 1,066 | 83.6 | 0.029 | 0.098 |
| 2025-26 | bands | 117,654 | 1,012 | 116.2 | 0.027 | 0.127 |
| 2025-26 | bands+score | 117,654 | 1,012 | 116.2 | 0.027 | 0.127 |
| 2025-26 | bands-fenwick | 85,047 | 1,012 | 84.0 | 0.022 | 0.075 |
| 2025-26 | model | 85,047 | 1,012 | 84.0 | 0.030 | 0.101 |
