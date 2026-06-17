# EyeWall Analytics Pipeline

Nightly data pipeline that populates Supabase with NHL stats, MoneyPuck analytics, shot events, shift charts, zone starts, RAPM-derived WAR, power rankings with AI narratives, AI-generated game summaries, predictions, matchup analysis, player scouting blurbs, special teams unit inference, and draft data.

## Setup

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

Required packages: `requests`, `supabase`, `scikit-learn`, `scipy`, `python-dotenv`, `beautifulsoup4`

### 2. Create your .env file
```bash
cp .env.example .env
```

Edit `.env`:
```
SUPABASE_URL=https://mqgasjzywoibdgxjjkux.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key_here
NHL_SEASON=20252026
PRIMARY_TEAM_ABBR=CAR
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id
CLOUDFLARE_API_KEY=your_cloudflare_api_key
WORKER_URL=https://eyewall-poller.billowing-queen-bf23.workers.dev
EYEWALL_POLL_SECRET=your_poll_secret
```

### 3. Run the pipeline
```bash
# Run everything (nightly order)
python run.py

# Run individual modules
python run.py nhl              # NHL stats only
python run.py shots            # Shot events (incremental)
python run.py shifts           # Shift charts (incremental)
python run.py shifts 20242025  # Shift charts backfill for a specific season
python run.py zones            # Zone starts (incremental)
python run.py zones 20242025   # Zone starts backfill for a specific season
python run.py rapm             # RAPM regression only
python run.py rapm 20242025    # RAPM backfill for a specific season
python run.py moneypuck        # MoneyPuck WAR + percentiles only
python run.py lines            # Line combinations only
python run.py special_teams    # Special teams unit inference only
python run.py rankings         # Power rankings + AI narratives only
python run.py validate         # Internal RAPM sanity checks (run after rapm.py)
python run.py validate eh.csv  # RAPM vs Evolving Hockey CSV (quarterly)
python run.py ai               # Run full AI pipeline (summaries + scouting + predictions)

# AI pipeline — run individually
python ai_summaries.py                          # Post-game summaries for all unprocessed games
python ai_summaries.py --game 2025030414        # Single game
python ai_summaries.py --game 2025030414 --force  # Regenerate even if exists

python ai_predictions.py                        # Pre-game predictions for today's games
python ai_predictions.py --game 2025030417 --home CAR --away VGK  # Single game
python ai_predictions.py --force                # Regenerate all upcoming games

python ai_scouting.py                           # Player scouting blurbs (all 32 teams)
python ai_scouting.py --team CAR                # One team only
python ai_scouting.py --missing                 # Only generate missing blurbs

# Power rankings — run individually
python power_rankings.py                        # All 32 teams — rankings + AI narratives
python power_rankings.py --team CAR             # One team only
python power_rankings.py --dry-run --team CAR   # Preview prompt, no DB writes
python power_rankings.py --no-narrative         # Rankings only, skip AI generation

# Draft
python draft_ingest.py --poll-picks             # Poll NHL API for live picks, insert new ones
python tankathon_ingest.py                      # Scrape Tankathon pick order → draft_pick_order_2026
python tankathon_ingest.py --dry-run            # Preview without writing to Supabase
python tankathon_ingest.py --round 1            # Scrape a specific round only

# Backfill prior seasons
python nhl_stats.py 20242025   # populate player_seasons rows for a prior season
python moneypuck.py 20242025   # populate WAR/percentiles for a prior season
```

## Pipeline modules

### Run order (nightly, via `run.py`)

```
nhl_stats → shot_events → shift_data → zone_starts → rapm → moneypuck → special_teams → line_combinations → power_rankings
```

AI pipeline runs after the data pipeline (needs fresh `player_seasons`):

```
game_scoring → ai_summaries → ai_scouting
```

AI predictions run separately on a morning cron (`ai_pipeline.yml`):

```
ai_predictions
```

Draft ingest runs on draft day only (`draft-ingest.yml`), independent of nightly pipeline.

