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
| `hit` | `pwhl_pbp_events.py` | `teamId` (confirmed real, 2026-07-05 — matches the generic guess already in code, no bug here), `player`, `onPlayer`, `xLocation`, `yLocation` |
| `faceoff` | `pwhl_pbp_events.py` | **No team-ID field at all** (confirmed 2026-07-05) — `homePlayer`, `visitingPlayer` instead; team must be derived from which side is home/visiting for the game, not read off the event. `homeWin` (code comment: unreliable, often always `true`) confirmed present as a string `"0"`/`"1"`, not boolean |
| `goalie_change` | `pwhl_pbp_events.py` | `team_id` (confirmed real, 2026-07-05 — matches the generic guess already in code, no bug here), `goalieComingIn`, `goalieGoingOut` (full player objects or `null`), `period`, `time` |
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

`homeTeam`/`visitingTeam` — **confirmed 2026-07-05** (real pull, game 261):
```
info: {id, name, city, nickname, abbreviation, logo, divisionName}
stats: {shots, goals, hits, powerPlayGoals, powerPlayOpportunities,
        goalCount, assistCount, penaltyMinuteCount, infractionCount,
        faceoffAttempts, faceoffWins, faceoffWinPercentage}
media: {audioUrl, videoUrl, webcastUrl}
coaches[]: {personId, firstName, lastName, role}
skaters[]: {info: {id, firstName, lastName, jerseyNumber, position,
                   birthDate, playerImageURL},
            stats: {goals, assists, points, penaltyMinutes, plusMinus,
                    faceoffAttempts, faceoffWins, shots, hits,
                    blockedShots, toi},
            starting, status}
goalies[]: same {info, stats, starting, status} shape as skaters, but
           stats differ: {goals, assists, points, penaltyMinutes,
                          plusMinus, faceoffAttempts, faceoffWins,
                          timeOnIce, shotsAgainst, goalsAgainst, saves}
goalieLog[]: {info, stats, periodStart, timeStart, periodEnd, timeEnd,
              result} — which goalie played which stretch of the game
seasonStats: {seasonId, teamRecord, teamStats}
```
This is a genuinely complete per-game box score — full skater/goalie stat
lines (including TOI, hits, blocked shots, faceoffs) already computed
server-side, plus coaching staff and goalie change log. **Not wired into
any pipeline module** — if a box-score feature is ever scoped, this is
the source rather than deriving the same numbers from PBP aggregation.

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

### `view=transactions` (feed=statviewfeed)
**Confirmed fully working, real data — 2026-07-04.** Standard
`sections[].data[].row` shape, identical pattern to what `extract_rows()`
already handles — no new parsing logic needed if this gets wired in.

Params sent (untested whether all are required): `season`, `game_id`
(likely irrelevant to this view — league-wide, not game-scoped).

Shape:
```
sections[0] (title: "transaction_results"):
    data[].row:
        transaction_date    — "YYYY-MM-DD"
        player_name         — includes position inline, e.g. "Sanni Ahola (G)"
        division            — always "PWHL" seen so far
        team_name, team_city
        transaction_type    — "ADD" seen so far; other values unconfirmed
        transaction         — "Signed" seen so far; other values unconfirmed
        from                — empty string in all examples seen; presumably
                               populated for trades/waiver claims
    data[].prop:
        player_name: {playerLink: <player_id>, seoName: <url-safe name>}
        team_city / team_name: {teamLink: <team_id>}
sections[1] (title: "num_results"):
    data[0].row.num_results  — total count across all pages (163 at time of test)
```

**Notable discovery, not just an API-mapping note:** this pull's real signing
transactions confirm HockeyTech team_id assignments for 3 of the 4 PWHL
expansion teams — Detroit=10, Hamilton=11, Las Vegas=12. **San Jose's ID
(13) was confirmed in a later pass this same session** — see "Follow-up #3"
below. Full set: **Detroit=10, Hamilton=11, Las Vegas=12, San Jose=13.**

