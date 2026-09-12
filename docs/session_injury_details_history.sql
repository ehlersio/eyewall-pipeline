-- Injury details + daily history for injuries.py (ESPN injuries feed).
-- Run this in the Supabase SQL editor BEFORE deploying the matching
-- injuries.py change -- the new code writes these columns/table on every
-- nightly run, and PostgREST rejects inserts naming a column that doesn't
-- exist yet (the whole injuries stage would fail, not just the new part).
--
-- 1) Detail columns on player_injuries (current league-wide state).
--    Sourced from each ESPN entry's `details` object, confirmed live
--    2026-09-12 across all 82 entries:
--      details.type       -- body part / category, present on every entry
--                            ("Upper Body", "Knee", "Undisclosed", ...)
--      details.side       -- "Left"/"Right", ~1 in 4 entries
--      details.detail     -- "Surgery"/"Fracture"/..., ~1 in 3 entries
--      details.returnDate -- ESPN's estimated return date, every entry
--    ESPN's "Not Specified" placeholder is stored as NULL (injuries.py's
--    clean_detail()); "Undisclosed" is kept -- it's real information (the
--    team is withholding it), not a placeholder. longComment is deliberately
--    not stored: it's a long third-party editorial blurb, not data.

alter table public.player_injuries
  add column if not exists injury_type   text,
  add column if not exists injury_side   text,
  add column if not exists injury_detail text,
  add column if not exists return_date   date;

-- 2) Daily snapshot history. player_injuries is a full delete-and-reinsert
--    every run (always "current state"), so without this table nothing
--    remembers who was hurt on a given day -- which man-games-lost / WAR-
--    lost-to-injury / injury-timeline features all need. One row per
--    (snapshot_date, team, player_name) per nightly run; re-running the
--    same day upserts over that day's rows rather than duplicating them.
--    player_name (not player_id) is in the key because player_id is
--    nullable -- an unmatched ESPN name still gets a history row, same
--    posture as player_injuries itself.

create table if not exists public.player_injury_history (
  id bigint generated always as identity primary key,
  snapshot_date   date not null,
  player_id       bigint,
  player_name     text not null,
  team            text not null,
  status          text not null,
  espn_status_raw text,
  comment         text,
  injury_type     text,
  injury_side     text,
  injury_detail   text,
  return_date     date,
  espn_updated_at timestamptz,
  created_at      timestamptz not null default now(),
  constraint player_injury_history_day_player_key unique (snapshot_date, team, player_name)
);

create index if not exists player_injury_history_player_idx
  on public.player_injury_history (player_id, snapshot_date) where player_id is not null;
create index if not exists player_injury_history_team_idx
  on public.player_injury_history (team, snapshot_date);

alter table public.player_injury_history enable row level security;

-- Read-only public content, same posture as player_injuries' own policy.
-- Writes stay service-role-only (injuries.py uses SUPABASE_SERVICE_KEY,
-- which bypasses RLS) -- no INSERT/UPDATE/DELETE policy for anon.
create policy "anon can read player_injury_history" on public.player_injury_history
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
