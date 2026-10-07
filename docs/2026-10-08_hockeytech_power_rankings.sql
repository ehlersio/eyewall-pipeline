-- {league}_power_rankings and {league}_power_rankings_narratives for
-- hockeytech_power_rankings.py (AHL, ECHL, PWHL). Run in the Supabase SQL
-- editor, then the CLAUDE.md RLS audit query. Until this has been run, the
-- nightly step logs one error and moves on.
--
-- {league}_power_rankings: one row per team per run (run_date, ET; written
-- only on nights after games were played). History is kept -- eyewall-poller's
-- GET /{league}/power-rankings serves the latest run plus each team's last 28
-- ranks for a sparkline.
--   rank         1 = best
--   prior_rank   the team's rank on the previous run_date (NULL on the
--                season's first run)
--   score        0-1, weighted blend of the normalised components
--   components   jsonb: { gp, points, record "W-L-OTL", pts_pct, l10 "W-L-OTL",
--                l10_pts_pct, gd_pg, pp_pct, pk_pct, special_teams,
--                cf_pct (PWHL only), ranks: { pts_pct, l10_pts_pct, gd_pg,
--                special_teams, cf_pct } }. Percentages are fractions (0-1);
--                any value can be null when the source has no number.
--
-- {league}_power_rankings_narratives: the EyeWall AI narrative per team per
-- run per locale ('en' | 'fr'), written by the --narratives flag.
--
-- Public read, service-role writes -- same posture as power_rankings_narratives.

create table if not exists public.ahl_power_rankings (
  id          bigint generated always as identity primary key,
  season_id   bigint not null,
  run_date    date not null,
  team_id     bigint not null,
  rank        integer not null,
  prior_rank  integer,
  score       double precision not null,
  components  jsonb not null,
  created_at  timestamptz not null default now(),
  constraint ahl_power_rankings_key unique (season_id, run_date, team_id)
);
create index if not exists ahl_power_rankings_team_idx
  on public.ahl_power_rankings (team_id, run_date desc);

create table if not exists public.ahl_power_rankings_narratives (
  id          bigint generated always as identity primary key,
  season_id   bigint not null,
  run_date    date not null,
  team_id     bigint not null,
  locale      text not null check (locale in ('en', 'fr')),
  narrative   text not null,
  created_at  timestamptz not null default now(),
  constraint ahl_power_rankings_narratives_key unique (season_id, run_date, team_id, locale)
);
create index if not exists ahl_power_rankings_narratives_team_idx
  on public.ahl_power_rankings_narratives (team_id, locale, run_date desc);

create table if not exists public.echl_power_rankings (like public.ahl_power_rankings including all);
create table if not exists public.pwhl_power_rankings (like public.ahl_power_rankings including all);
create table if not exists public.echl_power_rankings_narratives
  (like public.ahl_power_rankings_narratives including all);
create table if not exists public.pwhl_power_rankings_narratives
  (like public.ahl_power_rankings_narratives including all);

alter table public.ahl_power_rankings enable row level security;
alter table public.echl_power_rankings enable row level security;
alter table public.pwhl_power_rankings enable row level security;
alter table public.ahl_power_rankings_narratives enable row level security;
alter table public.echl_power_rankings_narratives enable row level security;
alter table public.pwhl_power_rankings_narratives enable row level security;

create policy "anon can read ahl_power_rankings" on public.ahl_power_rankings
  for select to anon using (true);
create policy "anon can read echl_power_rankings" on public.echl_power_rankings
  for select to anon using (true);
create policy "anon can read pwhl_power_rankings" on public.pwhl_power_rankings
  for select to anon using (true);
create policy "anon can read ahl_power_rankings_narratives" on public.ahl_power_rankings_narratives
  for select to anon using (true);
create policy "anon can read echl_power_rankings_narratives" on public.echl_power_rankings_narratives
  for select to anon using (true);
create policy "anon can read pwhl_power_rankings_narratives" on public.pwhl_power_rankings_narratives
  for select to anon using (true);
