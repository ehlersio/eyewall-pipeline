# Combined Prediction Calibration — Part A + B Results

**Type:** Execution report, per `COMBINED_CALIBRATION_IMPLEMENTATION.md`. Covers Part A (fit + validate isotonic calibration) and Part B (players.team schema fix). Part C (production wiring in `nhl.js`) is deliberately deferred — it needs `players.team` actually populated in production first (one nightly cycle after this merges), per the sequencing decision already made.

---

## Part B — `players.team` schema fix

- `docs/combined_calibration_players_team_column.sql` — DDL to hand to Matt (`alter table public.players add column team text;`). This repo has no migration tooling; applied manually in the Supabase SQL editor, same convention as every other recent column addition.
- `nhl_stats.py::fetch_roster` now attaches `team` to every player row it returns (was fetched but discarded before the `players` upsert). No other code changes needed — rides the existing nightly `run()` cadence, no new job.
- Confirmed PWHL doesn't need an equivalent fix — `pwhl_players` already writes `team_id` per row (`pwhl_stats.py:295-300`).
- New regression test (`test_nhl_stats_roster.py`, 3 cases, mocked — no network calls) covers the specific bug this fixes: team field silently missing/discarded.
- Full suite: 189 passed.

**Timing dependency, unchanged from the brief:** land this and let it run through one nightly cycle before Part C gets tested against the 2026-27 preseason (opener 2026-09-29).

---

## Part A — Isotonic calibration fit + validation

Fit on full-pool 2024-25 (1,166 games), validated on held-out full-pool 2025-26 (1,132 games) — never fit and evaluated on the same data. Source: `fit_scorecard_calibration.py`, reading `backtest_results.json` (from `backtest_predictions.py`).

**Confirmed rather than assumed** (per the brief's own instruction): directly verified against production that none of the 512 unique (season, cutoff, team) combinations underlying this dataset had zero games played at cutoff — the main backtest's data is genuinely all "current-season-to-date, however thin," never the zero-game preseason regime. 0 violations found.

### Holdout results (2025-26, n=1,132)

| | Brier ↓ | Log loss ↓ | Accuracy |
|---|---|---|---|
| Raw scorecard | 0.333 | 2.437 | 52.8% |
| **Isotonic-calibrated** | **0.256** | **0.706** | 52.4% |

A large, genuine improvement on both proper scoring rules (Brier −23%, log loss −71%), on data the fit never saw. Accuracy dips marginally (52.8%→52.4%) — expected and not a red flag: isotonic regression preserves rank order everywhere, but a handful of individual games sitting right at the raw-score/calibrated-score 0.5 crossover can flip sides even under a strictly monotonic transform. The whole point of this fix was calibration, not pick correctness, and pick correctness is essentially unchanged.

### Early-season slice — a real limitation, reported honestly rather than worked around

**The true 2025-26 holdout has zero early-season (<10 GP) games.** All 92 early-season games in this dataset are inside the 2024-25 *fit* set — 2025-26's first cutoff (Nov 1) landed after every team had already played 10+ games, likely because that season's early schedule was denser than 2024-25's. This means the brief's ask ("report before/after separately on the early-season slice... on the holdout set") cannot be fulfilled as literally specified — there's no such holdout data to report on.

Rather than silently drop this or fabricate a holdout evaluation, `fit_scorecard_calibration.py` reports an explicitly-labeled **in-sample sanity check** on the 92 early-season games instead — directional only, not validated evidence, since these games were part of the fit:

| (in-sample, n=92) | Brier ↓ | Log loss ↓ | Accuracy |
|---|---|---|---|
| Raw scorecard | 0.307 | 1.632 | 53.3% |
| Isotonic-calibrated | 0.237 | 0.666 | 60.9% |

Directionally consistent with the aggregate holdout result (same shape of improvement), which is reassuring — but this is not proof the calibration generalizes to early-season games specifically, since it was never tested on any early-season game the fit hadn't seen. If a genuinely-early-season holdout matters before shipping, it would need either a different train/holdout split (e.g. fit on 2025-26, validate on 2024-25's early games) or waiting for a future season's data.

### Deployment artifact

`scorecard_calibration.json` — 13 fitted breakpoints (`x_thresholds`/`y_thresholds`, isotonic regression's step function), small enough to bake directly into the Worker as a lookup/interpolation with no sklearn/runtime dependency, per `SCORECARD_CALIBRATION_FIX.md`'s deployment recommendation. Ready for Part C to consume.

---

## Recalibration cadence

Still an open question per the original brief — not decided here, doesn't block shipping v1.

## Explicitly not done here (deferred to Part C)
- Wiring the regime check, isotonic lookup, or continuity dampening into `nhl.js`
- Anything requiring `players.team` to actually be populated in production (needs the nightly cycle after this merges)
