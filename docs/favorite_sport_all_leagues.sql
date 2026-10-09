-- user_preferences.favorite_sport -- allow the AHL and ECHL.
-- Run this in the Supabase SQL editor.
--
-- docs/session91_favorite_sport_column.sql added the column with
-- check (favorite_sport in ('nhl', 'pwhl')), from before the AHL/ECHL
-- existed in the app. Every save of an AHL or ECHL primary team has failed
-- that check since (eyewall-analytics' favoriteTeamSync.js logs it and
-- carries on), so the account kept the old primary -- and on the next
-- launch the sign-in sync put that old team back. Seen 2026-10-09: an AHL
-- team's pages under a PWHL team's logo.
--
-- Postgres names an inline column check <table>_<column>_check.
alter table public.user_preferences
  drop constraint if exists user_preferences_favorite_sport_check;

alter table public.user_preferences
  add constraint user_preferences_favorite_sport_check
    check (favorite_sport in ('nhl', 'pwhl', 'ahl', 'echl'));

-- No RLS changes needed: the auth.uid() = user_id policies are row-level.