### `nhl_stats.py`
- Fetches rosters for all 32 NHL teams
- Fetches skater, goalie, and team stats
- Fetches CAR game log
- Accepts optional season argument: `python nhl_stats.py 20242025`
- Runtime: ~2-3 minutes

### `shot_events.py` *(league-wide)*
- Fetches PBP for all 32 teams' completed games
- Extracts shot coordinates, event type, situation code, goalie in net
- Stores real team abbreviations (e.g. `BOS`, `TBL`) + `car_game` flag
- Incremental — skips already processed games
- Runtime: ~10-15 minutes per season (one-time backfill), ~2 min nightly

### `shift_data.py` *(league-wide)*
- Fetches shift charts from NHL Stats API for all league games
- Falls back to NHL HTML shift reports when JSON API returns no data
- Stores per-player shift start/end times for both teams
- Used by `rapm.py` to determine on-ice players per shot event
- Incremental — skips already processed games
- Runtime: ~10-15 minutes per season (one-time backfill), ~2 min nightly

### `zone_starts.py` *(league-wide)*
- Fetches PBP faceoff data for all league games
- Falls back to NHL HTML shift reports when JSON shift chart API returns no data
- Records offensive/defensive/neutral zone start counts per player per game
- Away team zones flipped (NHL API reports from home team perspective)
- Used by `rapm.py` for zone-start adjustment
- Incremental — skips already processed games
- Runtime: ~15-20 minutes per season (one-time backfill), ~3 min nightly

### `score_state.py`
- Computes per-player expected weights based on score state distribution
- Used by `rapm.py` for score-state adjustment
- Accepts optional season argument: `python score_state.py 20242025`

### `rapm.py`
- Builds 3-year rolling ridge regression RAPM (current + 2 prior seasons)
- 5v5 only — uses `situation_code='1551'` filter on shot events
- Zone-start adjusted (players with DZ-heavy deployment upweighted)
- Signed xG formulation — measures xG differential, not raw xG
- Writes `rapm` column to `player_seasons` for current season
- Accepts optional season argument: `python rapm.py 20242025`
- **Beta model** — score-state adjustment pending
- Runtime: ~8-10 minutes (dominated by loading shift rows)

### `validate_rapm.py`
- **Internal checks** (no external data): distribution mean, position balance, known elite player rankings, year-over-year stability (r=0.90 for 20252026 vs 20242025)
- **EH comparison** (manual, quarterly): loads a manually exported Evolving Hockey RAPM CSV, computes Pearson correlation, identifies outliers
- Writes results to `rapm_validation` Supabase table
- Pass threshold: r ≥ 0.85 vs EH; Warn: r ≥ 0.75; Fail: r < 0.75
- Not included in nightly `run.py` — run manually after full-season pipeline runs
- See `VALIDATION_STEPS.md` for step-by-step instructions

### `moneypuck.py`
- Computes WAR using RAPM as the EV component (falls back to xGoals if RAPM unavailable)
- Computes percentile rankings vs all NHL forwards/defensemen
- Computes goalie GSAX, danger-zone SV%, percentiles
- Aggregates per-game `game_xg` rows into `team_seasons.xgf_pct` (5v5 season xGF% per team) — used by power rankings
- Writes analytics columns to `player_seasons`, `goalie_seasons`, and `team_seasons`
- Accepts optional season argument: `python moneypuck.py 20242025`
- Runtime: ~30-60 seconds

### `special_teams.py`
- Infers PP and PK unit compositions from shift + shot event data
- Identifies PP1/PP2 and PK1/PK2 groupings by co-deployment frequency
- Replaces the former static `ppUnits.js` — unit data is now Supabase-backed
- Writes to `special_teams_units` table; cached in Worker KV
- Must run after `shift_data` and `shot_events`
- Runtime: ~1-2 minutes

### `line_combinations.py`
- Infers forward lines and D pairs from shift + shot event data
- Computes per-unit xGF% and TOI together
- Writes to `line_combinations` table
- Must run after `shift_data` and `shot_events`

