# EyeWall Analytics Pipeline

Nightly data pipeline that populates Supabase with NHL + PWHL stats, MoneyPuck analytics, shot events, shift charts, zone starts, RAPM-derived WAR, power rankings with AI narratives, AI-generated game summaries, predictions, matchup analysis, player scouting blurbs (skaters + goalies), PWHL salary data, and PWHL news.

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
PRIMARY_TEAM_ABBR=CAR
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id
CLOUDFLARE_API_KEY=your_cloudflare_api_key
WORKER_URL=https://eyewall-poller.billowing-queen-bf23.workers.dev
POLL_SECRET=your_worker_poll_secret
```

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
python run.py validate         # RAPM sanity checks

# AI pipeline
python ai_summaries.py                           # Post-game summaries
python ai_summaries.py --game 2025030414 --force # Single game, force regenerate
python ai_predictions.py                         # Pre-game predictions
python ai_scouting.py --missing                  # Missing scouting blurbs only (skaters + goalies)
python ai_scouting.py --team CAR --dry-run       # Preview prompts for one team
python power_rankings.py --dry-run --team CAR    # Preview prompt, no DB writes

# PWHL — run individually (no orchestrator yet)
python pwhl_stats.py 8         # 2025-26 regular season stats
python pwhl_stats.py 9         # 2025-26 playoffs stats
python pwhl_pbp_events.py      # Current season PBP events (defaults to season 8)
python pwhl_pbp_events.py 9    # Specific playoff season
python pwhl_pbp_events.py --force  # Re-ingest already-processed games
python pwhl_shot_events.py     # Shot events
python pwhl_salaries.py        # Salary scraper (PWHLPA PDF)
python pwhl_salaries.py --dry-run  # Parse only, don't upsert
python pwhl_news.py            # Fetch PWHL news and POST to Worker
```

---

## NHL Pipeline Modules

### Run order (nightly, via `run.py`)
```
nhl_stats → shot_events → shift_data → zone_starts → rapm → moneypuck → line_combinations → power_rankings → ai_summaries → ai_scouting
```

### `nhl_stats.py`
Rosters, skater/goalie/team stats, game log for all 32 teams. Accepts season argument: `python nhl_stats.py 20242025`. Runtime: ~2-3 min.

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
Live NHL draft pick polling — NHL API → Supabase + AI analysis via Worker. `--poll-picks` loops every 60s, exits code 99 when all 224 picks complete.

### `tankathon_ingest.py`
2026 draft pick order scraper → `draft_pick_order_2026`.

### AI modules (`ai_summaries.py`, `ai_predictions.py`, `ai_scouting.py`, `ai_persona.py`, `ai_context.py`)

**`ai_scouting.py`** — Generates AI scouting blurbs for both skaters and goalies. Skaters pulled from `player_seasons` via `get_player_context()`; goalies pulled from `goalie_seasons` via `get_goalie_context()` (new — added this offseason). Goalies get a goalie-specific prompt in `build_player_scouting_prompt()` focused on SV%, GAA, GSAX, and percentile ranks rather than the skater-centric goals/assists framing. Respects `--force`, `--missing`, and `--dry-run` flags for both skaters and goalies.

**`ai_context.py`** — Added `get_goalie_context(team, season, min_gp=5)` that pulls from `goalie_seasons` with key metrics: SV%, GAA, GSAX, GSAX/60, QS%, EV/HD/MD/PK SV%, and percentile ranks.

**`ai_persona.py`** — `build_player_scouting_prompt()` now branches on `position == 'G'` to give goalies a tailored prompt.

**`power_rankings.py`** — AI narratives now cached per-team in Worker KV using `narrative:{period}:{gameId}:{carAbbr}` key pattern so each team's perspective is independently cached.

---

## PWHL Pipeline Modules

All PWHL modules use HockeyTech API (no authentication required) and write to `pwhl_*` Supabase tables.

### `pwhl_stats.py`
Main PWHL stats pipeline. Accepts `season_id` argument (e.g. `8` for 2025-26 regular, `9` for 2025-26 playoffs).

**What it does:**
- `fetch_roster()` — upserts to `pwhl_players`
- `fetch_skater_stats()` — upserts to `pwhl_player_seasons`
- `fetch_goalie_stats()` — upserts to `pwhl_goalie_seasons`
- `fetch_team_stats()` — two HockeyTech calls (`special=false` + `special=true`): standings + PP%/PK%/special teams raw counts → `pwhl_team_seasons`
- `run_team_shot_totals()` — computes CF/CA/FF/FA from `pwhl_shot_events` joined to `pwhl_game_log` → `pwhl_team_seasons`
- `fetch_game_log()` — upserts to `pwhl_game_log` including `game_date` (parsed from `date_with_day` via `_parse_game_date()`), `venue_name`, `venue_city`

**Special teams note:** HockeyTech `view=teams&special=true` returns PP%/PK% as strings like `"23.0%"`. `_parse_pct()` converts to float (0.23).

**Game date note:** HockeyTech returns `"Fri, Apr 30"` not a full ISO date. `_parse_game_date()` uses `SEASON_YEAR_MAP` to infer the year — months Sep-Dec use start year, Jan-Aug use start year + 1.

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

