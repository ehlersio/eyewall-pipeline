# HockeyTech / PWHL API — Internal Reference

Unofficial, reverse-engineered notes on the HockeyTech `statviewfeed` endpoint
that powers thepwhl.com. This is **not** an official or published API — PWHL
doesn't publish one. This doc exists so EyeWall doesn't re-discover the same
gotchas across sessions, and to have one place that reflects what's actually
confirmed against real responses rather than assumed.

**Internal use only for now** — see the 2026-07-04 conversation log for the
reasoning (unclear ToS standing, risk of increased traffic drawing attention
to an endpoint EyeWall depends on for production, maintenance burden of a
public-facing doc). Revisit "publish or contribute upstream" later once this
has held up over time.

Every entry below is dated and tied to the game_id it was confirmed against,
so entries can be re-verified rather than trusted blindly as data evolves.

---

## Base setup

**Two different base URLs are both in active use in the codebase** — this is
a real inconsistency, not a typo, so don't "fix" one to match the other
without testing first:

- `pwhl_stats.py`: `https://lscluster.hockeytech.com/feed/`
- `pwhl_shot_events.py` / `pwhl_pbp_events.py`: `https://lscluster.hockeytech.com/feed/index.php`
  (a code comment notes `index.php` is *required* for `gameCenterPlayByPlay`
  specifically — untested whether the other views require it too)

**Auth / identity params** (sent on every request):
```
key: 446521baf8c38984
client_code: pwhl
lang: en
```
`pwhl_stats.py` also sends `site_id: "0"` and `league_id: "1"`; the PBP/shot
fetchers send `league_id: ""` instead. Both work for their respective views —
not yet confirmed whether this matters or is just historical drift.

**Headers** (sent on every request, likely required — untested without them):
```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
Accept: application/json
Referer: https://www.thepwhl.com/
```

**Response format:** Sometimes raw JSON, sometimes JSONP-wrapped
(`callback(...)`). Always strip via:
```python
text = r.text.strip()
if "(" in text:
    text = text[text.index("(") + 1 : text.rindex(")")]
data = json.loads(text)
```

**Retry pattern used throughout:** 3 attempts, exponential backoff (`2**attempt`
seconds), `timeout=20`.

**Errors:** malformed/unknown `view` returns HTTP 200 with
`{"error": "InvalidView error: <view>"}` — status code alone doesn't tell you
the request failed, always check for the `error` key.

---

## Known views

### `view=gameCenterPlayByPlay`
Play-by-play event stream for one game. Param: `game_id`.

Returns a flat list of `{event, details}` dicts. Confirmed event types:

| event | owned by | key fields in `details` |
|---|---|---|
| `shot` (with `details.isGoal`) | `pwhl_shot_events.py` | `shooter`, `goalie`, `blocker`, `shooterTeamId`, `period.id`, `time`, `isGoal`, `shotQuality`, `shotType`, `xLocation`, `yLocation` |
| `blocked_shot` | `pwhl_shot_events.py` | same shape as `shot` |
| `penalty` | `pwhl_pbp_events.py` | `game_penalty_id`, `period.id`, `time`, **`againstTeam.id`** (the penalized team — NOT `teamId`/`team_id`, those don't exist on this event type), `minutes` (decimal **string**, e.g. `"2.00"` — `int()` on it raises, must `float()` first), `description`, `ruleNumber`, `takenBy`, `servedBy`, `isPowerPlay`, `isBench` |
| `hit` | `pwhl_pbp_events.py` | `player`, `onPlayer`, `xLocation`, `yLocation` — **team field NOT verified**, currently reads generic `teamId`/`team_id` which is unconfirmed to exist (only confirmed wrong for `penalty`) |
| `faceoff` | `pwhl_pbp_events.py` | `homePlayer`, `visitingPlayer`, `homeWin` (code comment: unreliable, often always `true`) — **team field NOT verified** |
| `goalie_change` | `pwhl_pbp_events.py` | `goalie`, `team` — **team field NOT verified** |
| `stoppage`, `period_start`, `period_end`, `game_end`, `shootout` | skipped entirely | not parsed by either module |

**Critical gotcha (fixed 2026-07-04, confirmed against game 261, 2026-01-20):**
`time` in this feed is **elapsed** seconds/mm:ss within the period (0 → 20:00),
matching NHL convention. It was previously assumed/documented in this codebase
to be a countdown — that was wrong, confirmed via exact match against the
PWHL's own recap language for 4 independent goals (Turnbull 1:18, Compher
2:54, Knight 9:52, Bilka 13:49) and reconfirmed on a second game (Girard's
natural hat trick, 2025-11-22: 7:49 / 13:39 / 16:48).

**`situation_code`** stored on `pwhl_shot_events` rows is a placeholder
(`"5v5"` hardcoded, `# TODO: derive from penalty log`) — do not trust it for
anything. Real strength state has to be derived by cross-referencing
`penalty` events (or, going forward, `gameSummary`'s `properties` — see
below).

---

### `view=gameSummary`
Box-score style single-game summary. Param: `game_id`. **Case-sensitive** —
see "confirmed invalid" list below.

