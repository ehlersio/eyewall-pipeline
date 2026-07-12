# EyeWall Analytics Pipeline

Nightly data pipeline that populates Supabase with NHL + PWHL stats, MoneyPuck analytics, shot events, shift charts, zone starts, RAPM-derived WAR, power rankings with AI narratives, AI-generated game summaries, predictions, matchup analysis, player scouting blurbs (skaters + goalies), PWHL salary data, PWHL news, and milestone detection (hat tricks, shorthanded goals, shutouts, season/career goal and points thresholds).

## Setup

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

Required packages: `requests`, `supabase`, `scikit-learn`, `scipy`, `python-dotenv`, `pdfplumber`

### 2. Create your .env file
```bash
cp .env.example .env
```

Edit `.env`:
```
SUPABASE_URL=https://mqgasjzywoibdgxjjkux.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key_here
NHL_SEASON=20252026
PWHL_SEASON=8
PRIMARY_TEAM_ABBR=CAR
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id
CLOUDFLARE_API_KEY=your_cloudflare_api_key
WORKER_URL=https://eyewall-poller.billowing-queen-bf23.workers.dev
POLL_SECRET=your_worker_poll_secret
```

**`NHL_SEASON`/`PWHL_SEASON` are now fallbacks, not the primary source.** Both are live-resolved from the Worker's `/config/seasons` endpoint via `season_lookup.py` — see [Live Season Resolution](#live-season-resolution) below. These env vars only matter if the Worker is unreachable when the pipeline starts.

### 3. Run the pipeline
```bash
# NHL — run everything (nightly order)
python run.py

# Run individual NHL modules
python run.py nhl              # NHL stats only
python run.py shots            # Shot events (incremental)
python run.py shifts           # Shift charts (incremental)
python run.py zones            # Zone starts (incremental)
python run.py rapm             # RAPM regression only
python run.py moneypuck        # MoneyPuck WAR + percentiles only
python run.py lines            # Line combinations only
python run.py rankings         # Power rankings + AI narratives only
python run.py playoffs         # Magic/tragic numbers only (needs fresh nhl_stats data)
python run.py validate         # RAPM sanity checks

# AI pipeline
python ai_summaries.py                           # Post-game summaries
python ai_summaries.py --game 2025030414 --force # Single game, force regenerate
python ai_predictions.py                         # Pre-game predictions
python ai_scouting.py --missing                  # Missing scouting blurbs only (skaters + goalies)
python ai_scouting.py --team CAR --dry-run       # Preview prompts for one team
python ai_results_vs_process.py --missing        # Missing results-vs-process blurbs (NHL skaters only)
python ai_results_vs_process.py --team CAR --dry-run  # Preview prompts for one team
python power_rankings.py --dry-run --team CAR    # Preview prompt, no DB writes

# PWHL — run individually (no orchestrator yet)
python pwhl_stats.py 8         # 2025-26 regular season stats
python pwhl_stats.py 9         # 2025-26 playoffs stats
python pwhl_pbp_events.py      # Current season PBP events (defaults to season 8)
python pwhl_pbp_events.py 9    # Specific playoff season
python pwhl_pbp_events.py --force  # Re-ingest already-processed games
python pwhl_shot_events.py     # Shot events + gameSummary merge (assists, PP/SH/EN/GW flags)
python pwhl_shot_events.py 9   # Specific season
python pwhl_shot_events.py --backfill-goals    # Merge gameSummary onto already-ingested goal rows missing it
python pwhl_shot_events.py --backfill-goals 9  # Backfill a specific season
python pwhl_shot_events.py --game 338          # Single game (debug -- ingest + merge just this game)
python pwhl_salaries.py        # Salary scraper (PWHLPA PDF)
python pwhl_salaries.py --dry-run  # Parse only, don't upsert
python pwhl_news.py            # Fetch PWHL news and POST to Worker
```

---

## NHL Pipeline Modules

### Run order (nightly, via `run.py`)
```
nhl_stats → playoff_race → shot_events → shift_data → zone_starts → rapm → moneypuck → line_combinations → power_rankings → ai_summaries → ai_scouting → ai_results_vs_process
```

### `nhl_stats.py`
Rosters, skater/goalie/team stats, game log for all 32 teams. Accepts season argument: `python nhl_stats.py 20242025`. Runtime: ~2-3 min.

**Standings enrichment (Session 57):** `fetch_standings()` (formerly `fetch_standings_l10()`) now parses the full `standings/now` response instead of discarding everything but L10 record. For regular-season (`game_type=2`) rows it's the canonical source for `team_seasons`' points/wins/losses/ot_losses/games_played (previously duplicated from `stats/rest/en/team/summary`, joined via a hardcoded teamId->abbr map) plus new columns: `division_abbrev`, `conference_abbrev`, `wildcard_sequence`, `regulation_wins`, `clinch_indicator`. `fetch_team_stats` (the summary endpoint) is now only used for the advanced stats standings/now doesn't carry (goals, PP%/PK%, shots/game). All five new columns are `NULL` for playoff (`game_type=3`) rows — standings/now has no bracket equivalent. Requires `docs/session57_new_columns.sql` to be run in Supabase first (no migration tooling in this repo).

### `playoff_race.py` (Session 57)
Magic number / elimination calculations for the regular season, run right after `nhl_stats.py` (needs its fresh `division_abbrev`/`conference_abbrev`/`points`/`games_played`). Writes `team_seasons.{magic_number, tragic_number, clinched, eliminated}`. Full algorithm, generic `clinched`/`eliminated`/`magic_number` functions, and the V1 simplifications (no tiebreak-chain modeling, 82-game season assumption) are documented in the module's own docstring — read that before changing the math. `tragic_number` is this module's own mirror of `magic_number` (the feature spec didn't define one) — see the docstring for the reasoning. Built-in nightly validation logs (doesn't fail the job) any team where computed `clinched`/`eliminated` disagrees with the NHL's own `clinch_indicator` once populated, plus a bonus cross-check against `wildcard_sequence` for pool-membership. Validated (Session 57) against the fully-resolved 2025-26 final standings — 0/32 mismatches on both checks — and against a `game_log`-reconstructed mid-season (2026-02-15) snapshot to exercise the games-remaining forecasting math (no live 2026-27 race existed yet to spot-check against an external tracker). `python playoff_race.py` runs standalone; accepts a season argument.

