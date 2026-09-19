# AHL / ECHL / PWHL Elo Backtest — Results (2026-09-19)

`backtest_hockeytech_elo.py`, read-only. Three regular seasons + playoffs per
league straight from HockeyTech's modulekit `schedule` view (2023-24 through
2025-26; `{league}_game_log` only holds 2025-26), so the season-to-season
carryover could be tested and the OT/SO flag (`game_status` "Final OT" /
"Final SO") was available for Elo's damped overtime updates.

Scored on the **2025-26 holdout season** (never used for tuning). Season 1 only
warms ratings up; season 2 is the tuning season.

| | AHL | ECHL |
|---|---|---|
| Games (3 seasons + playoffs) | 3,710 | 3,365 |
| Home win rate | 51.5% | 52.3% |
| Went to OT/SO | 22.4% | 20.7% |

## 1. Regular season (holdout)

Brier ↓ (0.25 = a coin flip), accuracy ↑. n = 1,152 AHL / 1,080 ECHL.

| Model | AHL Brier | AHL acc. | ECHL Brier | ECHL acc. |
|---|---|---|---|---|
| **Elo, elo.py defaults** | **0.2452** | 56.3% | **0.2428** | 56.2% |
| Elo, tuned (see §4) | 0.2458 | 56.9% | 0.2422 | 57.2% |
| Home win rate (constant) | 0.2498 | 51.7% | 0.2501 | 51.0% |
| **Live heuristic** (`/{league}/prediction`) | **0.3443** | 53.8% | **0.3379** | 55.5% |

## 2. The live heuristic is worse than a coin flip

`hockeytech.js`'s `/prediction` win % is an additive point split (points
diff, GF/GP, GA/GP, PP%, win streak) turned into a probability by dividing
one side's points by the total. Two properties make it badly calibrated:

- **It is routinely certain.** In the holdout season it served **0% or 100%
  in 459 of 1,152 AHL games (40%) and 465 of 1,080 ECHL games (43%)**, and
  those "certain" calls were wrong 184 and 185 times respectively.
- **Every tie goes to the away team** (`if (hGpg > aGpg) home += 0.6; else
  away += 0.6`, same for GA and PP). At the start of a season every team's
  stats are equal, so the home team gets **0%** (or 30% on a win streak):
  27 of the first 40 AHL holdout games and 18 of the first 40 ECHL games
  showed the home side at 0%.

Its Brier (~0.34) is worse than predicting 50% for every game (0.25), and its
log loss (3.8 AHL, 4.0 ECHL — vs 0.68 for Elo) reflects those confident
misses. The PP% term was left out of the port (not in the schedule feed);
that term also hands ties to the away team, so production is if anything
more extreme than the port. `/pwhl/prediction` uses the same point-split
shape — `docs/pwhl_elo_investigation.md` saw the same symptom (scorecard log
loss 2.56).

## 3. Opening two weeks and playoffs (small samples)

| Slice | n (AHL / ECHL) | Elo default | Elo tuned | Heuristic | Home rate |
|---|---|---|---|---|---|
| Opening 2 weeks, holdout — AHL | 79 | 0.2369 | 0.2342 | 0.3792 | 0.2498 |
| Opening 2 weeks, holdout — ECHL | 77 | 0.2613 | 0.2523 | 0.3693 | 0.2583 |
| Playoffs, holdout — AHL | 89 | 0.2575 | 0.2571 | 0.4634 | 0.2498 |
| Playoffs, holdout — ECHL | 81 | 0.2017 | 0.2004 | 0.3410 | 0.2449 |

Carrying last season's rating (regressed) into the opener works in the AHL
and is roughly break-even in the ECHL; either
way it's far better than the heuristic's 0%-home openers. AHL playoffs were
a wash against the home-rate baseline; n < 100 per slice, so no claims.

## 4. Tuning doesn't reliably help

A sweep of K × home advantage × between-season regression (560 combos),
selected on 2024-25 only, landed on K 6 / home 5 / regress 0 (AHL) and
K 10 / home 15 / regress 0.2 (ECHL). The Brier surface is flat — dozens of
combos tie to four decimals on the training season — and the AHL winner is
slightly *worse* than the defaults on the holdout. The ECHL winner is
0.0006 better. That's noise, not signal.

The one consistent direction is **home advantage**: both leagues' home win
rates (51.5%, 52.3%) are below the 55% that elo.py's 35-point default
implies, and the sweeps favor 5–20 Elo points. It's worth revisiting with a full 2026-27 season,
but not a reason to fork constants now.

## Recommendation

**Wire Elo into AHL and ECHL predictions with elo.py's existing constants**
— same model as NHL, no league-specific tuning:

1. Make `elo_ratings.py` / `win_probs.py` / `prediction_scorecard.py`
   league-aware (they're NHL-only today) and run them in `ahl-nightly.yml` /
   `echl-nightly.yml`, seeded from 2025-26 so 2026-27 opens on regressed
   ratings.
2. Switch the Worker's `/{league}/prediction` win % from the point split to
   the logged Elo probability. Keep the AI narrative and expected-score
   parts as they are.
3. Consider the same switch for `/pwhl/prediction`, which shares the flaw.

(1) and (2) shipped with this backtest: `hockeytech_elo.py` and
eyewall-poller's `/{league}/prediction`. AHL opens 2026-10-02, ECHL 2026-10-15.

## PWHL (added 2026-09-19)

Same script (`python backtest_hockeytech_elo.py pwhl`), seasons 1/3 (2024),
5/6 (2024-25), 8/9 (2025-26). `pwhl_game_log`'s `ot`/`shootout` columns are
false for every game, so the feed's "Final OT"/"Final SO" status is the only
overtime source here too (26.9% of PWHL games went past regulation).

| 2025-26 holdout, regular season (n = 120) | Brier | Acc. |
|---|---|---|
| **Elo, elo.py defaults** | **0.240** | 56% |
| Elo, tuned (K 4 / home 45 / regress 0 — noise again) | 0.241 | 56% |
| Home win rate (constant, 55.9%) | 0.247 | 57% |
| Live heuristic, ported without its PP% and Corsi terms | 0.312 | 57% |

Same failure shape: 0% or 100% in 49 of 120 games (19 of those wrong), and
the home team at 0% in 7 of the season's first 12 games. The sample is
small (one holdout season of 120 games), but Elo is the better model on
every slice, and the heuristic's certainty problem doesn't depend on
sample size. This supersedes `docs/pwhl_elo_investigation.md`'s "not
recommending production wiring" -- that report couldn't see the OT/SO
flag, and its scorecard port showed the same symptom (log loss 2.56).
`/pwhl/prediction` now uses Elo too; 2026-27's four expansion teams (DET,
HAM, LV, SJS) start at 1500.