---

## Follow-up: real params found via browser network inspection (2026-07-04)

The automated param-guessing pass (`probe_leaders_standings.py`) got 1/19 hits
and mostly confirmed "still null." Rather than keep guessing, inspecting
thepwhl.com's own Network tab (Chrome DevTools) directly gave the real
production calls in a handful of requests — much faster than iterating
param guesses blind. **This is the recommended approach for any future
"what params does view=X need" question** — the site's own widgets already
call these endpoints correctly.

### `view=leadersExtended` (NOT `leaders`) — confirmed real, working data
The homepage league-leaders widget calls this, not `view=leaders`. `leaders`
itself may be a legacy/unused view name — real data lives here instead.

Confirmed working params (from live network capture):
```
feed=statviewfeed
view=leadersExtended
key, client_code, site_id, lang     — standard auth params
league_id=undefined                 — sent literally as the string "undefined"
                                       by the site's own JS; untested whether
                                       omitting it or sending league_id=1
                                       changes anything
season_id=9                         — NOTE: "season_id", not "season"
division=, conference=              — blank = all (untested with real values)
team_id=0                           — 0 = all teams
playerTypes=skaters,goalies         — comma-separated
skaterStatTypes=points,goals,assists
goalieStatTypes=wins,save_percentage,goals_against_average
activeOnly=0
```

Response shape:
```
{
  "skaters": {
    "<StatName>": {                 — e.g. "Points", "Goals", "Assists"
      "results": [
        {rank, player_id, jersey_number, name, team_id, team_name,
         team_code, team_logo, team_logo_small, stat_formatted,
         type_formatted, photo, photo_small, position, division}
      ],
      "sortKey": "<lowercase field name>"   — e.g. "points"
    },
    ...
  },
  "goalies": { same shape, categories: "Wins", "Save Percentage",
               "Goals Against Average" }
}
```
Confirmed populated with real players/stats in the test pull (Abby Roque,
Marie-Philip Poulin, etc. — matches real 2025-26 season leaders). Note:
some `team_logo` URLs use `lscluster.hockeytech.com/download.php?...` and
others use `assets.leaguestat.com/pwhl/logos/<id>.jpg` directly —
inconsistent across teams, both work, no functional difference noticed.

### `view=teams` with `statsType=inline` — this IS how standings actually works
No separate `standings` view is used in production. The homepage standings
widget calls the already-known `teams` view (used since Session 32/earlier
for team stats) with one additional param:
```
feed=statviewfeed
view=teams
groupTeamsBy=division
context=overall
season=8            — NOTE: "season" here, not "season_id" — inconsistent
                       with leadersExtended above, both confirmed as sent
                       by the site's own widgets on the same page load
division=1
statsType=inline     — the new param; NOT previously known
sort=points
key, client_code, league_id, lang — standard auth params
```
Response shape: standard `sections[].data[].row` pattern, same as every
other `extract_rows()`-compatible view. Confirmed real, populated data
(actual current PWHL standings, all 8 established teams, division="1" —
expansion teams DET/HAM/LV not in this response, presumably not yet
assigned to a division or not yet playing games this season).

Row fields: `rank`, `name` (includes clinch prefix, e.g. `"x - Montréal
Victoire"`, `"e - Toronto Sceptres"`), `games_played`, `points`, `wins`,
`losses`, `non_reg_wins`, `non_reg_losses`, `percentage`. Team ID comes from
`prop.name.teamLink`, same pattern as `transactions`. **`x`/`e` prefix
meaning confirmed** (see `view=bootstrap` below): `x` = clinched playoff
position, `e` = eliminated from playoff contention. Only appears on
regular-season standings — a completed-playoffs pull for the same view
(`season=9`) showed no prefix at all, and used a different field set
entirely (`regulation_wins` instead of `wins`, plus `overall_rank`,
`games_remaining`, `goals_for`, `goals_against`) — **the `teams` view's
exact column set depends on `context`/season type, don't assume one fixed
schema covers both regular season and playoffs.**

