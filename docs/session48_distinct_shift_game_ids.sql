-- Session 48 — backfilling distinct_shift_game_ids into the repo. This
-- function already existed live in Supabase (predates this repo's session
-- history; its search_path was already hardened via ALTER FUNCTION back in
-- Session 42) but was never tracked in any migration file. shift_data.py's
-- get_processed_games() depends on it (see shift_data.py:110-133) to avoid
-- paginating through ~1M shift_events rows client-side.
--
-- Pulled verbatim via `select pg_get_functiondef(oid) from pg_proc where
-- proname = 'distinct_shift_game_ids';` run in the Supabase SQL editor —
-- not reconstructed from call-site usage. This file is a reference copy,
-- not executed by any pipeline code (this repo has no migration tooling —
-- schema changes are applied directly in Supabase, same as every existing
-- pwhl_* table and docs/session42_new_tables.sql, docs/session43_rls_cleanup.sql,
-- docs/session47_shift_events_index.sql).
CREATE OR REPLACE FUNCTION public.distinct_shift_game_ids(p_season integer, p_offset integer DEFAULT 0, p_limit integer DEFAULT 1000)
 RETURNS TABLE(game_id bigint)
 LANGUAGE sql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
    SELECT DISTINCT se.game_id
    FROM shift_events se
    WHERE se.season = p_season
    ORDER BY se.game_id
    LIMIT p_limit
    OFFSET p_offset;
$function$
