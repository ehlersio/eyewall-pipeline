# Elo Prediction Model — Investigation & Backtest Results

**Type:** Execution report. Runners: `eyewall-pipeline/elo.py` (pure Elo math),
`backtest_elo.py` (chronological in-season backtest), `backtest_preseason.py`
(extended with `elo_pred`/`continuity_vw_pred`), `tune_elo.py` (hyperparameter
sweep). All read-only against production — no writes anywhere.

**Motivating question:** the production preseason fallback
(`buildPreseasonFallback` in `nhl.js`, validated in
`true_preseason_backtest_results.md`) predicts an upcoming season off *last
season's final standings*, dampened by roster-continuity. The objection
raised: with rosters turning over every offseason (trades, free agency,
waivers), why lean on a team's old record at all instead of reconstructing
team strength from *currently rostered* players' individual value (WAR/RAPM)?

That hypothesis was tested directly, twice, before this investigation
(`prediction_model_backtest_results.md` §4, `true_preseason_backtest_results.md`
§3) — RAPM/Log5, roster-aware by construction, backtested *worse* than the
simple standings-based fallback in both the general and true-preseason cases
(47.6% accuracy in the preseason case, below a coin flip). This investigation
asked a different question instead: not "how do we patch the roster-value
idea," but "is there a model actually purpose-built for win prediction we
should be using instead of adapting tools built for other jobs (RAPM for
player evaluation, a hand-tuned scorecard) to this one?" — landing on Elo,
the standard tool for exactly this problem across sports analytics
(this follows FiveThirtyEight's published NHL Elo methodology).

---

## 1. Data reality check

Live query confirmed `game_log`'s earliest row is `2023-10-10` — only three
usable seasons exist (2023-24, 2024-25, 2025-26-in-progress), the same
boundary every other backtest in this repo already works within (2022-23 has
zero `game_log` rows). Elo needs nothing beyond `game_id, game_date,
home_team, away_team, home_score, away_score, period_end` — all already in
`game_log` — so it needed no new data collection.

Also found, not yet used: MoneyPuck publishes a team-level season-summary CSV
(`seasonSummary/{year}/regular/teams.csv`) with score/venue-adjusted xG%/
Corsi%/Fenwick% by situation — same domain this pipeline already pulls player
data from, zero new vendor integration. Not incorporated in this pass; a
plausible future enrichment if Elo alone ever needs a secondary signal (see
§6).

## 2. In-season backtest — Elo vs. the production scorecard

`backtest_elo.py` runs ONE continuous chronological pass over every regular-
season game across all 3 seasons (Elo is inherently cumulative, unlike the
scorecard's independent per-cutoff snapshots), carrying each team's rating
forward and regressing it toward the mean once at each season boundary. The
scorecard prediction is recomputed for the identical games (reusing
`backtest_predictions.py`'s own `standings_inputs_asof`/`scorecard_win_pct`)
for a same-game, same-coverage comparison — not a separate run.

| Model | n | Brier ↓ | Log loss ↓ | Accuracy ↑ |
|---|---|---|---|---|
| Production scorecard | 3,796 | 0.321 | 2.561 | 55.5% |
| **Elo** | 3,796 | **0.242** | **0.677** | **56.5%** |

Elo wins on every metric. The scorecard's log loss (2.561 — *worse* than
always guessing 50/50, which scores 0.693) reproduces the exact overconfidence
problem `prediction_model_backtest_results.md` §3 already flagged as "worth a
look on its own." Elo's log loss (0.677, better than the 50/50 baseline)
doesn't have that problem — it's honestly calibrated by construction, not
patched after the fact with isotonic regression the way the scorecard needed.

## 3. True-preseason backtest — the regime this whole thread is about

Same 105 real opening-week games `true_preseason_backtest_results.md` used
(2024-25 + 2025-26, 15-day post-opener window, each team's own first 2 games
used only to build a roster proxy, never scored). Two new variants added
alongside the three that already existed:

- **`elo_pred`** — each team's end-of-prior-season Elo rating (from §2's
  chronological run), regressed toward the mean once via
  `elo.regress_to_mean()`, exactly like a real season boundary. Needs zero
  current-season data — no roster proxy, no anchor games, no RAPM pool.
- **`continuity_vw_pred`** — the value-weighted-continuity idea from this
  investigation's first pass: weight each retained/departed player's role by
  prior-season RAPM (clamped ≥0) instead of raw TOI, on the theory that
  losing a star should count for more than losing a depth player with
  similar ice time.

| Variant | n | Brier ↓ | Log loss ↓ | Accuracy ↑ |
|---|---|---|---|---|
| Raw prior-season fallback | 105 | 0.299 | 2.039 | 58.1% |
| **Continuity-adjusted (currently shipped)** | 105 | 0.261 | 0.733 | 58.1% |
| Continuity, value-weighted (new — didn't help) | 105 | 0.270 | 0.780 | 57.1% |
| RAPM/Log5 | 105 | 0.418 | 2.039 | 46.7% |
| **Elo** | 105 | **0.237** | **0.666** | **60.0%** |

**Elo beats the currently-shipped continuity-adjusted fallback on every
metric here too**, using less machinery (no roster proxy, no RAPM pool, no
anchor-game bookkeeping) than the thing it beats.

**The value-weighted continuity idea did not work** — Brier, log loss, and
accuracy are all slightly worse than the plain TOI-weighted version already
in production. Recorded here so it isn't quietly re-proposed later without
this result being checked first: weighting continuity by RAPM instead of TOI
is a dead end, on this data.

**Note on drift vs. the original report:** the raw/continuity-adjusted
numbers here (58.1%/0.261/0.733) differ slightly from
`true_preseason_backtest_results.md`'s original run (56.2%/0.270/0.754) —
these backtests pull live data, and real games/backfills landed in the weeks
between the two runs. The ranking and conclusion are unaffected.

## 4. Hyperparameter tuning — does it help?

`elo.py`'s constants (`K=6`, `HOME_ADVANTAGE=35`, `REGRESS_FRACTION=1/3`) are
FiveThirtyEight's published NHL values, never fit against this pipeline's own
data. `tune_elo.py` swept `K ∈ {3,4,6,8,10,14}`, `home_adv ∈ {0,20,35,50,65}`,
`regress_fraction ∈ {0,0.15,1/3,0.5,0.7}` (150 combinations) — game_log
fetched once, then every combination simulated in memory (no repeated
network cost). Selection used Brier score on 2023-24+2024-25 only ("train");
2025-26 ("holdout") was never looked at during selection, only scored
afterward for the winning config, so overfitting would be visible rather than
hidden.

| | Train Brier | Train Acc | Holdout Brier | Holdout Acc |
|---|---|---|---|---|
| Default (538 values: K=6, adv=35, regress=1/3) | 0.2388 | 58.1% | **0.2484** | **53.6%** |
| Best-tuned by train Brier (K=8, adv=35, regress=0.15) | 0.2384 | 58.3% | 0.2504 | 52.3% |

**Two findings, both real:**

1. **Home-ice advantage of 35 Elo points is a genuine, validated signal** —
   every one of the top 10 configs by train Brier used `home_adv=35`; `0`,
   `20`, `50`, and `65` all scored measurably worse across every K/regress
   combination they appeared in. This isn't noise — it independently confirms
   538's published NHL home-ice value is right for this pipeline's own data
   too.
2. **K and regress_fraction barely matter, and "tuning" them is actively
   counterproductive.** The full top-10 train-Brier spread is 0.2384–0.2387 —
   a difference in the fourth decimal place, i.e. noise. Picking the
   train-best combination (K=8, regress=0.15) produces a *worse* holdout
   result (Brier 0.2504, accuracy 52.3%) than just using the untuned 538
   defaults (Brier 0.2484, accuracy 53.6%) — a textbook overfitting signature:
   optimizing against noise in the training metric, which doesn't transfer.

**Recommendation: ship the literature-default constants as-is. Do not tune
K/regress_fraction against this pipeline's own 3 seasons of data** — there
isn't enough signal in that range to tune profitably, and the one clear
result (home-ice = 35) already matches the untouched default. This also
means Elo's advantage over the scorecard/RAPM in §2–3 isn't a fragile,
cherry-picked-hyperparameter result — it holds with textbook defaults nobody
fit to this data.

## 5. Overall recommendation

**Elo beats both currently-shipped systems (in-season scorecard, preseason
continuity-adjusted fallback) on every metric measured, in both regimes,
using untuned literature defaults and no data this pipeline doesn't already
have.** It's also simpler than what it replaces — no roster proxy, no
anchor-game bookkeeping, no RAPM pool, no separate isotonic-calibration patch
for the scorecard's known overconfidence. One consistent rating carries
across the season boundary via regression-to-mean instead of two different
hand-built heuristic regimes.

## 6. Explicitly not decided/built here

- ~~**Production wiring**~~ — done. `team_elo_ratings` table + nightly
  `elo_ratings.py` (PR #106), both branches of `nhl.js`'s
  `/prediction/analyze` rewired to read from it (eyewall-poller PR #93), plus
  two real follow-on bugs found and fixed during rollout: a cold schedule
  cache silently returning "Game not found" for every non-CAR team
  (eyewall-poller #95), and the frontend never sending `team=` at all so the
  Worker always resolved the default team regardless of who was actually
  browsing (eyewall-poller #96 / eyewallanalytics #279).
- ~~**MoneyPuck's team-level adjusted CSV** as a secondary signal~~ — tested,
  see §8. Not worth pursuing further, at least not via the simple blend
  tried there.
- ~~**Whether Elo should also inform PWHL's own `/pwhl/prediction`**~~ —
  investigated, see `docs/pwhl_elo_investigation.md`. Directionally the same
  conclusion (Elo looks better than the current heuristic scorecard), held
  with meaningfully less confidence than this report's NHL result — PWHL
  has about 1/14th the game volume, only one prior-season boundary to test
  a true-preseason regime against, and the scorecard comparison used there
  is a reduced port (missing PP%/Corsi terms `pwhl_game_log` doesn't carry
  point-in-time). Not wired into production off that report alone.
- ~~**Margin-of-victory formula refinement**~~ — tested, see §7. Not worth
  pursuing further.

## 7. Margin-of-victory formula — tested, no real difference

`backtest_mov.py` compared three MOV multiplier variants against the same
chronological harness, K/home_adv/regress_fraction held at their validated
defaults throughout:

- `baseline` — the shipped formula (linear boost capped at 1.75x, OT/SO
  damped at 0.5x)
- `flat` — no margin scaling at all (a control: does MOV scaling matter at
  all, separate from whether its specific shape is right)
- `diff_dampened` — FiveThirtyEight's published NBA/NFL Elo formula
  (`ln(margin+1) * 2.2/(0.001*elo_diff_of_winner + 2.2)`), literature
  constants, not fit to this data. Dampens the MOV bonus when the actual
  winner was already favored pre-game (expected, less informative) and
  amplifies it when the winner was the underdog (surprising, more
  informative) — `baseline` only looks at raw margin, so a blowout by a
  heavy favorite and the same blowout by a heavy underdog get identical
  treatment today.

| Variant | All-seasons Brier | All-seasons Acc | Holdout Brier | Holdout Acc |
|---|---|---|---|---|
| baseline (shipped) | 0.2420 | 56.58% | 0.2484 | 53.58% |
| flat (no MOV scaling) | 0.2425 | 56.38% | 0.2483 | 53.28% |
| diff_dampened (538 formula) | 0.2419 | 56.30% | 0.2481 | 53.58% |

**No meaningful difference between any of the three.** The spread is in the
same fourth-decimal-place noise band the K/home_adv/regress_fraction sweep
already found — `diff_dampened` edges `baseline` on Brier/log loss by
0.0001–0.0002 but is *worse* on all-seasons accuracy (56.30% vs 56.58%),
and `flat` (literally no margin sensitivity at all) is barely
distinguishable from either. This is consistent with the general sports-Elo
literature finding that MOV refinements are a second-order effect on
single-game predictions — their real value (if any) is in faster/more
accurate rating convergence over a season, not in moving Brier/accuracy on
a backtest like this one.

**Recommendation: keep the shipped formula. Not worth the added
complexity.** Explicitly not tested here: an empty-net-goal-corrected
margin (stripping empty-net goals before computing margin, so a
late-empty-netter doesn't inflate a close game into a "decisive" one) —
`game_scoring`, the only table with goal-level, situation-code-flagged
data, only covers the 2025-26 season. Testing that variant against a third
of the data everything else in this report used would be a materially
weaker check, so it's left for a separate pass if/when `game_scoring` gets
backfilled further back — not assumed to help just because it's more
"correct" in principle, same posture as everything else here.
