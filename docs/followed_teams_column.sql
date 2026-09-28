-- user_preferences.followed_teams -- the teams a signed-in user follows,
-- from any league (eyewall-analytics' Settings > Your teams, 2026-09).
-- Run this in the Supabase SQL editor.
--
-- Same table as favorite_team/favorite_sport/preferred_locale
-- (docs/session90_user_preferences_table.sql and the columns added after
-- it). favorite_team/favorite_sport stay the PRIMARY team -- the one the
-- app runs as. This is the whole list, in the user's order, always
-- including the primary: [{"sport": "nhl", "abbr": "CAR"}, ...].
-- eyewall-analytics' utils/followedTeams.js writes it whenever the list
-- changes and, on sign-in, merges it with the device's list (both kept,
-- the account's order first).
alter table public.user_preferences
  add column followed_teams jsonb
    check (followed_teams is null or jsonb_typeof(followed_teams) = 'array');

-- No RLS changes needed: the existing auth.uid() = user_id policies
-- (select/insert/update) are row-level and already cover any column on
-- this table, including this one.
--
-- No default on purpose -- NULL means "this account has never saved a
-- list", which the app treats as "upload this device's list" on sign-in.
--
-- Until this runs, the app keeps working: the list stays on each device
-- and followedTeams.js logs the failed upsert/select instead of syncing.
