-- game_type on shot_events, shift_events, zone_starts and game_xg.
-- Run in the Supabase SQL editor, in the steps below. This repo has no
-- migration tooling.
--
-- Why: these four tables carry `season` and `game_id` but no game type,
-- and every season holds its preseason and playoff games alongside the
-- regular season's. A season-scoped read was all three game types unless
-- it decoded the type from each game_id in Python
-- (pipeline_common.nhl_game_type()), which is how 61 preseason games
-- became 2026-27's regular-season Corsi (#170). With game_type, a read
-- names the game type in the query itself: `?season=eq.S&game_type=eq.2`.
--
-- NHL game ids are YYYYTTNNNN (season start year, two-digit game type,
-- game number), so 2026020001 / 10000 % 100 = 2. Values are the NHL API's
-- gameType: 1 preseason, 2 regular season, 3 playoffs.
--
-- ============================================================================
-- Why a computed field and not a stored column
-- ============================================================================
-- The first version added a STORED generated column. On 2026-09-29 that
-- failed with `53100 could not extend file ... No space left on device`:
-- adding a stored column rewrites the whole table, and the rewrite of
-- shift_events (~4.4M rows) needs a second full copy of the table and its
-- indexes on disk until it commits. It rolled back; nothing was applied.
--
-- Instead, game_type is a PostgREST computed field: a SQL function taking
-- the table's row type. PostgREST lets a query select, filter and order on
-- it by name, exactly like a column (`game_type=eq.2`), so readers are the
-- same either way. Nothing is stored or rewritten, writers don't change,
-- and the functions are created instantly. The function is a one-line
-- IMMUTABLE SQL function, which Postgres inlines into the query, so a
-- `game_type = 2` filter becomes `(game_id / 10000 % 100)::smallint = 2`
-- and matches the expression indexes in step 2. The only disk cost is the
-- indexes themselves.
--
-- `select=*` doesn't include a computed field; name it (`select=game_id,
-- game_type`) to get it back.

-- ============================================================================
-- Step 0: check there's room. Run first, and read the numbers before step 2.
-- ============================================================================
-- default_transaction_read_only must be `off`. Supabase switches a
-- database to read-only when its disk passes 95%, and the failed rewrite
-- may have pushed it there; if it says `on`, fix that first (dashboard:
-- Project Settings > Compute and Disk).
--
-- show default_transaction_read_only;
-- select pg_size_pretty(pg_database_size(current_database())) as database_size;
-- select c.relname,
--        pg_size_pretty(pg_table_size(c.oid))          as table_size,
--        pg_size_pretty(pg_indexes_size(c.oid))        as indexes_size,
--        pg_size_pretty(pg_total_relation_size(c.oid)) as total
-- from pg_class c
-- where c.relname in ('shot_events', 'shift_events', 'zone_starts', 'game_xg');
-- select indexrelid::regclass as index, pg_size_pretty(pg_relation_size(indexrelid)) as size
-- from pg_index
-- where indrelid = 'public.shift_events'::regclass;
--
-- Each step-2 index needs about as much free disk as it will take up. The
-- shift_events one is roughly the size of shift_events_season_id_covering_idx
-- (same key columns plus one, same INCLUDE list).

-- ============================================================================
-- Step 1: the computed fields. Before merging the pipeline and poller PRs.
-- ============================================================================
-- Instant: no table is touched.

create or replace function public.game_type(public.shot_events) returns smallint
  language sql immutable parallel safe
  as $$ select (($1.game_id / 10000) % 100)::smallint $$;

create or replace function public.game_type(public.shift_events) returns smallint
  language sql immutable parallel safe
  as $$ select (($1.game_id / 10000) % 100)::smallint $$;

create or replace function public.game_type(public.zone_starts) returns smallint
  language sql immutable parallel safe
  as $$ select (($1.game_id / 10000) % 100)::smallint $$;

create or replace function public.game_type(public.game_xg) returns smallint
  language sql immutable parallel safe
  as $$ select (($1.game_id / 10000) % 100)::smallint $$;

comment on function public.game_type(public.shot_events) is
  'PostgREST computed field: NHL gameType from game_id. 1 preseason, 2 regular season, 3 playoffs.';
comment on function public.game_type(public.shift_events) is
  'PostgREST computed field: NHL gameType from game_id. 1 preseason, 2 regular season, 3 playoffs.';
comment on function public.game_type(public.zone_starts) is
  'PostgREST computed field: NHL gameType from game_id. 1 preseason, 2 regular season, 3 playoffs.';
comment on function public.game_type(public.game_xg) is
  'PostgREST computed field: NHL gameType from game_id. 1 preseason, 2 regular season, 3 playoffs.';

-- Make PostgREST see the new functions now rather than on its next reload.
notify pgrst, 'reload schema';

-- Check: every row should land in 1, 2 or 3. Anything else (or NULL) is a
-- game id that isn't YYYYTTNNNN and needs a look before merging.
--
-- select 'shot_events' t, game_type(s), count(*) from shot_events s group by 2
-- union all select 'shift_events', game_type(s), count(*) from shift_events s group by 2
-- union all select 'zone_starts', game_type(s), count(*) from zone_starts s group by 2
-- union all select 'game_xg', game_type(s), count(*) from game_xg s group by 2
-- order by 1, 2;

-- ============================================================================
-- Step 2: the indexes. Also before merging. One statement per run --
-- CONCURRENTLY can't share a submission or run in a transaction, and it
-- builds without blocking reads or writes.
-- ============================================================================
-- Readers page with `season = S and game_type = T and id > N order by id`
-- (keyset pagination). An index on (season, <game type expression>, id)
-- serves that in id order without a sort. The expression must be written
-- exactly as the function body above, or the planner won't match it.
--
-- shift_events keeps the INCLUDE list of the index it replaces
-- (docs/session47_shift_events_index.sql): without an Index Only Scan the
-- planner went back to scanning the primary key and filtering row by row,
-- ~9s a page, which timed out rapm's shift load. game_id is in the INCLUDE
-- list, which is what lets an Index Only Scan evaluate the expression.
--
-- If a build fails partway (disk again), it leaves an INVALID index behind:
-- `drop index concurrently if exists <name>;` before retrying.

create index concurrently if not exists shift_events_season_type_id_covering_idx
  on public.shift_events (season, (((game_id / 10000) % 100)::smallint), id)
  include (game_id, player_id, team, start_secs, end_secs);

create index concurrently if not exists shot_events_season_type_id_idx
  on public.shot_events (season, (((game_id / 10000) % 100)::smallint), id);

create index concurrently if not exists zone_starts_season_type_id_idx
  on public.zone_starts (season, (((game_id / 10000) % 100)::smallint), id);

create index concurrently if not exists game_xg_season_type_idx
  on public.game_xg (season, (((game_id / 10000) % 100)::smallint));

-- Check the shift_events plan uses the new index (expect
-- "Index Only Scan using shift_events_season_type_id_covering_idx" and a
-- few ms, not seconds). This is the query PostgREST builds for
-- `?season=eq.20232024&game_type=eq.2&id=gt.0&order=id&limit=999`:
--
-- explain (analyze, buffers)
-- select s.game_id, s.player_id, s.team, s.start_secs, s.end_secs
-- from shift_events s
-- where s.season = 20232024 and game_type(s) = 2 and s.id > 0
-- order by s.id
-- limit 999;

-- ============================================================================
-- Step 3: after the pipeline PR has merged and a nightly has run clean.
-- ============================================================================
-- Nothing reads shift_events by season alone any more, so the old covering
-- index is only write overhead and disk. Leave it until then: before the
-- merge, rapm.py's season-only shift load depends on it.
--
-- drop index concurrently if exists public.shift_events_season_id_covering_idx;