Once `clinch_indicator` is populated for a team, it's ground truth from the NHL itself — computed `magic_number`/`clinched`/`eliminated` are a pre-clinch estimate only. Preferring `clinch_indicator` for display is a Worker/frontend concern, not implemented in this pipeline pass.

### `shot_events.py`
League-wide shot coordinates from PBP. Incremental. Runtime: ~2 min nightly, ~10-15 min backfill.

### `shift_data.py`
Per-player shift start/end times. Falls back to HTML shift reports when JSON API returns no data. Used by `rapm.py`. Incremental.

### `zone_starts.py`
OZ/DZ/NZ faceoff start counts per player per game. Away team zones flipped. Used by `rapm.py`.

### `score_state.py`
Per-player expected weights by score state. Used by `rapm.py` for score-state normalization.

### `rapm.py`
3-year rolling ridge regression RAPM (alpha=2500). 5v5 only. Zone-start adjusted. Signed xG differential formulation. Writes `rapm` column to `player_seasons`. See RAPM methodology section.

### `validate_rapm.py`
Internal RAPM quality checks + optional Evolving Hockey CSV correlation. Run manually after full-season pipeline. Pass threshold: r ≥ 0.85 vs EH.

### `moneypuck.py`
WAR (RAPM-derived EV component), percentile rankings, goalie GSAX, per-game xG, `team_seasons.xgf_pct`. Accepts season argument.

**`MP_URL` fix (2026-07):** used to hardcode `"2025"` directly in the MoneyPuck CSV URL, completely decoupled from `NHL_SEASON` — meaning a correct `NHL_SEASON` flip alone would NOT have fixed this fetch each October. Now derived as `MP_START_YEAR = int(str(NHL_SEASON)[:4])`, so there's exactly one place this needs to be right.

**Results-vs-process columns (Session 56, NHL only):** `player_seasons.on_ice_gf_pct` (on-ice GF% at 5v5, from MoneyPuck's `OnIce_F_goals`/`OnIce_A_goals`) and `results_vs_process_diff` (`on_ice_gf_pct` minus the existing `ev_off_pct`, which is already on-ice xGF% at 5v5 — deliberately not duplicated under a new column name). Both are `NULL` below `RESULTS_VS_PROCESS_MIN_GP` (25 games — see Session 55's investigation) so every downstream consumer just checks "is this null", not a duplicated GP comparison. PWHL is out of scope — blocked on the same shift-event gap as PWHL WAR/RAPM, revisit in October. Requires `docs/session56_new_columns.sql` to be run in Supabase first (no migration tooling in this repo).

### `line_combinations.py`
Forward lines and D pairs inferred from shift + shot events. Computes per-unit xGF% and TOI. Must run after `shift_data` and `shot_events`.

### `power_rankings.py`
32-team nightly rankings. 5 weighted normalized components + early-season roster WAR prior (tapers 15%→0% by game 20). AI narrative per team via Workers AI ("Sticks" persona). Writes to `power_rankings_narratives` (history retained for movement arrows).

**Formula:**

| Component | Weight | Source |
|-----------|--------|--------|
| Points % | 25% | `team_seasons` |
| L10 points % | 25% | NHL standings API (frontend) |
| Goal diff/GP | 20% | `team_seasons` |
| 5v5 xGF% | 20% | `team_seasons.xgf_pct` |
| Special teams avg | 10% | `team_seasons` |
| Roster WAR | 0–15% (early season) | `player_seasons.war` |

### `special_teams.py`
PP/PK unit inference from shift + shot events → `special_teams_units` table.

### `draft_ingest.py`
Live NHL draft pick polling — NHL API → Supabase + AI analysis via Worker. `--poll-picks` loops every 60s, exits code 99 when all 224 picks complete. `--sync-pick-order` (Session 51) re-derives `draft_pick_order_2026` from `/draft/picks/{year}/all` — the NHL API's authoritative completed-draft results, now that the 2026 draft is over and Tankathon's projected order no longer applies to this table.

### `tankathon_ingest.py`
Draft pick order scraper. No longer scheduled against `draft_pick_order_2026` (Session 51 — see `draft_ingest.py --sync-pick-order` above); its Session 49 year-guard (PR #20) stays in the codebase and would still fire correctly if it were run. Retained for any future Tankathon-sourced use (mock draft, big board, etc.), none of which exist yet in this repo.

### AI modules (`ai_summaries.py`, `ai_predictions.py`, `ai_scouting.py`, `ai_results_vs_process.py`, `ai_persona.py`, `ai_context.py`)

**`ai_scouting.py`** — Generates AI scouting blurbs for both skaters and goalies. Skaters pulled from `player_seasons` via `get_player_context()`; goalies pulled from `goalie_seasons` via `get_goalie_context()` (new — added this offseason). Goalies get a goalie-specific prompt in `build_player_scouting_prompt()` focused on SV%, GAA, GSAX, and percentile ranks rather than the skater-centric goals/assists framing. Respects `--force`, `--missing`, and `--dry-run` flags for both skaters and goalies.

**`ai_context.py`** — Added `get_goalie_context(team, season, min_gp=5)` that pulls from `goalie_seasons` with key metrics: SV%, GAA, GSAX, GSAX/60, QS%, EV/HD/MD/PK SV%, and percentile ranks.

**`ai_persona.py`** — `build_player_scouting_prompt()` now branches on `position == 'G'` to give goalies a tailored prompt.

**`power_rankings.py`** — AI narratives now cached per-team in Worker KV using `narrative:{period}:{gameId}:{carAbbr}` key pattern so each team's perspective is independently cached.

**`ai_results_vs_process.py`** (Session 56, NHL only) — Generates "results vs. process" blurbs explaining *why* a player's on-ice goal results (`on_ice_gf_pct`) diverge from their underlying process (`ev_off_pct`), not just restating the two numbers. Pulls qualifying skaters (non-null `results_vs_process_diff` — moneypuck.py's GP≥25 guardrail is the only gate; this script never re-checks GP itself) via the new `get_results_vs_process_context()` in `ai_context.py`. Writes to a new `player_narratives` table rather than `player_scouting` — see that table's description below. Respects `--force`, `--missing`, `--dry-run`, `--team`, `--player` flags, same CLI shape as `ai_scouting.py`. Skater-only (MoneyPuck's on-ice GF/GA split doesn't exist for goalies).

