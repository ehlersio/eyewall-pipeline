-- draft_pick_history -- new table for draft_history.py.
-- Run this in the Supabase SQL editor before deploying draft_history.py.
--
-- Every NHL draft pick since 1963 (13,152 rows as of 2026-09), from the
-- NHL's own records API (records.nhl.com/site/api/draft), including the
-- chain of teams that owned each pick before it was used -- the NHL's
-- `teamPickHistory` field. Unlike the year-specific draft_rankings_2026 /
-- draft_picks_2026 / draft_pick_order_2026 tables (draft_ingest.py, live
-- draft-day tooling), this is the all-years historical record.
--
-- pick_chain       -- owners in order, original team first, drafting team
--                     last, e.g. {NYR,VAN,PIT,PHI} for 2025 #12. Parsed from
--                     two formats the API uses: dash chains ("NYR-VAN-PIT-
--                     PHI") and, for ~540 older picks, "NJD (from ATL)".
--                     A team can appear more than once (the pick came back).
-- times_traded     -- len(pick_chain) - 1 (repeat owners counted).
-- history_raw      -- the API's original teamPickHistory string, kept as-is.
-- team / original_team -- the API's own tri-codes, including defunct
--                     franchises (AFM, ATL, PHX, HFD, QUE, ...) -- not
--                     remapped to current franchises.
-- player_id        -- the NHL player id; null for ~790 mostly older picks.

create table if not exists public.draft_pick_history (
  id bigint generated always as identity primary key,
  draft_year        integer not null,
  overall_pick      integer not null,
  round             integer,
  pick_in_round     integer,
  team              text not null,
  original_team     text not null,
  pick_chain        text[] not null,
  times_traded      integer not null,
  history_raw       text,
  player_id         bigint,
  player_name       text,
  position          text,
  amateur_club      text,
  amateur_league    text,
  country_code      text,
  birth_date        date,
  draft_date        date,
  removed_outright  boolean,
  records_id        integer,
  updated_at        timestamptz not null default now(),
  constraint draft_pick_history_year_pick_key unique (draft_year, overall_pick)
);

create index if not exists draft_pick_history_team_idx on public.draft_pick_history (team, draft_year);
create index if not exists draft_pick_history_original_team_idx on public.draft_pick_history (original_team, draft_year);
create index if not exists draft_pick_history_player_idx on public.draft_pick_history (player_id) where player_id is not null;
create index if not exists draft_pick_history_chain_idx on public.draft_pick_history using gin (pick_chain);

alter table public.draft_pick_history enable row level security;

-- Read-only public content, same posture as player_injuries. Writes stay
-- service-role-only (draft_history.py uses SUPABASE_SERVICE_KEY, which
-- bypasses RLS) -- no INSERT/UPDATE/DELETE policy for anon.
create policy "anon can read draft_pick_history" on public.draft_pick_history
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
