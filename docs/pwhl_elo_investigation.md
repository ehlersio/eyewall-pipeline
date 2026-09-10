# PWHL Elo Investigation — Results

**Type:** Execution report. Runner: `eyewall-pipeline/backtest_pwhl_elo.py`.
Read-only against production — no writes anywhere. Follow-up to
`docs/elo_prediction_model_results.md`, which deliberately scoped NHL only
and flagged this as its own open question, not an assumed extension.

**Motivating question:** Elo beat NHL's production scorecard and preseason
fallback on every metric, in both regimes (`elo_prediction_model_results.md`).
Does the same hold for PWHL's own `/pwhl/prediction`, which is the same
class of hand-tuned heuristic scorecard NHL had before Elo replaced it?

---

## 1. Data reality — confirmed live, not assumed

`GET /config/seasons/pwhl-types` (the live, authoritative season_id→type
map, not a guess from date ranges) plus a full `pwhl_game_log` pull:

| season_id | Type | Teams | Games | Date range |
|---|---|---|---|---|
| 1 | Regular (inaugural) | 6 | 72 | 2024-01-01 → 05-05 |
| 2, 4, 7 | Preseason | — | — | — |
| 5 | Regular | 6 | 90 | 2024-11-30 → 2025-05-03 |
| 3, 6, 9 | Playoffs | — | — | — |
| 8 | Regular | 8 | 120 | 2025-11-21 → 2026-04-25 |
| 10 | Regular (current) | — | 0 so far | 2026-27, not yet started |

**282 total regular-season games exist across PWHL's entire history** — about
1/14th of the 3,936 NHL games `backtest_elo.py` used. Two structural
differences from NHL worth naming up front:

- **Only one prior-season boundary exists for a true-preseason test**
  (5→8) — season 1 is the league's own first season, nothing to regress
  from. NHL had two boundaries (2023-24→2024-25, 2024-25→2025-26) to build
  its 105-game preseason test set from; PWHL has one, and it's much
  smaller (see §3).
- **Season 8 already contains a real historical rehearsal of the exact
  scenario 2026-27 (season 10) is about to repeat**: 2 brand-new expansion
  teams (team_ids 8/9) entered with zero prior-season history. Season 10
  adds 4 more (DET/HAM/LV/SJS). This isn't hypothetical — §3 shows what
  actually happened the last time it occurred.

## 2. Scorecard port — intentionally incomplete, stated up front

`pwhl_game_log` has no PP%/Corsi columns at all (unlike NHL's `game_log`,
which carries `pp_goals`/`pp_opps`/`pk_goals_against`/`pk_opps` per row) —
computing those terms point-in-time-correctly isn't possible from this
table without pulling in more data than this pass scoped. The ported
scorecard (`backtest_pwhl_elo.py::scorecard_win_pct`) uses only
**points/GF-per-game/GA-per-game/streak** — 3 of the live `pwhl.js`
formula's 5 possible terms (points/GF/GA/streak/PP%, plus Corsi when
available). **This is a fair comparison against a weaker scorecard than the
one actually in production** — a win here is real, but the margin shouldn't
be read as "this is exactly how much better than the live system Elo is."

## 3. In-season backtest — a clear result, despite the reduced scorecard

Chronological Elo pass across all 282 games (season_id 1→5→8, regression-
to-mean applied at each boundary), scorecard recomputed point-in-time for
the same games:

| Model | n | Brier ↓ | Log loss ↓ | Accuracy ↑ |
|---|---|---|---|---|
| Reduced scorecard (this port) | 272 | 0.375 | **4.607** | 50.0% |
| **Elo** | 272 | **0.245** | **0.682** | **54.8%** |

The scorecard's log loss (4.61) is far worse than even NHL's own broken
scorecard (2.56) — and its accuracy is *exactly* 50.0%, meaning this
reduced 3-term version has no real ability to pick winners at all. Elo
clears the "always guess 50/50" log-loss baseline (0.693) comfortably.
Given the scorecard tested here is missing 2 of its live terms, this is a
real result but shouldn't be oversold as measuring the exact gap to
production — see §2.

## 4. True-preseason backtest — small sample, genuinely mixed, read carefully

13 games fall in season 8's opening 15-day window. **6 of the 13 involve
one of the 2 new expansion teams** — for those, the raw prior-season
fallback has literally nothing to offer (no season-5 row exists), while
Elo still produces an answer (a new team just starts at the neutral 1500
rating).

| Variant | n | Brier | Log loss | Accuracy |
|---|---|---|---|---|
| Raw prior-season record (non-expansion games only) | 7 | 0.251 | 3.294 | **57.1%** |
| Elo (same 7 games) | 7 | 0.254 | **0.702** | 42.9% |
| Elo (all 13 games, incl. expansion matchups) | 13 | 0.239 | 0.671 | 61.5% |

**This is genuinely mixed, and the sample is too small to call an accuracy
winner** — 7 games means one flipped result swings accuracy by 14 points.
Two things are real regardless of the small n, though:

1. **Elo's calibration is dramatically better on the identical 7 games** —
   log loss 0.702 vs. the raw fallback's 3.294. This is the same
   overconfidence pattern that shows up in *every* scorecard variant tested
   across both the NHL and PWHL investigations (NHL's scorecard, this
   port's in-season result in §3, and now the raw preseason fallback here)
   — a raw record-based heuristic keeps being badly overconfident at the
   extremes, and Elo keeps not having that problem.
2. **Elo has full coverage; the raw fallback structurally doesn't.** For
   6 of these 13 real games — nearly half — the current approach (if PWHL
   had one at all; `/pwhl/prediction` has no preseason branch in
   production today) would have nothing to say for a true expansion team.
   This matters concretely for 2026-27: 4 new teams, not 2.

**No accuracy claim is being made here** given n=7. The calibration
advantage and the expansion-coverage advantage are both real independent
of sample size, though.

## 5. Recommendation

**Directionally the same conclusion as NHL — Elo looks like a real
improvement over the current heuristic approach — but held with
meaningfully less confidence than the NHL result**, for reasons specific to
PWHL's data, not a weaker method:

- The in-season result (§3) is reasonably clear, but tested against a
  scorecard port missing 2 of its live terms (§2) — worth re-checking
  against the *real* production formula before treating this as final,
  ideally after `pwhl_game_log` (or another table) can support a
  point-in-time PP%/Corsi reconstruction.
- The preseason result (§4) is too small a sample (7 comparable games) to
  make an accuracy claim either way — only the calibration and
  expansion-coverage findings are load-bearing.

**Not recommending production wiring off this report alone.** Reasonable
next steps, in rough order of value: (a) get PP%/Corsi into a point-in-time-
computable form for a fairer scorecard comparison, (b) wait for season 10's
real games to accumulate a second, larger true-preseason sample, (c) if the
in-season result holds up under (a), that alone might justify wiring Elo
into `/pwhl/prediction`'s in-season branch even before the preseason
question is fully resolved — the preseason case is the weaker part of this
report, not the in-season one.

## 6. Not built here

- Production wiring — this is a research pass, same posture as the NHL
  investigation was before its own production PRs.
- A PP%/Corsi point-in-time reconstruction for a scorecard port that
  matches the live formula exactly.
- Continuity-dampening or any other preseason-specific correction for
  PWHL — NHL's own version of that was found to add little over Elo's own
  regression-to-mean (`elo_prediction_model_results.md` §3), so not
  assumed to be worth building here either without its own check.