### `power_rankings.py`
- Computes nightly 32-team power rankings using five weighted, normalised components
- Adds an early-season roster WAR prior (tapers from 15% → 0% weight by game 20)
- Generates a personalised AI narrative per team via Cloudflare Workers AI ("Sticks" persona)
- Narratives are team-specific: each references that team's rank, component breakdown, top players by WAR, and recent movement
- Accuracy rules enforced in prompt: only named players from top-5 WAR list may be mentioned, no invented stats or game results
- Writes `roster_war_score` to `team_seasons`, ranks + narratives to `power_rankings_narratives`
- History retained — one row per team per day, used for movement arrows (▲/▼) in UI
- Must run after `moneypuck` (needs fresh WAR and xGF%)
- Runtime: ~2-3 minutes (32 AI calls at ~0.5s each + Supabase reads/writes)

**Power rankings formula:**

| Component | Full-season weight | Source |
|-----------|-------------------|--------|
| Points % | 25% | `team_seasons` |
| L10 points % | 25% | NHL standings API (frontend) |
| Goal diff/GP | 20% | `team_seasons` |
| 5v5 xGF% | 20% | `team_seasons.xgf_pct` |
| Special teams avg | 10% | `team_seasons` |
| Roster WAR | 0–15% (early season) | `player_seasons.war` aggregated |

Note: L10 is only available from the live standings API, so the backend ranking (used for `prior_rank` storage) substitutes extra weight on Points% instead. The frontend recomputes rankings using L10 from the live standings call.

### `draft_ingest.py`
- Polls the NHL API for live draft picks during the NHL Draft (Jun 26–27)
- Diffs against `draft_picks_2026` in Supabase — only inserts new picks
- Generates AI analysis per pick via the Cloudflare Worker ("Sticks" persona)
- Matches picks against `draft_rankings_2026` for CS rank context
- Exits with code 99 when all 224 picks are inserted — signals the GH Actions loop to terminate
- Run via `draft-ingest.yml` workflow (polls every 60s with 6-hour timeout)

### `tankathon_ingest.py`
- Scrapes `tankathon.com/nhl/draft-order` for the 2026 draft pick order
- Parses server-rendered HTML — pick number, team, original team (traded picks), forfeited status
- Upserts all 224 picks (7 rounds × 32 teams) into `draft_pick_order_2026`
- SVG logo → team abbreviation mapping handles Tankathon naming quirks (e.g. `sj→SJS`, `nj→NJD`)
- Run via `tankathon-sync.yml` workflow (weekly Tuesdays) or manually after known pick trades
- Runtime: ~10-15 seconds

### `ai_summaries.py`
- Generates post-game summaries for completed games (both teams)
- Two Workers AI calls per game: full `summary_text` (250-400 words) + short `card_text` (50 words for export card)
- Writes to `game_summaries` table
- Incremental — skips already-generated summaries unless `--force`
- Runtime: ~2-4 seconds per game

### `ai_predictions.py`
- Generates pre-game predictions for today's upcoming games
- Two Workers AI calls per game: `prediction_text` (200-350 words) + `matchup_text` (line-by-line matchup analysis, 200-300 words)
- Uses standings, recent form, and player stats from Supabase
- Writes to `game_predictions` table
- Runtime: ~4-6 seconds per game

### `ai_scouting.py`
- Generates player scouting blurbs for all 32 teams
- One Workers AI call per player: `scouting_text` (150-250 words)
- Uses `player_seasons` RAPM/WAR/percentile data as context
- Writes to `player_scouting` table
- `--missing` flag: only generate for players without existing blurbs
- Runtime: ~1-2 seconds per player; ~30-60 min for all 32 teams

### `ai_persona.py`
- Defines the Sticks persona (system prompt) and all prompt templates
- No model calls — pure string formatters
- Functions: `build_game_summary_prompt`, `build_game_card_prompt`, `build_prediction_prompt`, `build_matchup_prompt`, `build_player_scouting_prompt`
- Power rankings and draft pick prompts are built inline in their respective modules (same persona, different context structure)

**Accuracy rules enforced in all prompts:**
- Only reference stats, scores, and player names explicitly provided in the data
- Never invent stats, scores, or outcomes
- Power rankings prompts additionally restrict: only name players from the provided TOP PLAYERS list; do not reference specific games unless listed; enforce correct season context (early/mid/late season language gated on GP count)

