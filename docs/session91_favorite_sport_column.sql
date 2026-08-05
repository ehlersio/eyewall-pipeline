-- user_preferences.favorite_sport -- Auth Phase 1 (favorite-team sync).
-- Run this in the Supabase SQL editor.
--
-- Phase 0's user_preferences.favorite_team was created as a bare team
-- abbreviation, with no sport dimension. Phase 1 investigation found that's
-- ambiguous: eyewall-analytics tracks NHL and PWHL team selection as two
-- independent localStorage values ('eyewall:team' vs 'eyewall:pwhl_team'),
-- and PWHL's own team list reuses several NHL abbreviations directly --
-- BOS, MIN, MTL, OTT, TOR, SEA, VAN, DET, SJS all exist in both leagues'
-- team configs (confirmed via grep of teamConfig.js/pwhlConfig.js). Storing
-- favorite_team alone would make e.g. 'DET' unrecoverably ambiguous between
-- the NHL Red Wings and PWHL's Detroit team. This adds the missing
-- discriminator instead of leaving that landmine for whoever reads this
-- column next.
alter table public.user_preferences
  add column favorite_sport text check (favorite_sport in ('nhl', 'pwhl'));

-- No RLS changes needed -- Postgres RLS is row-level, not column-level, and
-- the three existing auth.uid() = user_id policies (select/insert/update)
-- already cover any column on this table, including this new one.
