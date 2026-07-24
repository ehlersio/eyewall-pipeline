# Isotonic Recalibration Recheck — Results

**Type:** Execution report, per `ISOTONIC_RECALIBRATION_CADENCE.md`'s prerequisite step (not the quarterly cadence itself — that starts once 2026-27 data accumulates). This is a one-time recheck of the calibration curve fit merged in PR #48, prompted by a specific, already-known data-quality problem, not a scheduled review.

---

## Why this ran

The original isotonic fit (pipeline PR #48, merged 2026-07-23 14:55) predates the pp_goals/pp_opps `situationCode`-misindexing fix (`PP_GOALS_FULL_FIX.md`, pipeline PR #53, merged 2026-07-23 22:54) by about 8 hours. `backtest_predictions.py`'s `standings_inputs_asof()` sums `game_log.pp_goals`/`pp_opps` directly into `pp_pct`, one of the scorecard's five inputs — and that column had a near-universal misclassification bug all season (home teams' `pp_goals` forced to ~0.0/game in nearly every game, a directional bias, not noise). The original fit and its reported Brier/log-loss improvement were both computed on backtest data carrying this bug, on both the 2024-25 training set and the 2025-26 holdout set.

The PP fix's own consumer audit checked whether `backtest_predictions.py` *mis-parses* `situationCode` (it doesn't — clean) but not whether it *consumes* the `game_log` columns the bug corrupted. That's the gap this recheck closes.

## What ran

1. Re-ran `backtest_predictions.py` unmodified — it reads `game_log` live at runtime with no local caching, so it picked up the corrected `pp_goals`/`pp_opps` (backfilled across 2023-24/2024-25/2025-26 by PR #53) automatically. 3,380 games scored, same scope as the original run (full-pool 2024-25/2025-26 + degraded-pool 2022-23/2023-24).
2. Re-ran `fit_scorecard_calibration.py` unmodified against the new `backtest_results.json` — same fit/holdout split (fit: 2024-25, holdout: 2025-26), same train/holdout discipline as the original.

## Results

### Holdout (2025-26, n=1,132)

| | Brier ↓ | Log loss ↓ | Accuracy |
|---|---|---|---|
| Raw scorecard (original, buggy pp_pct) | 0.333 | 2.437 | 52.8% |
| Raw scorecard (corrected pp_pct) | 0.326 | 2.275 | 54.2% |
| Calibrated (original fit) | 0.256 | 0.706 | 52.4% |
| Calibrated (refit on corrected data) | 0.252 | 0.697 | 52.3% |

Both raw and calibrated metrics improve slightly with corrected data — expected, since `pp_pct` is now a real signal instead of a near-constant home-team-penalizing artifact. The calibrated-vs-calibrated delta is small in aggregate; the more important comparison is the curve shape itself (below), since that's what determines the calibrated output for any individual raw score in production.

### Curve comparison — materially different

Breakpoint count changed from 13 to 15. Comparing calibrated output at sample raw scores (interpolated):

| raw score | original curve | refit curve | delta (pp) |
|---|---|---|---|
| 0.0 | 43% | 41% | −2 |
| 0.1 | 53% | 50% | −3 |
| 0.4 | 56% | 52% | −4 |
| 0.5 | 56% | 60% | +4 |
| 0.6–0.7 | 63% | 60% | −3 |
| 0.9–1.0 | 70% | 69% | −1 |

Shifts of 2–4 percentage points across most of the input range — enough that a fan-facing win probability visibly differs between the two curves for the same underlying game. This clears the "clear, validated improvement" / "materially different" bar from `ISOTONIC_RECALIBRATION_CADENCE.md` for redeployment, on top of the more fundamental reason: the original curve was fit on an input distribution that no longer reflects how the scorecard's raw score is actually computed now that `pp_pct` is correct. Deploying the corrected-data curve isn't optional-but-nice — it's required for the calibration to match its own input going forward.

## Deployment

`scorecard_calibration.json` regenerated (15 breakpoints) and the `ISOTONIC_X`/`ISOTONIC_Y` arrays in `eyewall-poller/src/nhl.js` (`isotonicCalibrate()`) updated to match. One existing test (`nhl-routes.test.js`, the "CAR wins every scorecard factor" case) had a hardcoded expectation (`carWinPct: 70`) tied to the old curve's top breakpoint (0.70238) — updated to the new top breakpoint's value (0.68889 → 69). Full poller suite (194 tests) passes.

## Not done here

- The quarterly recalibration cadence itself — this was the one-time prerequisite recheck, not the first scheduled review. That starts once real 2026-27 (predicted, actual) pairs accumulate in volume, per `ISOTONIC_RECALIBRATION_CADENCE.md`.
- Re-validating the continuity-dampened preseason fallback (Part B) — unaffected, since it doesn't consume `pp_goals`/`pp_opps` (its `pp_pct` comes from `team_seasons`, not `game_log`).
