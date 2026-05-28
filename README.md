# EyeWall Analytics Pipeline

Nightly data pipeline that populates Supabase with NHL stats, MoneyPuck analytics, and shot event data.

## Setup

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 2. Create your .env file
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

Edit `.env`:
```
SUPABASE_URL=https://mqgasjzywoibdgxjjkux.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key_here
NHL_SEASON=20252026
```

The `SUPABASE_SERVICE_KEY` is in your Supabase dashboard under **Settings → API → service_role**.

### 3. Run the pipeline
```bash
# Run everything
python run.py

# Run individual modules
python run.py nhl          # NHL stats only (rosters, skater/goalie stats, team stats, game log)
python run.py moneypuck    # MoneyPuck WAR + percentiles only
python run.py shots        # Shot coordinates only (incremental — skips already processed games)
```

## What each module does

### `nhl_stats.py`
- Fetches rosters for all 32 NHL teams
- Fetches skater stats (summary + primary/secondary assist split)
- Fetches goalie stats
- Fetches team stats
- Fetches CAR game log
- Runs time: ~2-3 minutes

### `moneypuck.py`
- Downloads MoneyPuck season summary CSV (~500KB)
- Computes WAR (simplified xGoals-based)
- Computes percentile rankings vs all NHL forwards/defensemen
- Writes analytics columns to existing `player_seasons` rows
- Runs time: ~30-60 seconds

### `shot_events.py`
- Fetches play-by-play for each completed CAR game
- Extracts shot coordinates (x, y, type, period, time)
- Incremental — skips games already processed
- Runs time: ~1-2 minutes for a full season

## Scheduling (after development)

Deploy to Vercel as a cron job running nightly at 4am ET (after games finish):

```json
// vercel.json
{
  "crons": [{
    "path": "/api/pipeline",
    "schedule": "0 9 * * *"
  }]
}
```

## Database schema

See `../eyewall-supabase/schema.sql` for the full Supabase schema.
