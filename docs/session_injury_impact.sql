-- injury_games_lost + team_injury_impact -- new tables for injury_impact.py.
-- Run this in the Supabase SQL editor before deploying injury_impact.py.
--
-- injury_impact.py counts, for every completed regular-season game, the
-- players each team was missing to injury: on that day's injury report
-- (player_injury_history, injuries.py's daily ESPN snapshot -- latest one
-- on or before the game date, within 3 days) as day-to-day / out /
-- injured-reserve, and absent from the game's shift_events (didn't dress).
-- Suspensions don't count. A player ESPN's name couldn't be matched to an
-- NHL id can't be checked against shift_events, so they only count when
-- listed out / injured-reserve.
--
-- Each missed game carries the player's WAR per game (player_seasons.war
-- pooled over this regular season and last, / games played), so
-- war_per_game summed over a team's rows = WAR lost to injury. Goalies have
-- no WAR here (goalie_seasons has GSAx, not WAR) -- counted in games lost,
-- null war_per_game.
--
-- player_injury_history starts 2026-09-12, so this fills from the 2026-27
-- regular season on; there is no earlier injury data to backfill from.

create table if not exists public.injury_games_lost (
  id bigint generated always as identity primary key,
  season        integer not null,
  game_id       bigint not null,
  game_date     date not null,
  team          text not null,
  opponent      text,
  player_id     bigint,
  player_name   text not null,
  status        text not null,
  injury_type   text,
  war_per_game  double precision,
  updated_at    timestamptz not null default now(),
  constraint injury_games_lost_key unique (season, game_id, team, player_name)
);

create index if not exists injury_games_lost_team_idx
  on public.injury_games_lost (season, team, game_date);

-- One row per team per season, rebuilt nightly from injury_games_lost.
-- players (jsonb): [{ player_id, player_name, games, war_lost, last_date,
--   status, injury_type }], most games first. rank_* : 1 = most lost in
-- the league that season.
create table if not exists public.team_injury_impact (
  id bigint generated always as identity primary key,
  season          integer not null,
  team            text not null,
  games_played    integer not null,
  man_games_lost  integer not null,
  war_lost        double precision not null,
  players_injured integer not null,
  rank_man_games  integer,
  rank_war_lost   integer,
  players         jsonb not null default '[]'::jsonb,
  updated_at      timestamptz not null default now(),
  constraint team_injury_impact_key unique (season, team)
);

alter table public.injury_games_lost enable row level security;
alter table public.team_injury_impact enable row level security;

-- Read-only public content, same posture as player_injuries / playoff_odds.
-- Writes stay service-role-only (injury_impact.py uses SUPABASE_SERVICE_KEY,
-- which bypasses RLS) -- no INSERT/UPDATE/DELETE policy for anon.
create policy "anon can read injury_games_lost" on public.injury_games_lost
  for select to anon using (true);
create policy "anon can read team_injury_impact" on public.team_injury_impact
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