**`ai_persona.py`** — new `build_results_vs_process_prompt()`, dumps the player's on-ice GF%/process xGF%/diff and an explicit over/underperforming direction, with task instructions asking Sticks to explain the *why* (finishing luck, goaltending support, sustainability) rather than just restate the numbers.

---

## Live Season Resolution

Added 2026-07 (replacing a yearly manual flip across ~8 hardcoded locations in 3 repos). `season_lookup.py` is a small shared module that reads the current NHL and PWHL season from the Worker's `GET /config/seasons` endpoint (see `seasons.js` in `eyewall-poller`), which is itself resolved live from the NHL and HockeyTech APIs and cached in KV.

```python
from season_lookup import get_nhl_season, get_pwhl_season

nhl_season = get_nhl_season()      # int, e.g. 20252026
pwhl = get_pwhl_season()           # {"season_id": 8, "season_type": "regular", "start_year": 2025}
```

**`db.py`** and **`pwhl_stats.py`** both call these at import time — `NHL_SEASON` and `PWHL_SEASON` are now the *live-resolved* values, with the `.env` values above used only as a fallback if the Worker is unreachable. `pwhl_salaries.py`'s `SEASON_LABEL` (e.g. `"2025-26"`) and `moneypuck.py`'s `MP_URL` year are both derived the same way, closing two separate bugs where those values used to be hardcoded independently of `NHL_SEASON`/`PWHL_SEASON` and could silently drift out of sync.

**PWHL-specific gotcha, found the hard way (2026-07):** `get_pwhl_season()` deliberately resolves to the most recent **regular** season, not just the most recent season of any type — because almost every `pwhl.js` Worker endpoint filters `season_type=eq.regular`, and resolving to a playoffs-type season_id (which briefly shipped and broke Cypress across every PWHL view) makes those queries return nothing at all. This means `PWHL_SEASON` is *not* the right value for everything, though — see the `fetch_roster()` note below.

**KV override escape hatch:** if live resolution ever misjudges the real season boundary (most likely risk window: the real Sept/Oct transition, which has never been observed), it can be forced without a redeploy:
```bash
wrangler kv key put --binding=CACHE "config:season:nhl:override" '"20262027"'
wrangler kv key put --binding=CACHE "config:season:pwhl:override" '{"seasonId":9,"seasonType":"regular","startYear":2026}'
```
Delete the override key(s) once live resolution is confirmed correct again.

---



All PWHL modules use HockeyTech API (no authentication required) and write to `pwhl_*` Supabase tables.

### `pwhl_stats.py`
Main PWHL stats pipeline. Accepts `season_id` argument (e.g. `8` for 2025-26 regular, `9` for 2025-26 playoffs).

**What it does:**
- `fetch_roster()` — upserts to `pwhl_players`
- `fetch_skater_stats()` — upserts to `pwhl_player_seasons`
- `fetch_goalie_stats()` — upserts to `pwhl_goalie_seasons`
- `fetch_team_stats()` — two HockeyTech calls (`special=false` + `special=true`): standings + PP%/PK%/special teams raw counts → `pwhl_team_seasons`
- `fetch_game_log()` — upserts to `pwhl_game_log` including `game_date` (parsed from `date_with_day` via `_parse_game_date()`), `venue_name`, `venue_city`

**`run_team_shot_totals()` — computes CF/CA/FF/FA from `pwhl_shot_events` joined to `pwhl_game_log` → `pwhl_team_seasons`.** Run separately via `python pwhl_stats.py --shot-totals-only [season_id]`, NOT as part of the default `run()` above (split out Session 51). It has to run *after* `pwhl_shot_events.py` ingests that night's newly-completed games in `pwhl-nightly.yml` — `run()` runs *before* that step (it needs to write a current `pwhl_game_log` first, which `pwhl_shot_events.py` itself depends on to know which games to fetch). Computing shot totals as part of the original `run()` meant `corsi_for_pct` was silently ~24-48h stale on exactly the days a game just finished — found while scoping a PWHL prediction feature that needed same-night-accurate Corsi.

**Special teams note:** HockeyTech `view=teams&special=true` returns PP%/PK% as strings like `"23.0%"`. `_parse_pct()` converts to float (0.23).

**Game date note:** HockeyTech returns `"Fri, Apr 30"` not a full ISO date. `_parse_game_date()` uses `SEASON_YEAR_MAP` to infer the year — months Sep-Dec use start year, Jan-Aug use start year + 1. `SEASON_YEAR_MAP`/`SEASON_TYPE_MAP` are hardcoded per historical season_id, but the *current* season's entry is filled in live via `season_lookup.get_pwhl_season()` (`.setdefault()`, so it never overwrites a real historical entry) — no more manual map edit needed each October for the current season specifically. Historical IDs still need a manual entry if HockeyTech ever renumbers past seasons, which hasn't happened.

