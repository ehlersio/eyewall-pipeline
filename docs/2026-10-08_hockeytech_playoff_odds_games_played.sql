-- games_played on ahl_playoff_odds / echl_playoff_odds / pwhl_playoff_odds
-- (2026-10-08). Run in the Supabase SQL editor after
-- docs/2026-10-08_hockeytech_playoff_odds.sql (which creates the tables).
--
-- games_played = the team's GP in HockeyTech's standings feed at run time,
-- the same read as current_points. The app's early-season note ("odds
-- settle after a few games") reads it from the odds row instead of loading
-- the standings.
--
-- Safe to run before or after the pipeline change: until this column exists
-- hockeytech_playoff_odds.py logs "games_played is missing" once per run and
-- writes the rows without it. Nullable, no default: rows written before the
-- column existed read as unknown, and the next nightly run fills today's row.
-- No RLS change (the tables' existing anon-select policies cover it).
--
-- eyewall-poller's /{league}/playoff-odds selects an explicit column list:
-- add games_played to it only after this has been run (PostgREST rejects a
-- select of a column that doesn't exist).

alter table public.ahl_playoff_odds add column if not exists games_played integer;
alter table public.echl_playoff_odds add column if not exists games_played integer;
alter table public.pwhl_playoff_odds add column if not exists games_played integer;

-- PostgREST caches the schema: reload it so the new column is writable now.
notify pgrst, 'reload schema';
