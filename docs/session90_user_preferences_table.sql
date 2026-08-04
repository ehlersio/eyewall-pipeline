-- user_preferences -- new table backing Phase 0 of Supabase Auth
-- (magic-link sign-in, eyewall-analytics). Run this in the Supabase SQL
-- editor -- this repo has no migration tooling, same convention as every
-- other docs/*.sql file here.
--
-- One row per signed-in user, keyed by auth.users.id. Phase 0 only proves
-- the auth + RLS layer end to end; it does not wire favorite_team into any
-- UI yet (that's Phase 1). The column is created now anyway because schema
-- changes in this repo are manual/by-hand, so it's cheaper to add it once
-- here than to hand the user a second DDL file next session.
--
-- This is the first-ever auth.uid()-scoped RLS policy in this codebase --
-- every existing table (see session43_rls_cleanup.sql, player_narratives)
-- is either public-read (using (true)) or service-role-only, because
-- nothing before this had per-user ownership to enforce. Don't copy the
-- "Allow public read" pattern for this table.
--
-- No anon policy is added on purpose -- anonymous (not-signed-in) users
-- have no server-side row to read or write; the frontend stays fully
-- local-storage-backed for them, same as today.
create table public.user_preferences (
  user_id uuid primary key references auth.users(id) on delete cascade,
  favorite_team text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.user_preferences enable row level security;

create policy "authenticated can read own user_preferences"
  on public.user_preferences
  for select
  to authenticated
  using (auth.uid() = user_id);

create policy "authenticated can insert own user_preferences"
  on public.user_preferences
  for insert
  to authenticated
  with check (auth.uid() = user_id);

create policy "authenticated can update own user_preferences"
  on public.user_preferences
  for update
  to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- No delete policy -- nothing in this phase deletes a preferences row.
-- Callers must set updated_at explicitly on every update; there is no
-- trigger for it (keeping this table's first version minimal).

-- Standing RLS audit (see CLAUDE.md) -- run after creating this table to
-- confirm it doesn't show up as either failure mode (RLS disabled, or RLS
-- enabled with zero policies -- the exact bug that hit player_narratives):
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
