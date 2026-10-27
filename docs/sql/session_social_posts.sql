-- social_posts + the `social` storage bucket -- for social_posts.py. Run
-- this in the Supabase SQL editor before enabling
-- .github/workflows/social-posts.yml.
--
-- social_posts: one row per platform per automatic post, keyed by
-- (platform, post_key) with post_key = '<kind>-<ET date>', e.g.
-- 'winners-2026-10-20'. A 'published' row is never posted again, which is
-- what makes the workflow's backup crons safe, and lets a run that failed
-- on one platform retry just that one.
--   platform  'instagram' | 'facebook'
--   kind      'rankings' | 'winners' | 'recap'
--   status    'published' | 'rendered' (credentials not set) | 'failed'
--
-- Service-role only: RLS on and deliberately NO policies -- nothing outside
-- the pipeline reads it. It will show up in the standing RLS audit query
-- (CLAUDE.md) as "RLS ENABLED, ZERO POLICIES"; that's intended here, not
-- the player_narratives failure mode.

create table if not exists public.social_posts (
  id bigint generated always as identity primary key,
  platform      text not null,
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

alter table public.social_posts enable row level security;

-- Public bucket: Instagram and Facebook fetch each image by URL when the
-- post is created. Only the service role writes to it.
insert into storage.buckets (id, name, public)
values ('social', 'social', true)
on conflict (id) do nothing;
