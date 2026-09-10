-- team_elo_ratings -- persistent per-team Elo rating, one row per NHL team.
-- Run this in the Supabase SQL editor before deploying elo_ratings.py or
-- the nhl.js changes that read this table.
--
-- Backing docs/elo_prediction_model_results.md's validated result: Elo
-- beats both the production in-season scorecard and the preseason
-- continuity-adjusted fallback on every metric, using only game_log data
-- already collected. This table is the persistent state that lets Elo be
-- a live-updated rating instead of a value recomputed from scratch on
-- every single prediction request.
--
-- `season` records which season this team's rating was last updated for
-- -- elo_ratings.py checks it against the live-resolved current season
-- and applies elo.regress_to_mean() exactly once when they differ, then
-- writes the new season back. This is the persistent-state equivalent of
-- backtest_elo.py's "regress every team once at each season boundary"
-- step in the backtest.
--
-- No history table alongside this -- a full nightly recompute from
-- game_log (see elo_ratings.py) is cheap and self-healing (no incremental
-- state to get out of sync, no bug class where a game gets double-counted
-- or missed), so `rating` is always derivable from scratch and doesn't
-- need point-in-time snapshots preserved here. If a future need arises
-- for historical Elo trajectories (a chart, say), that's a new table, not
-- a retrofit of this one.
create table if not exists public.team_elo_ratings (
  team text primary key,
  season integer not null,
  rating double precision not null default 1500,
  updated_at timestamptz not null default now()
);

alter table public.team_elo_ratings enable row level security;

-- Public read-only data (a number, not sensitive) -- same posture as
-- player_narratives' read policy (docs/player_narratives_rls_fix.sql).
-- Writes stay service-role-only; elo_ratings.py uses SUPABASE_SERVICE_KEY,
-- which bypasses RLS -- no INSERT/UPDATE policy needed for anon.
create policy "anon can read team_elo_ratings" on public.team_elo_ratings
  for select to anon using (true);
