-- Session 81 — NHL percentile expansion + conference/division scoping.
-- Run this in the Supabase SQL editor. This file is a reference copy, not
-- executed by any pipeline code (this repo has no migration tooling --
-- schema changes are applied directly in Supabase, same as every existing
-- player_seasons/team_seasons column addition -- see e.g.
-- session57_new_columns.sql, session66_pwhl_percentile_parity.sql,
-- session80_new_columns.sql).

-- ── Piece 1: 11 new league-wide percentile categories ───────────────────
-- GP, +/-, SHG, GWG, Shots, TOI/G, FO%, Hits, Blocks, Takeaways, Giveaways
-- -- these are plain NHL box-score totals (already ingested by
-- nhl_stats.py onto player_seasons' games_played/plus_minus/sh_goals/
-- gw_goals/shots/toi_per_game/faceoff_win_pct/hits/blocked_shots/
-- takeaways/giveaways columns), ranked directly rather than as a per-60
-- rate like the existing 10 MoneyPuck-derived categories. See
-- moneypuck.py's NHL_BOX_STAT_COLUMNS/load_player_box_stats/box_stat.
alter table public.player_seasons
  add column pct_games_played integer,
  add column pct_plus_minus integer,
  add column pct_sh_goals integer,
  add column pct_gw_goals integer,
  add column pct_shots integer,
  add column pct_toi_per_game integer,
  add column pct_faceoff_win_pct integer,
  add column pct_hits integer,
  add column pct_blocked_shots integer,
  add column pct_takeaways integer,
  add column pct_giveaways integer;

-- ── Piece 2: conference/division-scoped percentiles ─────────────────────
-- All 21 categories (the 10 pre-existing MoneyPuck-derived ones + the 11
-- above) get a conference-scoped and a division-scoped variant, using
-- team_seasons.conference_abbrev/division_abbrev (already populated,
-- Sessions 57-59) joined via each player's resolved current team -- see
-- moneypuck.py's resolve_scoping_team/load_team_scoping/group_by_scope.
-- 21 categories x 2 scopes = 42 columns.
alter table public.player_seasons
  add column pct_ev_off_conf integer,
  add column pct_ev_off_div integer,
  add column pct_ev_def_conf integer,
  add column pct_ev_def_div integer,
  add column pct_pp_conf integer,
  add column pct_pp_div integer,
  add column pct_pk_conf integer,
  add column pct_pk_div integer,
  add column pct_finishing_conf integer,
  add column pct_finishing_div integer,
  add column pct_goals_conf integer,
  add column pct_goals_div integer,
  add column pct_a1_conf integer,
  add column pct_a1_div integer,
  add column pct_penalties_conf integer,
  add column pct_penalties_div integer,
  add column pct_competition_conf integer,
  add column pct_competition_div integer,
  add column pct_teammates_conf integer,
  add column pct_teammates_div integer,
  add column pct_games_played_conf integer,
  add column pct_games_played_div integer,
  add column pct_plus_minus_conf integer,
  add column pct_plus_minus_div integer,
  add column pct_sh_goals_conf integer,
  add column pct_sh_goals_div integer,
  add column pct_gw_goals_conf integer,
  add column pct_gw_goals_div integer,
  add column pct_shots_conf integer,
  add column pct_shots_div integer,
  add column pct_toi_per_game_conf integer,
  add column pct_toi_per_game_div integer,
  add column pct_faceoff_win_pct_conf integer,
  add column pct_faceoff_win_pct_div integer,
  add column pct_hits_conf integer,
  add column pct_hits_div integer,
  add column pct_blocked_shots_conf integer,
  add column pct_blocked_shots_div integer,
  add column pct_takeaways_conf integer,
  add column pct_takeaways_div integer,
  add column pct_giveaways_conf integer,
  add column pct_giveaways_div integer;

-- ── Piece 3: NHL Hits/Penalties (nhl_stats.py) ───────────────────────────
-- Closes the real pipeline gap identified in
-- NHL_PERCENTILE_AND_HITS_PENALTIES_BRIEF.md item 1 -- confirmed live that
-- gamecenter/{id}/right-rail's teamGameStats already carries a "hits"
-- category (same payload enrich_game_log already fetches for PP/PK), and
-- play-by-play already has explicit typeDescKey=="penalty" events with a
-- per-event eventOwnerTeamId (a directly countable event type, not a
-- situationCode reconstruction). See parse_team_hits()/
-- run_team_hits_penalties_rollup() in nhl_stats.py.
--
-- game_log: per-game team hit count + penalty count, filled by
-- enrich_game_log() alongside team_scored_first/PP/PK.
alter table public.game_log
  add column hits integer,
  add column penalties integer;

-- team_seasons: season-total rollup (sum of game_log.hits/penalties per
-- team), needed for the Shot Map "All N" summary cards -- the single-game
-- Shot Map view doesn't need this at all, it already reads live right-rail
-- data directly for the selected game.
alter table public.team_seasons
  add column hits integer,
  add column penalties integer;

-- ── Backfill for seasons ingested before this session ────────────────────
-- Existing game_log rows have hits/penalties = NULL. The nightly
-- incremental sweep (enrich_game_log's default force_all=False path) now
-- includes `hits.is.null` in its OR filter, so it WILL pick up and fill
-- every existing row over time -- but only as those specific game_ids
-- happen to still match the filter, once. For an immediate full-season
-- backfill (recommended for the current season at minimum), run:
--     python -c "from db import get_client; import nhl_stats; \
--       nhl_stats.enrich_game_log(get_client(), <season>, force_all=True); \
--       nhl_stats.run_team_hits_penalties_rollup(get_client(), <season>, 2); \
--       nhl_stats.run_team_hits_penalties_rollup(get_client(), <season>, 3)"
-- This is a call for the site owner to make (a real backfill run against
-- production) -- not run here, same convention as session66's
-- pwhl_shot_events backfill note.

-- ── Known gaps / decisions, not fixed by this migration ─────────────────
-- 1. PWHL conference/division scoping is NOT included here. Live checks
--    against HockeyTech's `view=teams` (groupTeamsBy=division, returns one
--    flat unlabeled section) and `view=bootstrap` (no division/conference
--    metadata anywhere) confirm PWHL has NO conference/division structure
--    today at all -- not "fewer than NHL," genuinely zero. pwhl_percentiles.py
--    is untouched by this session.
-- 2. Traded players: player_seasons.team is comma-joined for a mid-season
--    trade (e.g. "STL,DET", ~8% of rows live-checked this season).
--    Conference/division scoping resolves these to their LAST-listed
--    (current) team -- verified against 4 real 2025-26 trades via
--    player/{id}/landing's currentTeamAbbrev. See resolve_scoping_team()'s
--    docstring in moneypuck.py.
-- 3. A player whose current team has no team_seasons row yet (e.g. before
--    a season's games exist) gets NULL for every pct_*_conf/pct_*_div
--    column, same graceful-degradation shape as every other guarded pct_*
--    column in this module -- not a pipeline failure.
-- 4. This migration closes the DATA gap only. Wiring team_seasons.hits/
--    penalties (or game_log's per-game columns) through eyewall-poller's
--    Worker API and into the Shot Map "All N" summary cards in
--    eyewall-analytics (ShotMapView.jsx's `seasonStats`, alongside the
--    existing sog/blocked aggregates) is a separate follow-up in those two
--    repos, not attempted as part of this pipeline-only session.