**Season discrepancy — now understood, not just flagged.** The real
standings page itself (not just the homepage widget) sends `season=9` —
i.e. the site's *own* dedicated standings page currently defaults to
Playoffs, not the regular season. Combined with `view=bootstrap` (below),
this makes sense: `bootstrap`'s `current_season_id` is `"10"`
(2026-27 Pre-Season, no games yet), so every stats widget falls back to
whichever season actually has data — right now that's 9 (Playoffs), not 8
(Regular Season) or 10. Different widgets aren't inconsistent by accident;
they're each independently picking "most recent season with real data."
**Don't hardcode `PWHL_CURRENT_SEASON` assumptions when a page seems to
default to an unexpected season — check `bootstrap`'s season list first.**

---

## Follow-up #2: browsing more real site pages (2026-07-04, same session)

Went beyond the homepage to check the standings page, a team roster page,
a player profile page, the draft page, and a team landing page — confirming
some pages use HockeyTech at all, and finding two more real views.

**Pages confirmed to make NO HockeyTech calls at all** (pure CMS content,
not worth probing further): `/en/draft` (the draft page — confirms `draft`
is genuinely not a HockeyTech view, matches the earlier invalid-view
finding), `/en/teams/<team-slug>` (team landing pages — team-specific data
lives on `/en/stats/roster/<team_id>/<season_id>` instead).

### `view=bootstrap` — config/context payload, extremely useful for future sessions
Called once per page load (params seen: `season`, `game_id`, `pageName`,
plus the usual auth params) — returns the site's live reference data in one
shot. **Worth calling this FIRST for any future "what's the current
season/team list/division setup" question, instead of guessing or
cross-referencing hardcoded constants.**

