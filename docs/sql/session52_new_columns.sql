-- Session 52 — real Corsi/Fenwick columns for both leagues.
-- Run this in the Supabase SQL editor. This file is a reference copy, not
-- executed by any pipeline code (this repo has no migration tooling —
-- schema changes are applied directly in Supabase, same as every existing
-- pwhl_*/team_seasons column addition).

-- ── Item 1: PWHL 5v5-filtered Corsi/Fenwick.
-- pwhl_team_seasons already has all-situations corsi_for/corsi_against/
-- corsi_for_pct/fenwick_for/fenwick_against/fenwick_for_pct (existing
-- columns, populated by pwhl_stats.py::run_team_shot_totals). These are
-- the new 5v5-filtered siblings, populated by the new
-- run_team_shot_totals_5v5() in the same file — see that function's
-- docstring for the penalty-window derivation this is built from.
alter table public.pwhl_team_seasons
  add column corsi_for_5v5 integer,
  add column corsi_against_5v5 integer,
  add column corsi_for_pct_5v5 numeric,
  add column fenwick_for_5v5 integer,
  add column fenwick_against_5v5 integer,
  add column fenwick_for_pct_5v5 numeric;

-- ── Item 2: NHL real Corsi/Fenwick (all-situations AND 5v5), replacing the
-- SOG-share-only proxy previously computed inline in nhl.js's
-- /prediction/analyze. team_seasons currently has NO Corsi/Fenwick columns
-- at all (confirmed during Session 52's investigation) -- these are new,
-- not a rename/extension of anything existing. Populated by the new
-- run_team_corsi_rollup() in moneypuck.py, wired into moneypuck.run() as a
-- sub-stage alongside run_team_xgf_rollup/run_goalie_qs.
alter table public.team_seasons
  add column corsi_for integer,
  add column corsi_against integer,
  add column corsi_for_pct numeric,
  add column fenwick_for integer,
  add column fenwick_against integer,
  add column fenwick_for_pct numeric,
  add column corsi_for_5v5 integer,
  add column corsi_against_5v5 integer,
  add column corsi_for_pct_5v5 numeric,
  add column fenwick_for_5v5 integer,
  add column fenwick_against_5v5 integer,
  add column fenwick_for_pct_5v5 numeric;