**Expansion team IDs (added 2026-07):** `TEAM_ID_MAP` and `CITY_TEAM_MAP` include DET=10, HAM=11, LV=12, SJS=13, confirmed via HockeyTech's real signing data + team-filter dropdown. `find_hat_trick_candidates.py`, `get_candidate_game_info.py`, and `pwhl_milestones.py` all `import TEAM_ID_MAP` from here rather than keeping their own copy, so they picked up the new entries automatically. `pwhl_salaries.py` has its own separate `TEAM_NAME_MAP` (PWHLPA city names, not HockeyTech IDs) — updated independently, see below.

**`fetch_roster()` season_id gotcha, found 2026-07:** unlike stats (which correctly want `PWHL_SEASON`, the current *regular* season), roster data for brand-new expansion teams only exists under whatever season HockeyTech has them assigned to *right now* — during the 2026-27 preseason window, that's season **10** (`2026-27 Pre-Season`), not `PWHL_SEASON` (which resolves to `8`, the 2025-26 regular season, where DET/HAM/LV/SJS didn't exist). `run()` currently passes the same `season_id` to every fetch step including `fetch_roster()`, so a normal pipeline run won't backfill a new expansion team's roster until it's called explicitly against the season where HockeyTech actually has that data:
```python
from pwhl_stats import fetch_roster
fetch_roster(sb, "10")
```
`pwhl_players` has no season dimension at all (`on_conflict="player_id"` — one row per player, current team assignment only), so this is always safe to re-run and won't create duplicates or touch any other table. If this comes up again for a future expansion wave, worth considering whether `run()` should call `fetch_roster()` with the bootstrap's raw `current_season_id` instead of `PWHL_SEASON` by default, rather than needing a manual one-off call each time.

**Timing note:** the first attempt at this backfill (2026-07-05) silently only partially succeeded — Detroit got 2 of 15 players, the other three got 0 — not from a code bug (parsing and the JSONP unwrap both checked out fine against the raw response), but because HockeyTech's own roster data for these brand-new teams was still being populated at that exact moment. Re-running the same call a bit later succeeded completely. Worth trying again before assuming a code bug if this happens with some future expansion wave.

**Backfill:**
```bash
python pwhl_stats.py 1   # 2023-24 regular
python pwhl_stats.py 3   # 2023-24 playoffs
python pwhl_stats.py 5   # 2024-25 regular
python pwhl_stats.py 6   # 2024-25 playoffs
python pwhl_stats.py 8   # 2025-26 regular
python pwhl_stats.py 9   # 2025-26 playoffs
```

### `pwhl_pbp_events.py`
Ingests PWHL PBP events (faceoffs, hits, penalties, goalie changes) from HockeyTech. Incremental by default — skips already-processed games.

```bash
python pwhl_pbp_events.py          # Current season (defaults to PWHL_SEASON env or "8")
python pwhl_pbp_events.py 9        # Specific season
python pwhl_pbp_events.py --force  # Re-ingest all games
python pwhl_pbp_events.py --game 338  # Single game (debug)
```

**Important:** `PWHL_SEASON` env var must be non-empty or script defaults to `"8"`. If the GH Actions secret is empty, the default applies correctly via `.strip() or "8"`.

**Not yet part of live season resolution (as of 2026-07):** unlike `pwhl_stats.py`/`pwhl_salaries.py`/`db.py`, this file wasn't touched during the season-resolution rollout — it likely still reads `PWHL_SEASON` directly rather than via `season_lookup.get_pwhl_season()`. Worth checking before assuming it picks up the live-resolved season automatically.

### `pwhl_shot_events.py`
Ingests PWHL shot coordinates from HockeyTech PBP. Writes to `pwhl_shot_events` with `x_norm`, `y_norm`, `event_type`, `shooter_id`, `team_id`, `period_id`, `time_seconds`.

**Coordinate note:** `x_norm` is inverted vs NHL convention (positive = defending end). Frontend negates x before folding to attacking half. A pipeline-level fix is deferred.

**gameSummary merge (added Session 34):** After shot events are ingested for a game, a second fetch against `statviewfeed/gameSummary` pulls `periods[].goals[]`, which carries real assists (full player objects) and ground-truth per-goal flags the PBP feed doesn't have. Each gameSummary goal is matched to its existing `pwhl_shot_events` goal row on `(game_id, period_id, time_seconds, team_id, shooter_id)` and that row is updated in place with:
- `assist1_id`, `assist2_id` — primary/secondary assist, NULL if unassisted
- `is_power_play`, `is_short_handed`, `is_empty_net`, `is_game_winning_goal` — ground truth, supersedes any heuristic derivation
- `game_goal_id` — HockeyTech's own unique-per-goal ID (reference only, not used as a dedup key)

This unblocked PWHL season/career points milestones and lets `pwhl_milestones.py` use the ground-truth `is_short_handed` flag instead of its old penalty-window heuristic.

**Gotcha (fixed Session 34):** gameSummary's `properties` booleans (`isPowerPlay`, `isShortHanded`, etc.) come through as the **strings** `"true"`/`"false"`, not JSON booleans — a naive `bool(val)` marks every goal `true` for every flag, since `bool("false")` is `True` in Python for any non-empty string. `_gs_parse_bool()` handles this explicitly. Worth checking any other HockeyTech boolean field before trusting a bare `bool()` call on it.

```bash
python pwhl_shot_events.py                     # Ingest current season, merge gameSummary for newly-ingested games
python pwhl_shot_events.py 9                    # Specific season
python pwhl_shot_events.py --backfill-goals     # Merge gameSummary onto ALREADY-ingested goal rows missing it
python pwhl_shot_events.py --backfill-goals 9   # Backfill a specific season
python pwhl_shot_events.py --game 338           # Single game_id (debug -- ingest + merge just this game)
```

