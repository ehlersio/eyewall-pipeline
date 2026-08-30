# EyeWall Analytics Pipeline

Nightly data pipeline that populates Supabase with NHL + PWHL + AHL + ECHL stats, MoneyPuck analytics, shot events, shift charts, zone starts, RAPM-derived WAR, power rankings with AI narratives, AI-generated game summaries, predictions, matchup analysis, player scouting blurbs (skaters + goalies), PWHL salary data, PWHL/AHL/ECHL news, milestone detection (hat tricks, shorthanded goals, shutouts, season/career goal and points thresholds — NHL/PWHL only), and daily trivia questions (easy/medium tiers, guardrailed AI generation — see [Daily Trivia](#daily-trivia)).

AHL and ECHL are the same HockeyTech/LeagueStat vendor as PWHL and were added as data-layer-only builds (stats, per-game box scores, shot events, penalty shots, news, intraday live-score refresh) — see [AHL Pipeline Modules](#ahl-pipeline-modules) / [ECHL Pipeline Modules](#echl-pipeline-modules) below. Neither league has shift data or attempt-level PBP events (hits/blocked shots/faceoffs) in the HockeyTech feed at all, so WAR/RAPM/Corsi are not buildable for either — see [Known Limitations](#known-limitations).

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
OPENROUTER_API_KEY=your_openrouter_api_key
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
python ai_summaries.py                           # Post-game summaries, en + fr
python ai_summaries.py --game 2025030414 --force # Single game, force regenerate
python ai_summaries.py --game 2025030414 --locale fr  # Single game, French only
python ai_predictions.py                         # Pre-game predictions
python ai_scouting.py --missing                  # Missing scouting blurbs only (skaters + goalies), en + fr
python ai_scouting.py --team CAR --dry-run       # Preview prompts for one team
python ai_scouting.py --team CAR --locale fr --dry-run  # Preview French-only prompts for one team
python ai_results_vs_process.py --missing        # Missing results-vs-process blurbs (NHL skaters only), en + fr
python ai_results_vs_process.py --team CAR --dry-run  # Preview prompts for one team
python ai_line_chemistry.py --missing            # Missing line-chemistry blurbs (all 32 teams), en + fr
python ai_line_chemistry.py --team CAR --dry-run # Preview prompts for one team
python power_rankings.py --dry-run --team CAR    # Preview prompt, no DB writes
python trivia_questions.py                       # Today, both tiers, both sports, en + fr (manual/dry-run convenience)
python trivia_questions.py --sport nhl            # NHL only (what nightly.yml actually calls)
python trivia_questions.py --sport pwhl           # PWHL only (what pwhl-nightly.yml actually calls)
python trivia_questions.py --tier easy --dry-run --date 2026-08-10  # Preview without writing
python trivia_questions.py --tier easy --dry-run --locale fr        # Preview French-only questions

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

# AHL — run individually (no orchestrator yet)
python ahl_stats.py                        # current season (live-resolved)
python ahl_stats.py 90                     # specific season_id (90 = 2025-26 Regular)
python ahl_game_boxscore.py                # per-game skater/goalie box scores
python ahl_shot_events.py                  # shot events + goals with coordinates
python ahl_shot_events.py --game 1028362   # single game (debug)
python ahl_penalty_shots.py                # penalty shots (makes + misses)
python ahl_live_refresh.py                 # narrow live game_state/score refresh (5-min cron)
python ahl_news.py                         # AHL news -> Worker

# ECHL — run individually (no orchestrator yet)
python echl_stats.py                       # current season (live-resolved)
python echl_stats.py 73                    # specific season_id (73 = 2025-26 Regular)
python echl_game_boxscore.py               # per-game skater/goalie box scores
python echl_shot_events.py                 # shot events + goals with coordinates
python echl_penalty_shots.py               # penalty shots (makes + misses)
python echl_live_refresh.py                # narrow live game_state/score refresh (5-min cron)
python echl_news.py                        # ECHL news -> Worker
```

---

## NHL Pipeline Modules

### Run order (nightly, via `run.py`)
```
nhl_stats → playoff_race → shot_events → shift_data → zone_starts → rapm → moneypuck → line_combinations → power_rankings → ai_summaries → ai_scouting → ai_results_vs_process → ai_line_chemistry
```

### `nhl_stats.py`
Rosters, skater/goalie/team stats, game log for all 32 teams. Accepts season argument: `python nhl_stats.py 20242025`. Runtime: ~2-3 min.

**Standings enrichment (Session 57):** `fetch_standings()` (formerly `fetch_standings_l10()`) now parses the full `standings/now` response instead of discarding everything but L10 record. For regular-season (`game_type=2`) rows it's the canonical source for `team_seasons`' points/wins/losses/ot_losses/games_played (previously duplicated from `stats/rest/en/team/summary`, joined via a hardcoded teamId->abbr map) plus new columns: `division_abbrev`, `conference_abbrev`, `wildcard_sequence`, `regulation_wins`, `clinch_indicator`. `fetch_team_stats` (the summary endpoint) is now only used for the advanced stats standings/now doesn't carry (goals, PP%/PK%, shots/game). All five new columns are `NULL` for playoff (`game_type=3`) rows — standings/now has no bracket equivalent. Requires `docs/session57_new_columns.sql` to be run in Supabase first (no migration tooling in this repo).

**Season-mismatch guard (Session 66):** `standings/now` is a *date* redirect, not a season-scoped query — before a new season's games exist it keeps redirecting to the prior season's finale and returning that season's real, final data (this bit the app once already: 32/32 teams' `team_seasons` rows under the new season showed a stale full 82-game record). `fetch_standings()` now carries each row's own `seasonId` through (`season_id`); `run()` skips writing regular-season standings fields for any team whose `season_id` doesn't match the season being written, rather than blindly stamping the mismatch onto `team_seasons`. A team with no matching row simply has no `team_seasons` row for that season/game_type yet, same as any other not-started-season table in this pipeline.

**`game_log.pp_goals`/`pp_opps` — fixed a `situationCode` misindexing bug:** the old `team_scored_first` + PP/PK enrichment step (still PBP-based for `team_scored_first`) used to also reconstruct PP goals/opportunities from `situationCode`/penalty-duration, parsing `home_sk = int(sc[3])` — but `sc[3]` is the home goalie-in-net flag, not the home skater count (that's `sc[2]`). Since `sc[3]` is almost always `"1"` and away skater counts are realistically 3-6, this made `away_sk > home_sk` true for nearly every away-team goal, badly overcounting away `pp_goals` (season-wide: home teams averaged exactly 0.0 `pp_goals`/game, away teams ~2.99) while `pp_opps` (a separate, unaffected duration==2 counter) undercounted real opportunities that came from majors/double-minors/bench minors. `fetch_pp_stats()` replaces the whole reconstruction with the NHL's own official per-game box score (`gamecenter/{id}/right-rail`'s `teamGameStats` `powerPlay` field, a `"G/Opp"` string) — sidesteps the bug class entirely rather than patching the parser. The enrichment loop itself now lives in `enrich_game_log(client, season, force_all=False)`, callable standalone (`force_all=True` re-derives every row regardless of current value — used to backfill 2023-24/2024-25, which were 100% null, and to correct 2025-26's already-written-but-wrong values). `backfill_pp_stats.py` is a thin one-off driver for re-running this against an arbitrary past season. A parallel bug (inverted `home_pp`/`away_pp` labels) was found and fixed in `ai_context.py`'s `decode_situation()` during the same audit — it feeds the AI narrative's goal-scoring context, not `game_log`.

**`game_log.hits`/`penalties` + `team_seasons.hits`/`penalties` (Session 81):** closes the real pipeline gap identified in `NHL_PERCENTILE_AND_HITS_PENALTIES_BRIEF.md` item 1 — previously only per-player *season* PIM/hits existed, no team/game-level source for Hits and no penalty *count* (only PIM, a minutes-based proxy) anywhere. Confirmed live that `gamecenter/{id}/right-rail`'s `teamGameStats` already carries a `"hits"` category alongside `"powerPlay"` (same payload, no extra fetch) and that play-by-play already has explicit `typeDescKey=="penalty"` events with a per-event `eventOwnerTeamId` — a directly countable event type, not a `situationCode` reconstruction like the PP bug above. `fetch_pp_stats()` was split into `fetch_right_rail()` (raw fetch) + `parse_pp_stats()`/`parse_team_hits()` (pure parsers) so `enrich_game_log()` fetches right-rail once per game and derives both PP/PK and Hits from it, rather than fetching twice; `fetch_pp_stats()` itself is now a thin wrapper kept for backward compatibility with its existing direct callers/tests. Penalty count is summed straight from PBP's `penalty` events grouped by `eventOwnerTeamId` — unlike `pp_stats`/`hits_stats`, a count of zero is a real value (a disciplined game), not a missing-data signal. `run_team_hits_penalties_rollup(client, season, game_type)` sums `game_log.hits`/`penalties` into `team_seasons` — the season aggregate the Shot Map "All N" summary cards need (the single-game Shot Map view already works today via its own live right-rail fetch, independent of this). Requires `docs/session81_new_columns.sql` to be run in Supabase first, plus a one-time `force_all=True` backfill for existing seasons (see that file's backfill note). Wiring this into `eyewall-poller`'s Worker API and the frontend's "All N" cards is a separate follow-up, not done in this pipeline-only session.

**`player_seasons`/`goalie_seasons` duplicate-row bug — fixed a `team`-inclusive conflict key (Session 81, found live during the percentile backfill above):** both tables' upserts used to include `team` in their `on_conflict` key, but `nhl_stats.py`'s `team` (NHL API's `teamAbbrevs`, a possibly comma-joined trade-history string like `"VAN,SJS"`) and `moneypuck.py`'s `team` (MoneyPuck's CSV, current team only, e.g. `"SJS"`) don't reliably match for a traded player — every mismatch silently forked that player into two rows instead of merging into one: one row with real box-score stats and no analytics, the other with WAR/percentiles and no box-score stats. Confirmed and fixed in production: **338 duplicate `(player_id, season, game_type)` pairs in `player_seasons`** (81 in 2025-26, 257 accumulated in 2024-25 across many nightly runs — some players forked into 3+ rows as MoneyPuck's own team snapshot changed run to run) **plus 2 in `goalie_seasons`**, all merged (keeping the box-score-owning row, copying over the analytics columns) and the orphan rows deleted via a one-off script — see `docs/session81_dedupe_constraint_fix.sql`'s header comment for the full accounting. Fix: `team` dropped from both tables' conflict key (`player_id,season,game_type` is the real identity) in `nhl_stats.py`, and from `moneypuck.py`'s three upsert call sites (`run_goalie_qs`, `run_goalies`, the main skater-analytics block) — `moneypuck.py` no longer writes `team` at all, since it's `nhl_stats.py`'s column to own, not an analytics one. **Requires `docs/session81_dedupe_constraint_fix.sql` to be run in Supabase before this code runs again** — it drops the old team-inclusive unique constraint and adds the corrected one; without it, every `player_seasons`/`goalie_seasons` upsert fails outright with Postgres's `42P10` ("no unique or exclusion constraint matching the ON CONFLICT specification"), confirmed live. `rapm.py`'s own `player_seasons` update was already `team`-agnostic (filtered by `player_id`+`season`+`game_type` only) — unchanged, but it's exactly how the duplicate rows were first noticed: it was silently writing the same `rapm` value into both forked rows for a traded player.

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

**Off-season no longer fails the nightly job (2026-07-20):** `run()` returns `"off_season"` (distinct from `"no_data"`) when `player_seasons.rapm` is empty *and* `game_log` has zero completed games for the season — genuinely nothing to validate yet, not the Session 45 stale-data failure mode. `run.py`'s allowlist now accepts `"off_season"` alongside `"pass"`/`"warn"`. If RAPM is empty but completed games exist, it still returns `"no_data"` and fails loud, unchanged.

### `moneypuck.py`
WAR (RAPM-derived EV component), percentile rankings, goalie GSAX, per-game xG, `team_seasons.xgf_pct`. Accepts season argument.

**`MP_URL` fix (2026-07):** used to hardcode `"2025"` directly in the MoneyPuck CSV URL, completely decoupled from `NHL_SEASON` — meaning a correct `NHL_SEASON` flip alone would NOT have fixed this fetch each October. Now derived as `MP_START_YEAR = int(str(NHL_SEASON)[:4])`, so there's exactly one place this needs to be right. The URL itself is split into `MP_SKATERS_URL`/`MP_GOALIES_URL` (both built from `MP_START_YEAR`), not a single `MP_URL`.

**Season-not-yet-published 404s are non-fatal (Session 66 follow-up):** an early `NHL_SEASON` flip (KV override, ahead of the real schedule) makes `MP_START_YEAR` point at a season whose CSV MoneyPuck hasn't published yet — a 404 that's expected and temporary, not a real failure. `run()` now catches a 404 specifically on the initial skaters-CSV fetch, logs it, and returns cleanly (skipping the whole stage, since every substage depends on that CSV) instead of letting `run.py` mark the nightly job red every night until the season actually starts. Any other error (non-404 HTTP status, network failure, a genuine URL-scheme change) still raises loudly, same as before.

**Goalie GSAX restored (2026-07):** real goalie GSAX (Goals Saved Above Expected, from MoneyPuck's externally-modeled `goalies.csv`) was originally added, ran once, then was accidentally deleted two days later as collateral damage from the `MP_URL`/`fetch_csv()` refactor above. `run_goalies(client, season)` restores it as a `_run_substage` alongside `game_xg`/`team_xgf_rollup`/`goalie_qs`/`team_corsi_rollup`, writing `gsax`, `gsax_per60`, `ev_sv_pct`, `hd_sv_pct`, `md_sv_pct`, `pk_sv_pct` (plus percentiles) to `goalie_seasons` on the same `player_id,season,team,game_type` conflict key `run_goalie_qs()` uses, so it merges cleanly without clobbering QS% columns.

**Results-vs-process columns (Session 56, NHL only):** `player_seasons.on_ice_gf_pct` (on-ice GF% at 5v5, from MoneyPuck's `OnIce_F_goals`/`OnIce_A_goals`) and `results_vs_process_diff` (`on_ice_gf_pct` minus the existing `ev_off_pct`, which is already on-ice xGF% at 5v5 — deliberately not duplicated under a new column name). Both are `NULL` below `RESULTS_VS_PROCESS_MIN_GP` (25 games — see Session 55's investigation) so every downstream consumer just checks "is this null", not a duplicated GP comparison. PWHL is out of scope — blocked on the same shift-event gap as PWHL WAR/RAPM, revisit in October. Requires `docs/session56_new_columns.sql` to be run in Supabase first (no migration tooling in this repo).

**11 new percentile categories (Session 81):** closes `NHL_PERCENTILE_AND_HITS_PENALTIES_BRIEF.md` item 2 — GP, +/-, SHG, GWG, Shots, TOI/G, FO%, Hits, Blocks, Takeaways, Giveaways now get `pct_*` columns alongside the pre-existing 10 (ev_off/ev_def/pp/pk/finishing/goals/a1/penalties/competition/teammates). All 11 raw values were already ingested by `nhl_stats.py` onto `player_seasons` — the gap was purely the percentile column, confirmed per-stat before building. These live on the *NHL API* side of `player_seasons`, not MoneyPuck's CSV (which doesn't carry `plus_minus`, `gw_goals`, or NHL's own `faceoff_win_pct`/`toi_per_game` at all), so they can't be read off the same `all_map` rows the original 10 categories use — `load_player_box_stats(client, season)` loads a second per-player map from `player_seasons` itself (same OFFSET-pagination shape as the existing `rapm_map` load). Ranked on the raw season value directly, not a per-60 rate like the other 10. `giveaways` is the one category negated before pooling (`box_stat(invert=True)`) so the module's existing "higher percentile = better" convention (see `pk_def`'s `1/xga60`, `penalties60`'s negated PIM) holds for it too — fewer giveaways ranks higher. Same `MIN_TOI_MINUTES` display floor as every other `pct_*` column.

**Conference/division percentile scoping (Session 81, NHL only):** closes item 3 — every category above (10 original + 11 new = 21) now also gets a `pct_*_conf` and `pct_*_div` column (42 total), scoped to position × conference and position × division pools instead of just league-wide. `team_seasons.conference_abbrev`/`division_abbrev` (Sessions 57-59) are reused directly — confirmed still populated, no new lookup data needed. `resolve_scoping_team()` resolves a traded player's comma-joined `team` field (e.g. `"STL,DET"`, ~8% of rows live-checked this season) to their LAST-listed team — verified against 4 real 2025-26 trades via `player/{id}/landing`'s `currentTeamAbbrev`, all 4 matched. **PWHL is explicitly out of scope, not just deferred:** live checks against HockeyTech's `view=teams` (returns one flat unlabeled section despite requesting `groupTeamsBy=division`) and `view=bootstrap` (no division/conference metadata anywhere) confirm PWHL has zero conference/division structure today, not merely fewer divisions than the NHL — `pwhl_percentiles.py` is untouched. Requires `docs/session81_new_columns.sql` to be run in Supabase first.

### `line_combinations.py`
Forward lines and D pairs inferred from shift + shot events, for all 32 teams (loop; `--team` for one). Computes per-unit xGF% and TOI. Must run after `shift_data` and `shot_events`.

**32-team expansion (2026-07):** previously CAR-only. Shot events are now fetched by looking up the target team's own `game_id`s from `game_log`, then filtering `shot_events` by that game_id list + `situation_code='1551'` — not `shot_events.car_game`, which only ever flags games CAR played in and can't be reused as a generic per-team filter. Verified against CAR's previously-stored 20252026 rows: identical shift/shot counts, unit composition, TOI, and xGF% before and after the refactor.

### `power_rankings.py`
32-team nightly rankings. 5 weighted normalized components + early-season roster WAR prior (tapers 15%→0% by game 20). AI narrative per team via `ai_client.py` ("Sticks" persona). Writes to `power_rankings_narratives` (history retained for movement arrows).

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

**32-team fix (2026-07):** same `car_game` trap as `line_combinations.py` above — this module's own per-team shot fetch (`fetch_pp_shots_for_team`) was still silently CAR-scoped after that fix landed. Now resolves each team's own `game_id`s from `game_log` first (`fetch_game_ids_for_team`), then fetches PP and PK shots from the same situational-rows fetch (`fetch_situational_shots_for_team` + `filter_pp_shots`/`filter_pk_shots`) instead of a `car_game=True` filter.

### `draft_ingest.py`
Live NHL draft pick polling — NHL API → Supabase + AI analysis via Worker. `--poll-picks` loops every 60s, exits code 99 when all 224 picks complete. `--sync-pick-order` (Session 51) re-derives `draft_pick_order_2026` from `/draft/picks/{year}/all` — the NHL API's authoritative completed-draft results, now that the 2026 draft is over and Tankathon's projected order no longer applies to this table.

### `tankathon_ingest.py`
Draft pick order scraper. No longer scheduled against `draft_pick_order_2026` (Session 51 — see `draft_ingest.py --sync-pick-order` above); its Session 49 year-guard (PR #20) stays in the codebase and would still fire correctly if it were run. Retained for any future Tankathon-sourced use (mock draft, big board, etc.), none of which exist yet in this repo.

### `milestones.py`
Nightly (yesterday's completed games only). Detects hat tricks, natural hat tricks (3 *consecutive* goals by one skater, no other scorer of either team between them), shorthanded goals (from real `situation_code`, shootout-period goals excluded), shutouts (goalie played the whole game, 0 goals against), and threshold crossings — season goals (50), season points (100), career points (500/1000/1500, via a live NHL `/player/{id}/landing` call, only for players who crossed a season threshold tonight), career wins (200/300/400, goalie must have earned a credited win tonight). Writes into the shared `milestones` table (see [Shared Tables](#shared-tables-both-leagues-one-table)) with `is_pwhl=false`. Mirrored by PWHL's `pwhl_milestones.py` below — every `milestone_type` string is intentionally identical between the two pipelines except the numeric thresholds themselves, which are tuned per league (see that module's docstring for why PWHL's are much lower).

```bash
python milestones.py                    # yesterday's games
python milestones.py --date 2026-06-15  # specific date
python milestones.py --since 2026-06-01 # date range through yesterday
```

### AI modules (`ai_client.py`, `ai_summaries.py`, `ai_predictions.py`, `ai_scouting.py`, `ai_results_vs_process.py`, `ai_line_chemistry.py`, `power_rankings.py`, `trivia_questions.py`, `ai_persona.py`, `ai_context.py`)

#### Model provider: OpenRouter

**`ai_client.py`** (2026-08) — shared `generate(prompt, system=None, max_tokens=1024)` used by all 6 AI-generation scripts (`ai_scouting.py`, `ai_summaries.py`, `ai_predictions.py`, `power_rankings.py`, `trivia_questions.py`, and — via `ai_scouting.py`'s re-export — `ai_results_vs_process.py`/`ai_line_chemistry.py`), replacing 5 near-identical copies of the same HTTP call. Calls OpenRouter's `google/gemma-4-26b-a4b-it`, switched from Cloudflare Workers AI's `llama-3.1-8b-instruct-fp8-fast`.

Why the switch: side-by-side testing against this pipeline's actual persona prompts (both English and French) found real accuracy problems with the old model that generic benchmarks alone wouldn't have caught — a fabricated power-play goal stat not present in the input data, and wrong-sport vocabulary in French output ("balle"/ball instead of "rondelle"/puck, "coups de poing"/punches instead of "mises en échec"/hits). The new model didn't reproduce either failure in the same test. Cost impact at this pipeline's real volume is negligible either way (well under $1/month).

Why OpenRouter and not Cloudflare's own `@cf/google/gemma-4-26b-a4b-it` (which exists and is cheaper input-side): as tested, it defaults to a hidden "thinking" mode that consumes the entire completion budget on internal reasoning and returns empty `content`, even at 3x the normal `max_tokens`. Neither `reasoning: {enabled: false}` nor other param shapes disabled it via Cloudflare's endpoint. OpenRouter's own `reasoning: {enabled: false}` param works correctly against the same underlying model — that's the param `ai_client.py` sends on every call.

Requires `OPENROUTER_API_KEY` (see Setup above) instead of `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_KEY` for AI generation. Those two secrets are still needed elsewhere in this repo though -- `draft-ingest.yml` uses them directly (curl, not Python) for Cloudflare KV storage access, an unrelated concern. Don't remove them from GitHub Actions secrets or from a local `.env` if you're working on draft ingestion; they're just no longer required for any of the AI scripts.

**`ai_scouting.py`** — Generates AI scouting blurbs for both skaters and goalies. Skaters pulled from `player_seasons` via `get_player_context()`; goalies pulled from `goalie_seasons` via `get_goalie_context()` (new — added this offseason). Goalies get a goalie-specific prompt in `build_player_scouting_prompt()` focused on SV%, GAA, GSAX, and percentile ranks rather than the skater-centric goals/assists framing. Respects `--force`, `--missing`, and `--dry-run` flags for both skaters and goalies.

**French/English localization (Track B Phase B1, complete)** — `player_scouting`, `player_narratives`, `game_summaries`, and `trivia_questions` rows all carry a `locale` column (`en`/`fr`), part of each table's upsert conflict key (`docs/session_locale_trackb_b0_schema.sql`, applied to prod). `ai_scouting.py`, `ai_results_vs_process.py`, `ai_line_chemistry.py`, and `ai_summaries.py` default to generating both locales per run (an outer `for locale in LOCALES` loop, `LOCALES` defined in `ai_scouting.py`); pass `--locale en`/`--locale fr` to generate just one, e.g. for a targeted backfill. French output uses `ai_persona.py`'s `get_system_prompt('fr')`, which appends a Québécois hockey-terminology instruction to the existing English persona rather than replacing it — the persona's accuracy/formatting rules apply the same regardless of output language. `trivia_questions.py` is wired differently since it has no persona voice (a one-sentence phrasing task with a hard guardrail — see its module docstring): `generate_question_text()` takes its own inline French instruction rather than `get_system_prompt()`, and `STAT_CATEGORIES` carries a `label_fr` for each category since the deterministic `explanation` field (not just the AI prompt) is directly user-facing. Live spot-checks against the original model (llama-3.1-8b-instruct-fp8-fast) produced fluent, grammatically sound French that correctly left `RAPM`/`WAR` untranslated and used correct gender agreement (PWHL "patineuses" vs. NHL "patineurs"), but wasn't perfect glossary compliance — one blurb used "power play" in English despite the glossary specifying "avantage numérique," and produced one garbled/invented word ("événance"). Track B Phase B2 (`eyewall-poller` serving + `eyewallanalytics` frontend locale param) shipped in a follow-up session — the whole localization plan (Track A + Track B) is complete. See [Model provider](#model-provider-openrouter) below — the model backing all of this (French and English both) changed again shortly after, superseding the quality notes above.

**`ai_context.py`** — Added `get_goalie_context(team, season, min_gp=5)` that pulls from `goalie_seasons` with key metrics: SV%, GAA, GSAX, GSAX/60, QS%, EV/HD/MD/PK SV%, and percentile ranks.

**`ai_persona.py`** — `build_player_scouting_prompt()` now branches on `position == 'G'` to give goalies a tailored prompt.

**`power_rankings.py`** — AI narratives now cached per-team in Worker KV using `narrative:{period}:{gameId}:{carAbbr}` key pattern so each team's perspective is independently cached.

**`ai_results_vs_process.py`** (Session 56, NHL only) — Generates "results vs. process" blurbs explaining *why* a player's on-ice goal results (`on_ice_gf_pct`) diverge from their underlying process (`ev_off_pct`), not just restating the two numbers. Pulls qualifying skaters (non-null `results_vs_process_diff` — moneypuck.py's GP≥25 guardrail is the only gate; this script never re-checks GP itself) via the new `get_results_vs_process_context()` in `ai_context.py`. Writes to a new `player_narratives` table rather than `player_scouting` — see that table's description below. Respects `--force`, `--missing`, `--dry-run`, `--team`, `--player` flags, same CLI shape as `ai_scouting.py`. Skater-only (MoneyPuck's on-ice GF/GA split doesn't exist for goalies).

**`ai_persona.py`** — new `build_results_vs_process_prompt()`, dumps the player's on-ice GF%/process xGF%/diff and an explicit over/underperforming direction, with task instructions asking Sticks to explain the *why* (finishing luck, goaltending support, sustainability) rather than just restate the numbers.

**`ai_line_chemistry.py`** (2026-07, all 32 teams) — Generates "line chemistry" blurbs explaining *why* an inferred line/D-pair performs the way it does: how its xGF% compares to the team's other units of the same type and to the league-wide average, and what its members' individual 5v5 process stats (`xgf_per60`, `xga_per60`, `goals_per60`, `pct_ev_off`, `pct_ev_def`) suggest is driving that gap. Pulls context via the new `get_line_chemistry_context()` in `ai_context.py`. Writes to `player_narratives` (`narrative_type='line_chemistry'`) — since that table is keyed one row per player, a unit's blurb is written identically to each of its 2-3 members' rows rather than once per unit. Respects `--force`, `--missing`, `--dry-run`, `--team` flags, same CLI shape as `ai_results_vs_process.py` (no `--player` mode — units aren't single-player-addressable the same way).

`get_line_chemistry_context()`'s league-wide average is `None` until `line_combinations` has rows from at least 2 teams for that unit type — a "league average" of one team isn't a real comparison, which matters early in the 32-team backfill.

**`build_line_chemistry_prompt()` precomputes the unit's xGF% rank** among its same-team, same-type siblings rather than handing the model a raw list and trusting it to infer relative position — an 8B model reliably got this wrong in testing (claimed a unit with the *lowest* xGF% of its group "ranks second, just behind the top line"). Doing the comparison in Python and stating the rank as a given fact in the prompt, with an explicit instruction not to recompute it, eliminated the hallucination in spot-checks across CAR's 7 units. The prompt also deliberately avoids inviting claims about zone starts, quality of competition, or matchups — no per-line data source for any of those exists yet.

---

## Daily Trivia

**`trivia_questions.py`** (Session 92) generates daily trivia questions for the frontend's Trivia tab — **easy** (league-wide) and **medium** (per-team, all 32 NHL + 12 PWHL teams) tiers. **Hard** tier is hand-curated directly in the Supabase SQL editor (no admin UI in v1) — this module never writes `tier='hard'` rows.

**Guardrail (non-negotiable — mirrors `eyewall-poller`'s H2H narrative pattern, `shared.js`'s `buildHeadToHeadPayload` + `/team-seasons/head-to-head/narrative`):** the correct answer and all three distractors are real values queried from `player_seasons`/`pwhl_player_seasons` and shuffled in Python **before** the LLM is ever called. The model is never asked to supply, verify, or rank a fact — its only job is to phrase one question sentence around a stat category name. It never sees which of the four names is correct, so it cannot get that part wrong.

**Two real bugs found generating live, both about the question *sentence*, not the answer (options/`correct_index` were never touched):**
1. A bare team abbreviation (`"SEA"`) got read by the model as "Southeast Asia."
2. Even given the *correct* full team name ("Seattle Torrent"), the model substituted the wrong-but-more-famous NHL team sharing that city ("Seattle Kraken") instead.

Fixed by removing team names from the prompt entirely for medium tier — the frontend conveys team identity via a logo (the row's own real `team` column), never via anything the model says. Full detail in the module's own docstring.

**Real-value sourcing:** one stat category (`goals`/`assists`/`points`/`pp_goals`) applies globally per day, rotating by `question_date.toordinal() % 4` — simpler than per-scope rotation and thematically fine ("today everyone's answering a goals question, at different scopes"). A scope with fewer than 4 qualified players, or where the top 3 distractors can't be made value-distinct from the leader, fails closed (skipped, not guessed) rather than shipping an ambiguous question.

```bash
python trivia_questions.py --sport nhl    # what nightly.yml calls — NHL only
python trivia_questions.py --sport pwhl   # what pwhl-nightly.yml calls — PWHL only, first-ever AI step in that workflow
python trivia_questions.py --dry-run      # preview without writing, both sports
```

Writes to `trivia_questions` (public-read, no owner — same RLS posture as `player_narratives`) via `on_conflict=question_date,tier,sport,team`. Schema + RLS: `docs/session92_trivia_tables.sql`.

---

## Live Season Resolution

Added 2026-07 (replacing a yearly manual flip across ~8 hardcoded locations in 3 repos). `season_lookup.py` is a small shared module that reads the current NHL and PWHL season from the Worker's `GET /config/seasons` endpoint (see `seasons.js` in `eyewall-poller`), which is itself resolved live from the NHL and HockeyTech APIs and cached in KV.

```python
from season_lookup import get_nhl_season, get_pwhl_season

nhl_season = get_nhl_season()  # int, e.g. 20252026
pwhl = get_pwhl_season()  # {"season_id": 8, "season_type": "regular", "start_year": 2025}
```

**`db.py`** and **`pwhl_stats.py`** both call these at import time — `NHL_SEASON` and `PWHL_SEASON` are now the *live-resolved* values, with the `.env` values above used only as a fallback if the Worker is unreachable. `pwhl_salaries.py`'s `SEASON_LABEL` (e.g. `"2025-26"`) and `moneypuck.py`'s `MP_SKATERS_URL`/`MP_GOALIES_URL` year are both derived the same way, closing two separate bugs where those values used to be hardcoded independently of `NHL_SEASON`/`PWHL_SEASON` and could silently drift out of sync.

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

**`compute_gw_goals()` — rolls up `pwhl_shot_events.is_game_winning_goal` into `pwhl_player_seasons.gw_goals`.** `fetch_skater_stats()` above hardcodes that column to 0 (HockeyTech's league-wide `players` view doesn't carry it), but the per-goal flag exists on `pwhl_shot_events` via that module's `gameSummary` merge. Run separately via `python pwhl_stats.py --gw-goals-rollup-only [season_id]` — same merge-upsert-only-existing-rows shape `compute_toi_per_game()`'s `--toi-rollup-only` uses for `toi_per_game` — must run *after* `pwhl_shot_events.py` has ingested that night's goals.

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

**Worker endpoint:** `POST /pwhl/news/ingest` — merges new articles with existing cached articles, deduplicates by ID, keeps top 60, stores in `pwhl:news` KV with a 25hr TTL (fixed from 30min during the news-ingestion investigation — the short TTL meant `pwhl:news` sat empty most of the day between this script's own infrequent runs).

### `pwhl_milestones.py`
Mirrors `milestones.py` (NHL) in structure — see that module's entry above for the shared detection categories. Same shared `milestones` table, `is_pwhl=true`. Key differences from the NHL version:
- Thresholds are tuned to real PWHL scoring volume (30 GP/season, not NHL's 82) rather than scaled proportionally — season goals 15/20, season points 20/30, career points 50/100, career wins 25/50. All verified against real data (career wins confirmed 2026-08-14: leader is Ann-Renée Desbiens at 42, 25 already fired for the top 3 goalies, 50 is a real future target).
- Career point/win totals need no external API call — the PWHL launched Jan 2024, so summing every historical `season_type='regular'` row already covers full career history.
- Shorthanded-goal detection uses HockeyTech's ground-truth `is_short_handed` flag where available (merged from `gameSummary`), falling back to a penalty-window heuristic for older/un-merged rows.
- `milestone_type` values are identical to `milestones.py`'s across every category, including `"sh_goal"` — this diverged as `"shorthanded_goal"` for a period (until 2026-08-13), which silently broke the frontend's icon/label lookup and detail-line rendering for every PWHL shorthanded goal (both keyed only on the literal string `"sh_goal"`). If you ever add a new milestone type to either pipeline, keep the string identical on both sides unless there's a real reason not to.

```bash
python pwhl_milestones.py                    # yesterday's games
python pwhl_milestones.py --date 2026-03-15   # specific date
python pwhl_milestones.py --since 2026-01-01  # date range through yesterday
python pwhl_milestones.py --game 261          # single game_id (debugging/spot-checks)
```

### `pwhl_goalie_percentiles.py` (added 2026-08)
Goalie-side analogue of `pwhl_percentiles.py` (skaters) — a category NHL's own `moneypuck.py`/`goalie_seasons` already had (GSAX, GSAX/60, 5v5/HD/MD/PK SV%) but PWHL never built, since `pwhl_percentiles.py` explicitly excludes goalies. Computes:
- **GSAX-proxy**: same 3-bucket danger-zone xG proxy `pwhl_shot_xg.py` uses for shooters (independent copy, this codebase's usual convention for feed-derived math), applied to shots *against* a goalie. `gsax = xg_against - actual_goals_against`.
- **GSAX/60**: rate-adjusted using `pwhl_goalie_seasons.toi` (an `"MM:SS"` string from HockeyTech, reliably populated).
- **5v5 SV% / PK SV%**: reuses `pwhl_strength_state.py`'s penalty-window logic (same machinery `pwhl_milestones.py`'s SH-goal detection and `pwhl_stats.py`'s 5v5 Corsi already use), applied per-shot. "PK for the goalie" = the *other* team in that shot's game (not the shooter's own) has an active penalty window — this sidesteps needing the goalie's own `team_id` via `game_log` home/away.
- **HD/MD SV%**: same distance buckets as the xG proxy.

`MIN_GP = 10`, matching the skater convention — 11 of 20 current-season-8 goalies qualify.

**Danger-zone values recalibrated (2026-08)**: the bucket values were originally ported verbatim from NHL's own calibration (rapm.py), unvalidated against PWHL's actual shot-danger distribution — this made absolute GSAX magnitude run high relative to NHL norms (+22 to +66 for current-season starters, vs NHL's typical -15 to +25). Fixed by computing real PWHL goal-conversion rates per bucket from 24,885 historical shot attempts across every season with shot-event coverage: high 0.14 (was 0.20), medium 0.08 (was 0.07), low 0.03 (unchanged). Same fix applied to `pwhl_shot_xg.py`'s skater-side `finishing` metric, which shares this constant. Production `gsax`/`finishing`/percentile columns backfilled with the recalibrated values.

**Bug caught building this**: a first version wrongly counted `blocked_shot` events as shots the goalie faced — a blocked shot never reaches the goalie at all (stopped by a teammate defender in front of them). This inflated both shots-faced and xG-against by ~20% (blocked_shot's share of the feed), silently boosting every goalie's GSAX. Fixed by scoping goalie-faced shots to `("goal", "shot")` only — see `GOALIE_FACED_TYPES` in the module.

```bash
python pwhl_goalie_percentiles.py             # current season (PWHL_SEASON)
python pwhl_goalie_percentiles.py 8           # specific season_id
```

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

## AHL Pipeline Modules

All AHL modules use the same HockeyTech/LeagueStat vendor API as PWHL (`lscluster.hockeytech.com`) but a different client config (`client_code=ahl`, `key=ccb91f29d6744675`, `league_id=4`, `site_id=3`) and write to `ahl_*` Supabase tables. Dedicated tables rather than a shared NHL/PWHL-shaped schema with a league discriminator column — deliberate choice (see `docs/ahl_new_tables_ddl.sql`'s header comment): AHL's real data shape has no shift/TOI/attempts-based-Corsi/faceoff/hits data at all, so forcing it into PWHL's schema would mean permanently-null columns on every row. No `ahl_teams` table exists either — same as PWHL, team display metadata lives in the frontend, and `ahl_stats.py` hardcodes `TEAM_ID_MAP` the same way `pwhl_stats.py` does. Full investigation notes and endpoint-by-endpoint findings: `docs/hockeytech-ahl-api-notes.md`.

**Season resolution deliberately does NOT go through `season_lookup.py`'s Worker-backed pattern** the way `pwhl_stats.py`/`nhl_stats.py` do — AHL was this pipeline's first module, and `eyewall-poller` had no AHL season-config endpoint at the time it was built (adding one is a fast-follow, not a blocker). `ahl_stats.py`'s own `resolve_current_season()` queries HockeyTech's live `seasons` feed directly (env var `AHL_SEASON` as a fallback), picking the most recent `career="1"` season whose `start_date` has already passed — **not simply the max `season_id`**. This matters in practice: confirmed live 2026-08-29 that the feed's highest `career=1` season_id (94, "2026-27 Regular Season") has a `start_date` of 2026-10-02, still in the future — naively taking the max would return a season with zero games, the same mistake `docs/hockeytech-api-notes.md`'s PWHL section already documents avoiding. Every other AHL module imports `resolve_current_season()`/`resolve_season_type()` from `ahl_stats.py` rather than re-deriving season logic itself.

### `ahl_stats.py`
Fetches rosters, skater/goalie/team stats, and the game log. Structurally mirrors `pwhl_stats.py` (same vendor, same `sections[].data[].row` shape for `feed=statviewfeed` views — `extract_rows()` is an unmodified copy), but `feed=modulekit` views (`roster`, `teamsbyseason`, `seasons`, `scorebar`) nest everything under a top-level `"SiteKit"` key instead, handled by a separate `_modulekit_get()` helper.

```bash
python ahl_stats.py                  # current season (live-resolved)
python ahl_stats.py 90               # specific season_id (90 = 2025-26 Regular)
```

**Real field/param differences from PWHL, confirmed live and written up in `docs/hockeytech-ahl-api-notes.md`:**
- `feed=modulekit&view=roster` wants `season_id`, not `season` — sending `season` silently returns an empty roster rather than an error. `teamsbyseason` wants the opposite param name (`season`, not `season_id`).
- Roster is a flat list (no Forwards/Defenders/Goalies sections the way PWHL's is), height is hyphenated feet-inches (`"6-3"`, its own `_parse_height_inches()` — not PWHL's apostrophe-format regex), and `weight` is real data (PWHL's is always `"0"`, never ingested there).
- The skater `players` view has no `shooting_percentage`/`power_play_assists`/`short_handed_assists` fields at all — confirmed absent, not occasionally null — so `ahl_player_seasons` has no columns for them.
- `wins` on the team-stats view is already the season total (regulation + OT/SO) — no PWHL-style `regulation_wins + non_reg_wins` addition needed. `ot_losses` and `shootout_losses` are reported as two separate columns, unlike PWHL's single combined `non_reg_losses`.
- The game log comes from `feed=modulekit&view=scorebar` — a completely different view from PWHL's `feed=statviewfeed&view=schedule`, found via live network capture. It gives `HomeID`/`VisitorID` directly, no PWHL-style city-name-to-team_id mapping needed.

**`_modulekit_get()`'s JSONP-unwrap bug — the "roster-fetch mystery," RESOLVED 2026-08-30:** for weeks this function treated the FIRST `"("` anywhere in a response as a JSONP wrapper's open-paren and the LAST `")"` as its close. `modulekit/roster` responses are plain JSON, never actually JSONP-wrapped, but routinely contain literal parentheses in real field values (e.g. a `draft_status` string like `"Prince George Cougars (WHL) (College) 2019"`) — the old logic sliced straight through the middle of otherwise-valid JSON and corrupted it. This silently broke ~23 of 32 teams' roster fetches on every nightly run, non-deterministically by team (whichever teams happened to have a parenthesized affiliation string on a player that night). Three earlier diagnostic PRs (#91, #92, #93) each guessed a specific failure *shape* ("truly empty HTTP body," then "well-formed JSONP envelope with nothing between the parens") and were each disproven by checking the diagnostic's own output against a real run — the response was never malformed on the wire, the unwrap step was mangling a well-formed one. Found while building `echl_stats.py` (hit the identical symptom via a real Florida Everblades player's `draft_status`), then reproduced directly against AHL data: 32 of 33 teams' rosters failed under the old logic, 0 failed after changing the guard to `if text.startswith("(") and text.endswith(")")`. Fixed in both `ahl_stats.py` and the new `echl_stats.py` in the same PR (#98). **Lesson, kept in the code's own comments:** a diagnostic that keeps returning "no answer" across several iterations is a sign the working theory (a cache-regeneration race, in this case) may be entirely wrong, not just under-instrumented — the real bug was in code that had shipped untouched since the original PR and was never itself a suspect. Note `ht_get()` (the `statviewfeed` fetcher) still uses the older unconditional unwrap (`if "(" in text: ...`) — left as-is because `statviewfeed` responses genuinely are JSONP-wrapped and haven't shown this failure mode; only the `modulekit` views (roster/scorebar/seasons) needed the fix.

**A real historical franchise rename, not a bug:** Bridgeport Islanders (`team_id` 317, code `"BRI"`) relocated to become the Hamilton Hammers (`team_id` 457, code `"HAM"`) for 2026-27. `teamsbyseason` only ever returns each franchise's CURRENT identity, even when queried with an old `season_id` — so ingesting a historical season (e.g. 90, 2025-26) needs the OLD code merged in separately. `_HISTORICAL_TEAM_CODES` holds these (currently just `317: "BRI"`), merged into `TEAM_ID_MAP` at import time — add future renames there, not to the main map.

**`_season_day_window()` — a real production bug, not a hypothetical:** the game-log fetch (`feed=modulekit&view=scorebar`) does NOT filter by `season_id` the way PWHL's `schedule` view does. A blanket `numberofdaysback=10000&numberofdaysahead=10000` (the pattern this was originally copied from PWHL with) returns games sorted OLDEST-first across the league's entire 20+-year history, and `limit` truncates the response before ever reaching a recent season — confirmed live: a 5000-game pull returned only seasons 1-69, zero season-90 games. `_season_day_window()` fixes this by computing a day-window that actually brackets the target season's own `start_date`/`end_date` (padded ±3 days), instead of a huge blanket window.

**Bigger-than-a-bug finding: AHL's entire 2025-26 regular season was never ingested.** The original PR only ever ran a "current season" ingestion, and by the time it first ran, `resolve_current_season()` was already returning season 92 (2026 Calder Cup Playoffs, 24 games) — so `season_id` 90 (the actual ~72-game regular season) sat with ZERO rows anywhere in Supabase for weeks. This silently affected every AHL view already shipped in the frontend, not just a new one — every AHL page was quietly showing playoffs-only data instead of the full season. Fixed by adding a `workflow_dispatch` `season_id` input to `ahl-nightly.yml` (the scripts already accepted a `season_id` CLI arg; the workflow just never passed one through, PR #94) and triggering a manual backfill (`gh workflow run ahl-nightly.yml --ref <branch> -f season_id=90`, ~41 minutes for all 32 teams).

### `ahl_game_boxscore.py`
Per-game, per-player box score — fetches `statviewfeed/gameSummary`'s `homeTeam`/`visitingTeam.skaters[]`/`goalies[]` and writes one row per player per game to `ahl_skater_game_box`/`ahl_goalie_game_box`. Fills the per-game granularity gap `ahl_player_seasons`/`ahl_goalie_seasons` (season aggregates only) don't have. Structurally mirrors `pwhl_game_boxscore.py`.

```bash
python ahl_game_boxscore.py                  # current season
python ahl_game_boxscore.py 90               # specific season_id
python ahl_game_boxscore.py --game 1028992   # single game_id (debug)
```

**Confirmed real gap, not a data-entry hole:** every skater's `hits`, `faceoffAttempts`, `faceoffWins`, `blockedShots`, and `toi` field in `gameSummary` reads exactly `0`/`"0:00"` regardless of real ice time (confirmed against a real completed game, 1028992 — a skater with a recorded shot and assist still shows `toi: "0:00"`), consistent with AHL's PBP having no hit/faceoff event types at all. These fields are NOT ingested at all — no columns for them on `ahl_skater_game_box` — rather than stored as a fabricated always-zero value. Goalie fields (`timeOnIce`/`shotsAgainst`/`goalsAgainst`/`saves`) are unaffected and real (confirmed: Levi 59:49 TOI / 32 SA / 5 GA / 27 SV in the same game) and are kept.

### `ahl_shot_events.py`
Fetches play-by-play for completed games and extracts shot attempts + goals with coordinates. Structurally mirrors `pwhl_shot_events.py`, but the underlying PBP schema is simpler for AHL (and ECHL) than for PWHL.

```bash
python ahl_shot_events.py                    # current season
python ahl_shot_events.py 90                 # specific season_id
python ahl_shot_events.py --game 1028362     # single game_id (debug)
```

**Real, confirmed schema difference from PWHL that simplifies this module considerably:** `goal` is its own distinct PBP event type here, unlike PWHL (whose PBP only has goals embedded in `shot` events via an `isGoal` flag — no separate `goal` event exists there). AHL's `goal` event already carries `assists[]`, `properties` (`isPowerPlay`/`isShortHanded`/`isEmptyNet`/`isPenaltyShot`/`isInsuranceGoal`/`isGameWinningGoal`), and `plus_players[]`/`minus_players[]` directly — no separate `gameSummary` fetch/merge step is needed the way `pwhl_shot_events.py`'s `merge_game_summary()` requires. Confirmed across 16+ real games (both AHL and ECHL) that `shot`-with-`isGoal=true` and `goal` describe the same goal with near-exact 1:1 counts, `goal` being a strict superset — so `shot` rows are ingested only when NOT a goal, `goal` rows separately, with no dedup/merge logic needed at all.

Also confirmed absent from AHL's (and ECHL's) PBP entirely, across the same 16+-game sample: `blocked_shot`, `hit`, `faceoff` event types — not a parsing gap, genuinely not charted for either league. Coordinate transform reuses PWHL's unmodified `CANVAS_W`/`CANVAS_H` (600×300) fold — an 8-game AHL sample landed in the same x/y range as PWHL's own sample. Do one visual overlay check against a real rink before fully trusting this on a shot map — numeric range matching alone doesn't catch an axis swap.

**Real production bug, fixed same-day as the initial ship (PR #90):** the first real `ahl-nightly.yml` run wrote 526 skater rows / 34 goalie rows / 23 team rows / 89 games cleanly, but crashed on 5 of 89 games with Postgres error `21000` ("ON CONFLICT DO UPDATE command cannot affect row a second time"). Root cause: the natural key omitted `x_raw`/`y_raw`, so two shots by the same player in the same recorded second collided within a single upsert batch — the exact failure mode `pwhl_shot_events.py` had already solved the same way. Fixed by widening the key to include coordinates (`docs/ahl_shot_events_constraint_fix.sql`); `echl_shot_events.py` was built with this fix already in place from day one rather than rediscovering it.

### `ahl_penalty_shots.py`
Ingests the PBP `penaltyshot` event (makes AND misses) directly — unlike `pwhl_penalty_shots.py`, which deliberately avoids the PBP `penaltyshot` event (PWHL's version has a thinner team object) in favor of `gameSummary`'s `penaltyShots[]` key. AHL's PBP `penaltyshot` event already has a fully-resolved `shooter_team` object plus full shooter/goalie player objects, so no `gameSummary` fetch is needed at all here.

```bash
python ahl_penalty_shots.py                  # current season
python ahl_penalty_shots.py 90               # specific season_id
python ahl_penalty_shots.py --game 1028362   # single game_id (debug)
```

No coordinate data exists for penalty shots (make or miss) — same as PWHL, `ahl_penalty_shots` has no x/y columns and these events are never written to `ahl_shot_events`.

### `ahl_live_refresh.py`
Lightweight, frequent refresh of just `ahl_game_log`'s live-volatile fields (`game_state`, `game_status_code`, `home_score`, `away_score`) for games in a ±1-day window around today. Run via `live-score-refresh.yml`'s 5-minute cron, separate from `ahl_stats.py`'s full nightly ingest.

**Why this exists:** `ahl_stats.py` only runs once nightly (3:40 AM ET) and writes the whole season including future/scheduled games — nothing updates `game_state`/scores again until the following night. A game happening today would sit at whatever status the last nightly snapshot showed for the entire day, even after it goes live or finishes, meaning the Worker's per-minute live-game polling could never actually see a live game. This traced back to a real gap found while building AHL/PWHL live-tracking parity: `pwhl_game_log.game_state` had exactly the same problem, so `pwhl_live_refresh.py` was built as a companion fix in the same PR (#97) rather than an AHL-only patch.

**`game_status_code`** is HockeyTech's numeric `GameStatus` field from `scorebar` — a companion to the existing string `game_state` (`GameStatusString`). Needed because a not-yet-started game's `GameStatusString` is literally its scheduled clock time (e.g. `"7:00PM"`), not a state word, so string-only matching can't reliably distinguish "scheduled" from an unrecognized live state. Confirmed live: `1`=scheduled, `4`=final; `2`/`3` unconfirmed (no in-progress game observed during this build) — the Worker treats "not 1, not 4" as live rather than guessing the exact code. Requires `docs/live_score_refresh_ddl.sql` (adds `game_status_code` to `ahl_game_log`/`pwhl_game_log` — `echl_game_log` got the column from day one in its own foundation DDL, no retrofit needed).

```bash
python ahl_live_refresh.py
```

### `ahl_news.py`
Fetches AHL news from RSS and POSTs to the Worker's `/ahl/news/ingest` endpoint. Runs from GitHub Actions (Cloudflare Workers IPs are blocked by most RSS sources — same reason `pwhl_news.py` runs from Actions). Mirrors `pwhl_news.py`'s structure exactly.

```bash
python ahl_news.py
```

**Sources — all 3 AHL-scoped by construction, unlike PWHL's, so no keyword filter (`AHL_KEYWORDS`) is needed anywhere in this file:** `theahl.com/feed` (the official league site — no PWHL equivalent exists, since pwhl.com has no news RSS at all), `thehockeywriters.com/category/ahl/feed/` (a dedicated AHL category feed), and `oursportscentral.com/feeds/l17.xml` (AHL press releases, league id 17 on that site — mirrors PWHL's `osc-pwhl` pattern on the same site).

---

## AHL Season ID Map

| ID | Season | Type | Notes |
|----|--------|------|-------|
| 46 | 2004-05 | Regular | Earliest cleanly-listed season |
| 90 | 2025-26 | Regular | Most recent completed regular season |
| 92 | 2026 | Calder Cup Playoffs | 24 games, Apr-Jun 2026 |
| 94 | 2026-27 | Regular | Starts 2026-10-02, zero games as of this writing |

Confirmed live via `feed=modulekit&view=seasons`, 2026-08-29 — season_id is NOT sequential-by-year the way it might look (seasons back to 2004-05 exist). Always resolve current from the live feed (`ahl_stats.py`'s `resolve_current_season()`), never hardcode a "current" value here.

---

## ECHL Pipeline Modules

All ECHL modules use the same HockeyTech/LeagueStat vendor as AHL/PWHL (confirmed live 2026-08-30) and write to `echl_*` tables — structurally a near-exact mirror of `ahl_*.py` (`client_code=echl`, `key=2c2b89ea7345cae8`, `league_id=1`, `site_id=0`, same base URL). Dedicated tables, same reasoning as AHL's own. Full DDL: `docs/echl_new_tables_ddl.sql`/`docs/echl_game_boxscore_ddl.sql` — both straight `ahl_` → `echl_` renames of AHL's DDL, confirmed live to have the identical data-shape ceiling (no shift data, no hit/faceoff/blocked_shot PBP events, hits/faceoffs hardcoded `"0"` in box scores). ECHL was deliberately scoped as a foundation + basic-display pass, NOT full AHL parity, in its first build — matching AHL's own two-pass history (foundation first, parity-with-PWHL as a later, separate pass).

**One real operational difference from AHL/PWHL/OHL/WHL/QMJHL, worth remembering if this key ever breaks:** ECHL's HockeyTech key is NOT exposed on echl.com's own site — the site was rebuilt on Laravel/Livewire and renders stats server-side, so the "open the network tab" recovery path AHL/PWHL use doesn't work here. This key was recovered from `sportsdataverse-py`'s league registry (`sportsdataverse/hockeytech/_leagues.py`) and independently re-verified live against the real feed. If it ever stops working, re-check that registry first — a network-tab hunt on echl.com will not work, for the same reason it didn't during the original investigation.

### `echl_stats.py`
Fetches rosters, skater/goalie/team stats, and game log. Same season-resolution pattern as `ahl_stats.py` (live-resolves from HockeyTech's own `seasons` feed, `ECHL_SEASON` env var fallback, "most recent started `career=1` season" logic, not simply max `season_id`).

```bash
python echl_stats.py                  # current season (live-resolved)
python echl_stats.py 73               # specific season_id (73 = 2025-26 Regular)
```

**One real field-shape difference from AHL, confirmed live 2026-08-30 (not assumed to transfer):** ECHL's `players` (skaters) `statviewfeed` view carries `team_name` (e.g. `"Kansas City Mavericks"`), NOT `team_code` the way AHL's (and ECHL's own goalie/team views) do — `fetch_skater_stats()` resolves `team_id` via a separate `TEAM_ID_BY_NAME` map instead of `CODE_TO_TEAM_ID` for that one view only. Goalie and team views both carry `team_code` normally.

`_modulekit_get()` was built with the fixed JSONP-unwrap guard (`if text.startswith("(") and text.endswith(")")`) from day one — this module is actually what originally surfaced AHL's own long-standing roster-fetch bug (see `ahl_stats.py`'s writeup above): a Florida Everblades player's `draft_status` field containing literal parentheses hit the identical corrupted-JSON symptom while this file was being written, which is what triggered re-examining (and fixing) `ahl_stats.py`'s copy of the same helper in the same PR (#98).

### `echl_game_boxscore.py`
Same purpose/shape as `ahl_game_boxscore.py` — per-game skater/goalie box scores from `gameSummary`. Same confirmed data wall: every skater's `hits`/`faceoffAttempts`/`faceoffWins`/`blockedShots`/`toi` reads exactly `0`/`"0:00"` regardless of real ice time (confirmed live, game 24296 — Wyatt McLeod has a real recorded shot but `toi: "0:00"`), not ingested at all. Goalie fields are real (confirmed: Hunter Jones 59:16 TOI / 28 SA / 5 GA / 23 SV, same game) and kept.

```bash
python echl_game_boxscore.py                # current season
python echl_game_boxscore.py 73             # specific season_id
python echl_game_boxscore.py --game 24296   # single game_id (debug)
```

### `echl_shot_events.py`
Same disjoint `shot`/`goal` event-pair structure as `ahl_shot_events.py` — confirmed live 2026-08-30 against a real game (24296: 81 events — `goalie_change` x4, `shot` x59, `penalty` x7, `goal` x11). Same confirmed absence of `blocked_shot`/`hit`/`faceoff` event types. Same coordinate canvas (confirmed live: x∈[36,579], y∈[12,282], matching AHL/PWHL's range). Built with the natural key already widened to include `x_raw`/`y_raw` from day one — AHL's own pipeline needed a follow-up migration for this (see `ahl_shot_events.py`'s writeup above); ECHL shipped correctly the first time.

```bash
python echl_shot_events.py                  # current season
python echl_shot_events.py 73               # specific season_id
python echl_shot_events.py --game 24296     # single game_id (debug)
```

### `echl_penalty_shots.py`
Same reasoning and shape as `ahl_penalty_shots.py` — reads the PBP `penaltyshot` event directly (confirmed live 2026-08-30, games 24320/24340, identical shape to AHL's: fully-resolved `shooter_team`, full shooter/goalie objects, no coordinates).

```bash
python echl_penalty_shots.py                # current season
python echl_penalty_shots.py 73             # specific season_id
python echl_penalty_shots.py --game 24320   # single game_id (debug)
```

### `echl_live_refresh.py`
Mirrors `ahl_live_refresh.py` exactly. Confirmed live 2026-08-30 that ECHL's own `modulekit&view=scorebar` has the identical `GameStatus`/`GameStatusString` shape as AHL's (1=scheduled, 4=final; 2/3 unconfirmed). **No DDL retrofit was needed for this module** — unlike AHL, `echl_game_log` already had `game_status_code` from the original foundation-pass DDL (`docs/echl_new_tables_ddl.sql` included it from day one).

```bash
python echl_live_refresh.py
```

### `echl_news.py`
Mirrors `ahl_news.py`, with one real difference: only **2** sources exist for ECHL, not 3. `echl.com` has no discoverable RSS feed at all (confirmed live 2026-08-30: `/feed` and `/rss` both 404 — same Laravel/Livewire rebuild reason its HockeyTech key isn't network-tab-discoverable either).

```bash
python echl_news.py
```

**Sources:** `thehockeywriters.com/category/echl/feed/` (dedicated ECHL category feed) and OurSportsCentral's ECHL press-release feed — **league id 18, NOT 17 like AHL's** (searched for it live rather than assumed; league ids on that site aren't sequential-by-launch-date). Both ECHL-scoped by construction, no keyword filter needed.

---

## ECHL Season ID Map

| ID | Season | Type | Notes |
|----|--------|------|-------|
| 73 | 2025-26 | Regular | Last completed full season |
| 75 | 2026 | All-Star | |
| 76 | 2026 | Kelly Cup Playoffs | |
| 77 | 2026 | Preseason | |
| 78 | 2026-27 | Regular | Not started as of this writing |

Confirmed live via `feed=modulekit&view=seasons`, 2026-08-30. ECHL's playoffs-season label convention is `"{year} Kelly Cup Playoffs"` — a real, different format from AHL's bare `"{year} Playoffs"` string (checked directly against `echl.com` rather than assumed to transfer from AHL, a recurring lesson across every league addition in this system).

---

## Database Schema

### NHL Tables
| Table | Description |
|-------|-------------|
| `players` | Player master. `team` (current-team affiliation) added for the Combined Prediction Calibration work — was previously fetched per-team by `fetch_roster()` but discarded before the upsert, leaving no way to answer "which team is player X on right now" anywhere in the codebase |
| `player_seasons` | Per-player stats + WAR/RAPM/percentiles. 21 total percentile categories as of Session 81 (10 MoneyPuck-derived + 11 NHL box-score), each with a league-wide `pct_*`, plus `pct_*_conf`/`pct_*_div` conference/division-scoped variants (63 `pct_*` columns total). Unique on `(player_id, season, game_type)` — `team` was dropped from this key in Session 81 after it caused 338 duplicate rows in production, see `nhl_stats.py`'s writeup above |
| `goalie_seasons` | Per-goalie stats + GSAX/percentiles. Same `(player_id, season, game_type)` uniqueness fix as `player_seasons` (Session 81) |
| `team_seasons` | Per-team stats + `xgf_pct` + `roster_war_score` + `faceoff_win_pct` (Session 80 — from the same `team/summary` endpoint already fetched for PP%/PK%/shots-per-game, just previously unmapped) + `hits`/`penalties` season totals (Session 81, rolled up from `game_log`). Regular-season rows also carry standings (`division_abbrev`/`conference_abbrev`/`wildcard_sequence`/`regulation_wins`/`clinch_indicator`, Session 57) and computed playoff race (`magic_number`/`tragic_number`/`clinched`/`eliminated`, `playoff_race.py`) — all `NULL` for playoff rows |
| `game_log` | All-team game-by-game results (one row per team per game), including per-game `hits`/`penalties` (Session 81) |
| `shot_events` | League-wide shot coordinates |
| `shift_events` | Per-player shift times |
| `zone_starts` | OZ/DZ/NZ start counts |
| `player_score_state_dist` | Score state distribution weights |
| `skipped_games` | Games skipped per pipeline module |
| `rapm_validation` | RAPM validation history |
| `game_summaries` | AI post-game summaries |
| `game_predictions` | AI pre-game predictions |
| `player_scouting` | AI scouting blurbs |
| `player_narratives` | (Session 56) AI narrative blurbs keyed on `(player_id, season, team, narrative_type)` — supports multiple blurb types. Written by `ai_results_vs_process.py` (`narrative_type='results_vs_process'`) and `ai_line_chemistry.py` (`narrative_type='line_chemistry'`, one row per unit member). **Found broken 2026-07:** RLS was enabled on this table at some point after creation with zero policies attached, silently blocking every anon-key read (`eyewall-poller`'s `/player-results-vs-process`) while writes kept succeeding via the pipeline's service-role key — the feature had real content (1,092 rows) that nobody could actually see. Fixed by `docs/player_narratives_rls_fix.sql` (adds the missing public-read policy); worth checking any other table for the same gap if this comes up again. |
| `game_scoring` | Goal-by-goal scoring data |
| `game_xg` | Per-game expected goals |
| `line_combinations` | Inferred lines and D pairs, all 32 teams (2026-07 — previously CAR-only) |
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

### AHL Tables
| Table | Description |
|-------|-------------|
| `ahl_players` | Player master. Unlike `pwhl_players`, carries a real `weight_lbs` (AHL's roster feed has real weight data; PWHL's is always `"0"` and never ingested there) |
| `ahl_player_seasons` | Per-player per-season stats (GP, G, A, PTS, +/-, PIM, shots, PP/SH goals). No `shot_pct`/`pp_assists`/`sh_assists` columns — confirmed absent from AHL's `players` view entirely, not just occasionally null |
| `ahl_goalie_seasons` | Per-goalie per-season stats (GP, W/L/OTL, SV%, GAA, shutouts, TOI as HockeyTech's `MM:SS` text) |
| `ahl_team_seasons` | Per-team per-season stats + PP%/PK%/special-teams counts. `wins` is already the season total (no `regulation_wins`/`non_reg_wins` split needed, unlike PWHL); `ot_losses`/`shootout_losses` are separate columns, unlike PWHL's combined `non_reg_losses` |
| `ahl_game_log` | Game results/schedule from `feed=modulekit&view=scorebar` (a different view from PWHL's `schedule`) — `game_status_code` (numeric HockeyTech `GameStatus`) added via `docs/live_score_refresh_ddl.sql` for live-game tracking |
| `ahl_shot_events` | Shot coordinates (`x_norm`/`y_norm`), `event_type` ('shot'\|'goal'). Goal rows carry `assist1_id`/`assist2_id`/PP/SH/EN/GWG flags directly from the PBP `goal` event — no PWHL-style `gameSummary` merge needed. Natural key includes `x_raw`/`y_raw` (see `ahl_stats.py`'s writeup above — a real production Postgres 21000 crash without it) |
| `ahl_penalty_shots` | Penalty shots (makes + misses), no coordinates — sourced directly from the PBP `penaltyshot` event (unlike PWHL, which needs `gameSummary`) |
| `ahl_skater_game_box` | Per-skater per-game box score: G/A/P, PIM, +/-, shots. No hits/faceoff/blocked-shots/TOI columns — confirmed always 0/`"0:00"` in the source feed |
| `ahl_goalie_game_box` | Per-goalie per-game box score: G/A/P, PIM, TOI (real data), shots/goals against, saves |
| `ahl_skipped_games` | Games skipped per AHL pipeline module (mirrors `pwhl_skipped_games`) |

### ECHL Tables
| Table | Description |
|-------|-------------|
| `echl_players` | Player master, same shape as `ahl_players` (real `weight_lbs`) |
| `echl_player_seasons` | Per-player per-season stats, same shape as `ahl_player_seasons` |
| `echl_goalie_seasons` | Per-goalie per-season stats, same shape as `ahl_goalie_seasons` |
| `echl_team_seasons` | Per-team per-season stats, same shape as `ahl_team_seasons` |
| `echl_game_log` | Game results/schedule, same `scorebar` source as AHL. `game_status_code` was included from day one in the original foundation DDL — no retrofit needed here, unlike AHL |
| `echl_shot_events` | Same shape as `ahl_shot_events`; natural key already includes `x_raw`/`y_raw` from day one (built after AHL's own fix was known) |
| `echl_penalty_shots` | Same shape as `ahl_penalty_shots` |
| `echl_skater_game_box` | Same shape as `ahl_skater_game_box`, same confirmed hits/faceoff/TOI-always-zero gap |
| `echl_goalie_game_box` | Same shape as `ahl_goalie_game_box` |
| `echl_skipped_games` | Games skipped per ECHL pipeline module |

### Shared Tables (both leagues, one table)

| Table | Description |
|-------|-------------|
| `trivia_questions` | (Session 92) Daily trivia — one row per `(question_date, tier, sport, team)`, `team` defaults to `'ALL'` for easy/hard so the unique constraint enforces one question per exact scope per day. Public-read, no owner (same RLS posture as `player_narratives`) — see [Daily Trivia](#daily-trivia). `tier='easy'`/`'medium'` written nightly by `trivia_questions.py`; `tier='hard'` is hand-inserted directly in the Supabase SQL editor. |
| `trivia_answers` | (Session 92) Signed-in users' trivia answers — `auth.uid()`-scoped RLS, same posture as `user_preferences` below. **Not written by this pipeline** — the frontend writes directly via the Supabase JS client (the one deliberate exception to "the frontend never talks to Supabase directly," same as `user_preferences`). Documented here because its DDL lives in this repo's `docs/` folder alongside every other schema reference. |
| `user_preferences` | (Session 90-91) One row per signed-in user — `favorite_team`/`favorite_sport`, synced from the frontend's Settings. **Not written by this pipeline** — same Supabase-Auth-direct exception as `trivia_answers` above. DDL: `docs/session90_user_preferences_table.sql` + `docs/session91_favorite_sport_column.sql`. |

---

## GitHub Actions Workflows

| Workflow | Schedule | Description |
|----------|----------|-------------|
| `nightly.yml` | 3 AM ET daily | NHL-only pipeline (`run.py` + Ruff lint) — `run.py`'s AI sub-pipeline now includes `trivia_questions.py --sport nhl` (Session 92) alongside `ai_summaries`/`ai_scouting`/`ai_results_vs_process`/`ai_line_chemistry` |
| `pwhl-nightly.yml` | 3:20 AM ET daily | PWHL stats/rosters, shot events, PBP events, game box scores, skater + goalie percentiles, milestones, news, daily trivia — 20 min offset to avoid Supabase contention. `trivia_questions.py --sport pwhl` (Session 92) is the first-ever AI-generation step in this workflow |
| `ahl-nightly.yml` | 3:40 AM ET daily | AHL news, stats/rosters/standings, per-game box scores, shot events, penalty shots — 20 min offset after PWHL's own nightly run. `workflow_dispatch` accepts an optional `season_id` to backfill a specific season (added after AHL's entire 2025-26 regular season was found to have never been ingested — see `ahl_stats.py` above) |
| `echl-nightly.yml` | 4:00 AM ET daily | Same step order as `ahl-nightly.yml` (news, stats, box scores, shot events, penalty shots), 20 min after AHL's own nightly run |
| `live-score-refresh.yml` | Every 5 minutes | Runs `ahl_live_refresh.py` + `pwhl_live_refresh.py` + `echl_live_refresh.py` in sequence — a narrow refresh of just `game_state`/`game_status_code`/scores so a live game doesn't sit stale until the next nightly run for any of the 3 leagues. 5 minutes is GitHub Actions' practical scheduling floor, not a hard real-time guarantee |
| `moneypuck-ingest.yml` | Nightly | MoneyPuck CSV fetch via GH runner (CF IPs blocked). Separate from `moneypuck.py`'s own fetch — feeds `eyewall-poller`'s `moneypuck:raw`/`moneypuck:skaters:{abbr}` KV cache, not Supabase. Tries a hardcoded `PRIMARY_YEAR`, falls back to `PRIMARY_YEAR - 1` on a non-200 (2026-07-20 fix — the primary year had been bumped ahead of MoneyPuck actually publishing that season, with no fallback, breaking the ingest for 4 days). Safe to bump `PRIMARY_YEAR` early each summer now; it just serves last season's data until MoneyPuck catches up. |
| `sbnation-ingest.yml` | Every 4 hours | 24 SBNation/Vox team-blog RSS/Atom feeds → Worker `/atom/ingest` (Session 61 — was `reddit-ingest.yml`, ran every 30 min and also fetched 32 subreddits despite Reddit having blocked GH Actions runner IPs the whole time; dropped the dead Reddit half and cut the cadence. Expanded from 5 to 24 feeds in the news ingestion investigation session — covers 28 of 32 NHL teams now, up from 5) |
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

### AHL / ECHL
Neither league is wired into `season_lookup.py`'s live Worker-based season resolution — each pipeline resolves its own current season directly from HockeyTech's `seasons` feed (`ahl_stats.py`/`echl_stats.py`'s own `resolve_current_season()`). No manual `AHL_SEASON`/`ECHL_SEASON` secret update is needed for the normal case — both are fallback-only, same as `NHL_SEASON`/`PWHL_SEASON` — but a manual `workflow_dispatch -f season_id=<id>` backfill is still needed to ingest a specific season the live resolver wouldn't pick on its own (e.g. the most recently COMPLETED regular season, once the live resolver has moved on to that season's playoffs or the next regular season) — see `ahl_stats.py`'s "2025-26 regular season never ingested" writeup above for why this matters in practice, not just in theory. AHL's 2026-27 regular season starts 2026-10-02; ECHL's 2026-27 regular season (season_id 78) also had not started as of this writing. No salary source, no shift/Corsi/WAR data exists for either league — see [Known Limitations](#known-limitations).

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

- **PWHL news:** CF datacenter IPs blocked by some RSS sources; `pwhl_news.py` (GH Actions) fetches successfully every night. Session: news ingestion investigation found the real gap wasn't the sources — `/pwhl/news/ingest`'s KV write used a 30min TTL for a once-nightly job, so the real articles it found were only ever visible for 30 minutes a day. Fixed to 25hr, and the Worker's own on-demand `fetchPWHLNews()` now merges with (rather than overwrites) whatever this nightly job already wrote. Also swapped the broken `espn-pwhl` source (ESPN has no working hockey/PWHL RSS category at all) for The Athletic's dedicated women's-hockey feed.
- **PWHL Corsi/Fenwick:** No missed shots in HockeyTech — FF% is SOG-based proxy only.
- **`nhl_stats.py` fragile loop:** `for game_type in [2, 3]` body references `game_type` as if a parameter — works via Python scoping but fragile. Fix before next major pipeline work.
- **UTA missing from `team_seasons`:** Excluded from power rankings until their row appears.
- **RAPM linemate collinearity:** Documented in `validate_rapm.py`.
- **Transactions/Injuries:** No reliable free NHL API. Deferred pending PuckPedia.
- **Reddit ingest:** removed (Session 61) rather than fixed — GH Actions IPs blocked by Reddit, new app registration blocked by Responsible Builder Policy, and every 30-min cron run was pure wasted GH Actions minutes with zero working output. Re-confirmed live (Session: news ingestion investigation) with a real GH Actions runner — still a 403 bot-block page. The per-team `TEAM_NEWS_SOURCES` reddit-* entries and the now-permanently-no-op `/reddit/ingest` route (both in `eyewall-poller`) were removed in the same session; 25 teams had reddit as their *only* team-specific source, and 21 of those 25 got a real replacement blog instead (see `sbnation-ingest.yml`'s comment for the list). MIN/STL/SEA/UTA still have no team-specific blog source at all (28 of 32 teams now do). Revisit Reddit only if a workaround surfaces; not planned for October 2026.
- **PWHL WAR/RAPM:** Blocked — HockeyTech PBP has no `player_change` shift events across all 3 seasons (confirmed June 2026). Revisit October 2026.
- **AHL/ECHL WAR/RAPM/Corsi: not buildable, a harder wall than PWHL's own shift-data gap, no revisit date set.** Confirmed live (2026-08-28/29: 4 AHL games across 3 seasons including the 2025 Calder Cup Finals G5, and 5 ECHL games) that `modulekit&view=gameshifts` returns `{"home":[],"visitor":[]}` for every real game tested — verified against a real PWHL control game (which DOES return populated shifts through the identical request shape), so this isn't a request-shape mistake on this pipeline's part. AHL/ECHL's PBP also has no `hit`/`blocked_shot`/`faceoff` event types at all (only `goal`/`shot`/`penalty`/`goalie_change`/`penaltyshot` confirmed present across 16+ games each) — the box-score schema even carries `faceoff_wins`/`faceoff_attempts`/`hits` fields per player, but they're hardcoded `"0"` in every game checked for both leagues, confirming it's genuinely not charted, not a PBP-only omission. Consequence: not even attempts-based Corsi/Fenwick is computable for either league, only shots-on-goal + goals — parity with a plain box score, not an advanced stat. Unlike PWHL's shift gap, there's no known future HockeyTech data source that would unblock this, so no revisit date is set.
- **No AHL/ECHL salary data source exists anywhere** — no PWHLPA-equivalent salary guide has been found for either league.
- **AHL/ECHL `_modulekit_get()` JSONP-unwrap bug ("the roster-fetch mystery"), RESOLVED 2026-08-30:** the unwrap logic treated the first `"("` anywhere in a `modulekit` response as a JSONP wrapper's open-paren and the last `")"` as its close, but `modulekit/roster` responses are plain JSON and routinely contain literal parentheses in real field values (e.g. a `draft_status` string like `"Prince George Cougars (WHL) (College) 2019"`), corrupting otherwise-valid JSON. Silently broke ~23 of 32 AHL teams' roster fetches per nightly run for weeks; three earlier diagnostic PRs (#91/#92/#93) each guessed the wrong failure shape before this was found while building `echl_stats.py`. Fixed in both `ahl_stats.py` and `echl_stats.py` by only stripping parens when the response actually starts/ends with them (PR #98). See `ahl_stats.py`'s README writeup above for the full history.
- **`ahl_shot_events`/`echl_shot_events` same-second-shot collision:** two shots by the same player in the same recorded second are real (confirmed live) and crash a same-batch upsert with Postgres error `21000` unless the natural key includes `x_raw`/`y_raw` — the same failure mode `pwhl_shot_events.py` already solved this way. AHL needed a follow-up migration (`docs/ahl_shot_events_constraint_fix.sql`) after hitting this in a real production run; `echl_shot_events.py` shipped with the wider key from day one.
- **Ruff formatting is a repo-wide CI gate, not per-league:** a new `echl_*.py` file that didn't match ruff's canonical line-wrapping for two chained Supabase query-builder calls broke the completely unrelated NHL nightly workflow's `ruff format --check .` step — that gate runs against the whole repo regardless of which league's files changed. Fixed via `ruff format .` pinned to the workflow's exact version (0.15.20), pure formatting, no logic change (PR #99). Run `ruff check .`/`ruff format --check .` locally before committing any new file to this repo, regardless of which league it's for.
- **HockeyTech boolean fields:** gameSummary's `properties` booleans arrive as strings (`"true"`/`"false"`), not JSON booleans — confirmed Session 34 via `pwhl_shot_events.py`'s gameSummary merge (a naive `bool(val)` marked every goal `true` for every flag). `gameCenterPlayByPlay`'s `isPowerPlay`/`isBench` on penalty events appear to be real JSON booleans by contrast (real `False` values already observed in production, pre-Session-34). Check any new HockeyTech boolean field against real data before trusting a bare `bool()` call on it.
- ~~`pwhl_milestones.py` undocumented~~ — resolved 2026-08-13: both `milestones.py` (NHL) and `pwhl_milestones.py` (PWHL) now have their own README sections. Same session found and fixed a real bug this gap likely helped hide: PWHL wrote shorthanded goals as `milestone_type: "shorthanded_goal"` while NHL wrote `"sh_goal"` — the one unintentional divergence out of every shared milestone type — which silently broke the frontend's icon/label lookup and detail-line rendering for every PWHL shorthanded goal. Also added current-season filtering to the Worker's `/milestones` and `/milestones/latest` routes (see `eyewall-poller`'s README) after a stale prior-season milestone sat as the *only* NHL row in the table for over a month with nothing newer to push it off.
- **Cache-busting order matters (learned 2026-07):** busting the Worker's KV cache *before* confirming the underlying data fix has actually landed just repopulates the same stale/empty entry on the next request. Always confirm the data is correct first (direct Supabase query, or hit the Worker endpoint with a fresh/never-cached key), then bust. This bit us twice during the expansion-team rollout — once for the season-resolution fix, once for the roster backfill.
- **HockeyTech `bootstrap` feed type:** it's `feed=statviewfeed`, not `feed=modulekit` — the latter returns a 200 OK with no real payload (`{"SiteKit":{"Undefined":"Undefined Tab bootstrap"}}`), which silently masqueraded as a fallback-triggering failure for a while before being caught. If a URL for this endpoint looks like it's built from a written description rather than a captured real request, verify it against actual DevTools traffic before trusting it.
- **One-off scripts in this repo:** `seed_expansion_teams.py` and `diagnose_roster_fetch.py` were one-time tools for the 2026-07 expansion backfill — safe to delete once no longer needed, not part of the regular pipeline. `test_season_lookup.py` is a real, permanent pytest suite — keep it. `backfill_pp_stats.py` (thin driver around `nhl_stats.enrich_game_log(..., force_all=True)`, kept as the reusable pattern for re-running the PP/PK enrichment against an arbitrary past season) and `diagnose_narrative_impact.py`/`regenerate_affected_narratives.py` (one-time tools for the 2026-07 PP-goals-bug narrative regeneration — the latter reads `narrative_impact_result.json`, a scratch data file from the former, not checked in) were built for that same session — safe to delete once no longer needed.

---

## Disclaimer

EyeWall Analytics is an independent, fan-built analytics project and is not
affiliated with, endorsed by, or sponsored by the National Hockey League
(NHL), the Professional Women's Hockey League (PWHL), or any of their
member teams. All team names, logos, and related marks are the property of
their respective owners and are used here for informational and editorial
purposes only.
