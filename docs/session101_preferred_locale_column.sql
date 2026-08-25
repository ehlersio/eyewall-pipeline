-- user_preferences.preferred_locale -- French/English localization,
-- Phase A0 (frontend i18n infra). Run this in the Supabase SQL editor.
--
-- Same table as favorite_team/favorite_sport (docs/session90_user_preferences_table.sql,
-- docs/session91_favorite_sport_column.sql) -- one row per signed-in user,
-- extended rather than given its own table. eyewall-analytics's
-- localeSync.js mirrors favoriteTeamSync.js's write-immediately /
-- reconcile-on-sign-in shape against this same column.
alter table public.user_preferences
  add column preferred_locale text check (preferred_locale in ('en', 'fr'));

-- No RLS changes needed -- same reasoning as session91: the three existing
-- auth.uid() = user_id policies (select/insert/update) are row-level and
-- already cover any column on this table, including this new one.
--
-- No default value on purpose -- NULL here means "signed-in user has never
-- set a preference on any device," which localeSync.js's
-- syncLocaleOnSignIn() treats as "upload whatever this device has locally"
-- rather than as an implicit 'en'. A default of 'en' would make that
-- distinction unrecoverable.