**Penalty shots moved out (Session 42):** penalty-shot goals are NOT ingested here anymore — `extract_gamesummary_goals()` explicitly skips any goal with `isPenaltyShot=true` rather than trying to match it against a `pwhl_shot_events` row that will never exist (penalty shots have no coordinates at all, confirmed live for both makes and misses; see `pwhl_penalty_shots.py`). `is_penalty_shot` remains a column on this table but will only ever read `false` going forward — likely dead weight, left in place rather than dropped this session.

### `pwhl_game_boxscore.py` (added Session 41, wired into nightly Session 50)
Ingests `gameSummary`'s `homeTeam`/`visitingTeam.skaters[]`/`goalies[]` — full per-player, per-game stat lines (TOI, hits, blocked shots, faceoffs, etc.) that don't exist anywhere else in the pipeline. Writes to `pwhl_skater_game_box` / `pwhl_goalie_game_box`, one row per player per game. Independent fetch from `pwhl_shot_events.py`'s gameSummary merge (that one reads `periods[].goals[]` for per-goal data; this one reads `homeTeam`/`visitingTeam` for full box-score lines).

Was manual-only from Session 41 until Session 50 added it to `pwhl-nightly.yml` — like every other nightly PWHL step, the default (no season arg) invocation only sweeps the live-resolved **regular** season (`resolvePWHLSeason()` deliberately prefers "most recent regular" over "most recent of any type"), so a completed playoff season needs an explicit manual backfill, same as `pwhl_shot_events.py`/`pwhl_pbp_events.py`.

```bash
python pwhl_game_boxscore.py            # Ingest current (live-resolved regular) season
python pwhl_game_boxscore.py 9          # Specific season (e.g. a completed playoffs)
python pwhl_game_boxscore.py --game 338 # Single game_id (debug)
```

### `pwhl_penalty_shots.py` (added Session 42)
Ingests penalty shots (makes AND misses) from `gameSummary`'s `penaltyShots.homeTeam[]`/`visitingTeam[]` — not the PBP `"penaltyshot"` event and not `periods[].goals[]` (which only has goals, so misses are invisible there). Confirmed via a full scan of all 329 completed games: 9 games had a penalty shot, only 1 was a goal (game 277) — misses dominate 8-to-1. No coordinate data exists for these events at all, on a make or a miss, so `pwhl_penalty_shots` has no x/y columns and these rows are never written to `pwhl_shot_events` (a coordinate-based shot-map table).

```bash
python pwhl_penalty_shots.py            # Ingest current season, mark no-penalty-shot games skipped
python pwhl_penalty_shots.py 9          # Specific season
python pwhl_penalty_shots.py --game 277 # Single game_id (debug)
```

### `pwhl_goal_on_ice.py` (added Session 42)
Ingests `gameSummary`'s `periods[].goals[].plus_players[]`/`minus_players[]` — the full on-ice skater roster (by team) at the moment of each goal — one row per `(game_goal_id, player_id)` in `pwhl_goal_on_ice`. Convention (empirically validated against `pwhl_skater_game_box.plus_minus`, full historical backfill, 10,669/10,669 player-games matched): summing `on_ice_for` (+1)/not (-1) across every goal **except power-play goals** reproduces HockeyTech's own `plusMinus` exactly — short-handed, empty-net, and penalty-shot goals all count toward it, only power-play goals are excluded. Each row carries `is_power_play`/`is_short_handed`/`is_empty_net`/`is_penalty_shot` directly so consumers don't need to join back to `pwhl_shot_events`.

This is goal-scoped, not continuous shift data — it does **not** change the WAR/RAPM October-2026 blocker calculus (see "PWHL Analytics Roadmap" below) and is too coarse a signal (goals are rare relative to total ice time) to substitute for real line-combination detection the way `line_combinations.py` does for NHL.

`pwhl_on_ice_differential.py` is the first consumer: computes each player's on-ice goals-for/against split (not just the net `+/-` number `pwhl_player_seasons`/`pwhl_skater_game_box` already have) for a season. Currently a report/script, not yet a persisted table or frontend surface — see its docstring.

```bash
python pwhl_goal_on_ice.py            # Ingest current season
python pwhl_goal_on_ice.py 9          # Specific season
python pwhl_goal_on_ice.py --game 277 # Single game_id (debug)
python pwhl_on_ice_differential.py 8  # Print GF/GA leaderboard for a season
```

### `pwhl_salaries.py`
Scrapes PWHLPA salary guide PDF and upserts to `pwhl_salaries`.

```bash
python pwhl_salaries.py            # Download latest PDF and upsert
python pwhl_salaries.py --dry-run  # Parse only, print matches, no upsert
python pwhl_salaries.py --pdf path/to/local.pdf  # Use local PDF (skip download)
```

**How it works:**
1. Fetches `https://www.pwhlpa.com/salary-guide` to find current PDF URL
2. Downloads PDF, parses with `pdfplumber`
3. Matches players to `pwhl_players` by name (with alias map for legal vs nickname mismatches)
4. Upserts to `pwhl_salaries` on `(first_name, last_name, season)`

**Name alias map** (in `NAME_ALIASES` dict): Abigail→Abby Boreen, Jennifer→Jenn Gardiner, Gabrielle→Gabbie Hughes, Abigail→Abbey Levy, Kimberly→Kim Newell. Update if new mismatches appear.

**`SEASON_LABEL` (fixed 2026-07):** used to be a separately hardcoded `"2025-26"` string, decoupled from `PWHL_SEASON` — same bug shape as `moneypuck.py`'s old `MP_URL`. Now derived from `season_lookup.get_pwhl_season()['start_year']` (e.g. `2025` → `"2025-26"`). This feeds the Supabase upsert's conflict key (`first_name,last_name,season`), so getting it right matters for correctness, not just cosmetics.

