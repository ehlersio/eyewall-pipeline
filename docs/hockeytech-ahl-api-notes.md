# HockeyTech / AHL API — Internal Reference

Unofficial, reverse-engineered notes on the HockeyTech feed that powers
theahl.com. Same vendor/backend as `docs/hockeytech-api-notes.md` (PWHL),
but a different client with real shape differences confirmed below — don't
assume the two are interchangeable beyond the base request pattern.

Every entry below is dated and tied to real live responses, not assumed
from the PWHL doc or from third-party packages (`fastRhockey` /
`sportsdataverse-py`), which were used to find the config below but then
independently re-verified against the real feed.

---

## Base setup

```
base_url:    https://lscluster.hockeytech.com/feed/index.php
key:         ccb91f29d6744675
client_code: ahl
site_id:     3
league_id:   4
lang:        en
```

Still exposed client-side (confirmed 2026-08-29): theahl.com's own page
source loads `lscluster.hockeytech.com/statview-1.4.1/js/client/ahl/base.r3.js`,
which sets `var appKey = 'ccb91f29d6744675'; var clientCode = "ahl";`
directly — if this ever needs re-checking, that script is the fastest way,
no third-party package needed (unlike ECHL, see that league's future notes
doc if/when built).

**Response format:** JSONP-wrapped (`angular.callbacks._0(...)`). Same
strip-and-parse pattern as PWHL:
```python
text = r.text.strip()
if "(" in text:
    text = text[text.index("(") + 1 : text.rindex(")")]
data = json.loads(text)
```

**`feed=modulekit` vs `feed=statviewfeed` — both real, different shapes,
confirmed 2026-08-29:**
- `modulekit` responses nest everything under `SiteKit`: `{"SiteKit": {"Parameters": {...}, "<ViewName>": [...]}}`.
- `statviewfeed` responses use the `sections[].data[].row` shape PWHL's
  `extract_rows()` already handles — same helper works unchanged for AHL.

**`gc` feed uses `tab=`, not `view=`** — confirmed same HockeyTech-wide
quirk documented in the PWHL notes (sending `view=` to `gc` returns
"Undefined Tab").

---

## Known views (AHL-specific findings — assume PWHL's documented views/params
otherwise transfer, since it's the same backend, unless noted different below)

### `feed=modulekit&view=seasons`
No params beyond auth. Returns `SiteKit.Seasons[]`: `season_id`,
`season_name`, `shortname`, `career` ("1"/"0"), `playoff` ("1"/"0"),
`start_date`, `end_date`. Seasons list back to 2004-05 (season_id 46).
**Always resolve the current season_id from this live, never hardcode** —
confirmed as of 2026-08-29: season_id 94 = 2026-27 Regular Season (starts
Oct 2, no games yet), 90 = 2025-26 Regular Season (most recent completed),
92 = 2026 Calder Cup Playoffs.

### `feed=modulekit&view=teamsbyseason&season=<season_id>`
**Param is `season`, not `season_id`, for this view** (see roster below,
which is the opposite). Returns `SiteKit.Teamsbyseason[]`: `id`, `name`,
`city`, `nickname`, `code`, `division_id`, `division_long_name`,
`division_short_name`, `team_logo_url`. 32 teams confirmed for season 94.
No `pwhl_teams`-equivalent table exists in this pipeline (PWHL doesn't
have one either — team metadata/display lives in the frontend, not here),
so this view is only used to build the hardcoded `TEAM_ID_MAP` below, the
same way PWHL's is hardcoded — not called at runtime by any ingestion
function.

### `feed=modulekit&view=roster&team_id=<id>&season_id=<season_id>`
**Param is `season_id`, not `season`, for this view — the opposite of
`teamsbyseason` above.** Sending `season` instead silently returns an
empty roster (`"Roster": [[]]`) rather than an error — confirmed the hard
way, don't repeat that mistake.

Response is `SiteKit.Roster[]`, a **flat list, not sectioned by
Forwards/Defenders/Goalies like PWHL's roster.** Real fields differ from
PWHL in two useful ways:
- `height` is hyphenated feet-inches (`"6-3"`), not PWHL's apostrophe
  format (`"5'11"`) — needs its own parse regex, not a shared one.
- `weight` is **real data** (e.g. `"195"`), unlike PWHL where it's always
  `"0"` — worth ingesting for AHL even though PWHL's `pwhl_players` schema
  deliberately omits it.

Other fields used: `player_id`, `first_name`, `last_name`, `position`
(direct code, e.g. `"D"` — no section-title mapping needed), `shoots`,
`birthdate`, `homeplace` (city, comma-separated with province/state —
PWHL's `hometown` field doesn't exist here), `tp_jersey_number` (same
field name as PWHL), `latest_team_id`.

