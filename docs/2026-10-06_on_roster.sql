-- on_roster on ahl_players / echl_players / pwhl_players (2026-10-06).
--
-- true  = the player was on this team's roster feed in the last nightly run
--         (hockeytech_stats.py / pwhl_stats.py fetch_roster()).
-- false = the player's team_id still points at a team whose roster came back
--         without him (released, sent down, traded and not yet moved by the
--         stats sweep).
-- null  = never marked (a team whose roster fetch failed or was empty, or a
--         player only ever seen in the stats view).
--
-- eyewall-poller's Roster tab (/{league}/players, /pwhl/players) and the
-- call-up watch hide only on_roster = false (on_roster=not.is.false), so null
-- rows keep showing exactly as they do today.
--
-- Safe to run before or after the pipeline change: until this column exists
-- the roster ingest logs "on_roster is missing" once per run and writes the
-- rosters without it. The next nightly run after this fills the current
-- rosters. Nullable, no default, so existing rows read as "unknown".

alter table public.ahl_players add column if not exists on_roster boolean;
alter table public.echl_players add column if not exists on_roster boolean;
alter table public.pwhl_players add column if not exists on_roster boolean;

-- PostgREST caches the schema: reload it so the new column is writable now.
notify pgrst, 'reload schema';