**Expansion team cities (added 2026-07):** `TEAM_NAME_MAP` and the `_parse_text_page()` regex fallback both include Detroit=10, Hamilton=11, Las Vegas=12, San Jose=13. Two separate places in this file enumerate team names (the map and the regex), and both needed the update — easy to fix one and miss the other.

**2025-26 results:** 194 rows parsed, 190 matched (97.9%). 4 unmatched (Kaley Doyle, Kristyna Kaltounkova, Kimberly Newell, Megan Warrener) — in `pwhl_salaries` with `player_id = null`.

**PWHL CBA:** Average target $58,349.50/player (±10%), team ceiling ~$1.3M, increases 3%/yr through 2031.

### `pwhl_news.py`
Fetches PWHL news from RSS sources and POSTs to the Worker's `/pwhl/news/ingest` endpoint.

**Why GH Actions and not the Worker directly:** Cloudflare datacenter IPs are blocked by most RSS sources (ESPN 503, IIHF 403, Sportsnet varies). GH Actions runner IPs are not blocked.

```bash
python pwhl_news.py    # Fetch and POST to Worker
```

**Sources:** Women's Hockey Life (`womenshockeylife.com/feed`) and OurSports Central (`oursportscentral.com/feeds/l277.xml`) — added after TSN (404) and The Score (0 items) were removed. WHL requires PWHL keyword filtering; OSC is PWHL-only press releases (no filter needed). Result: 1 → 22 articles per run.

**Worker endpoint:** `POST /pwhl/news/ingest` — merges new articles with existing cached articles, deduplicates by ID, keeps top 60, stores in `pwhl:news` KV with 30-min TTL.

---

## PWHL Season ID Map

| ID | Season | Type |
|----|--------|------|
| 1 | 2023-24 | Regular |
| 3 | 2023-24 | Playoffs |
| 5 | 2024-25 | Regular |
| 6 | 2024-25 | Playoffs |
| 8 | 2025-26 | Regular |
| 9 | 2025-26 | Playoffs |
| 10 | 2026-27 | Pre-Season (current as of 2026-07; `hide_in_standings: true`, no games yet) |

IDs 2, 4, 7 are real preseason entries in HockeyTech's own `bootstrap` response (confirmed 2026-07 — they're not missing/gapped as this table previously assumed), just hidden from standings and with little-to-no game data.

**Discrepancy worth flagging, not yet resolved:** `pwhl_stats.py`'s `SEASON_TYPE_MAP` labels ID `2` as `"showcase"` (comment: "2024 Showcase, 9 games, pre-launch tournament"), but the real `bootstrap` response (confirmed 2026-07-05) names it `"2024 Preseason"` with no showcase designation. Haven't dug into which is authoritative — `SEASON_TYPE_MAP`'s comment implies specific prior research into that season, so it wasn't overwritten here without confirming. Worth checking against real 2024 game data (a genuine 9-game exhibition slate would be pretty distinguishable from a normal preseason) before changing either one.

---

## PWHL Analytics Roadmap (post-launch)

The PWHL currently has no equivalent to MoneyPuck WAR/RAPM. Building it requires:

### What we have
- ✅ `pwhl_shot_events` — coordinates, event_type, shooter_id, team_id, game_id, period, time (~6,000+ shots/season)
- ✅ `pwhl_pbp_events` — faceoffs, hits, penalties, goalie changes
- ✅ 3 seasons of data (2023-24, 2024-25, 2025-26)

### Build plan

**Step 1 — PWHL xG model** (`pwhl_xg.py`)
Train logistic regression on `pwhl_shot_events`: distance + angle → goal probability. Store per-shot xG in new `xg` column on `pwhl_shot_events`. ~6,000 shots/season is sufficient for a basic model.

**Step 2 — Shift data** (`pwhl_shift_data.py`)
HockeyTech PBP confirmed to have NO `player_change` events across all 3 seasons (checked June 2026). Cannot derive shift intervals from existing data. PWHL WAR/RAPM blocked until season 4 data becomes available in October 2026 — HockeyTech may add shift events for the expanded league.

**Correction (Session 42):** `pwhl_goal_on_ice` (goal-level on-ice rosters, see above) does **not** change this calculus, despite being "independent of the shift-derivation approach" in a narrow sense. It's goal-scoped, not continuous — it only captures on-ice composition at the instant of a goal, and goals are rare relative to total ice time, so it's far too coarse/sparse a signal to substitute for real shift intervals. Don't treat it as a lighter-weight WAR/RAPM path.

**Alternative:** Use lineup-based approach — derive approximate on-ice time from faceoff events + penalties from `pwhl_pbp_events`. Less accurate but buildable from existing data.

**Step 3 — Zone starts** (`pwhl_zone_starts.py`)
Count OZ/DZ/NZ faceoffs per player from `pwhl_pbp_events`.

**Step 4 — RAPM** (`pwhl_rapm.py`)
Ridge regression marginal xG/60 at 5v5. Mirror `rapm.py`. Needs shift data from Step 2.

**Step 5 — Surface in UI**
Add Analytics tab to `PWHLPlayerPopup`. Show CF%, FF%, xGF%, Corsi rank. Near-term alternative: surface team-level Corsi/Fenwick rankings (already in `pwhl_team_seasons`) as a League Analytics view.

**Estimated effort:** 3-4 sessions. Recommend October 2026 when new season data starts accumulating.

---

## Database Schema

