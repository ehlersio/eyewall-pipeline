-- {league}_playoff_odds for hockeytech_playoff_odds.py (AHL, ECHL, PWHL).
-- Run in the Supabase SQL editor, then the CLAUDE.md RLS audit query.
-- Until this has been run, the nightly step logs one error and moves on.
--
-- One row per team per nightly run (run_date, ET) -- history is kept so the
-- app can draw a sparkline. Read by eyewall-poller's GET /{league}/playoff-odds
-- and /pwhl/playoff-odds.
--
-- Columns:
--   make_playoffs_pct  share of simulated seasons (0-1, like the NHL's
--                      playoff_odds.playoff_pct) in which the team qualifies.
--                      NULL when the season's playoff format hasn't been
--                      verified from the league's published rules
--                      (hockeytech_leagues.py PlayoffFormat) -- then
--                      `format` is 'unverified'.
--   win_division_pct   share finishing first in its division (0-1). NULL for
--                      leagues/seasons without divisions (PWHL) and when the
--                      format is unverified.
--   proj_points_p10/p50/p90  10th/50th/90th percentile of final standings
--                      points across the simulated seasons.
--   current_points, games_remaining  at run time.
--   games_played       added by 2026-10-08_hockeytech_playoff_odds_games_played.sql
--                      (run that file after this one).
--   sims               simulated seasons (>= 2,000; 10,000 by default).
--   format             the verified format's one-line description, or
--                      'unverified'.
--
-- Public read (numbers, not sensitive), service-role writes -- same posture
-- as playoff_odds and {league}_team_elo_ratings.

create table if not exists public.ahl_playoff_odds (
  id                 bigint generated always as identity primary key,
  season_id          bigint not null,
  team_id            bigint not null,
  run_date           date not null,
  make_playoffs_pct  double precision,
  win_division_pct   double precision,
  proj_points_p10    integer not null,
  proj_points_p50    integer not null,
  proj_points_p90    integer not null,
  current_points     integer not null,
  games_remaining    integer not null,
  sims               integer not null,
  format             text not null,
  created_at         timestamptz not null default now(),
  constraint ahl_playoff_odds_key unique (season_id, team_id, run_date)
);
create index if not exists ahl_playoff_odds_team_idx
  on public.ahl_playoff_odds (team_id, run_date desc);

create table if not exists public.echl_playoff_odds (like public.ahl_playoff_odds including all);
create table if not exists public.pwhl_playoff_odds (like public.ahl_playoff_odds including all);

alter table public.ahl_playoff_odds enable row level security;
alter table public.echl_playoff_odds enable row level security;
alter table public.pwhl_playoff_odds enable row level security;

create policy "anon can read ahl_playoff_odds" on public.ahl_playoff_odds
  for select to anon using (true);
create policy "anon can read echl_playoff_odds" on public.echl_playoff_odds
  for select to anon using (true);
create policy "anon can read pwhl_playoff_odds" on public.pwhl_playoff_odds
  for select to anon using (true);
