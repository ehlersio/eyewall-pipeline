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
- `pwhl_pbp_events.py` — `PWHL_SEASON` live-resolved and `SEASON_TYPE_MAP` filled via `setdefault`, same pattern as `pwhl_stats.py` (closed the gap noted in Session 35–36; was previously reading `PWHL_SEASON` env var directly)

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
`test_season_lookup.py` covers live success, network-failure fallback, malformed responses, shared-cache behavior. Run the full pytest suite before pushing, especially before touching anything season- or team-ID-related.

## Deploy
GitHub Actions cron. Don't reconstruct HockeyTech URLs from written notes — pull real requests from DevTools; both real season-resolution bugs traced back to exactly that shortcut.
