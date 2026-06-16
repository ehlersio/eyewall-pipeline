# EyeWall Analytics Pipeline

Nightly data pipeline that populates Supabase with NHL stats, MoneyPuck analytics, shot events, shift charts, zone starts, RAPM-derived WAR, power rankings with AI narratives, and AI-generated game summaries, predictions, matchup analysis, and player scouting blurbs.

## Setup

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

Required packages: `requests`, `supabase`, `scikit-learn`, `scipy`, `python-dotenv`

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

# Backfill prior seasons — nhl_stats.py and moneypuck.py accept a season argument
python nhl_stats.py 20242025   # populate player_seasons rows for a prior season
python moneypuck.py 20242025   # populate WAR/percentiles for a prior season
```

## Pipeline modules

### Run order (nightly, via `run.py`)

```
nhl_stats → shot_events → shift_data → zone_starts → rapm → moneypuck → line_combinations → power_rankings
```

AI pipeline runs after the data pipeline (needs fresh `player_seasons`):

```
game_scoring → ai_summaries → ai_scouting
```

AI predictions run separately on a morning cron (`ai_pipeline.yml`):

```
ai_predictions
```

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
- Power rankings prompts are built inline in `power_rankings.py` (same persona, different context structure)

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
| `player_scouting` | AI player scouting blurbs (`scouting_text`) per player per season |
| `game_scoring` | Goal-by-goal scoring data (scorer, assists, situation, score after) |
| `game_xg` | Per-game expected goals by team and situation |
| `line_combinations` | Inferred forward lines and D pairs (xGF%, TOI) per team per season |
| `power_rankings_narratives` | Nightly AI power ranking narrative per team per day (rank, prior_rank, narrative). History retained — used for movement arrows (▲/▼) in the app. |

See `schema.sql` for full definitions.

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
```

## Scheduling

GitHub Actions runs two workflows:

**`nightly.yml`** — 3AM ET (7AM UTC): Full data pipeline in order:
`nhl_stats` → `shot_events` → `shift_data` → `zone_starts` → `rapm` → `moneypuck` → `line_combinations` → `power_rankings` → `ai_summaries` → `ai_scouting`

Incremental — only new completed games are processed by shot/shift/zone modules. Full runtime after backfill: ~10-15 minutes.

**`ai_pipeline.yml`** — Two jobs:
- **Night job** (8AM UTC / 4AM ET): `ai_summaries.py` (post-game summaries for last night's games) + `ai_scouting.py --missing` (any players without blurbs)
- **Morning job** (2PM UTC / 10AM ET): `ai_predictions.py` (pre-game predictions for tonight's games)
- Manual dispatch via `workflow_dispatch` with `job` input (`night`, `morning`, or `all`)
- Also runnable locally: `python run.py ai`

All AI inference via Cloudflare Workers AI (`@cf/meta/llama-3.1-8b-instruct-fp8-fast`).

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

# Power rankings baseline (no narrative — establishes prior_rank for movement arrows)
python power_rankings.py --no-narrative
```

## Known limitations

- **UTA missing from `team_seasons`:** Utah not yet populated by `nhl_stats.py` — excluded from power rankings until their row appears.
- **Power rankings L10:** L10 points % is only available from the live NHL standings API, not from Supabase. The backend ranking (used for `prior_rank` storage) weights Points% higher to compensate. The frontend recomputes rankings with L10 from the live API call.
- **RAPM non-primary-team players:** Players on other teams only appear in 2–5 games vs the primary team per season. RAPM estimates have high variance — validation thresholds are relaxed accordingly.
- **RAPM linemate collinearity:** Draisaitl and Makar rank anomalously low due to dominant co-deployment. Documented in `validate_rapm.py` — treat as known artifact, not pipeline error.
- **Transactions / Injuries:** No reliable free NHL API endpoint. Deferred pending PuckPedia API access.
