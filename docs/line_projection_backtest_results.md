# Line projection backtest

`backtest_line_projection.py` (read-only), run 2026-09-17. Question: from shift data alone,
how well can we project a team's forward lines and D pairs for its **next** game? **Answer:
well for groupings, poorly for line order.** "Same as last game" gets 3 of 4 linemate pairs
right and 81% of D pairs exactly. Season-to-date lines, which is what the Scouting tab shows
today, are much worse as a projection.

## Method

- **Truth**, for each team-game, is that game's actual 5v5 units. Shared 5v5 seconds (both
  teams with exactly 5 skaters on) for every F-F and D-D pair are greedily partitioned into
  trios and pairs. Units are ranked by their members' 5v5 TOI, so Line 1 is the most-used trio.
- **Projection** runs the same partition over pair weights from the team's **earlier** games
  only, restricted to the skaters who dressed that night. This is the **known-lineup**
  assumption: the model is told who plays and has to arrange them.
- **Models**:
  - `last`: the previous game only. Older history just breaks ties for players who sat.
  - `decay_h`: all earlier games, weighted 0.5^(games ago / h).
  - `season`: season-to-date equal weights, i.e. `line_combinations.py`'s current output.
- **Seasons**: half-life tuned on 2024-25 (h = 1 won out of 1, 2, 3, 5, 8, 13, 21). Scored on
  2025-26, 2,622 team-games. Regular season only. 2024-25 and 2025-26 agree within ~1 point
  on every metric.

## Results (2025-26)

| Metric | `last` | `decay_1` | `season` (today's Scouting tab) |
|---|---|---|---|
| Forward linemate pairs right | **74.5%** | 73.4% | 50.9% |
| Exact forward trios | **64.3%** | 62.8% | 32.7% |
| All 4 lines exactly right | **48.5%** | 46.3% | 11.4% |
| Line 1 exactly right (trio + rank) | 41.4% | **43.2%** | 26.2% |
| Forward top-6 / bottom-6 right | 72.2% | **74.5%** | 72.0% |
| Exact D pairs | **81.3%** | 80.9% | 61.9% |
| D top-4 / third pair right | 76.6% | **79.7%** | 75.6% |

Subsets (`last`):

| Subset | Games | Linemates | Exact trios | D pairs |
|---|---|---|---|---|
| Same 18 skaters as last game | 792 | 88.0% | 82.8% | 94.2% |
| Lineup changed | 1,830 | 68.6% | 56.3% | 75.7% |
| Team's first 5 games (last season's lines carried over) | 160 | 68.9% | 58.3% | 72.4% |

**Lineup churn**: 69.5% of games have at least one skater who didn't dress last game (1.14 new
skaters per game on average).

## Takeaways

1. **Recency is everything.** The best half-life is one game, and "last game" beats every
   longer memory. Lines change often enough that a season-long view describes the season, not
   tonight.
2. **Today's Scouting-tab lines are a poor stand-in for current lines**: 51% of linemates vs
   75%. Weighting that table toward recent games would help immediately, projections or not.
3. **Line order is the weak spot.** Groupings are stable, but which trio gets the most ice in
   a given game is noisy (score effects, penalties, matchups). Only 41–43% for Line 1, ~72–75%
   for top-6 tier. Rank by recency-weighted TOI (`decay_1`) rather than last game alone.
4. **Lineup changes are where the upside is.** When the same 18 dress, last game's lines are
   right 88% of the time. The drop to 69% when lineups change is a rule for who slots in where
   (position, handedness, the missing player's role), and it can be tested with this harness.
5. **Carrying lines over the summer holds up reasonably** (69% linemates in the first 5 games),
   but that small sample includes games 2–5 already informed by games 1–4. The real
   opening-night test (last season's lines plus offseason moves, graded against game 1) is still
   to do.

## Caveats

- **Known lineup is an upper bound for pre-game use.** Before morning skate we don't know who
  dresses, and it changes in ~70% of games. A realistic pre-game number sits below these, by
  an amount this backtest doesn't measure.
- **Truth is inferred from shifts, not official lines.** Within-game partitions capture ~78%
  of all F-F shared 5v5 time, and the rest is in-game shuffling.

## Data issue found along the way

Games ingested through `shift_data.py`'s HTML fallback have **no goalie shifts** (507 of 1,311
in 2025-26; 69 in 2024-25; 20 in 2023-24): `parse_html_shifts` skips `G`. The JSON shift-chart
path keeps them, because its `detailCode == 1` check doesn't catch goalies. So `shift_events`
has goalies for some games and not others. Nothing in production depends on it
(`rapm.py`, `special_teams.py` and `line_combinations.py` all take 5v5 from shot
`situation_code`), but any future shift-based strength-state logic must not assume goalie
rows exist. This backtest's first run did assume it, and got a spurious ~20-point accuracy
drop on 2025-26.
