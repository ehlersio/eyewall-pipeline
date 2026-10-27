-- NHL dead cap -- one table for nhl_contracts.py (2026-10-25), which mirrors
-- the open nhl-contracts dataset's new dead_cap.json (nhl-contracts #2):
-- the cap charges that are not roster contracts, so a reader can get from a
-- team's roster cap hit (the Worker's /contracts summary.rosterCapHit) to an
-- estimate of its cap space. Run in the Supabase SQL editor, then the
-- CLAUDE.md RLS audit query. Until this has been run the nightly stage's
-- nhl_dead_cap upsert fails and run.py's stage isolation records it; the
-- other contracts tables still load.
--
-- One row per (team, kind, player, season):
--   team           the team carrying the charge (the retaining team, or the
--                  team that bought the player out); app tri-code
--   kind           'retained' | 'buyout'
--   player_key     the NHL player_id as text when known, else the dataset's
--                  normalized name -- the upsert key, stable across loads
--   counterparty   for a retention, the acquiring team when the source names
--                  it (PuckPedia trade posts); null from the archived team pages
--   season         'YYYY-YY' the charge applies to
--   cap_hit_usd    the charge for that season as stated, or the stated share of
--                  the player's cap hit on the trade line; null when the
--                  dataset's same-tier sources disagree (see conflicts)
--   pct            the retained share when stated or derived; null for buyouts
--   contract_key   the player's contract in nhl_contracts when the dataset
--                  filled the span from it
--   event_date     the trade or buyout date when a dated post is the source
--   best_tier      D (PuckPedia public posts, from mid-2023) or E (CapFriendly
--                  team pages archived July 2024, every charge then running
--                  with its full remaining schedule)
--   flags          the dataset's ';'-joined flags: end_season_unknown,
--                  derived:span_from_contract, derived:charge_from_pct,
--                  previous_retention, team_page_dead_cap, ...
--   dataset_updated_at   the load that last wrote the row; older rows are
--                  deleted after each load (the table mirrors the release)
--
-- Still not in the dataset: LTIR, performance-bonus overages, recapture
-- penalties, and retentions only the trade call itself announced. An
-- estimate built from these tables is "before LTIR" and should say so.

create table if not exists public.nhl_dead_cap (
  id                  bigint generated always as identity primary key,
  team                text not null,
  kind                text not null check (kind in ('retained', 'buyout')),
  player_name         text not null,
  player_id           bigint,
  player_key          text not null,
  counterparty        text,
  season              text not null,
  cap_hit_usd         bigint,
  pct                 numeric(5, 1),
  contract_key        text,
  event_date          date,
  conflicts           text,
  n_sources           integer,
  best_tier           text,
  source_ids          text,
  flags               text,
  dataset_updated_at  timestamptz not null,
  created_at          timestamptz not null default now(),
  constraint nhl_dead_cap_key unique (team, kind, player_key, season)
);
create index if not exists nhl_dead_cap_team_season_idx
  on public.nhl_dead_cap (team, season);
create index if not exists nhl_dead_cap_player_idx
  on public.nhl_dead_cap (player_id);
create index if not exists nhl_dead_cap_updated_idx
  on public.nhl_dead_cap (dataset_updated_at);

alter table public.nhl_dead_cap enable row level security;
-- Public read (CC0 data), service-role writes -- same posture as the other
-- nhl_contract* tables.
create policy "anon can read nhl_dead_cap" on public.nhl_dead_cap
  for select to anon using (true);

-- The load log gains the count.
alter table public.nhl_contracts_loads add column if not exists n_dead_cap integer;
