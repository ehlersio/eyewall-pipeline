-- game_scratches -- new table for scratches.py.
-- Run this in the Supabase SQL editor before deploying scratches.py.
--
-- One row per scratched player per game, from the NHL's own
-- gamecenter/{id}/right-rail payload (gameInfo.homeTeam/awayTeam.scratches
-- -- the same list GameStatsPopup.jsx already shows for a single game, but
-- never persisted). Confirmed present for regular-season and playoff games
-- back to at least 2023-24. Playoff lists run much longer (15 for one team
-- in a 2025-26 playoff game -- extra reserve players are carried), so
-- consumers should split by game_type rather than pooling.
--
-- scratch_type classifies each scratch against player_injury_history's
-- snapshot for the game date (latest snapshot on or before it, within 3
-- days -- see scratches.py's classify()):
--   'healthy'   -- scratched and not on that day's injury report
--   'injured'   -- on that day's report as out / IR / day-to-day
--   'suspended' -- on that day's report as a suspension
--   'unknown'   -- no injury snapshot close enough to judge (every game
--                  before player_injury_history started, 2026-09-12)
-- player_id is the NHL's own id (not nullable -- the right-rail payload
-- always carries it, unlike ESPN's injury feed).

create table if not exists public.game_scratches (
  id bigint generated always as identity primary key,
  game_id      bigint not null,
  season       integer not null,
  game_type    integer,
  game_date    date,
  team         text not null,
  opponent     text,
  is_home      boolean,
  player_id    bigint not null,
  player_name  text,
  scratch_type text not null,
  injury_status text,
  updated_at   timestamptz not null default now(),
  constraint game_scratches_game_player_key unique (game_id, player_id)
);

create index if not exists game_scratches_team_season_idx
  on public.game_scratches (team, season, game_date);
create index if not exists game_scratches_player_idx
  on public.game_scratches (player_id, season);

alter table public.game_scratches enable row level security;

-- Read-only public content, same posture as player_injuries. Writes stay
-- service-role-only (scratches.py uses SUPABASE_SERVICE_KEY, which
-- bypasses RLS) -- no INSERT/UPDATE/DELETE policy for anon.
create policy "anon can read game_scratches" on public.game_scratches
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
