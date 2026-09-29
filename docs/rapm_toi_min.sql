-- player_seasons.rapm_toi_min: the ice time behind a player's RAPM.
-- Run in the Supabase SQL editor before merging the playoff-RAPM PR (step 5
-- of the game-type split). This repo has no migration tooling.
--
-- rapm.py now writes a playoff RAPM to the game_type 3 rows (the pool
-- seasons' playoff games, shrunk toward the player's regular-season RAPM),
-- alongside the regular-season one on the game_type 2 rows. How far a
-- playoff value can move from the regular-season one depends on how much
-- playoff ice time is behind it, so the app shows it next to the number:
-- rapm_toi_min is the player's ice time, in minutes, in the RAPM pool's
-- games of that row's game type (three seasons, all strengths -- the same
-- ice time the 150-minute regular-season and 60-minute playoff floors are
-- measured on). NULL wherever rapm is NULL.
--
-- Adding a nullable column with no default is metadata-only: nothing is
-- rewritten.

alter table public.player_seasons
  add column if not exists rapm_toi_min numeric;

comment on column public.player_seasons.rapm_toi_min is
  'Minutes of ice time (all strengths) in the RAPM pool''s games of this row''s game type -- the sample behind rapm. NULL when rapm is NULL.';