### `ai_context.py`
- Pulls and structures Supabase data for AI prompt input
- No model calls — pure data fetchers
- Functions: `build_game_summary_context`, `build_prediction_context`, `build_matchup_context`, `get_line_combos`, `get_scouting_blurbs`

## Database schema

| Table | Description |
|-------|-------------|
| `players` | Player master (id, name, position) |
| `player_seasons` | Per-player per-season stats + analytics (war, rapm, percentiles) |
| `goalie_seasons` | Per-goalie per-season stats + analytics (gsax, sv%, percentiles) |
| `team_seasons` | Per-team per-season stats + `xgf_pct` (5v5 xGF%, nightly) + `roster_war_score` (normalised 0–1, nightly) |
| `game_log` | CAR game-by-game results |
| `shot_events` | League-wide shot coordinates (car_game flag for CAR-specific queries) |
| `shift_events` | League-wide per-player shift start/end times |
| `zone_starts` | Per-player OZ/DZ/NZ start counts per game |
| `player_score_state_dist` | Per-player score state distribution weights (used by rapm.py) |
| `skipped_games` | Games with no source data, per pipeline, to avoid retrying |
| `rapm_validation` | RAPM validation run history (internal checks + EH correlation) |
| `game_summaries` | AI post-game summaries (`summary_text`, `card_text`) per team per game |
| `game_predictions` | AI pre-game predictions (`prediction_text`, `matchup_text`) per game |
| `game_scoring` | Goal-by-goal scoring data (scorer, assists, situation, score after) |
| `game_xg` | Per-game expected goals by team and situation |
| `player_scouting` | AI player scouting blurbs (`scouting_text`) per player per season |
| `line_combinations` | Inferred forward lines and D pairs (xGF%, TOI) per team per season |
| `special_teams_units` | Inferred PP/PK unit compositions per team per season |
| `power_rankings_narratives` | Nightly AI power ranking narrative per team per day (rank, prior_rank, narrative). History retained — used for movement arrows (▲/▼) in the app. |
| `draft_rankings_2026` | NHL Central Scouting final rankings (472 prospects across 4 categories) |
| `draft_picks_2026` | Live 2026 draft picks as announced — populated by `draft_ingest.py` on draft day |
| `draft_pick_order_2026` | 2026 pick order for all 32 teams across all 7 rounds — populated by `tankathon_ingest.py` |

See `schema.sql` for full definitions.

## GitHub Actions workflows

| Workflow | Schedule | Description |
|----------|----------|-------------|
| `nightly.yml` | 3 AM ET (7 AM UTC) | Full pipeline: nhl_stats → rapm → moneypuck → special_teams → power_rankings → ai_summaries → ai_scouting |
| `ai_pipeline.yml` | 8 AM UTC + 2 PM UTC | Night: ai_summaries + ai_scouting --missing. Morning: ai_predictions |
| `moneypuck-ingest.yml` | Nightly | MoneyPuck CSV fetch via GH runner (Cloudflare datacenter IPs blocked) |
| `reddit-ingest.yml` | Every 30 min | Reddit (32 subreddits) + SBNation atom feeds → Worker. Note: Reddit currently failing — GH Actions IPs blocked by Reddit. |
| `tankathon-sync.yml` | Weekly (Tue 8 AM ET) | Tankathon draft pick order scrape → `draft_pick_order_2026` |
| `draft-ingest.yml` | Jun 26 10:45 PM UTC + Jun 27 2 PM UTC | Live draft pick polling loop (60s interval, exits on 224 picks, 6-hr timeout) |

## Required migrations (run once in Supabase SQL editor)

