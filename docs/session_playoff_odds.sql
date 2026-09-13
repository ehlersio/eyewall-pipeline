-- playoff_odds + playoff_odds_game_impacts -- new tables for playoff_odds.py.
-- Run this in the Supabase SQL editor before deploying playoff_odds.py.
--
-- playoff_odds.py simulates the rest of the NHL regular season N times
-- (default 10,000) from team_elo_ratings, the current standings
-- (team_seasons) and the live remaining schedule, then counts how often
-- each team makes the playoffs (top 3 per division + 2 wild cards per
-- conference). One row per team per nightly run -- history is kept, so
-- odds can be charted over time.
--
-- change (jsonb) explains the move since the previous run:
--   { "prev_run_date": "2026-10-14", "prev_pct": 0.72, "delta": 0.06,
--     "contributions": [ { "game_id": ..., "game_date": ..., "home": "CAR",
--       "away": "OTT", "winner": "CAR", "delta": 0.04 }, ... ],
--     "residual": 0.005 }
-- Each contribution comes from the PREVIOUS run's playoff_odds_game_impacts
-- row for that game and its actual result: (odds conditional on that
-- result) - (previous odds). `residual` is what the played games don't
-- account for (Elo rating changes, games further out, simulation noise).
-- null on the first run of a season.
--
-- playoff_odds_game_impacts: for every game on the next game-day, each
-- team's playoff odds conditional on each result (outcome 'home' = home
-- team wins, 'away' = away team wins). ~2 x games x 32 rows per run. Used
-- by the next run's change explanation, and usable on its own for
-- "what's at stake tonight".

create table if not exists public.playoff_odds (
  id bigint generated always as identity primary key,
  season          integer not null,
  run_date        date not null,
  team            text not null,
  playoff_pct     double precision not null,
  division_pct    double precision not null,
  proj_points     double precision not null,
  points_p10      integer,
  points_p90      integer,
  current_points  integer not null,
  games_played    integer not null,
  games_remaining integer not null,
  elo_rating      double precision,
  sims            integer not null,
  change          jsonb,
  created_at      timestamptz not null default now(),
  constraint playoff_odds_season_run_team_key unique (season, run_date, team)
);

create index if not exists playoff_odds_team_idx on public.playoff_odds (team, run_date desc);
create index if not exists playoff_odds_run_idx on public.playoff_odds (season, run_date desc);

create table if not exists public.playoff_odds_game_impacts (
  id bigint generated always as identity primary key,
  season      integer not null,
  run_date    date not null,
  game_id     bigint not null,
  game_date   date not null,
  home_team   text not null,
  away_team   text not null,
  outcome     text not null check (outcome in ('home', 'away')),
  team        text not null,
  playoff_pct double precision not null,
  sims        integer not null,
  created_at  timestamptz not null default now(),
  constraint playoff_odds_game_impacts_key unique (season, run_date, game_id, outcome, team)
);

create index if not exists playoff_odds_game_impacts_run_idx
  on public.playoff_odds_game_impacts (season, run_date, team);

alter table public.playoff_odds enable row level security;
alter table public.playoff_odds_game_impacts enable row level security;

-- Read-only public content, same posture as player_injuries. Writes stay
-- service-role-only (playoff_odds.py uses SUPABASE_SERVICE_KEY, which
-- bypasses RLS) -- no INSERT/UPDATE/DELETE policy for anon.
create policy "anon can read playoff_odds" on public.playoff_odds
  for select to anon using (true);
create policy "anon can read playoff_odds_game_impacts" on public.playoff_odds_game_impacts
  for select to anon using (true);

-- Standing RLS audit (see CLAUDE.md) -- run afterwards; zero rows back = clean:
--
-- select t.schemaname, t.tablename,
--   case when t.rowsecurity = false then 'RLS DISABLED...'
--        when count(p.policyname) = 0 then 'RLS ENABLED, ZERO POLICIES...' end as risk,
--   t.rowsecurity as rls_enabled, count(p.policyname) as policy_count
-- from pg_tables t
-- left join pg_policies p on p.schemaname=t.schemaname and p.tablename=t.tablename
-- where t.schemaname = 'public'
-- group by t.schemaname, t.tablename, t.rowsecurity
-- having t.rowsecurity = false or count(p.policyname) = 0
-- order by risk, t.tablename;
