-- shot_events.event_id -- the NHL's own id for the play a row came from.
-- Run this in the Supabase SQL editor before deploying shot_events.py's
-- change or running backfill_shot_event_ids.py.
--
-- Why: every row here is one play from a game's play-by-play, and that
-- play has an eventId the NHL uses in its other feeds too -- the game
-- center `landing` payload names the same id for a goal, and a goal's
-- player-and-puck tracking replay is addressed by (gameId, eventId)
-- (eyewall-poller's /nhl/goal-replay). Without it, a row here can't be
-- pointed back at the play it came from, so the shot map's season-wide
-- view ("All N games") can offer neither the NHL's goal video nor
-- EyeWall's tracking replay -- only a single selected game can, because
-- there the events come straight from the play-by-play and still carry
-- their ids.
--
-- Nullable on purpose: rows written before this column existed have no
-- id until backfill_shot_event_ids.py re-processes their game, and the
-- app treats a missing id as "no replay for this dot" rather than an
-- error. Not unique -- eventId is only unique within a game, so
-- (game_id, event_id) is the identifying pair, and that's what the index
-- below serves.

alter table public.shot_events
  add column if not exists event_id integer;

comment on column public.shot_events.event_id is
  'NHL play-by-play eventId for this play. Unique within a game, not across games. Null for rows written before 2026-09 whose game has not been re-processed.';

create index if not exists shot_events_game_event_idx
  on public.shot_events (game_id, event_id);