### NHL Tables
| Table | Description |
|-------|-------------|
| `players` | Player master |
| `player_seasons` | Per-player stats + WAR/RAPM/percentiles |
| `goalie_seasons` | Per-goalie stats + GSAX/percentiles |
| `team_seasons` | Per-team stats + `xgf_pct` + `roster_war_score`. Regular-season rows also carry standings (`division_abbrev`/`conference_abbrev`/`wildcard_sequence`/`regulation_wins`/`clinch_indicator`, Session 57) and computed playoff race (`magic_number`/`tragic_number`/`clinched`/`eliminated`, `playoff_race.py`) — all `NULL` for playoff rows |
| `game_log` | All-team game-by-game results (one row per team per game) |
| `shot_events` | League-wide shot coordinates |
| `shift_events` | Per-player shift times |
| `zone_starts` | OZ/DZ/NZ start counts |
| `player_score_state_dist` | Score state distribution weights |
| `skipped_games` | Games skipped per pipeline module |
| `rapm_validation` | RAPM validation history |
| `game_summaries` | AI post-game summaries |
| `game_predictions` | AI pre-game predictions |
| `player_scouting` | AI scouting blurbs |
| `player_narratives` | (Session 56) AI narrative blurbs keyed on `(player_id, season, team, narrative_type)` — supports multiple future blurb types, not just `results_vs_process` (the only type written this round). Written by `ai_results_vs_process.py` |
| `game_scoring` | Goal-by-goal scoring data |
| `game_xg` | Per-game expected goals |
| `line_combinations` | Inferred lines and D pairs |
| `power_rankings_narratives` | Nightly rankings + AI narrative history |
| `special_teams_units` | PP/PK unit inference |
| `draft_rankings_2026` | NHL Central Scouting rankings |
| `draft_picks_2026` | Live/completed draft picks |
| `draft_pick_order_2026` | Pick order per team (NHL API, `draft_ingest.py --sync-pick-order` — Session 51; Tankathon-sourced before the 2026 draft concluded) |

### PWHL Tables
| Table | Description |
|-------|-------------|
| `pwhl_players` | Player master (player_id, first_name, last_name, position, team_id). **No season dimension** — `on_conflict="player_id"`, one row per player reflecting their current team assignment, not versioned historically. |
| `pwhl_player_seasons` | Per-player per-season stats (GP, G, A, PTS, shots, PP/SH/GW goals, +/-, PIM, shot_pct) |
| `pwhl_goalie_seasons` | Per-goalie per-season stats (GP, W, L, OTL, GAA, SV%, SO, saves, GA) |
| `pwhl_team_seasons` | Per-team per-season stats + PP%/PK%/special teams + Corsi/Fenwick + reg_wins/non_reg_wins |
| `pwhl_game_log` | Game results with scores, dates, venue, OT/SO flags |
| `pwhl_shot_events` | Shot coordinates (x_norm, y_norm), event_type, shooter_id, team_id, period, time; goal rows also carry `assist1_id`/`assist2_id`, `is_power_play`/`is_short_handed`/`is_empty_net`/`is_game_winning_goal`, `game_goal_id` (merged from gameSummary, Session 34 — NULL until merged). `is_penalty_shot` always `false` now (Session 42 — see `pwhl_penalty_shots` below) |
| `pwhl_pbp_events` | PBP events: faceoffs (homeWin string), hits, penalties, goalie changes |
| `pwhl_skater_game_box` | (Session 41, nightly since Session 50) Per-skater per-game box score: G/A/P, PIM, +/-, faceoff attempts/wins, shots, hits, blocked_shots, toi_seconds, position_raw/position_group, starting/status. Sourced from `gameSummary`'s `homeTeam`/`visitingTeam.skaters[]` |
| `pwhl_goalie_game_box` | (Session 41, nightly since Session 50) Per-goalie per-game box score: G/A/P, PIM, +/-, faceoff attempts/wins, toi_seconds, shots_against, goals_against, saves, starting/status. Sourced from `gameSummary`'s `homeTeam`/`visitingTeam.goalies[]` |
| `pwhl_penalty_shots` | (Session 42) Penalty shots (makes + misses), no coordinates: game_id, season_id, team_id, player_id (shooter), goalie_id, period_id, time_seconds, is_goal. Sourced from `gameSummary.penaltyShots`, not PBP |
| `pwhl_goal_on_ice` | (Session 42) On-ice skater roster per goal, one row per (game_goal_id, player_id): team_id, on_ice_for, is_power_play/is_short_handed/is_empty_net/is_penalty_shot. Sourced from `gameSummary`'s `plus_players[]`/`minus_players[]` |
| `pwhl_salaries` | Player salary data from PWHLPA PDF (first_name, last_name, player_id, team_id, salary, season) |
| `pwhl_game_summaries` | AI post-game summaries (PWHL) |
| `pwhl_game_predictions` | AI pre-game predictions (PWHL) |
| `pwhl_player_scouting` | AI scouting blurbs (PWHL) |
| `pwhl_power_rankings_narratives` | PWHL nightly power rankings + AI narrative history |
| `pwhl_seasons` | PWHL season metadata |
| `pwhl_teams` | PWHL team master. **`pwhl_players.team_id` has a foreign key constraint against this table** — a new team_id (e.g. an expansion team) must be seeded here first, or `fetch_roster()`'s upsert fails with a `23503` FK violation. Not automated; see `seed_expansion_teams.py` pattern from the 2026-07 expansion backfill if this comes up again. |
| `pwhl_shift_events` | PWHL shift events (sparse — no player_change in HockeyTech PBP; WAR blocked until Oct 2026) |
| `pwhl_skipped_games` | Games skipped per PWHL pipeline module |

---

## GitHub Actions Workflows

