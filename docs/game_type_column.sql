-- game_type on shot_events, shift_events, zone_starts and game_xg.
-- Run in the Supabase SQL editor, in three steps. This repo has no
-- migration tooling.
--
-- Why: these four tables carry `season` and `game_id` but no game type,
-- and every season holds its preseason and playoff games alongside the
-- regular season's. A season-scoped read was all three game types unless
-- it decoded the type from each game_id in Python
-- (pipeline_common.nhl_game_type()), which is how 61 preseason games
-- became 2026-27's regular-season Corsi (#170). With the column, a read
-- names the game type in the query itself.
--
-- The column is generated from game_id, so no writer changes: NHL game ids
-- are YYYYTTNNNN (season start year, two-digit game type, game number), and
-- 2026020001 / 10000 % 100 = 2. Values are the NHL API's gameType:
-- 1 preseason, 2 regular season, 3 playoffs.
--
-- Writers must never send game_type (Postgres rejects a non-DEFAULT value
-- for a generated column); none of them do today.

-- ============================================================================
-- Step 1: the columns. Before merging the pipeline and poller PRs.
-- ============================================================================
-- Each ALTER rewrites its table under an ACCESS EXCLUSIVE lock, so reads
-- and writes wait until it finishes. shift_events is ~4.4M rows; run this
-- when the nightly isn't. The timeout is raised because the editor's
-- default can be shorter than the shift_events rewrite.

set statement_timeout = '30min';

alter table public.shot_events
  add column if not exists game_type smallint
  generated always as (((game_id / 10000) % 100)::smallint) stored;

alter table public.shift_events
  add column if not exists game_type smallint
  generated always as (((game_id / 10000) % 100)::smallint) stored;

alter table public.zone_starts
  add column if not exists game_type smallint
  generated always as (((game_id / 10000) % 100)::smallint) stored;

alter table public.game_xg
  add column if not exists game_type smallint
  generated always as (((game_id / 10000) % 100)::smallint) stored;

comment on column public.shot_events.game_type is
  'NHL gameType, generated from game_id: 1 preseason, 2 regular season, 3 playoffs.';
comment on column public.shift_events.game_type is
  'NHL gameType, generated from game_id: 1 preseason, 2 regular season, 3 playoffs.';
comment on column public.zone_starts.game_type is
  'NHL gameType, generated from game_id: 1 preseason, 2 regular season, 3 playoffs.';
comment on column public.game_xg.game_type is
  'NHL gameType, generated from game_id: 1 preseason, 2 regular season, 3 playoffs.';

-- Check: every row should land in 1, 2 or 3. Anything else (or NULL) is a
-- game id that isn't YYYYTTNNNN and needs a look before merging.
--
-- select 'shot_events' t, game_type, count(*) from shot_events group by 2
-- union all select 'shift_events', game_type, count(*) from shift_events group by 2
-- union all select 'zone_starts', game_type, count(*) from zone_starts group by 2
-- union all select 'game_xg', game_type, count(*) from game_xg group by 2
-- order by 1, 2;

-- ============================================================================
-- Step 2: the indexes. Also before merging. One statement per run --
-- CONCURRENTLY can't share a submission or run in a transaction.
-- ============================================================================
-- Readers page with `season = S and game_type = T and id > N order by id`
-- (keyset pagination). (season, game_type, id) serves that in id order
-- without a sort.
--
-- shift_events keeps the INCLUDE list of the index it replaces
-- (docs/session47_shift_events_index.sql): without an Index Only Scan the
-- planner went back to scanning the primary key and filtering row by row,
-- ~9s a page, which timed out rapm's shift load.

create index concurrently if not exists shift_events_season_type_id_covering_idx
  on public.shift_events (season, game_type, id)
  include (game_id, player_id, team, start_secs, end_secs);

create index concurrently if not exists shot_events_season_type_id_idx
  on public.shot_events (season, game_type, id);

create index concurrently if not exists zone_starts_season_type_id_idx
  on public.zone_starts (season, game_type, id);

create index concurrently if not exists game_xg_season_type_idx
  on public.game_xg (season, game_type);

-- Check the shift_events plan picks the new index (expect
-- "Index Only Scan using shift_events_season_type_id_covering_idx" and a
-- few ms, not seconds):
--
-- explain (analyze, buffers)
-- select game_id, player_id, team, start_secs, end_secs
-- from shift_events
-- where season = 20232024 and game_type = 2 and id > 0
-- order by id
-- limit 999;

-- ============================================================================
-- Step 3: after the pipeline PR has merged and a nightly has run clean.
-- ============================================================================
-- Nothing reads shift_events by season alone any more, so the old covering
-- index is only write overhead. Leave it until then: before the merge,
-- rapm.py's season-only shift load depends on it.
--
-- drop index concurrently if exists public.shift_events_season_id_covering_idx;
