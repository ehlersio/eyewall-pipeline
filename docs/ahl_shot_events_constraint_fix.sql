-- Fix ahl_shot_events' natural key: widen it to include x_raw/y_raw.
--
-- Found in the first real production run of ahl_shot_events.py
-- (2026-08-29, workflow_dispatch of ahl-nightly.yml): 5 of 89 games in
-- season 92 crashed the ingest with Postgres error 21000 ("ON CONFLICT DO
-- UPDATE command cannot affect row a second time"). Root cause: two shot
-- events can share the same (game_id, event_type, period_id, time_seconds,
-- team_id, shooter_id) when a player has two distinct shots in the same
-- recorded second -- the original constraint didn't disambiguate those,
-- so a single upsert batch containing both crashed instead of just
-- colliding harmlessly. This is the exact same failure mode
-- pwhl_shot_events.py already solved for PWHL by including x_raw/y_raw in
-- its own natural key -- ahl_shot_events.py's on_conflict clause has been
-- updated to match; this migration brings the DB constraint in line with
-- that code change.
--
-- Run this in the Supabase SQL editor. This repo has no migration
-- tooling -- schema changes are applied by hand, same as every other
-- docs/*.sql file.

alter table public.ahl_shot_events
  drop constraint if exists ahl_shot_events_natural_key;

alter table public.ahl_shot_events
  add constraint ahl_shot_events_natural_key
  unique (game_id, event_type, period_id, time_seconds, team_id, shooter_id, x_raw, y_raw);