| Workflow | Schedule | Description |
|----------|----------|-------------|
| `nightly.yml` | 3 AM ET daily | NHL-only pipeline (`run.py` + Ruff lint) |
| `pwhl-nightly.yml` | 3:20 AM ET daily | PWHL stats/rosters, shot events, PBP events, game box scores, milestones, news — 20 min offset to avoid Supabase contention |
| `moneypuck-ingest.yml` | Nightly | MoneyPuck CSV fetch via GH runner (CF IPs blocked) |
| `reddit-ingest.yml` | Every 30 min | Reddit (32 subreddits) + SBNation atom feeds → Worker |
| `tankathon-sync.yml` | Weekly (Tue 8am ET) | `draft_pick_order_2026` sync from NHL API results (Session 51; runs `draft_ingest.py --sync-pick-order`, despite the filename — Tankathon is no longer this table's source) |
| `draft-ingest.yml` | Jun 26 + Jun 27 | Live NHL draft pick polling loop |

---

## October Season Prep

**Most of this is now automatic (2026-07)** — `NHL_SEASON`, `PWHL_SEASON`, `CURRENT_SEASON`/`PWHL_CURRENT_SEASON` in the frontend, the Worker's own internal season usage, and `MP_SEASON`/`SEASON_LABEL` all resolve live via `season_lookup.py`/`seasons.js`. See [Live Season Resolution](#live-season-resolution). What's left:

### NHL
1. ~~Update `NHL_SEASON` GH Actions secret~~ — automatic now (fallback only, safe to leave stale)
2. ~~Update `MP_SEASON` in `moneypuck.py`~~ — automatic now, derived from `NHL_SEASON`
3. Run `python tankathon_ingest.py` for new draft year — still manual, unrelated to season resolution

### PWHL
1. ~~Update `PWHL_SEASON` GH Actions secret~~ — automatic now (fallback only)
2. ~~Update `SEASON_YEAR_MAP`/`SEASON_TYPE_MAP` in `pwhl_stats.py`~~ — current season's entry fills in live now; only needed if a *historical* season_id ever needs correcting
3. ~~Update `PWHL_CURRENT_SEASON` in frontend `pwhlConfig.js`~~ — automatic now, fetched from the Worker at app boot
4. ~~Add expansion team IDs to `pwhlConfig.js`~~ — done 2026-07 (DET=10, HAM=11, LV=12, SJS=13)
5. Run `python pwhl_salaries.py` when PWHLPA publishes the new salary guide — still manual
6. Run backfill for the new season: `python pwhl_stats.py {new_season_id}` — still manual (this is a real data ingest, not a config flip)
7. **New for future expansion waves:** if HockeyTech assigns a new team_id mid-cycle again, remember the `fetch_roster()` season-mismatch gotcha above — roster data needs the literal current/preseason season_id, not `PWHL_SEASON`, and `pwhl_teams` needs the new team_id seeded before `fetch_roster()` can succeed at all (FK constraint). Also bust the Worker's KV cache for the new team+season combos *after* confirming the backfill actually succeeded, not before — busting first just repopulates the same stale/empty entry if the data isn't there yet.

---

## RAPM Methodology

True RAPM via ridge regression (alpha=2500):
- **Pool:** 3-year rolling window (~420k 5v5 shot attempts, all 32 teams)
- **Formulation:** Signed xG differential; zone-start adjusted
- **Minimum sample:** 150 min EV ice time
- **Validation:** r ≥ 0.85 vs Evolving Hockey; YoY stability r=0.90

**Known limitations:**
- Draisaitl/Makar rank anomalously low due to dominant linemate collinearity — documented artifact
- Non-primary-team players have high variance (only 2-5 games in pool)

---

## Known Limitations

- **PWHL news:** CF datacenter IPs blocked by RSS sources. GH Actions runner fetches successfully. Low volume in offseason.
- **PWHL Corsi/Fenwick:** No missed shots in HockeyTech — FF% is SOG-based proxy only.
- **`nhl_stats.py` fragile loop:** `for game_type in [2, 3]` body references `game_type` as if a parameter — works via Python scoping but fragile. Fix before next major pipeline work.
- **UTA missing from `team_seasons`:** Excluded from power rankings until their row appears.
- **RAPM linemate collinearity:** Documented in `validate_rapm.py`.
- **Transactions/Injuries:** No reliable free NHL API. Deferred pending PuckPedia.
- **Reddit ingest:** GH Actions IPs blocked by Reddit. New app registration blocked by Responsible Builder Policy. Deferred to October 2026.
- **PWHL WAR/RAPM:** Blocked — HockeyTech PBP has no `player_change` shift events across all 3 seasons (confirmed June 2026). Revisit October 2026.
- **HockeyTech boolean fields:** gameSummary's `properties` booleans arrive as strings (`"true"`/`"false"`), not JSON booleans — confirmed Session 34 via `pwhl_shot_events.py`'s gameSummary merge (a naive `bool(val)` marked every goal `true` for every flag). `gameCenterPlayByPlay`'s `isPowerPlay`/`isBench` on penalty events appear to be real JSON booleans by contrast (real `False` values already observed in production, pre-Session-34). Check any new HockeyTech boolean field against real data before trusting a bare `bool()` call on it.
- **`pwhl_milestones.py` undocumented:** This README has no section for the milestones pipeline (NHL `milestones.py` or PWHL `pwhl_milestones.py`) — pre-existing gap, not from Session 34. Worth a dedicated write-up at some point.
- **Cache-busting order matters (learned 2026-07):** busting the Worker's KV cache *before* confirming the underlying data fix has actually landed just repopulates the same stale/empty entry on the next request. Always confirm the data is correct first (direct Supabase query, or hit the Worker endpoint with a fresh/never-cached key), then bust. This bit us twice during the expansion-team rollout — once for the season-resolution fix, once for the roster backfill.
- **HockeyTech `bootstrap` feed type:** it's `feed=statviewfeed`, not `feed=modulekit` — the latter returns a 200 OK with no real payload (`{"SiteKit":{"Undefined":"Undefined Tab bootstrap"}}`), which silently masqueraded as a fallback-triggering failure for a while before being caught. If a URL for this endpoint looks like it's built from a written description rather than a captured real request, verify it against actual DevTools traffic before trusting it.
- **One-off scripts in this repo:** `seed_expansion_teams.py` and `diagnose_roster_fetch.py` were one-time tools for the 2026-07 expansion backfill — safe to delete once no longer needed, not part of the regular pipeline. `test_season_lookup.py` is a real, permanent pytest suite — keep it.