### `feed=statviewfeed&view=players&season=<season_id>&position=skaters|goalies&context=overall&rookie=false&limit=<n>&sort=<field>`
Same view/params as PWHL. Row shape mostly matches, with real differences:
- Skaters: **no `shooting_percentage`, `power_play_assists`, or
  `short_handed_assists` fields** — PWHL's `fetch_skater_stats()` reads
  all three, AHL's response simply doesn't have them. Don't `.get()` them
  expecting a real value; they're always absent, not just occasionally
  null.
- Goalies: shape matches PWHL closely (`minutes_played`, `saves`, `shots`,
  `save_percentage`, `goals_against`, `shutouts`, `wins`, `losses`,
  `ot_losses`, `goals_against_average` all present, same field names).

### `feed=statviewfeed&view=teams&season=<season_id>&context=overall&groupTeamsBy=division&sort=points&special=false|true&conference_id=-1&division_id=-1`
Same view/params as PWHL, but AHL's `wins` field is **already the total**
(regulation + OT/SO), not just regulation wins — no need for PWHL's
`regulation_wins + non_reg_wins` addition. AHL separately reports
`regulation_wins`, `ot_wins` (special=true only), and `shootout_wins`
(special=true only) if a regulation/OT/SO breakdown is ever wanted, but
the plain `wins` field is already the real total. Similarly `ot_losses`
and `shootout_losses` are both reported directly, unlike PWHL's single
combined `non_reg_losses`.

`special=true` fields (separate call, same as PWHL): `power_play_pct`,
`penalty_kill_pct`, `power_play_goals`, `power_play_goals_against`,
`short_handed_goals_for`, `short_handed_goals_against`, `power_plays`,
`times_short_handed`, `ot_wins`, `shootout_wins`.

Team codes carry the same clinch-prefix convention as PWHL (`"y - Providence Bruins"`) — strip via the same `raw.split(" - ")[-1]` pattern.

### `feed=modulekit&view=scorebar&numberofdaysback=<n>&numberofdaysahead=<n>&limit=<n>&league_id=4&season_id=<season_id>`
**This is the AHL schedule/game-log source — a different view entirely
from PWHL's `statviewfeed&view=schedule`,** found during investigation via
live network capture rather than assumed from the PWHL doc. Confirmed
gotcha: `numberofdaysback`/`numberofdaysahead` count from **today's real
date**, not from the requested `season_id`'s start — passing a `season_id`
alone does NOT scope the results to that season's own games unless the
day-window is also large enough to reach them (PWHL's own fetch pattern
uses `numberofdaysback=10000&numberofdaysahead=10000` for exactly this
reason — do the same here for a full-season pull, not a small window).

Row shape gives team IDs **directly** (`HomeID`, `VisitorID`) — no
city-name-to-team_id mapping hack needed, unlike PWHL's `fetch_game_log()`
(which has to map `home_team_city`/`visiting_team_city` through
`CITY_TEAM_MAP` because PWHL's `schedule` view doesn't give IDs directly).
Other fields: `ID` (game_id), `SeasonID`, `Date`, `GameDateISO8601`,
`HomeGoals`, `VisitorGoals`, `GameStatusString` (e.g. `"Final"`),
`venue_name`, `venue_location` (already two separate fields — no
PWHL-style pipe-split needed).

### `feed=statviewfeed&view=gameCenterPlayByPlay&game_id=<id>&league_id=`
**Real, meaningful difference from PWHL's PBP schema — confirmed across
16+ real games, both AHL and ECHL:**

