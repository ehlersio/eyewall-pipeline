-- social_posts + social_tokens + the `social` storage bucket -- for
-- instagram_posts.py. Run this in the Supabase SQL editor before enabling
-- .github/workflows/instagram.yml.
--
-- social_posts: one row per automatic post attempt, keyed by
-- (platform, post_key) with post_key = '<kind>-<ET date>', e.g.
-- 'winners-2026-10-20'. A 'published' row is never posted again, which is
-- what makes the workflow's backup crons safe to run.
--   kind    'rankings' | 'winners' | 'recap'
--   status  'published' | 'rendered' (no IG credentials set) | 'failed'
--
-- social_tokens: the current long-lived Instagram token. instagram_posts.py
-- refreshes it weekly and writes the new one here (a GitHub secret can't be
-- rewritten from inside a run). seed_fingerprint is a hash of the
-- IG_ACCESS_TOKEN secret it was refreshed from, so re-setting the secret
-- takes over from the stored token.
--
-- Both tables are service-role only: RLS on and deliberately NO policies --
-- social_tokens holds a credential and nothing outside the pipeline reads
-- either table. These two will show up in the standing RLS audit query
-- (CLAUDE.md) as "RLS ENABLED, ZERO POLICIES"; that's intended here, not
-- the player_narratives failure mode.

create table if not exists public.social_posts (
  id bigint generated always as identity primary key,
  platform      text not null default 'instagram',
  kind          text not null,
  post_key      text not null,
  status        text not null,
  image_urls    jsonb not null default '[]'::jsonb,
  caption       text,
  media_id      text,
  error         text,
  published_at  timestamptz,
  updated_at    timestamptz not null default now(),
  constraint social_posts_key unique (platform, post_key)
);

create table if not exists public.social_tokens (
  platform          text primary key,
  access_token      text not null,
  seed_fingerprint  text not null,
  refreshed_at      timestamptz not null
);

alter table public.social_posts enable row level security;
alter table public.social_tokens enable row level security;

-- Public bucket: Instagram fetches each image by URL when the post is
-- created. Only the service role writes to it.
insert into storage.buckets (id, name, public)
values ('social', 'social', true)
on conflict (id) do nothing;
