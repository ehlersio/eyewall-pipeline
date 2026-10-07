-- AHL/ECHL xG proxy and percentiles (2026-10-07, audit 2026-10-06 §8 / Phase 3
-- contract C4). Run in the Supabase SQL editor; the pipeline steps that write
-- these tables (hockeytech_shot_xg.py, hockeytech_percentiles.py,
-- hockeytech_goalie_percentiles.py, in ahl-/echl-nightly.yml) log one error
-- and skip until it has been run. No PWHL change: PWHL keeps its columns on
-- pwhl_player_seasons / pwhl_goalie_seasons.
--
-- {league}_player_xg            one row per (player, team, season, type):
--                               shot-location xG proxy (hockeytech_shot_xg.py)
-- {league}_player_percentiles   the columns the PWHL percentile route reads
--                               off pwhl_player_seasons (toi_per_game, xg_for,
--                               finishing, pct_goals, pct_a1, pct_penalties,
--                               pct_finishing) plus gp and rate_basis_per_gp
-- {league}_goalie_percentiles   the columns the PWHL goalie percentile route
--                               reads off pwhl_goalie_seasons (gsax,
--                               gsax_per60, *_sv_pct, pct_*) plus gp and
--                               rate_basis_per_gp
--
-- rate_basis_per_gp = true on every AHL/ECHL row: there is no TOI in their
-- box scores, so every rate is per game played -- including gsax_per60 /
-- pct_gsax60, which keep PWHL's column names but hold gsax / gp.
-- toi_per_game is always NULL (kept so the row has PWHL's column set).
-- ev_sv_pct / pk_sv_pct / pct_ev_sv / pct_pk_sv are NULL until AHL/ECHL
-- penalties are ingested (their shot rows carry no strength state).
--
-- Public read, service-role writes, like every other {league}_* table. Check
-- the CLAUDE.md RLS audit query afterwards.

create table if not exists public.ahl_player_xg (
  id          bigint generated always as identity primary key,
  player_id   bigint not null,
  team_id     bigint not null,
  season_id   bigint not null,
  season_type text not null,
  attempts    integer not null default 0,  -- goals + shots with a location
  goals       integer not null default 0,
  xg_for      numeric,
  finishing   numeric,                     -- goals - xg_for
  updated_at  timestamptz not null default now(),
  constraint ahl_player_xg_natural_key unique (player_id, team_id, season_id, season_type)
);
create index if not exists ahl_player_xg_season_idx on public.ahl_player_xg (season_id, season_type);

create table if not exists public.ahl_player_percentiles (
  id                bigint generated always as identity primary key,
  player_id         bigint not null,
  team_id           bigint not null,
  season_id         bigint not null,
  season_type       text not null,
  gp                integer not null default 0,
  toi_per_game      bigint,               -- always NULL for AHL/ECHL (no TOI)
  xg_for            numeric,
  finishing         numeric,
  pct_goals         integer,
  pct_a1            integer,
  pct_penalties     integer,
  pct_finishing     integer,
  rate_basis_per_gp boolean not null default true,
  updated_at        timestamptz not null default now(),
  constraint ahl_player_percentiles_natural_key unique (player_id, team_id, season_id, season_type)
);
create index if not exists ahl_player_percentiles_player_idx on public.ahl_player_percentiles (player_id, season_id);

create table if not exists public.ahl_goalie_percentiles (
  id                bigint generated always as identity primary key,
  player_id         bigint not null,
  team_id           bigint not null,
  season_id         bigint not null,
  season_type       text not null,
  gp                integer not null default 0,
  gsax              numeric,
  gsax_per60        numeric,              -- gsax per game played when rate_basis_per_gp
  ev_sv_pct         numeric,
  hd_sv_pct         numeric,
  md_sv_pct         numeric,
  pk_sv_pct         numeric,
  pct_gsax          integer,
  pct_gsax60        integer,
  pct_ev_sv         integer,
  pct_hd_sv         integer,
  pct_md_sv         integer,
  pct_pk_sv         integer,
  rate_basis_per_gp boolean not null default true,
  updated_at        timestamptz not null default now(),
  constraint ahl_goalie_percentiles_natural_key unique (player_id, team_id, season_id, season_type)
);
create index if not exists ahl_goalie_percentiles_player_idx on public.ahl_goalie_percentiles (player_id, season_id);

create table if not exists public.echl_player_xg (like public.ahl_player_xg including all);
create table if not exists public.echl_player_percentiles (like public.ahl_player_percentiles including all);
create table if not exists public.echl_goalie_percentiles (like public.ahl_goalie_percentiles including all);

alter table public.ahl_player_xg enable row level security;
alter table public.echl_player_xg enable row level security;
alter table public.ahl_player_percentiles enable row level security;
alter table public.echl_player_percentiles enable row level security;
alter table public.ahl_goalie_percentiles enable row level security;
alter table public.echl_goalie_percentiles enable row level security;

create policy "public read access" on public.ahl_player_xg for select using (true);
create policy "public read access" on public.echl_player_xg for select using (true);
create policy "public read access" on public.ahl_player_percentiles for select using (true);
create policy "public read access" on public.echl_player_percentiles for select using (true);
create policy "public read access" on public.ahl_goalie_percentiles for select using (true);
create policy "public read access" on public.echl_goalie_percentiles for select using (true);
