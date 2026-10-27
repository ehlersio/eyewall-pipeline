-- Session 43 — RLS policy naming cleanup + one missing public-read grant.
-- Run this in the Supabase SQL editor. This file is a reference copy, not
-- executed by any pipeline code (this repo has no migration tooling —
-- schema changes are applied directly in Supabase, same as every existing
-- pwhl_* table).
--
-- Background: the cross-repo audit (Session 42.5) flagged RLS policy
-- naming as inconsistent ("3 variants"). A live pg_policies check ahead
-- of Session 43 found it was actually worse -- 10 distinct policy names
-- across ~40 tables, all granting the exact same thing (public SELECT).
-- No functional/security difference between any of them -- pure naming
-- sprawl. This consolidates everything to "Allow public read", the
-- existing plurality convention (24 of ~40 tables already use it).

-- ── Step 1: rename every non-standard SELECT policy to "Allow public read"
-- Skips the one policy that isn't a public-read grant at all
-- (player_score_state_dist's "Service role full access", cmd ALL --
-- untouched, still applies after this runs).
do $$
declare
  r record;
begin
  for r in
    select schemaname, tablename, policyname
    from pg_policies
    where schemaname = 'public'
      and policyname <> 'Allow public read'
      and policyname <> 'Service role full access'
  loop
    execute format(
      'alter policy %I on %I.%I rename to %I',
      r.policyname, r.schemaname, r.tablename, 'Allow public read'
    );
  end loop;
end $$;

-- ── Step 2: player_score_state_dist has no public-read policy at all today
-- (only "Service role full access", cmd ALL, which the pipeline's
-- service-role key doesn't even need -- service_role bypasses RLS
-- regardless). Internal RAPM score-state weighting table (score_state.py
-- writes it, rapm.py reads it to normalize Macdonald score-state weights),
-- same category as rapm_validation/skipped_games, which already have
-- public-read policies. No sensitive columns (player_id, season,
-- expected_weight only) -- being the one table without public read looks
-- like an oversight from whenever it was created, not a deliberate
-- restriction, per Matt's call.
create policy "Allow public read" on public.player_score_state_dist
  for select using (true);

-- ── Step 3: verification -- re-run the audit's own grouping query.
-- Expect exactly two rows: "Allow public read" covering every table, and
-- "Service role full access" covering only player_score_state_dist.
select
  policyname,
  count(*) as table_count,
  array_agg(tablename order by tablename) as tables
from pg_policies
where schemaname = 'public'
group by policyname
order by policyname;
