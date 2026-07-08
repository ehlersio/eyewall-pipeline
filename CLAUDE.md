# eyewall-pipeline

Python data pipeline for EyeWall Analytics, run nightly via GitHub Actions. Ingests NHL and PWHL stats, shot events, milestones, salaries, draft data into Supabase.

## Stack
- Python, `db.py` as the consolidated Supabase access point
- GitHub Actions cron (`nightly.yml` for NHL, `pwhl-nightly.yml` for PWHL — kept separate)
- pytest for tests
- Ruff for linting

## Sibling repos
Lives in `eyewall/` alongside `eyewall-poller` (Cloudflare Workers backend) and `eyewall-analytics` (React frontend). This repo writes to Supabase; `eyewall-poller` and the frontend read from it (plus live APIs directly for some things).

## Git branch hygiene (standing rule — read before any session)

Before making any file changes in a new session, always run:

```
git status
git branch
```

If the current branch is not `main`, or if `main` locally is behind `origin/main`, stop and do this first:

```
git checkout main
git pull origin main
```

Then sweep local branches from prior sessions: for each `sessionNN-*` branch still present locally, confirm on GitHub that its PR merged, then delete the local branch (`git branch -d <branch>`; use `-D` only if it's confirmed merged but not fast-forward-mergeable locally). Remote branches auto-delete on merge in this repo (and the other two EyeWall repos), so this sweep is local-only. Do not delete a branch whose PR hasn't merged, even if it looks stale.

Once `main` is current and stale local branches are cleared, cut a fresh branch for the new session:

```
git checkout -b <new-branch-name-for-this-session>
```

Only start editing files after confirming you're on a fresh branch cut from an up-to-date `main`. Do not assume the working directory is already in the right state, even if the previous session ended with a merge — branch switches are a manual step and are easy to forget.

Name the new branch for what the session is actually doing (e.g. `session43-line-combinations`), not a generic name, so it's identifiable later if it needs recovering.

## README hygiene (standing rule — read before opening any PR)

Before opening a PR, check whether the change affects anything `README.md` documents — setup/install steps, environment variables, available scripts/commands, API routes or endpoints, known limitations, test counts, or architecture description. If yes, update the README in the same PR. Purely internal changes (refactors, bug fixes with no behavior/interface change) don't need a README touch — don't pad PRs with unnecessary doc churn.

## Python environment hygiene (standing rule — read before any Python command)

This repo uses a project-local venv at `.venv/` (not the global system Python). Before running any Python command (`pip`, `pytest`, `python <script>.py`, etc.), confirm the venv is active and matches `requirements.txt` — don't assume it's already activated just because it exists.

Quick check:

```
pip show supabase
```

This should report `Version: 2.31.0` (or whatever `requirements.txt` currently pins). If it reports something older (e.g. `2.3.4`), the venv is either not activated or out of sync — stop and flag this before proceeding, rather than silently running against a stale/global interpreter. Do not assume a passing or failing test result reflects the real dependency state without this check first — a stale environment can produce misleading import errors that look like code bugs but are actually local environment drift (this happened once already, in Session 42).

If the venv needs activating:

```
.venv\Scripts\Activate.ps1
```

If it's active but out of sync with `requirements.txt`, ask before running `pip install -r requirements.txt` rather than assuming it's safe to just sync silently.

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
- `gameSummary`'s homeTeam/visitingTeam box score payload (rich per-player TOI/hits/blocked-shots/faceoffs) is not yet wired into any pipeline module (scoped in Session 41; table-shape decision pending before implementation).
- ~~`gameSummary`'s `goal.plus_players[]`/`minus_players[]`~~ — resolved Session 42: wired into `pwhl_goal_on_ice.py` / `pwhl_goal_on_ice` table, convention validated against `pwhl_skater_game_box.plus_minus` (10,669/10,669 player-games matched across the full historical backfill: sum `on_ice_for` excluding power-play goals only). Does NOT change the WAR/RAPM October-2026 blocker below — goal-scoped, not continuous shift data, too coarse a signal to substitute.
- Penalty shots (`"penaltyshot"` PBP event / `gameSummary.penaltyShots`) — resolved Session 42: own table (`pwhl_penalty_shots.py` / `pwhl_penalty_shots`), sourced from `gameSummary.penaltyShots` (has misses; `periods[].goals[]` doesn't). No coordinate data exists for these at all. `pwhl_shot_events.is_penalty_shot` now always reads `false` — likely dead weight, not dropped this session.
- `inspect_*`/`test_*` diagnostic files are intentionally kept around (not scratch files to delete) — not audited recently though.

## `special_teams.py` is NHL-only — not a PWHL PP%/PK% source
Despite the generic name, `special_teams.py` uses `NHL_SEASON` and the NHL
`game_log`/`shot_events`/`shift_events` tables, and it infers **PP/PK unit
compositions** (which players form PP1/PP2/PK1/PK2) — it does not compute
PP%/PK% *percentages* for either league. There is no PWHL-side module that
independently derives PP%/PK% from shot/shift data. PWHL's actual PP%/PK%
(`pwhl_team_seasons.pp_pct`/`pk_pct`) comes from a second HockeyTech call in
`pwhl_stats.py::fetch_team_stats` (`view=teams&special=true`) — i.e. it's
already a server-computed HockeyTech value, same as `gameCenterPreview`'s
`powerPlayStats`/`penaltyKillStats`. Confirmed via live pull (Session 41,
game 326 — season 8's actual final game): the two are consistent, with
`gameCenterPreview`'s numbers reflecting the cumulative record *entering*
that game rather than the season's final total. Don't conflate this module
with a PWHL special-teams calculator when scoping future work.

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
