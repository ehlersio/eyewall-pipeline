-- trivia_questions + trivia_answers -- Phase 2 (daily trivia).
-- Run this in the Supabase SQL editor -- this repo has no migration
-- tooling, same convention as every other docs/*.sql file here.
--
-- trivia_questions: public, no owner, auto-published nightly (easy/medium
-- tiers) or hand-inserted directly in the SQL editor (hard tier -- no admin
-- UI in v1, intentional). Same posture as player_narratives: anon SELECT
-- using (true), no anon write policy, writes stay service-role-only (the
-- pipeline's SUPABASE_SERVICE_KEY bypasses RLS). Named "anon can read..."
-- rather than the Session 43 "Allow public read" standard on purpose --
-- matching player_narratives_rls_fix.sql, the nearest same-shape precedent
-- (nightly-generated AI content, no per-row owner), not the older
-- 40-table sweep that predates this table.
--
-- `team` defaults to 'ALL' rather than being nullable so the unique
-- constraint below actually enforces "one question per exact scope per
-- day" for every tier -- Postgres unique constraints treat NULL as
-- distinct-from-NULL, so a nullable team column would have silently let
-- two "easy" rows land on the same day for the same sport.
create table public.trivia_questions (
  id bigint generated always as identity primary key,
  question_date date not null,
  tier text not null check (tier in ('easy', 'medium', 'hard')),
  sport text not null check (sport in ('nhl', 'pwhl')),
  team text not null default 'ALL',
  question_text text not null,
  options jsonb not null,
  correct_index smallint not null,
  explanation text,
  source text not null default 'ai' check (source in ('ai', 'curated')),
  created_at timestamptz not null default now(),
  unique (question_date, tier, sport, team)
);

alter table public.trivia_questions enable row level security;

create policy "anon can read trivia_questions"
  on public.trivia_questions
  for select
  to anon
  using (true);

-- trivia_answers: user-owned, exactly the same auth.uid() = user_id
-- posture as user_preferences (Phase 0). Anonymous answer tracking never
-- touches this table at all -- it stays local-storage-only on the
-- frontend, same as favorite-team was before Phase 1's sync layer.
--
-- No update/delete policy -- an answer is immutable once submitted (the
-- frontend reveals the correct answer right after, so "change your
-- answer" isn't a real use case, and immutability makes the union-merge
-- logic in favoriteTeamSync-style sync code simpler: a question_id either
-- has a row for this user or it doesn't, never a row that needs
-- reconciling against a locally-edited value).
create table public.trivia_answers (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  question_id bigint not null references public.trivia_questions(id) on delete cascade,
  selected_index smallint not null,
  is_correct boolean not null,
  answered_at timestamptz not null default now(),
  unique (user_id, question_id)
);

alter table public.trivia_answers enable row level security;

create policy "authenticated can read own trivia_answers"
  on public.trivia_answers
  for select
  to authenticated
  using (auth.uid() = user_id);

create policy "authenticated can insert own trivia_answers"
  on public.trivia_answers
  for insert
  to authenticated
  with check (auth.uid() = user_id);

-- Standing RLS audit (see CLAUDE.md) -- run after creating these tables:
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
