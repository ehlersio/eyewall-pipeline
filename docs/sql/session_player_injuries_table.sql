-- player_injuries -- new table for injuries.py (ESPN injuries feed).
-- Run this in the Supabase SQL editor before deploying injuries.py.
--
-- The NHL's own API has no injuries/scratches endpoint at all (confirmed
-- via direct inspection of live api-web.nhle.com responses). ESPN's site
-- API does, at the same "stable but unofficial" tier as MoneyPuck's CSVs
-- and HockeyTech (AHL/ECHL/PWHL) -- see injuries.py's module docstring
-- for the full investigation.
--
-- player_id is nullable: ESPN has no shared id with this app's players
-- table, so matching is by normalized name -- a genuine miss (a name
-- ESPN spells differently than the NHL API does, or a call-up not yet in
-- the players table) still gets a row written (player_name kept, logged
-- as unmatched by injuries.py) rather than silently dropped.
--
-- Always a full refresh (injuries.py deletes all rows and reinserts each
-- run) -- no season/team partitioning, this is always "current league-
-- wide state," small enough (dozens of rows) that diffing isn't worth it.
create table public.player_injuries (
  id bigint generated always as identity primary key,
  player_id bigint,
  player_name text not null,
  team text not null,
  status text not null,
  espn_status_raw text,
  comment text,
  espn_updated_at timestamptz,
  updated_at timestamptz not null default now()
);

create index player_injuries_team_idx on public.player_injuries (team);
create index player_injuries_player_id_idx on public.player_injuries (player_id) where player_id is not null;

alter table public.player_injuries enable row level security;

-- Read-only content, same posture as player_narratives'/nhl_odds' read
-- policies -- unrestricted SELECT is intentional. Writes stay service-
-- role-only (injuries.py uses SUPABASE_SERVICE_KEY, which bypasses RLS)
-- -- no INSERT/UPDATE/DELETE policy needed or added for anon.
create policy "anon can read player_injuries" on public.player_injuries
  for select to anon using (true);

-- Standing RLS audit (see CLAUDE.md) -- run after creating this table to
-- confirm it doesn't show up as either failure mode (RLS disabled, or RLS
-- enabled with zero policies -- the exact bug that hit player_narratives
-- once, silently, for an unknown period):
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
