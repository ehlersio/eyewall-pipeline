-- Session 80 — NHL team-level faceoff win % for team_seasons.
-- Run this in the Supabase SQL editor. This file is a reference copy, not
-- executed by any pipeline code (this repo has no migration tooling —
-- schema changes are applied directly in Supabase, same as every existing
-- pwhl_*/team_seasons column addition).

-- team_seasons has no faceoff-win-% column today (confirmed during
-- Session 80's investigation of the Shot Map "All N" summary cards brief).
-- The NHL /nhl-stats/stats/rest/en/team/summary endpoint this pipeline
-- already fetches (fetch_team_stats() in nhl_stats.py) carries
-- `faceoffWinPct` on every row -- it just wasn't being mapped into the
-- team_seasons row dict. Now mapped in both the regular-season and
-- playoff branches of nhl_stats.py's team stats stage; this column just
-- needs to exist for that upsert to stop erroring on an unknown column.
alter table public.team_seasons
  add column faceoff_win_pct numeric;
