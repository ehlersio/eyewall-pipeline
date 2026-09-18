# Opening-night line projection backtest

`backtest_opening_night.py` (read-only), run 2026-09-17. Companion to
[line_projection_backtest_results.md](line_projection_backtest_results.md) (in-season, next
game). Question: with only what's known before the season starts, how well can we project each
team's opening-night lines? **Answer: preseason shift data is the whole story.** Group
players the way they played together in preseason, rank those units by last season's NHL ice
time, and opening-night accuracy matches the in-season "same as last game" model. Last
season's lines, which is what `line_combinations.py`'s prior-season blend shows today, are far
worse.

## Method

- **Truth and scoring**: the same as the in-season backtest. Each game's actual 5v5 units come
  from `shift_events`. Known-lineup assumption: the model is told who dressed and has to
  arrange them.
- **Sources** (strictly before each team's first regular-season game):
  - **prior**: the team's previous regular season.
  - **pre**: its own preseason games. Preseason shift data exists for 2024 and 2025 only, so
    preseason models score 64 openers (2024-25 and 2025-26). Prior-only models also score
    2023-24, for 96 openers.
- **Ranking fallback**: players with no source TOI are ranked by their league-wide
  previous-season 5v5 TOI, from any team.
- **Later games**: each opener projection is also scored, unchanged, against the team's games
  2 and 3.

## Results: opening night (2024-25 + 2025-26, 64 openers)

| Model | Linemates | Exact trios | All 4 lines | Line 1 | F top-6 | D pairs | D top-4 |
|---|---|---|---|---|---|---|---|
| Last season's final game | 30.4% | 10.5% | 0.0% | 14.1% | 61.6% | 27.6% | 63.4% |
| Last season, 5-game half-life | 36.9% | 16.4% | 4.8% | 23.4% | 66.7% | 49.5% | 71.6% |
| Last season, full (today's prior-season blend) | 38.6% | 18.4% | 1.6% | 29.7% | 65.0% | 47.4% | 68.8% |
| Last preseason game only | 63.4% | 49.0% | 30.6% | 23.4% | 61.9% | 56.8% | 66.7% |
| All preseason games | **70.0%** | **58.5%** | **43.5%** | 34.4% | 63.3% | **79.7%** | 64.6% |
| **Preseason groupings, ranked by last season's TOI** | **70.0%** | **58.5%** | **43.5%** | **46.9%** | **73.5%** | **79.7%** | **71.9%** |
| Preseason + 0.5 × last season | 67.9% | 55.7% | 38.7% | 32.8% | 63.9% | 75.0% | 63.1% |
| Preseason + 1 × last season | 56.3% | 40.0% | 21.0% | 29.7% | 61.9% | 64.6% | 62.1% |
| Preseason + 2 × last season | 51.2% | 34.0% | 19.4% | 26.6% | 60.9% | 64.6% | 62.9% |

For reference, the in-season "same as last game" model gets 74.5% linemates, 64.3% exact
trios and 81.3% D pairs.

Per season, the preseason-groupings / last-season-ranking model:

| Opener | Linemates | Exact trios | Line 1 | F top-6 | D pairs | D top-4 |
|---|---|---|---|---|---|---|
| 2024-25 | 72.6% | 61.2% | 56.2% | 73.1% | 82.3% | 66.7% |
| 2025-26 | 67.4% | 55.7% | 37.5% | 74.0% | 77.1% | 77.1% |

Last-season-only models across all three openers (2023-24 to 2025-26, 96 openers) come out the
same: 31–39% linemates, 11–19% exact trios.

## How long the opener projection holds (linemates / exact trios, the same model)

| | 2024-25 | 2025-26 |
|---|---|---|
| Game 1 | 72.6% / 61.2% | 67.4% / 55.7% |
| Game 2 | 66.9% / 53.1% | 58.9% / 44.3% |
| Game 3 | 61.3% / 44.8% | 53.9% / 37.5% |
| Game 2 from game 1's actual lines | 80.5% / 71.1% | 81.8% / 74.2% |

## Takeaways

1. **Preseason lines are the opening-night signal.** Pooling all preseason games gets 70% of
   linemates and 80% of D pairs on opening night, close to what last game's lines give
   mid-season. The last preseason game alone is worse, because it often isn't the full
   regular lineup (only ~77% of opening-night forwards have a linemate history in it,
   against ~98% for all preseason games).
2. **Last season's lines are a poor opening-night projection** (38.6% linemates). Mixing them
   into preseason only hurts, more the heavier they're weighted. Rosters and coaching plans
   change over the summer. This is what the Scouting tab shows before a team's first
   game today.
3. **Rank lines by real NHL ice time, not preseason ice time.** Coaches manage veterans'
   minutes in preseason, so preseason TOI orders lines badly. Keeping preseason groupings but
   ranking by last season's TOI raised Line 1 from 34% to 47% and forward top-6 from 63% to
   74%, in both seasons. It's a post-hoc choice (added after the first run showed the
   problem), so the 2026-27 opener is its real test.
4. **One real game beats the whole preseason.** By game 2, the opener projection has faded to
   59–67% while game 1's actual lines get 81%. So the pipeline should switch from preseason to
   regular-season lines immediately at a team's first game. That's the in-season model
   ([line_projection_backtest_results.md](line_projection_backtest_results.md)).

## Caveats

- **Known lineup, again.** Before opening night we don't know who dresses. Final roster cuts
  land days before the opener, so lineups are more knowable than mid-season, but these are still
  upper bounds.
- **Two seasons and 64 openers per model.** Differences under ~5 points are within noise.

## Data issue found along the way

`game_log` has **no ARI rows** for 2022-23 or 2023-24, while `shift_events` has all 82 games
both years. This backtest falls back to shift-data game ids for any team-season missing from
`game_log`. Anything else that reads a team's schedule from `game_log` (for example
`line_combinations.py`'s per-team game-id lookup) silently gets nothing for Arizona in those
seasons.