Confirmed top-level keys (game 261, 2026-01-20):
`details`, `referees`, `linesmen`, `scorekeepers`, `mostValuablePlayers`,
`hasShootout`, `homeTeam`, `visitingTeam`, `periods`, `penaltyShots`,
`featuredPlayer`.

`homeTeam`/`visitingTeam` — not yet explored in depth (likely full box-score
stat lines; worth a dedicated inspection pass).

`periods[]` — **this is the important one, not yet wired into any pipeline
module as of 2026-07-04:**
```
periods[].info: {id, shortName, longName}
periods[].stats: {homeGoals, homeShots, visitingGoals, visitingShots}
periods[].goals[]:
    game_goal_id       — real unique ID per goal (cleaner than our
                          x_raw/y_raw dedup workaround in pwhl_shot_events.py)
    team: {id, name, city, nickname, abbreviation, logo, divisionName}
    period: {id, shortName, longName}
    time                — elapsed, same convention as PBP (see above)
    scorerGoalNumber    — running goal count for that player
    scoredBy            — full player object
    assists[]           — 0-2 full player objects (primary, secondary)
    assistNumbers[]     — running assist counts, parallel to assists[]
    properties:
        isPowerPlay
        isShortHanded    — GROUND TRUTH shorthanded flag, no derivation needed
        isEmptyNet
        isPenaltyShot
        isInsuranceGoal
        isGameWinningGoal
    plus_players[] / minus_players[]  — full on-ice player objects
```

**No shot x/y coordinates in this view** — it supplements
`pwhl_shot_events.py`, doesn't replace it.

**Planned integration (not started — see memory for full scope):** merge
`gameSummary` goals into `pwhl_shot_events` rows (match on period + time +
shooter), adding `assist1_id`, `assist2_id`, `is_power_play`,
`is_short_handed`, `is_empty_net`, `is_game_winning_goal` columns. This
unblocks PWHL season/career points milestones (blocked since — no assist
data existed anywhere before this) and would let `is_short_handed` replace
the hand-built penalty-window SH-goal detector in `pwhl_milestones.py`
(`detect_shorthanded_goals`), which has documented edge-case gaps (OT,
penalty cancellation, etc.) that HockeyTech's own flag doesn't have.

---

### `view=roster`
Params: `team_id`, `season`. Used by `pwhl_stats.py::fetch_roster`. Shape not
documented here yet — pull from `pwhl_stats.py` directly for now.

### `view=players`
League-wide stats leaderboard, used for both skaters and goalies via the
`position` param. Confirmed params (from `pwhl_stats.py`):
```
season, context="overall", position="skaters"|"goalies",
rookie="false", limit=<int>, sort=<field, e.g. "points"|"wins">
```

### `view=teams`
Standings/team stats. Confirmed params (from `pwhl_stats.py`):
```
season, context="overall", groupTeamsBy="division", sort="points",
special="false"|"true"  (true = PP%/PK% special-teams variant, fetched
                          as a second call and merged),
conference_id="-1", division_id="-1"
```

### `view=schedule`
Season schedule/results. Confirmed params (from `pwhl_stats.py`):
```
season, month="0"  (0 = full season, unconfirmed whether 1-12 filters
                     by calendar month), team_id="-1"  (-1 = all teams)
```

---

## Confirmed INVALID view names (tried 2026-07-04, game 261)

Saves re-trying these — all returned `InvalidView error`:
`gamesummary` (lowercase — case matters!), `boxscore`, `gameCenterBoxscore`,
`scoringSummary`, `gameCenterScoringSummary`, `gameCenterGameSummary`.

---

## Reference constants (from `pwhl_stats.py` / project memory)

```
HOCKEYTECH_KEY = "446521baf8c38984"
CLIENT_CODE    = "pwhl"

Team IDs: BOS=1, MIN=2, MTL=3, NY=4, OTT=5, TOR=6, SEA=8, VAN=9
Season IDs: regular=1/5/8, playoffs=3/6/9, showcase=2 (pre-launch
            exhibition — excluded from analytics), preseason=4/7
PWHL_CURRENT_SEASON = 8 (as of 2025-26 season; flip each October)
```

---

## TODO — comprehensive endpoint mapping (separate task, not started)

Everything above was discovered opportunistically while building specific
features, not via a systematic sweep. A dedicated pass would:

- Enumerate `homeTeam`/`visitingTeam` shape in `gameSummary`
- Try view names beyond goal/box-score guesses (standings variants, player
  landing pages, draft, transactions, injury reports — anything
  thepwhl.com's own site calls, inspectable via browser dev tools Network
  tab rather than guessing)
- Confirm the real team-ID field for `hit`/`faceoff`/`goalie_change` events
  in `gameCenterPlayByPlay` (only `penalty` has been verified so far —
  same class of bug as the `againstTeam.id` fix, may well affect these too)
- Document whether `feed/` vs `feed/index.php` matters for views beyond
  `gameCenterPlayByPlay`
- Confirm `month` param behavior on `schedule` (currently only `"0"` used)

Not urgent, but worth doing once before the next big feature build rather
than continuing to discover gaps mid-feature the way `isShortHanded` came up
today.
