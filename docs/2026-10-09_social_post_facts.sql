-- social_post_facts -- rotation memory for cherry_picked.py's Sunday
-- "Cherry Picked Stats" post. Run in the Supabase SQL editor before the
-- first Sunday it should post: until this table exists, a real run exits 1
-- ("run docs/2026-10-09_social_post_facts.sql") and posts nothing. Dry runs
-- work without it (no rotation history).
--
-- One row per fact per post, written once at least one platform has
-- published. cherry_picked.py skips any fact_key posted in the last 28 days
-- and takes categories least-recently-used first, so the mix changes week
-- to week.
--   post_key   'cherry-picked-<ET date>', same as social_posts.post_key
--   league     'NHL' | 'PWHL' | 'AHL' | 'ECHL'
--   category   'streak' | 'since' | 'only' | 'split' | 'comeback' |
--              'period' | 'player'
--   fact_key   '<league>:<category>:<kind>:<team or player id>', e.g.
--              'NHL:streak:win:CAR'
--   statement  the sentence as posted, for reference
--
-- Service-role only, like social_posts: RLS on and deliberately NO
-- policies -- nothing outside the pipeline reads it. It will show up in the
-- standing RLS audit query (CLAUDE.md) as "RLS ENABLED, ZERO POLICIES";
-- that's intended here.

create table if not exists public.social_post_facts (
  id bigint generated always as identity primary key,
  post_key    text not null,
  posted_on   date not null,
  league      text not null,
  category    text not null,
  fact_key    text not null,
  statement   text not null,
  created_at  timestamptz not null default now(),
  constraint social_post_facts_key unique (post_key, fact_key)
);

create index if not exists social_post_facts_posted_on_idx
  on public.social_post_facts (posted_on);

alter table public.social_post_facts enable row level security;
