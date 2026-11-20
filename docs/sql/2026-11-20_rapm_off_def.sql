-- rapm_off / rapm_def on player_seasons -- the stint RAPM design's offence
-- and defence (rapm_stints.py; rapm.py --design stints). Run this in the
-- Supabase SQL editor before running rapm.py with --design stints. The
-- default (per-attempt) design doesn't write these columns.
--
--   rapm      net = rapm_off - rapm_def (xG/60 at 5v5, under the stint design)
--   rapm_off  xG/60 a player adds for his team
--   rapm_def  xG/60 he allows; lower is better
--
-- player_seasons already has RLS with a public read policy; new columns
-- need no policy change.
alter table public.player_seasons
  add column if not exists rapm_off numeric,
  add column if not exists rapm_def numeric;