| event | confirmed present | key fields |
|---|---|---|
| `shot` | yes | `shooter`, `goalie`, `shooterTeamId`, `period.id`, `time`, `isGoal` (true for ~10% of `shot` rows — see below), `shotQuality`, `shotType`, `xLocation`, `yLocation` |
| `goal` | yes, **own event type distinct from `shot`, unlike PWHL** (PWHL only has goals embedded in `shot` events with `isGoal:true`, no separate `goal` event confirmed in `docs/hockeytech-api-notes.md`) | `game_goal_id`, `team{id}`, `period.id`, `time`, `scorerGoalNumber`, `scoredBy{id,firstName,lastName,jerseyNumber,position}`, `assists[]` (0-2 full player objects), `assistNumbers[]`, `properties{isPowerPlay,isShortHanded,isEmptyNet,isPenaltyShot,isInsuranceGoal,isGameWinningGoal}` (all present as `"0"`/`"1"` strings), `plus_players[]`/`minus_players[]` (full on-ice player objects — same shape PWHL's `pwhl_goal_on_ice` sources from `gameSummary`, but available **directly on the PBP event here**, no separate gameSummary call needed), `xLocation`, `yLocation` |
| `penaltyshot` | yes | `shooter`, `goalie`, `shooter_team{id}`, `period.id`, `time`, `isGoal` — **no coordinates** (breakaway attempts aren't location-tracked). Confirmed both makes and misses present. |
| `penalty` | yes | not yet field-mapped for AHL specifically — assume PWHL's shape (`againstTeam.id`, `minutes` as decimal string) as a starting point, verify before depending on it |
| `goalie_change` | yes | not yet field-mapped for AHL specifically — assume PWHL's shape as a starting point |
| `hit`, `blocked_shot`, `faceoff` | **confirmed absent**, 16+ games sampled across both AHL and ECHL | N/A — not charted for this league at all. The box-score schema even carries `faceoff_wins`/`faceoff_attempts`/`hits` per-player fields (via `gc&tab=gamesummary`), but they're hardcoded `"0"` for every player in every game checked — not a PBP-only gap. |

**Practical consequence for ingestion:** `shot` and `goal` are a clean
disjoint pair here — a `shot` event with `isGoal:true` and the
corresponding `goal` event both exist for the same goal (confirmed: AHL
sample had 47 `shot`-with-`isGoal:true` rows and 48 `goal` rows across 8
games, ECHL had an exact 47/47 match), **but the `goal` event is a strict
superset** (same coordinates, plus assists/properties/on-ice players the
`shot` event doesn't have). Recommended ingestion: use `shot` events where
`isGoal` is falsy for the shot-attempt dataset, and `goal` events
(exclusively) for the goal dataset — no dedup logic needed, unlike PWHL's
`shot`+`isGoal` overlap-and-merge-with-gameSummary approach. This also
means **no `merge_game_summary()`-equivalent step is needed for AHL** —
everything PWHL has to fetch a second time from `gameSummary` (assists,
PP/SH/EN/GWG flags, on-ice players) is already on the PBP `goal` event
directly.

**No shift data** (`modulekit&view=gameshifts` returns
`{"home":[],"visitor":[]}` for every real game tested — 4 AHL games across
3 seasons incl. the 2025 Calder Cup Finals G5, confirmed against a live
PWHL control game which DOES return populated shifts through the
identical request shape). No TOI, no on-ice Corsi/Fenwick, no WAR/RAPM
possible from this feed for AHL.

Coordinate canvas is the same ~600×300 unit space PWHL uses (AHL 8-game
sample: x∈[37,585], y∈[12,285]) — PWHL's `transform_coords()` fold
(period-parity + `is_home`) is very likely reusable as-is. Do one visual
overlay check before fully trusting it (see `session_rink_svg_accuracy`
project memory — numeric ranges alone don't catch an axis swap).

### `feed=gc&tab=gamesummary&game_id=<id>`
Not yet fully field-mapped for AHL (used only to confirm the
hits/faceoffs-are-always-zero finding above). Assume PWHL's `gameSummary`
shape as a starting point if this is ever wired in for anything beyond
that confirmation — verify field names before depending on them, same
caution as `penalty`/`goalie_change` above.

---

## Reference constants

```
HOCKEYTECH_KEY = "ccb91f29d6744675"
CLIENT_CODE    = "ahl"
LEAGUE_ID      = 4
SITE_ID        = 3

Team IDs (32, current as of season 94 / 2026-27):
  307=HFD 309=PRO 313=LV  316=WBS 319=HER 321=MB  323=ROC 324=SYR
  327=MIL 328=GR  330=CHI 335=TOR 372=RFD 373=CLE 380=TEX 384=CLT
  389=IA  390=UTC 402=BAK 403=ONT 404=SD  405=SJ  411=SPR 412=TUC
  413=BEL 415=LAV 419=COL 437=HSK 440=ABB 444=CGY 445=CV  457=HAM

Season IDs (live-resolved, do not hardcode current — historical anchors
only, confirmed 2026-08-29):
  90 = 2025-26 Regular Season (most recent completed)
  92 = 2026 Calder Cup Playoffs
  94 = 2026-27 Regular Season (starts 2026-10-02)
  46 = 2004-05 Regular Season (earliest cleanly-listed season)
```

---

## Still open / not yet chased

- `penalty` and `goalie_change` PBP event field shapes not yet confirmed
  for AHL specifically (assumed same as PWHL, unverified).
- Historical PBP/coordinate depth beyond the current+recent seasons not
  spot-checked — treat 2004-05 as a listed-but-unverified floor for
  anything beyond box scores.
- `gc&tab=gamesummary`'s full shape (goalie log, per-period breakdown,
  three stars) not fully mapped — only checked far enough to confirm the
  hits/faceoffs-always-zero finding.
