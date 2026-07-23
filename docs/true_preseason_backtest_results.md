# True-Preseason Backtest — Results

**Type:** Execution report, per `TRUE_PRESEASON_BACKTEST_EXTENSION.md`. Runner: `eyewall-pipeline/backtest_preseason.py` (reusable, builds on `backtest_predictions.py`). Read-only — no writes anywhere.

**Scope actually run:** 2024-25 and 2025-26 only. **2023-24 was excluded** — confirmed its prior season (2022-23) has zero rows in both `game_log` and `player_seasons`, so none of the three variants (all of which need a prior season's data) could be built for it. 105 opening-week games scored total (33 from 2024-25, 72 from 2025-26), 15-day post-opener window, each team's own first 2 games used only to build its roster/continuity proxy (never scored).

---

## Headline numbers

| Variant | n | Brier ↓ | Log loss ↓ | Accuracy |
|---|---|---|---|---|
| Raw prior-season fallback | 105 | 0.309 | 2.067 | 56.2% |
| **Continuity-adjusted fallback** | 105 | **0.270** | **0.754** | 56.2% |
| RAPM/Log5 | 105 | 0.403 | 1.903 | **47.6%** (below chance) |

Average roster continuity across all team-instances: **73%** of prior-season TOI retained by opening week — a plausible real-world number, not an artifact.

## 1. Does raw fallback actually perform worse in low-continuity cases?

**No — the data doesn't support the original hypothesis, and if anything points the other way.** Splitting the 105 games at the median continuity value (0.734):

| Group | n | Raw Brier | Raw accuracy | Continuity-adj Brier |
|---|---|---|---|---|
| Low continuity | 52 | 0.267 | **63.5%** | 0.233 |
| High continuity | 53 | 0.350 | **49.1%** | 0.306 |

Raw-fallback accuracy is *better* in the low-continuity half than the high-continuity half. This is the opposite of Matt's original objection. **Treat this as suggestive, not confirmed** — 52/53 games per bucket is a small enough split that this could easily be noise, and I wouldn't rule out the original concern being real with a larger sample. But it did not show up here, and the honest thing is to report that plainly rather than force the result to match the hypothesis it was built to test.

## 2. Does continuity-adjustment help, and why?

**Yes, clearly — but probably not for the reason originally hypothesized.** It improves Brier score in *both* the low- and high-continuity halves (0.267→0.233 and 0.350→0.306), not just where turnover is high. Combined with §1's finding (low continuity isn't actually where raw fallback struggles most), the more likely explanation is that the continuity discount is functioning as a general overconfidence correction rather than a targeted turnover-detector — consistent with the main season backtest's own finding that the standings scorecard is overconfident at the extremes (`prediction_model_backtest_results.md` §3). Dampening toward 0.5 helps broadly because the underlying model is overconfident broadly, and continuity happens to be a cheap, available dampening signal — not necessarily because it's capturing "this team is a different team than its record suggests" the way it was designed to.

Accuracy is identical between raw and continuity-adjusted by construction — dampening toward 0.5 never flips which side is favored, only how confidently. The entire gain here is calibration, not pick correctness. That's still a real, valuable improvement (log loss drops 63%), just worth describing accurately rather than as "correctly detects roster turnover."

## 3. Does RAPM/Log5 specifically help in this scenario?

**No — it's uniformly bad, not specifically bad only in the general-case backtest.** This was the scenario RAPM's roster-awareness was theoretically supposed to matter most for (zero current-season data, pure roster/skill signal), and it performs *worse* here than in the full-season backtest (47.6% accuracy — below a coin flip). This directly answers the question `prediction_model_backtest_results.md` §4 left open ("diluted by testing across scenarios where it doesn't matter" vs. genuinely underperforming) — it's the latter. Combined with the two reports, there's now no scenario in which this implementation of RAPM/Log5 beats the standings-based approach.

## 4. Recommendation

**Ship the continuity-adjusted fallback, not the raw one — and not RAPM/Log5 for this use case.** The improvement is real, cheap (a single division and a linear dampening step on top of data already being computed), and carries no accuracy downside. Frame it honestly per §2: it's a calibration fix riding on a continuity signal, not a validated "detects bad roster turnover" mechanism — the data here doesn't support that specific story, even though the signal still helps.

**Do not pursue RAPM/Log5 further without the tuning/diagnosis pass already recommended in the main backtest report** — this extension is a second, independent confirmation that the current implementation has a real problem, not a scenario-selection artifact.

## Caveats
- **Sample size is genuinely small** (105 games, 2 seasons) — per the brief's own instruction, treat all of the above as suggestive evidence, not a definitive verdict.
- **A prior-season PP% data-completeness gap was noticed but not chased down**: several teams' prior-season standings inputs showed `pp_pct` falling back to the 22% league-average default (e.g., Carolina's 2023-24 row), suggesting the historical PP/PK backfill enrichment in `game_log` may be incomplete for some past seasons. Didn't block this run (the scorecard formula degrades gracefully to a tie on that one input), but worth a separate look if PP% accuracy matters elsewhere.
- 2023-24 remains fully untested for this true-preseason scenario — would need a `game_log`/`player_seasons` backfill for 2022-23 first, which is out of scope here.

## Explicitly not decided here
- Whether to actually ship the continuity-adjusted fallback into production `nhl.js` — this report is the evidence, not the implementation
- Whether to chase the PP% backfill gap
- Any further RAPM/Log5 tuning work
