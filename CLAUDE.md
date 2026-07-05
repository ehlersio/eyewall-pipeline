# eyewall-pipeline

Python data pipeline for EyeWall Analytics, run nightly via GitHub Actions. Ingests NHL and PWHL stats, shot events, milestones, salaries, draft data into Supabase.

## Stack
- Python, `db.py` as the consolidated Supabase access point
- GitHub Actions cron (`nightly.yml` for NHL, `pwhl-nightly.yml` for PWHL — kept separate)
- pytest for tests
- Ruff for linting

## Sibling repos
Lives in `eyewall/` alongside `eyewall-poller` (Cloudflare Workers backend) and `eyewall-analytics` (React frontend). This repo writes to Supabase; `eyewall-poller` and the frontend read from it (plus live APIs directly for some things).

## Live season resolution (built Session 35–36)

`season_lookup.py` reads `GET /config/seasons` from the `eyewall-poller` Worker (with an env-var fallback if the Worker is unreachable), and is the entry point other modules use instead of hardcoding season constants:

- `db.py` — `NHL_SEASON` now live-resolved
- `pwhl_stats.py` — `PWHL_SEASON` live-resolved; `TEAM_ID_MAP`/`CITY_TEAM_MAP` have expansion entries; `SEASON_YEAR_MAP`/`SEASON_TYPE_MAP` auto-fill current season via `setdefault`
- `pwhl_salaries.py` — `SEASON_LABEL` live-derived; `TEAM_NAME_MAP` + regex fallback have expansion entries
- `moneypuck.py` — `MP_URL` derived from `NHL_SEASON` instead of a separately hardcoded year
- `pwhl_pbp_events.py` — `PWHL_SEASON` live-resolved and `SEASON_TYPE_MAP` filled via `setdefault`, same pattern as `pwhl_stats.py` (closed the gap noted in Session 35–36; was previously reading `PWHL_SEASON` env var directly). Follow-up fix: `run()`'s single-game (`--game`) debug mode was leaking the sweep-level `season_id`/`season_type` (i.e. `PWHL_SEASON`, the regular-season-preferring value) onto rows for games from other season types. Now reads the game's own `season_id` off its `pwhl_game_log` row instead. Regression-covered in `test_pwhl_pbp_events_season.py`.

## Arbitrary season_id → season_type lookup (`get_season_type()`, Session 37)

`SEASON_TYPE_MAP.get(id, "regular")` — used across `pwhl_pbp_events.py`, `pwhl_stats.py`, `pwhl_shot_events.py`, and `pwhl_milestones.py` — silently mislabeled any season_id with no hardcoded entry as `"regular"` instead of raising. Fixed by adding `season_lookup.get_season_type(season_id) -> str | None`, which proxies the Worker's new `GET /config/seasons/pwhl-types` route (see `eyewall-poller`'s CLAUDE.md). That route is backed by HockeyTech's **full** bootstrap `seasons[]` list — every season_type for every season_id it has ever assigned — unlike `/config/seasons`, which only ever returns the single resolved "current" season. `get_season_type()` returns `None` (not a guess) if the id genuinely isn't recognized; cached per-process like `get_pwhl_season()`, but in its own cache slot (different endpoint) — no TTL needed since a pipeline run is short-lived anyway.

Each of the four modules above now has a local `_resolve_season_type(season_id)` helper: **hardcoded `SEASON_TYPE_MAP` first, `get_season_type()` as the live fallback.** This is deliberate, not a straight replacement — the hardcoded map holds a manual correction (season "2" — see "Known open items" below) that live bootstrap data would silently overwrite if trusted outright. When neither source recognizes an id, behavior splits by call-site shape:
- **Unattended sweep/loop paths** (`pwhl_pbp_events.py run()`, `pwhl_stats.py run()`, `pwhl_shot_events.py run()`, `pwhl_milestones.py get_games_for_date()`) — log an error and skip (the whole run, or just that one game in a per-date loop). One bad/future season_id shouldn't crash a nightly cron job.
- **`--game` debug paths** (all four modules have one) — raise `ValueError`. A human is watching that output directly; a loud failure beats a silently-wrong label.

**Important distinction:** the resolver deliberately prefers "most recent **regular** season" over "most recent season of any type" (a real bug once resolved to a playoffs season_id and silently broke every query filtering `season_type=eq.regular`). But roster/preseason-specific fetches (e.g. `fetch_roster()`) need the **literal current/preseason season ID** instead — these two concepts intentionally disagree. Check which one any given module actually needs before wiring it through `season_lookup.py`.

## PWHL team IDs
HockeyTech IDs, including 2026-27 expansion teams: DET=10, HAM=11, LV=12, SJS=13. This map is duplicated across `TEAM_ID_MAP`/`CITY_TEAM_MAP` here, `pwhl_salaries.py`'s `TEAM_NAME_MAP` + regex fallback, and independently in `eyewall-poller`'s `pwhl.js` and `eyewall-analytics`'s `pwhlConfig.js`. Any future expansion wave needs all of these touched — confirm via grep, don't assume from memory.

New teams also need a `pwhl_teams` row seeded before `pwhl_players.team_id` FK inserts will work — see `seed_expansion_teams.py` as a reference pattern (disposable script, safe to delete, kept as an example of the seeding shape).

## Known open items
- Season ID 2 naming discrepancy: `SEASON_TYPE_MAP` here labels it `"showcase"`; the real HockeyTech `bootstrap` response calls it `"2024 Preseason"`. Don't silently "fix" either direction — verify against real 2024 game data first.
- `gameSummary`'s homeTeam/visitingTeam box score payload (rich per-player TOI/hits/blocked-shots/faceoffs) is not yet wired into any pipeline module.
- `inspect_*`/`test_*` diagnostic files are intentionally kept around (not scratch files to delete) — not audited recently though.

## HockeyTech API facts (hard-won, from direct DevTools inspection — see `docs/hockeytech-api-notes.md`)
- `feed=statviewfeed` is the only valid feed (not `modulekit` — silently returns a fake 200 with no payload)
- Real leaderboard view is `leadersExtended`, not `leaders`
- Standings uses `view=teams` with `statsType=inline`
- `view=bootstrap` resolves season names/dates/clinch prefixes and `current_season_id`
- `view=player` has career stats, per-game logs, shot-location data
- `lineupPairingReport` is always null (confirmed across 10 games) — not viable for line analysis
- `getGameSettings` confirms icing/offside/odd-man-rush are not tracked
- PWHL WAR/RAPM blocked until October — no `player_change` shift events in HockeyTech PBP yet
- `time_seconds` in PBP events is **elapsed** time (0→1200), not countdown — don't assume otherwise

## Testing
`test_season_lookup.py` covers live success, network-failure fallback, malformed responses, shared-cache behavior (including `get_season_type()`'s separate cache). `test_pwhl_pbp_events_season.py` covers the single-game season-leak fix and the sweep-skips/`--game`-raises split for unrecognized season_ids. Run the full pytest suite before pushing, especially before touching anything season- or team-ID-related.

## Deploy
GitHub Actions cron. Don't reconstruct HockeyTech URLs from written notes — pull real requests from DevTools; both real season-resolution bugs traced back to exactly that shortcut.
