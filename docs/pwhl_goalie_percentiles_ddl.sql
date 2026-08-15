-- pwhl_goalie_percentiles_ddl.sql
-- Run in the Supabase SQL editor before pwhl_goalie_percentiles.py's nightly
-- upsert will succeed (this repo has no migration tooling -- see CLAUDE.md).
-- Until this runs, the module degrades gracefully: it computes everything
-- correctly, then logs a loud error on the upsert step and skips, rather
-- than crashing the nightly run.

-- gsax_percentile already existed on this table (from an earlier,
-- never-completed attempt at this feature) but broke this repo's own
-- pct_* naming convention (pwhl_player_seasons.pct_goals/pct_a1/
-- pct_penalties/pct_finishing). Safe to rename -- confirmed 100% NULL,
-- zero real data in it as of 2026-08.
alter table pwhl_goalie_seasons
  rename column gsax_percentile to pct_gsax;

alter table pwhl_goalie_seasons
  add column if not exists gsax_per60 numeric,
  add column if not exists ev_sv_pct numeric,
  add column if not exists hd_sv_pct numeric,
  add column if not exists md_sv_pct numeric,
  add column if not exists pk_sv_pct numeric,
  add column if not exists pct_gsax60 integer,
  add column if not exists pct_ev_sv integer,
  add column if not exists pct_hd_sv integer,
  add column if not exists pct_md_sv integer,
  add column if not exists pct_pk_sv integer;

-- gsax itself already existed too (also 100% NULL) -- no change needed to
-- that column, pwhl_goalie_percentiles.py writes directly to it.
