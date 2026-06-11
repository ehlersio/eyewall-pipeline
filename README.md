# EyeWall Analytics Pipeline

Nightly data pipeline that populates Supabase with NHL stats, MoneyPuck analytics, shot events, shift charts, zone starts, and RAPM-derived WAR.

## Setup

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

Required packages: `requests`, `supabase`, `scikit-learn`, `scipy`

### 2. Create your .env file
```bash
cp .env.example .env
```

Edit `.env`:
```
SUPABASE_URL=https://mqgasjzywoibdgxjjkux.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key_here
NHL_SEASON=20252026
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
python run.py validate         # Internal RAPM sanity checks (run after rapm.py)
python run.py validate eh.csv  # RAPM vs Evolving Hockey CSV (quarterly)

# Backfill prior seasons — nhl_stats.py and moneypuck.py accept a season argument
python nhl_stats.py 20242025   # populate player_seasons rows for a prior season
python moneypuck.py 20242025   # populate WAR/percentiles for a prior season
```

## Pipeline modules

### Run order (dependencies matter)

```
nhl_stats -> shot_events -> shift_data -> zone_starts -> score_state -> rapm -> moneypuck
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
- Writes analytics columns to `player_seasons` and `goalie_seasons`
- Accepts optional season argument: `python moneypuck.py 20242025`
- Runtime: ~30-60 seconds

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

# MoneyPuck WAR + percentiles
python moneypuck.py
```

## Database schema

| Table | Description |
|-------|-------------|
| `players` | Player master (id, name, position) |
| `player_seasons` | Per-player per-season stats + analytics (war, rapm, percentiles) |
| `goalie_seasons` | Per-goalie per-season stats + analytics (gsax, sv%, percentiles) |
| `team_seasons` | Per-team per-season stats |
| `game_log` | CAR game-by-game results |
| `shot_events` | League-wide shot coordinates (car_game flag for CAR-specific queries) |
| `shift_events` | League-wide per-player shift start/end times |
| `zone_starts` | Per-player OZ/DZ/NZ start counts per game |
| `player_score_state_dist` | Per-player score state distribution weights (used by rapm.py) |
| `skipped_games` | Games with no source data, per pipeline, to avoid retrying |
| `rapm_validation` | RAPM validation run history (internal checks + EH correlation) |

See `schema.sql` for full definitions.

## Scheduling

GitHub Actions nightly cron runs at 3AM ET (8AM UTC) via `.github/workflows/nightly.yml`. The nightly run is incremental — only new completed games are processed by `shot_events.py`, `shift_data.py`, and `zone_starts.py`. Full runtime after backfill: ~6-10 minutes.