### `pwhl_shot_events.py`
Ingests PWHL shot coordinates from HockeyTech PBP. Writes to `pwhl_shot_events` with `x_norm`, `y_norm`, `event_type`, `shooter_id`, `team_id`, `period_id`, `time_seconds`.

**Coordinate note:** `x_norm` is inverted vs NHL convention (positive = defending end). Frontend negates x before folding to attacking half. A pipeline-level fix is deferred.

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

IDs 2, 4, 7 don't exist or have no data (likely preseason/gaps in HockeyTech numbering).

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
| `team_seasons` | Per-team stats + `xgf_pct` + `roster_war_score` |
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
| `game_scoring` | Goal-by-goal scoring data |
| `game_xg` | Per-game expected goals |
| `line_combinations` | Inferred lines and D pairs |
| `power_rankings_narratives` | Nightly rankings + AI narrative history |
| `special_teams_units` | PP/PK unit inference |
| `draft_rankings_2026` | NHL Central Scouting rankings |
| `draft_picks_2026` | Live/completed draft picks |
| `draft_pick_order_2026` | Pick order per team (Tankathon) |

### PWHL Tables
| Table | Description |
|-------|-------------|
| `pwhl_players` | Player master (player_id, first_name, last_name, position, team_id) |
| `pwhl_player_seasons` | Per-player per-season stats (GP, G, A, PTS, shots, PP/SH/GW goals, +/-, PIM, shot_pct) |
| `pwhl_goalie_seasons` | Per-goalie per-season stats (GP, W, L, OTL, GAA, SV%, SO, saves, GA) |
| `pwhl_team_seasons` | Per-team per-season stats + PP%/PK%/special teams + Corsi/Fenwick + reg_wins/non_reg_wins |
| `pwhl_game_log` | Game results with scores, dates, venue, OT/SO flags |
| `pwhl_shot_events` | Shot coordinates (x_norm, y_norm), event_type, shooter_id, team_id, period, time |
| `pwhl_pbp_events` | PBP events: faceoffs (homeWin string), hits, penalties, goalie changes |
| `pwhl_salaries` | Player salary data from PWHLPA PDF (first_name, last_name, player_id, team_id, salary, season) |
| `pwhl_game_summaries` | AI post-game summaries (PWHL) |
| `pwhl_game_predictions` | AI pre-game predictions (PWHL) |
| `pwhl_player_scouting` | AI scouting blurbs (PWHL) |
| `pwhl_power_rankings_narratives` | PWHL nightly power rankings + AI narrative history |
| `pwhl_seasons` | PWHL season metadata |
| `pwhl_teams` | PWHL team master |
| `pwhl_shift_events` | PWHL shift events (sparse — no player_change in HockeyTech PBP; WAR blocked until Oct 2026) |
| `pwhl_skipped_games` | Games skipped per PWHL pipeline module |

---

## GitHub Actions Workflows

| Workflow | Schedule | Description |
|----------|----------|-------------|
| `nightly.yml` | 3 AM ET daily | NHL-only pipeline (`run.py` + Ruff lint) |
| `pwhl-nightly.yml` | 3:20 AM ET daily | PWHL PBP events + PWHL news — 20 min offset to avoid Supabase contention |
| `moneypuck-ingest.yml` | Nightly | MoneyPuck CSV fetch via GH runner (CF IPs blocked) |
| `reddit-ingest.yml` | Every 30 min | Reddit (32 subreddits) + SBNation atom feeds → Worker |
| `tankathon-sync.yml` | Weekly (Tue 8am ET) | Tankathon draft order scrape |
| `draft-ingest.yml` | Jun 26 + Jun 27 | Live NHL draft pick polling loop |

---

## October Season Prep

### NHL
1. Update `NHL_SEASON` GH Actions secret to `20262027`
2. Update `MP_SEASON` in `moneypuck.py`
3. Run `python tankathon_ingest.py` for new draft year

### PWHL
1. Update `PWHL_SEASON` GH Actions secret to new regular season ID (verify with HockeyTech)
2. Update `SEASON_YEAR_MAP` in `pwhl_stats.py` for new season IDs
3. Update `PWHL_CURRENT_SEASON` in frontend `pwhlConfig.js`
4. Add expansion team IDs to `pwhlConfig.js` once HockeyTech assigns them (DET, HAM, LAS, SJS)
5. Run `python pwhl_salaries.py` when PWHLPA publishes 2026-27 salary guide
6. Run backfill for new season: `python pwhl_stats.py {new_season_id}`

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
- **PWHL expansion teams:** DET, HAM, LAS, SJS deferred until HockeyTech assigns season IDs (October 2026).
- **`nhl_stats.py` fragile loop:** `for game_type in [2, 3]` body references `game_type` as if a parameter — works via Python scoping but fragile. Fix before next major pipeline work.
- **UTA missing from `team_seasons`:** Excluded from power rankings until their row appears.
- **RAPM linemate collinearity:** Documented in `validate_rapm.py`.
- **Transactions/Injuries:** No reliable free NHL API. Deferred pending PuckPedia.
- **Reddit ingest:** GH Actions IPs blocked by Reddit. New app registration blocked by Responsible Builder Policy. Deferred to October 2026.
- **PWHL WAR/RAPM:** Blocked — HockeyTech PBP has no `player_change` shift events across all 3 seasons (confirmed June 2026). Revisit October 2026.
