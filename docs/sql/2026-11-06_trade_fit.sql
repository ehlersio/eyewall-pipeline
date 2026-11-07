-- trade_fit / trade_fit_players -- for each player in the trade fit
-- population, which of the other 31 NHL teams he'd fit, in tiers with the
-- reasons (trade_fit.py). Run this in the Supabase SQL editor before
-- deploying trade_fit.py.
--
-- One current set: trade_fit.py upserts both tables each night and deletes
-- what it didn't write (generated_at older than the run). Outside the season
-- window (before the first projection, after the trade deadline) it empties
-- both, and the app hides the section.

-- One row per player: what the fit is computed from.
--   rating: WAR per 82 (skaters) / GSAx per 60 (goalies), team_needs' rule;
--           rating_basis 'season' | 'last_season'
--   rental: the contract ends this season (years_left 0)
--   clause_kind: NMC | M-NMC | NTC | M-NTC | null (none on record -- the
--                dataset can't tell "none" from "not reported")
create table if not exists public.trade_fit_players (
  player_id bigint primary key,
  season integer not null,
  team text not null,
  name text,
  position text not null,
  shoots text,
  rating double precision not null,
  rating_basis text not null,
  games_played integer not null,
  cap_hit bigint,
  years_left integer,
  expires_after text,
  expiry_type text,
  expiry_type_derived boolean not null default false,
  rental boolean not null,
  clause_kind text,
  clause_list_size integer,
  generated_at timestamptz not null
);

-- One row per (player, other team).
--   tier: strong | possible | long_shot; rank 1..31 within the player
--   slot / gap: the team_needs slot he'd fill and his rating minus its
--               weakest rated player's (null when he'd fill none)
--   cap_state: fits | fits_retained | needs_salary | unknown
--   reasons: [{code, ...params}] -- need_*, window_*, cap_*, hand_* codes
--            the app translates
create table if not exists public.trade_fit (
  player_id bigint not null,
  team text not null,
  from_team text not null,
  season integer not null,
  tier text not null check (tier in ('strong', 'possible', 'long_shot')),
  rank integer not null,
  slot text,
  gap double precision,
  need_met boolean not null,
  window_met boolean not null,
  cap_state text not null check (cap_state in ('fits', 'fits_retained', 'needs_salary', 'unknown')),
  reasons jsonb not null,
  generated_at timestamptz not null,
  primary key (player_id, team)
);

alter table public.trade_fit_players enable row level security;
alter table public.trade_fit enable row level security;

-- Public read-only data, same posture as team_needs
-- (docs/sql/2026-11-06_team_needs.sql). Writes stay service-role-only;
-- trade_fit.py uses SUPABASE_SERVICE_KEY, which bypasses RLS.
create policy "anon can read trade_fit_players" on public.trade_fit_players
  for select to anon using (true);
create policy "anon can read trade_fit" on public.trade_fit
  for select to anon using (true);

-- Afterwards, run the RLS audit query in this repo's CLAUDE.md: neither
-- table should appear in its output.
