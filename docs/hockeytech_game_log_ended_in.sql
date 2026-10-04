-- ended_in on ahl_game_log / echl_game_log (2026-10-04).
--
-- 'OT' or 'SO' when a final went past regulation, null otherwise. Read from
-- scorebar's GameStatusStringLong ("Final OT" / "Final SO") by
-- hockeytech_stats.py's fetch_game_log() and hockeytech_live_refresh.py --
-- the short GameStatusString stored in game_state says "Final" for all three,
-- so the app could only ever show plain "Final" for AHL/ECHL games.
--
-- Run BEFORE merging the pipeline change that writes it: an upsert naming a
-- column the table doesn't have fails the whole chunk. The next nightly run
-- fills the current season; older seasons fill when re-ingested
-- (python ahl_stats.py <season_id> / python echl_stats.py <season_id>).

alter table public.ahl_game_log
  add column if not exists ended_in text check (ended_in in ('OT', 'SO'));

alter table public.echl_game_log
  add column if not exists ended_in text check (ended_in in ('OT', 'SO'));
