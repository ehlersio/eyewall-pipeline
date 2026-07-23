# Prediction Model Backtest — Results

**Type:** Execution report, per `BACKTEST_EXECUTION.md`. This is the last gate before the preseason-only vs. permanent/blended decision. Runner: `eyewall-pipeline/backtest_predictions.py` (reusable, committed — not throwaway). Read-only against production: `player_seasons`/`player_score_state_dist`/`zone_starts`/`players` were never written.

**3,380 games scored** across 4 seasons, 18.4 minutes total wall-clock (see §5).

---

## Data-quality finding (discovered running this, not previously known)

**`game_log` has zero rows for season 2022-23** (confirmed directly: `count=0` for both `game_type=2` and `3`), even though `shot_events`/`shift_events` were backfilled for that season per `RAPM_SPEC.md`. Consequence:

- 2022-23 **cannot be a standalone backtest season** — no dates to build cutoffs from, no games to score against. It's excluded from the degraded-pool results below (only 2023-24 could run standalone).
- It still works fine as a **prior pool contributor** to 2023-24's and 2024-25's RAPM regressions (shot/shift team labels don't depend on `game_log`), but every regression that pools it has 2022-23's shots contributing with no `game_home` entry — `score_state_at()`'s home/away determination silently defaults those shots to the "away" branch of the score-differential calculation. This is a minor, second-order imprecision (affects only the score-state normalization weight, not the core shooting/defending team assignment), not a correctness blocker, but worth fixing (backfilling `game_log` for 2022-23) if RAPM's pool ever leans on that season more heavily.

---

## 1–2. Head-to-head metrics

| Segment | n | Model | Brier ↓ | Log loss ↓ | Accuracy ↑ |
|---|---|---|---|---|---|
| **Full-pool, all** | 2,298 | RAPM/Log5 | 0.358 | **1.157** | 0.510 |
| | | Scorecard | **0.327** | 2.466 | **0.547** |
| Full-pool, early season (<10 GP) | 92 | RAPM/Log5 | 0.363 | **1.154** | 0.511 |
| | | Scorecard | **0.307** | 1.632 | **0.533** |
| Full-pool, mid/late season | 2,206 | RAPM/Log5 | 0.358 | **1.157** | 0.510 |
| | | Scorecard | **0.328** | 2.500 | **0.547** |
| **Degraded-pool (2023-24 only)** | 1,082 | RAPM/Log5 | 0.408 | 1.399 | **0.465** (below chance) |
| | | Scorecard | **0.304** | **2.284** | **0.575** |

(2022-23 excluded from degraded-pool — see data-quality finding above.)

Brier and log loss disagree on direction here — that's diagnostic, not a bug, and it's explained by the calibration tables below.

## 3. Calibration

**Standings scorecard — real discriminative power, but badly overconfident at the extremes.** Predicted-probability buckets trend in the right direction (low bucket → low actual win rate, high bucket → high actual win rate), but the extremes are wildly miscalibrated:

| Predicted range | n | Predicted avg | Actual win rate |
|---|---|---|---|
| 0.0–0.1 | 454 | 0.020 | **0.482** |
| 0.7–0.8 | 209 | 0.756 | 0.608 |
| 0.8–0.9 | 360 | 0.857 | 0.614 |
| 0.9–1.0 | 169 | 0.979 | **0.663** |

When the scorecard predicts a ~98% win, the real win rate is 66%. When it predicts a ~2% win, the real win rate is 48% — a coin flip. This is exactly what tanks its log loss despite a good Brier score and the best raw accuracy: confident-wrong predictions are cheap in Brier, expensive in log loss.

**RAPM/Log5 — calibration curve is nearly flat.** Actual win rate hovers at 0.52–0.57 across *every* predicted bucket from 0.0–0.1 to 0.9–1.0 (full detail in `backtest_summary.json`). A model with real skill should show actual rate tracking predicted probability; this one barely does. Its accuracy (51%) and this flat curve tell the same story: as implemented, it has very little game-to-game discriminative power.

## 4. Does RAPM/Log5 outperform the standings scorecard, and where?

**No — not in any segment measured, and it's worse than chance in the degraded-pool case.** This is the direct answer the whole investigation was building toward, and it's a real result, not a modeling artifact of this run: the scorecard wins on Brier score and accuracy everywhere, and wins on log loss everywhere too (the metric RAPM "wins" on in the headline table is misleading — RAPM's better log loss reflects that its predictions are timidly clustered near 0.5, which is cheap on log loss precisely because it's rarely confidently wrong, not because it's actually more accurate). The early-season segment (n=92, small — treat as directional, not conclusive) doesn't show RAPM closing the gap either, so the original motivating hypothesis (RAPM should help most exactly when standings data is thinnest) isn't supported by this run.

**A separate, valuable finding regardless of the RAPM question:** the current production scorecard is meaningfully overconfident at the extremes. That's fixable independent of anything else here (e.g., a simple dampening/temperature-scaling pass on its output) and worth a look on its own.

**Plausible reasons RAPM underperforms here** (diagnosis, not proven — would need a follow-up investigation to confirm):
- The Ridge `alpha=2500` and `SHOTS_PER_60=25.0` scaling constants were tuned for a *season-long player display* use case (matching Evolving Hockey's public RAPM), never for a per-game win-probability pipeline — this backtest is the first real test of repurposing them that way, and nothing here validated that the resulting Impact/GOALS_PER_WIN/Log5 chain produces a *meaningfully-scaled* per-game signal rather than one clustered too close to 0.5 to discriminate.
- Neither model has a home-ice term (a deliberate scope simplification, kept symmetric across both models) — if home ice is a real chunk of true win variance the scorecard indirectly captures through a correlated input, RAPM may be missing it entirely.
- Season-to-date average TOI (not that night's actual lineup) can't reflect a star player being out — a known, already-flagged gap between "buildable now" and "ideal."

## 5. Runner performance

**18.4 minutes total (1,106s), all 4 seasons** — beat the ~20 min estimate, which only covered the 2 full-pool seasons. Season-data caching worked as designed: the first cutoff of each new season paid the full fetch cost (206s for 2024-25's first cutoff, loading all 3 pool seasons cold), every subsequent cutoff in that season cost 25-55s (cache hit), and 2023-24's cutoffs were cheapest of all (13-35s) since both its pool seasons were already cached from the full-pool runs.

---

## Recommendation

**Do not replace the standings scorecard with this implementation of RAPM/Log5.** The theoretical case (§4 of the methodology findings) was sound, and the point-in-time reconstruction (this report's whole purpose) is now honestly validated — but the specific numeric pipeline, as scoped and built, does not beat the existing model on any metric that matters, and underperforms notably with a thinner pool. This is directly relevant to PWHL: since PWHL will likely never have a full 3-season pool for years, the degraded-pool result here (accuracy below chance) is a real caution against expecting a PWHL RAPM model to be usable soon after its data becomes available in October, even once the ToS/data questions are resolved.

If RAPM-based prediction is still wanted, the next step is a **tuning/diagnosis pass** on the Impact→win-probability conversion specifically (not a re-run of this backtest as-is) — investigate the actual spread of computed Impact values across real matchups, whether `GOALS_PER_WIN`/the Log5 clamp bounds are suppressing real signal, and whether a home-ice term recovers some of the gap. That's new work, not covered by this report.

## Explicitly not decided here
- Whether to pursue the tuning/diagnosis follow-up above
- Whether to fix the 2022-23 `game_log` backfill gap
- Any change to the production standings scorecard's calibration (flagged as a separate opportunity, not scoped or built here)