```sql
-- Add xGF% and roster WAR score to team_seasons
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS xgf_pct numeric;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS roster_war_score numeric;

-- Power rankings narrative history
CREATE TABLE IF NOT EXISTS power_rankings_narratives (
  id             serial primary key,
  team           text        not null,
  season         integer     not null,
  generated_date date        not null,
  rank           integer     not null,
  prior_rank     integer,
  narrative      text,
  UNIQUE (team, season, generated_date)
);
CREATE INDEX IF NOT EXISTS prn_team_season_date
  ON power_rankings_narratives (team, season, generated_date DESC);

-- Special teams units
CREATE TABLE IF NOT EXISTS special_teams_units (
  id          serial primary key,
  team        text    not null,
  season      text    not null,
  unit_type   text    not null,  -- 'PP1', 'PP2', 'PK1', 'PK2'
  players     jsonb   not null,
  updated_at  timestamptz default now(),
  UNIQUE (team, season, unit_type)
);

-- 2026 draft tables
CREATE TABLE IF NOT EXISTS draft_rankings_2026 (
  id                  serial primary key,
  category_id         integer not null,  -- 1=NA Skaters, 2=Intl Skaters, 3=NA Goalies, 4=Intl Goalies
  final_rank          integer,
  midterm_rank        integer,
  first_name          text,
  last_name           text,
  position_code       text,
  shoots_catches      text,
  height_inches       integer,
  weight_pounds       integer,
  last_amateur_club   text,
  last_amateur_league text,
  birth_country       text,
  UNIQUE (category_id, final_rank)
);

CREATE TABLE IF NOT EXISTS draft_picks_2026 (
  pick_overall        integer primary key,
  round               integer,
  pick_in_round       integer,
  team_abbrev         text,
  prospect_first      text,
  prospect_last       text,
  position_code       text,
  last_amateur_club   text,
  last_amateur_league text,
  birth_country       text,
  height_inches       integer,
  weight_pounds       integer,
  shoots_catches      text,
  final_rank          integer,
  midterm_rank        integer,
  category_id         integer,
  ai_analysis         text,
  inserted_at         timestamptz default now()
);

CREATE TABLE IF NOT EXISTS draft_pick_order_2026 (
  pick_overall    integer primary key,
  round           integer not null,
  pick_in_round   integer not null,
  team_abbrev     text    not null,
  original_team   text,             -- null if own pick, else team who originally held pick
  forfeited       boolean default false,
  updated_at      timestamptz default now()
);

-- Enable RLS on all public tables
ALTER TABLE power_rankings_narratives ENABLE ROW LEVEL SECURITY;
ALTER TABLE game_scoring              ENABLE ROW LEVEL SECURITY;
ALTER TABLE player_scouting           ENABLE ROW LEVEL SECURITY;
ALTER TABLE game_summaries            ENABLE ROW LEVEL SECURITY;
ALTER TABLE game_predictions          ENABLE ROW LEVEL SECURITY;
ALTER TABLE draft_rankings_2026       ENABLE ROW LEVEL SECURITY;
ALTER TABLE draft_picks_2026          ENABLE ROW LEVEL SECURITY;
ALTER TABLE draft_pick_order_2026     ENABLE ROW LEVEL SECURITY;
ALTER TABLE special_teams_units       ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow public read" ON power_rankings_narratives FOR SELECT USING (true);
CREATE POLICY "Allow public read" ON game_scoring              FOR SELECT USING (true);
CREATE POLICY "Allow public read" ON player_scouting           FOR SELECT USING (true);
CREATE POLICY "Allow public read" ON game_summaries            FOR SELECT USING (true);
CREATE POLICY "Allow public read" ON game_predictions          FOR SELECT USING (true);
CREATE POLICY "Allow public read" ON draft_rankings_2026       FOR SELECT USING (true);
CREATE POLICY "Allow public read" ON draft_picks_2026          FOR SELECT USING (true);
CREATE POLICY "Allow public read" ON draft_pick_order_2026     FOR SELECT USING (true);
CREATE POLICY "Allow public read" ON special_teams_units       FOR SELECT USING (true);
```

## RAPM methodology

True Regularized Adjusted Plus-Minus via ridge regression (alpha=2500):