Confirmed real content (2026-07-04 pull):
- `current_league_id`, `current_season_id` (was `"10"` at pull time)
- `seasons[]` — full list with `id`, `name`, `start_date`,
  `hide_in_standings`. **Confirms season 9's real site-facing name is
  "2026 Playoffs"**, not "2025-26 Playoffs" as this doc's Reference
  Constants section (and `pwhl_stats.py`'s docstring) calls it — same ID,
  just two different naming conventions (calendar-year vs. season-span).
  Not a real mismatch, just worth knowing both names refer to the same
  season so nobody goes looking for a missing one.
- `regularSeasons[]` / `playoffSeasons[]` — pre-filtered subsets of the
  above, handy if a future pipeline module needs "just the playoff season
  IDs" without hardcoding them
- `conferences`/`divisions` (+`...All` variants with an "All" sentinel row)
  — only one conference (`"PWHL"`) and one division (`"PWHL"`) exist
  currently despite 12 teams — the league doesn't subdivide by conference/
  division yet even post-expansion
- `teams[]` — canonical team list with `id`, `name`, `nickname`,
  `team_code`, `division_id`, `logo` — **only shows the original 8
  established teams**, not the 3 confirmed expansion teams (DET/HAM/LV).
  Matches `transactions`' confirmation that those teams exist at the
  league level, but they're not yet in this particular team roster
  (makes sense — no assigned roster/division yet pre-season)
- **`standingsFooter`: `"x = Clinched Playoff Position<br/>e = Eliminated
  from playoff contention"`** — resolves the standings prefix question
  directly, straight from the site's own footer text
- `playerStatsFooter`: `"* indicates rookie<br/>x indicates inactive"` —
  same trick, resolves what `*`/`x` mean on player stat tables
- `svfConfig.player.show_draft_info: true` — confirms `view=player`
  includes draft info (see below)
- `positions[]`: `skaters`/`forwards`/`defencemen`; separate `goalies[]`:
  `qualified`/`all` — the real position-filter values, slightly different
  from what's assumed in `pwhl_stats.py`'s `players` view usage

### `view=player` (singular) — full player profile, not yet used anywhere in the pipeline
Params confirmed: `player_id`, `season_id`, `site_id`, `key`, `client_code`,
`league_id` (sent blank), `lang`, `statsType=standard`.

This is a large, genuinely rich payload — much more than a name/position
lookup:
```
info: {jerseyNumber, firstName, lastName, playerId, personId, position,
       shoots, catches, height (3 formats: plain/sans-hyphen/hyphenated),
       weight, birthDate, profileImage, teamImage,
       bio (rich HTML string — career highlights, awards, medals),
       teamName, nickName, division,
       drafts[] (empty for this player — HockeyTech's own draft-tracking
                 field, separate from PWHL's actual draft system),
       draft_type, display_drafts, commitment (college commitment, or
       false), currentTeam, suspension_games_remaining, birthPlace,
       nationality: {name, flag_image}, playerType,
       mostRecentTeam: {teamName, teamNickname, jerseyNumber, teamLogo}}
currentSeason: "<season_id>"
careerStats[]: sections split by season TYPE (e.g. "Regular Season",
       "Playoffs" as separate sections), each row per season the player
       played, full stat line: games_played, goals, assists, points,
       plus_minus, penalty_minutes, faceoff_wins/attempts/pct,
       power_play_goals, short_handed_goals, game_winning_goals,
       shootout_goals/attempts/percentage, shots, shooting_percentage,
       plus a "Total" row per section
currentSeasonStats[]: simplified single-season line
gameByGame[]: full per-game log for the player — date_played, shots,
       goals, assists, points, plusminus, penalty_minutes, faceoffsTaken,
       faceoffPercentage, pp/sh/gw goals, shootout stats,
       ice_time_minutes_seconds (TOI), hits, game (matchup string, e.g.
       "MIN @ MTL"), with game_id available via prop.game.gameLink
playerShots[]: {game_id, x_location, y_location, orientation} — a genuine
       shot-location dataset AT THE PLAYER LEVEL, independent of
       pwhl_shot_events.py's own game-level PBP-derived coordinates.
       **Coordinate range doesn't obviously match the existing
       CANVAS_W=600/CANVAS_H=300 convention** (saw x values up to 641) —
       needs its own validation pass before treating this as the same
       coordinate space as pwhl_shot_events, do not assume it lines up
media.images[]: player photo gallery with dimensions/URLs
seasons[]: same season list as bootstrap, scoped to ones this player has stats in
draftInfo[]: table SCHEMA for draft_year/draft_league/draft_team/
       draft_round/draft_rank/draft_junior_team/draft_logo — empty `data`
       for this player, structure only; a drafted player would presumably
       populate this
translations: UI label glossary (not useful for pipeline code)
```

**Not wired into any pipeline module yet — this is a discovery, not an
integration.** If a player-profile feature ever gets scoped, `careerStats`
and `gameByGame` are worth building from directly rather than deriving the
same numbers from PBP/shot-event aggregation, since they're already
computed server-side.

### Views confirmed via UI label strings, not yet tried directly
`view=bootstrap`'s `svfLang` block hints at other real views worth trying
in a future pass, based on UI labels for pages not yet visited: **Playoff
Brackets**, **Team Attendance**, **Coach Stats** / **Coach Media**,
**Person Search**, **College Commitments**, **Text Box Score** (a
different name than the already-confirmed-invalid `boxscore` guesses —
worth trying literally as `textBoxscore` or similar), **Player Media**,
**Player Awards**, **Head to Head**. None of these were probed this
session — flagged for a future pass if any becomes relevant to a feature.

### Two items resolved 2026-07-05 via local script (browser had hit a size limit)
`homeTeam`/`visitingTeam` shape and the real `hit`/`faceoff`/`goalie_change`
team fields — both closed out with a direct local pull
(`inspect_gamesummary_and_pbp_teamfields.py`, same request pattern as
`pwhl_pbp_events.py`) once the browser's text-extraction tools proved too
size-limited for the full `gameSummary` payload. See the updated
`gameSummary` section above and the `gameCenterPlayByPlay` event table for
the real shapes. Bottom line: `hit`'s `teamId` and `goalie_change`'s
`team_id` were **already correct** in the existing code's generic guess —
`penalty` was the only one of the four event types that actually had a
wrong field name. `faceoff` has no team field at all, by design.

---

## Follow-up #3: chasing the remaining hinted views (2026-07-04, same session)

Went after every view name hinted at by `bootstrap`'s `svfLang` labels from
Follow-up #2. Found four more real, working views plus one more confirmed
dead end, and resolved San Jose's team_id along the way.

**San Jose's expansion team_id is confirmed: 13.** Found directly in the
team-filter dropdown on `/en/stats` (not by digging through API responses —
just reading the page). Full expansion set now confirmed:
**Detroit=10, Hamilton=11, Las Vegas=12, San Jose=13.**

**Pages confirmed to make NO HockeyTech calls** (same pattern as `/en/draft`
from Follow-up #2): the Playoff Brackets page (`/en/stats/playoffs/<season>`)
is pure CMS content — the bracket display itself isn't API-driven, despite
`bootstrap` hinting at a "Playoff Brackets" feature.

### `view=attendanceallteams` — confirmed real, fully populated data
Params: `season_id_1`, `season_id_2` (compares two seasons side by side),
`group_by_div`, `site_id`, `limit`, plus standard auth params. Real,
populated response confirmed comparing seasons 8 vs. 5 (two completed
regular seasons) — comparing season 10 vs. 9 (current preseason vs. just-
finished playoffs) returned all zeros, presumably because preseason has no
games yet. **If ever wired in, pick two seasons that both have completed
games, not just "current" ones.**

Row shape: `team`, `capacity`, `games_played_s1`/`s2`, `attendance_s1`/`s2`
(totals), `percent_capacity_s1`/`s2`, `attendance_avg_s1`/`s2`,
`attendance_plus_minus`, `attendance_avg_plus_minus`,
`attendance_percent_change`, `capacity_percent_change`, `division`. Team ID
via `prop.team.teamAttendanceLink`. A `"Totals"` row is included alongside
per-team rows, same pattern as other `sections[].data[].row` views.

### `view=searchperson` — confirmed real, working data
The actual view behind the site's Person Search page. Params:
`search_name`, `search_active` (`active`/presumably `all` or similar),
`search_letter` (browse-by-letter, untested), `first` (pagination offset),
`limit`, plus standard auth params.

Shape: `sections[0]` (title `"search_results"`) with rows: `profile_image`
(raw `<img>` HTML string, not just a URL), `player`, `currentteam`,
`birthdate`, `birthplace`. Player ID via `prop.player.playerLinkSearch`,
team ID via `prop.currentteam.teamLinkSearch`. `sections[1]` (title
`"num_results"`) gives a total count, same pagination pattern as
`transactions`. Confirmed with a real multi-result search ("Poulin" → 2
real players, Maude Poulin-Labelle and Marie-Philip Poulin, both with real
birthdates/birthplaces/teams).

### `view=getGameSettings` — per-game feature-flag config
Params: just `game_id` plus standard auth params (no `season`/`lang`
needed). Returns a flat dict of booleans describing what this league
tracks, confirmed for game 261:
```
sport_lax: false
track_faceoffs: true, track_shots: true, track_shot_locations: true,
track_shot_location: true (both singular and plural keys present),
track_shots_blocked: true, track_shots_wide: false
track_goal_locations: false   — NOTE: shots are location-tracked but
                                 goals specifically are not, per this flag
game_center_heat_map: false   — confirms no native HockeyTech heat map
                                 feature is active for this league
track_hits: true
track_icing: false, track_offside: false, track_breakouts: false,
track_timeouts: false, track_odd_man_rush: false
   — these five are simply NOT TRACKED for this league. Even if a PBP
     event of one of these types appears in a raw feed, don't expect
     real structured data behind it.
alternate_roster_title: true, display_penalty_rule_numbers: false
show_toi_on_roster: true, show_blocked_shots_on_roster: false,
show_hits_on_roster: true, show_faceoffs_on_roster: true
hide_pts_on_team_stats: true, show_faceoffs_on_team_stats: true
```
**Worth checking this per-game before assuming a stat type has real data**
— these flags may differ by season (e.g. if tracking capability changed
over time) even though this pull only checked one game.

### `view=gameCenterPreview` — pre-game preview payload, very rich
Params: `game_id` plus standard auth params. This is a genuinely large
discovery — pre-game context that's already computed server-side and not
derived anywhere in the current pipeline:
```
homeTeam / visitingTeam:
    teamInfo: {id, name, city, nickname, abbreviation, logo,
               divisionName, conferenceName}
    teamRecord: {overall, home, visiting, past_10_games, streak}
                — each a {wins, losses, ties, OTWins, OTLosses,
                          SOLosses, formattedRecord} split
    goalsFor, goalsAgainst
    miscellaneousRecords: situational win-loss splits (leading/trailing
                after 1st/2nd, out-shooting opponent, by-goal-margin
                records) — all-zero in the test pull, unclear if these
                are genuinely uncomputed for PWHL or just empty for this
                particular game/season combo
    leadingRookie, leadingPIM: {info: player bio, stats: full stat line,
                starting, status}
    powerPlayStats / penaltyKillStats: {overall, home, visiting} splits,
                each with raw counts AND a pre-computed percentage —
                same numbers `special_teams.py` computes independently;
                worth comparing against this as a validation source
    gamesPlayed, penaltyMinutes, penaltyMinutesPerGame
    longestStreaks: {points, goals, assists} — each an array of
                {player, streak, length} for current active streaks
    previousGames[]: last 5 games, {id, date_played,
                game_date_iso_8601, home_team, homeCode, homeCity,
                home_goal_count, visiting_team, visitingCode,
                visitingCity, visiting_goal_count, winner}
    leadingScorers[]: top 5 by points, same {info, stats} shape as
                leadingRookie/leadingPIM
    lineup: {goalies: [], skaters: []} — empty in this pull (game was
                well in the past; may populate closer to actual game time
                for genuinely upcoming games, untested)
headToHeadRecords:
    homeTeam / visitingTeam, each: {previousYear, currentYear,
    previousFiveYears} — same {wins, losses, ..., formattedRecord} shape
previousMeetings[]: full game-by-game head-to-head log, {gameId,
    visitingTeamId, visitingCity, visitingScore, homeTeamId, homeCity,
    homeScore, datePlayed, game_date_iso_8601, status}
lineupPairingReport: **confirmed null across 10 real games, 2026-07-05**
    (regular season Dec 2025–Jan 2026, plus playoffs across multiple
    rounds) — either not populated for this league at all, or only
    populates in a context not tested (e.g. a genuinely upcoming/live
    game — untestable right now, preseason hasn't started). Not usable
    as a data source until/unless a populated example is found.
```
**Not wired into any pipeline module.** The `powerPlayStats`/
`penaltyKillStats` percentages in particular are worth a comparison pass
against `special_teams.py`'s own calculations before ever depending on
this — either they should match (useful validation) or a mismatch would
reveal something about how each side defines the stat.

### `view=teamsForSeason` — simple, scoped team list
Params: `season` plus standard auth params. Just a `teams[]`/`teamsNoAll[]`
pair (same shape as `bootstrap`'s team list) scoped to whichever season is
requested — confirmed season 8 only shows the original 8 teams, not the 3
expansion teams, confirming expansion teams weren't part of that season.
Minor view, mostly useful for historical "which teams existed this season"
filtering if that's ever needed.

### `textboxscore` / `textBoxScore` — tried both casings, both invalid
`bootstrap`'s "Text Box Score" label doesn't correspond to either casing as
a JSON view — `InvalidView error` for both. Likely a static report/PDF
link rather than a `statviewfeed` view; not worth further guessing without
finding the actual link the site uses for it.

### Still not tried: Coach Stats/Media, Player Media/Awards, College Commitments, Head to Head (as its own page)
`/en/stats/coach-stats` redirected to the schedule default (not a real
route under that URL) — the real route wasn't found this pass. These
remain speculative until a future session finds the actual page.

---



## Confirmed INVALID view names (tried 2026-07-04, game 261)

Saves re-trying these — all returned `InvalidView error`:
`gamesummary` (lowercase — case matters!), `boxscore`, `gameCenterBoxscore`,
`scoringSummary`, `gameCenterScoringSummary`, `gameCenterGameSummary`.

**`feed=modulekit` gotcha (comprehensive pass, 2026-07-04):** `modulekit`
does NOT validate the `view=` param — it returns HTTP 200 with a
`{"SiteKit": {...}}` body for literally any view name, real or fake. The
only way to tell if a `modulekit` view is real is to check the *inner* key:
a real view gets a properly-named key (`"Roster"`, `"Schedule"`,
`"Standings"`, `"Transactions"`); a fake one gets a generic `"Undefined"`
key. **A 200 response from `modulekit` alone is not evidence a view exists.**
Of ~26 view names tried under `modulekit` this pass, only `roster`,
`schedule`, `standings`, and `transactions` got a real key — everything else
(`players`, `gameCenterPlayByPlay`, `gameSummary`, `playerstats`, `gamecenter`,
`gameCenterMatchup`, `playbyplay`, `shootout`, `draft`, `news`, etc.) came
back `"Undefined"`.

**`feed=statview` (no "feed" suffix) is entirely invalid** — every view name
tried under this returned `"Unsupported feed."` on both base URLs. Not a
real feed name, don't retry it.

**Confirmed invalid under `statviewfeed`, comprehensive pass (2026-07-04):**
`standings` (works under `modulekit` instead — see above), `playerstats`,
`teamstats`, `seasonstats`, `careerstats`, `gamecenter`, `gameCenterMatchup`,
`gameCenterLinescore`, `gameCenterLineCombinations`,
`gameCenterFaceoffComparison`, `gameCenterShotSummary`,
`gameCenterPenalties`, `playbyplay`, `pbp`, `shootout`, `streaks`,
`injuries`, `draft`, `prospects`, `awards`, `news`, `arena`, `venue`,
`officials`, `attendance`, `headtohead`, `gameSummaryV2`,
`gameSummaryExtended`.

**Confirmed real, working data (comprehensive pass, 2026-07-04):**
`leaders` and `transactions` (both `statviewfeed`) — see sections above.
`transactions` in particular is fully populated and ready to wire in;
`leaders` has confirmed field names but needs additional params to return
real values (see "partially-confirmed" section above).

**Confirmed aliases:** `feed/` and `feed/index.php` produced identical
results for every view/feed combination tried this pass — no behavioral
difference found between them beyond the one already-documented requirement
that `gameCenterPlayByPlay` needs `index.php` specifically.

---

## Reference constants (from `pwhl_stats.py` / project memory)

```
HOCKEYTECH_KEY = "446521baf8c38984"
CLIENT_CODE    = "pwhl"

Team IDs: BOS=1, MIN=2, MTL=3, NY=4, OTT=5, TOR=6, SEA=8, VAN=9
Expansion team IDs (fully confirmed 2026-07-04):
            DET=10, HAM=11, LV=12 (via `transactions` view),
            San Jose=13 (via team-filter dropdown on /en/stats)
Season IDs: regular=1/5/8, playoffs=3/6/9, showcase=2 (pre-launch
            exhibition — excluded from analytics), preseason=4/7
            (site's own naming uses calendar-year, e.g. season 9 =
            "2026 Playoffs" per `view=bootstrap` — same ID, different
            label convention than this doc's "2025-26 Playoffs")
PWHL_CURRENT_SEASON = 8 (as of 2025-26 season; flip each October)
```

---

## TODO — comprehensive endpoint mapping

**Comprehensive pass done 2026-07-04** (script: `hockeytech_endpoint_mapper.py`,
216 combinations tried across 2 base URLs × 3 feed names × ~36 view names),
**followed by three rounds of real-site browser inspection the same day.**
Resolved:
- ~~Try view names beyond goal/box-score guesses~~ — done; found `leaders`,
  `transactions`, and `standings` (via `modulekit`), ruled out ~26 others
- ~~Document whether `feed/` vs `feed/index.php` matters for views beyond
  `gameCenterPlayByPlay`~~ — confirmed identical for every view tried
- ~~Find the right extra params to get real data out of `leaders` and
  `standings`~~ — resolved: real views are `leadersExtended` (not `leaders`)
  and `teams`+`statsType=inline` (not a separate `standings` view at all)
- ~~Understand the `season`/`season_id` inconsistency~~ — resolved:
  `bootstrap`'s `current_season_id` (10, preseason, no data yet) isn't what
  individual widgets actually use; each falls back to whichever season has
  real data, which right now is 9 (Playoffs)
- ~~Decode the `x -`/`e -` standings prefixes~~ — resolved directly from
  `bootstrap`'s `standingsFooter`: `x` = clinched, `e` = eliminated
- ~~Enumerate more real site views~~ — found `bootstrap`, `player`,
  `attendanceallteams`, `searchperson`, `getGameSettings`,
  `gameCenterPreview`, `teamsForSeason` — 7 previously-unknown real views
  total across this session
- ~~Confirm draft data isn't in this API~~ — confirmed: `/en/draft` makes
  zero HockeyTech calls, pure CMS content. Same confirmed for Playoff
  Brackets (`/en/stats/playoffs/<season>`) — also pure CMS
- ~~Confirm San Jose's expansion team_id~~ — confirmed: 13. Full expansion
  set: DET=10, HAM=11, LV=12, San Jose=13
- ~~Enumerate `homeTeam`/`visitingTeam` shape in `gameSummary`~~ — resolved
  2026-07-05 via local script; full box-score shape now documented above
- ~~Confirm the real team-ID field for `hit`/`faceoff`/`goalie_change`~~ —
  resolved 2026-07-05: `hit`'s `teamId` and `goalie_change`'s `team_id`
  were already correct in the existing code; `faceoff` has no team field
  at all (derive from home/visiting side instead). `penalty` remains the
  only one of the four that was genuinely wrong.

Still open:
- Confirm `month` param behavior on `schedule` (currently only `"0"`/`"7"`
  seen; `"7"` appeared to correspond to the current real month, suggesting
  it likely is a literal calendar-month filter, but not confirmed by testing
  a specific month against known games)
- Find the real routes for: Coach Stats/Media, Player Media/Awards, College
  Commitments, Head to Head (as a dedicated page) — `/en/stats/coach-stats`
  wasn't a real route, the actual URL pattern wasn't found this pass
- Confirm what a real `textBoxscore`-equivalent view is called, if one
  exists as JSON at all (both obvious casings are invalid — may be a static
  report/PDF instead, not a `statviewfeed` view)
- ~~Explore `gameCenterPreview`'s `lineupPairingReport` field~~ — confirmed
  null across 10 games (regular season + playoffs, multiple rounds)
  2026-07-05. Not usable unless a populated example turns up later (e.g.
  once real preseason/regular-season games are underway again)
- Validate whether `player` view's `playerShots[]` coordinate space matches
  `pwhl_shot_events.py`'s `CANVAS_W=600/CANVAS_H=300` convention before ever
  treating the two as interchangeable (saw x values up to 641 in one pull)
- Compare `gameCenterPreview`'s server-computed `powerPlayStats`/
  `penaltyKillStats` percentages against `special_teams.py`'s own
  calculations — either a useful validation check or a sign the two define
  the stat differently

Nothing left is urgent — everything remaining is speculative (routes not
yet found) or only worth chasing once a specific feature needs it.
