-- shot_events.assist1_id / assist2_id / blocker_id -- who assisted on a
-- goal and who blocked a blocked shot.
-- Run this in the Supabase SQL editor BEFORE merging the shot_events.py
-- change that writes them: the nightly insert names these columns and
-- fails without them.
--
-- Why: the app's shot map shows a season's shots from this table
-- (eyewall-poller's /nhl/shots), and a dot's popup could name the
-- shooter and goalie but never the assists or the blocker -- only a
-- single selected game, read straight from the play-by-play, had them.
-- The play-by-play carries all three (details.assist1PlayerId,
-- assist2PlayerId, blockingPlayerId); this keeps them.
--
-- Nullable on purpose: most rows have none (only goals have assists,
-- only blocked shots have a blocker), and rows written before this
-- column existed stay null until their game is re-processed
-- (`python shot_events.py --reprocess <season>`; done for 2026-27).
-- NHL player ids, same as player_id/goalie_id; names come from `players`.

alter table public.shot_events
  add column if not exists assist1_id integer,
  add column if not exists assist2_id integer,
  add column if not exists blocker_id integer;

comment on column public.shot_events.assist1_id is
  'NHL player id of the primary assist on a goal. Null for non-goals, unassisted goals, and rows written before 2026-10 whose game has not been re-processed.';
comment on column public.shot_events.assist2_id is
  'NHL player id of the secondary assist on a goal. Null otherwise (see assist1_id).';
comment on column public.shot_events.blocker_id is
  'NHL player id of the player who blocked a blocked shot. Null for other event types and rows written before 2026-10 whose game has not been re-processed.';
