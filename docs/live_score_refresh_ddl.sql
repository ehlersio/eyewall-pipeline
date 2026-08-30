-- New column for ahl_game_log/pwhl_game_log — supports
-- ahl_live_refresh.py/pwhl_live_refresh.py (Phase 6, AHL/PWHL live-game
-- parity). This repo has no migration tooling — same direct-in-Supabase
-- convention as docs/ahl_game_boxscore_ddl.sql.
--
-- game_status_code is HockeyTech's numeric GameStatus field from
-- feed=modulekit&view=scorebar — a companion to the existing string
-- game_state (GameStatusString) column. Confirmed live 2026-08-29 that a
-- NOT-YET-STARTED game's GameStatusString is literally its scheduled
-- clock time (e.g. "7:00PM"), not a state word — string-only matching
-- can't reliably tell "scheduled" apart from an unrecognized live state.
-- The numeric code is unambiguous: 1=scheduled, 4=final confirmed live;
-- 2/3 unconfirmed (no in-progress game observed during this build) --
-- the Worker treats "not 1, not 4" as live rather than guessing the
-- exact code for those.
--
-- ahl_game_log gets this column populated by BOTH ahl_stats.py's nightly
-- full ingest (already uses the scorebar view) and ahl_live_refresh.py's
-- frequent narrow refresh. pwhl_game_log gets it ONLY from
-- pwhl_live_refresh.py — pwhl_stats.py's own nightly ingest uses a
-- different view (feed=statviewfeed&view=schedule) confirmed live to have
-- no numeric status field at all, and is not being switched over (would
-- risk a working nightly job for no benefit it needs).

alter table public.ahl_game_log add column if not exists game_status_code integer;
alter table public.pwhl_game_log add column if not exists game_status_code integer;