- **Pool:** 3-year rolling window (current + 2 prior seasons)
- **Events:** ~420k 5v5 shot attempts across all 32 teams
- **Matrix:** (n_shots × n_players), +1 for shooting team, -1 for defending team
- **Outcome y:** Signed xG — positive for alphabetically-first team, negative for the other. This measures xG *differential* so forwards and defensemen are treated symmetrically.
- **Zone-start adjustment:** Players with low OZS% (DZ-heavy) get upward weight per shot event. Weight = 1.0 + (0.50 - OZS%) × 0.5
- **Score-state adjustment:** Pending — requires `home_team` in `shot_events` table for non-CAR games
- **Minimum sample:** 150 minutes EV ice time across 3-season pool
- **Validation:** Periodic correlation check vs Evolving Hockey public RAPM (target r ≥ 0.85); YoY stability r=0.90 (742 shared players, 20252026 vs 20242025)

## One-time backfill (first run)

Run these before the first nightly run to populate historical data:

```bash
# NHL stats (populates player_seasons rows needed by rapm.py upsert)
python nhl_stats.py 20222023
python nhl_stats.py 20232024
python nhl_stats.py 20242025
python nhl_stats.py 20252026

# Shot events (league-wide, all 4 seasons)
python shot_events.py 20222023
python shot_events.py 20232024
python shot_events.py 20242025
python shot_events.py 20252026

# Shift charts (league-wide, all 4 seasons)
python shift_data.py 20222023
python shift_data.py 20232024
python shift_data.py 20242025
python shift_data.py 20252026

# Zone starts (all 4 seasons)
python zone_starts.py 20222023
python zone_starts.py 20232024
python zone_starts.py 20242025
python zone_starts.py 20252026

# Score state (all 4 seasons)
python score_state.py 20222023
python score_state.py 20232024
python score_state.py 20242025
python score_state.py 20252026

# RAPM (current season — uses all 4 seasons as pool)
python rapm.py

# MoneyPuck WAR + percentiles + xGF% aggregation
python moneypuck.py

# Special teams unit inference
python special_teams.py

# Draft order (run once before draft; re-run weekly via tankathon-sync.yml)
python tankathon_ingest.py

# Power rankings baseline (no narrative — establishes prior_rank for movement arrows)
python power_rankings.py --no-narrative
```

## Season prep (each October)

1. Update `NHL_SEASON` in the `NHL_SEASON` GitHub Actions secret
2. Update `NHL_SEASON` in `.env` for local runs
3. Run `nhl_stats.py` for the new season to seed `player_seasons`
4. Run `tankathon_ingest.py` once draft order for the new year is published
5. Run `power_rankings.py --no-narrative` to establish baseline `prior_rank` values
6. Verify `special_teams.py` produces clean output for the new season

## Known limitations

- **UTA missing from `team_seasons`:** Utah not yet populated by `nhl_stats.py` — excluded from power rankings until their row appears.
- **Power rankings L10:** L10 points % is only available from the live NHL standings API, not from Supabase. The backend ranking (used for `prior_rank` storage) weights Points% higher to compensate. The frontend recomputes rankings with L10 from the live API call.
- **RAPM non-primary-team players:** Players on other teams only appear in 2–5 games vs the primary team per season. RAPM estimates have high variance — validation thresholds are relaxed accordingly.
- **RAPM linemate collinearity:** Draisaitl and Makar rank anomalously low due to dominant co-deployment. Documented in `validate_rapm.py` — treat as known artifact, not pipeline error.
- **Reddit ingest:** All 32 subreddits currently failing — Reddit blocks unauthenticated GH Actions IPs. Deferred to October; consider OAuth or alternative source.
- **Future draft picks:** Per-team multi-year pick inventory not yet built. `tankathon_ingest.py` covers the current draft year only. PuckPedia picks tab appears to load dynamically — scraping approach TBD.
- **Transactions / Injuries:** No reliable free NHL API endpoint. Deferred pending PuckPedia API access.
- **`nhl_stats.py` loop cleanup:** `for game_type in [2, 3]` loop body references `game_type` as if it's a parameter — works due to Python scoping but flagged by Ruff; clean up before next season.
- **supabase-ecosystem Dependabot:** Jump from 2.3.4→2.31.0 deferred — verify `ClientOptions` bug resolved in 2.31.0 before merging.
