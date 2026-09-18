# Projected lines backtest (production rules, lineup unknown)

`backtest_projected_lines.py` (read-only), run 2026-09-17. The two earlier backtests
([line_projection_backtest_results.md](line_projection_backtest_results.md),
[opening_night_backtest_results.md](opening_night_backtest_results.md)) chose
`projected_lines.py`'s grouping and ranking rules while being **told** who dressed. This one
calls `projected_lines.py`'s own functions and also makes it **choose the lineup**, as it has
to before a real game. These are the numbers to quote publicly.

## In-season (a team's games 2+, 2024-25 and 2025-26, 5,182 team-games)

| Lineup | Lineup recall | Linemates | Exact trios | Line 1 | F top-6 | D pairs | D top-4 |
|---|---|---|---|---|---|---|---|
| Known (ceiling) | 100% | 75.1% | 65.1% | 45.0% | 74.9% | 81.8% | 80.8% |
| Last game's lineup, no injury info | 94.4% | 71.0% | 60.3% | 44.1% | 75.0% | 75.1% | 81.4% |
| Perfect injury/scratch report | 97.3% | 72.8% | 62.6% | 44.9% | 74.8% | 79.3% | 81.0% |

Both seasons agree within half a point. **Production sits between the last two rows**:
it removes players the ESPN report lists as out, on injured reserve or suspended, and players
no longer on the live roster. It can't see healthy scratches or day-to-day players who sit.

Fill-ins: the "perfect report" row fills vacancies from anyone who dressed for the team earlier
that season. Production fills from the live roster, by recent games dressed and then NHL TOI.

## Opening night (64 openers, 2024-25 and 2025-26)

The preseason lineup is the top 12 F / 6 D of the pool by one of three rules. The backtest's
pool is **everyone who dressed for the team in preseason**, which is bigger and harder than
production's pool (the live roster: about 22 F / 15 D during camp).

| Lineup | Lineup recall | Linemates | Exact trios | Line 1 | F top-6 | D pairs | D top-4 |
|---|---|---|---|---|---|---|---|
| Known (ceiling) | 100% | 70.0% | 58.5% | 48.4% | 71.7% | 79.7% | 70.8% |
| `nhl`: last season's NHL TOI × games | 84.8% | 50.3% | 36.2% | 40.6% | 69.3% | 53.6% | 73.5% |
| `pre`: recent preseason 5v5 TOI | 79.0% | 47.0% | 34.5% | 32.8% | 69.3% | 45.8% | 65.8% |
| **`combo`: mean percentile of both** | **86.8%** | **55.4%** | **41.7%** | 40.6% | 68.1% | **59.4%** | 69.6% |

`combo` has the best recall, linemates, trios and D pairs in **both** seasons, so it's
production's `OPENING_RULE`. The "known" row ranks lines by `player_seasons` all-situations
TOI, while the opening-night backtest used 5v5 TOI from shifts. That's why Line 1 is 48.4%
here against 50.0% there.

## Takeaways

1. **In-season projections lose little to not knowing the lineup**: 71–73% of linemates and
   75–79% of D pairs, against 75% / 82% with the lineup known. About 94% of a team's skaters
   dress again the next game.
2. **Opening night is lineup-limited.** With the lineup known, preseason groupings get 70%
   of linemates. Picking the lineup from camp costs about 15 points. The live-roster pool
   should do better than this backtest's everyone-in-camp pool, but that can't be backtested:
   there's no historical record of the live camp roster. The 2026-27 opener will be the
   first real test.
3. **The picked lineup is the weakest link, so show it.** `filled_ids` marks every player
   who wasn't in the basis lineup, so the UI can flag the least certain slots.

## Suggested public wording

"Projected lines are built from each team's most recent game (in preseason, from its
preseason games). In testing on the last two seasons, they got about 7 in 10 forward linemate
pairs and 3 in 4 defense pairs right before the game."
