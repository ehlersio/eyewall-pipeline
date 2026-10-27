-- NHL contracts -- six tables for nhl_contracts.py, which mirrors the open
-- nhl-contracts dataset (github.com/ehlersio/nhl-contracts, CC0; rolling
-- GitHub release `contracts`, refreshed hourly) into Supabase each night.
-- Run in the Supabase SQL editor, then the CLAUDE.md RLS audit query. Until
-- this has been run the nightly `nhl_contracts` stage fails on its first
-- upsert and run.py's stage isolation records it; nothing else is affected.
--
-- Data dictionary: that repo's README. Tiers A-C are official sources (club
-- release, NHL.com, ESPN wire); D (PuckPedia public posts, r/hockey quotes
-- of insiders) and E (CapFriendly pages archived by the Internet Archive,
-- to July 2024) are reported. Money columns are USD integers; seasons are
-- 'YYYY-YY' labels ('2026-27'); team is the app's tri-code.
--
-- nhl_contracts: one row per dataset contract_key (the pipeline keeps the
-- fuller row when the dataset repeats a key, which it does for ~54 keys --
-- franchise relocations and a PTO followed by a standard deal in the same
-- month). Only contracts with an NHL player_id are loaded. The same contract
-- can still appear under two keys (one by id, one by normalized name) when
-- the dataset could not join a name-only source; readers should expect more
-- than one is_current row per player and prefer the better best_tier.
--   contract_key         '<player_id or normalized name>|TEAM|YYYY-MM'
--   contract_type        'ELC' | 'standard' | 'extension' | 'PTO' | null
--   expiry_status        the dataset's value as given ('UFA', 'RFA (Arb)',
--                        'UFA (Group 6)', 'UFA (No QO)', '10.2(c)', ...)
--   expiry_type          'UFA' | 'RFA' -- expiry_status when it names one,
--                        else the age rule (27 or older on June 30 of the
--                        expiry year = UFA) when a birth date is known
--   expiry_type_derived  true when expiry_type came from the age rule
--   is_current           the span contains the season containing today
--                        (July 1 boundary); recomputed every load
--   years_left           seasons after the current one (0 once expired;
--                        null when the dataset gives no span)
--   conflicts            dataset fields whose sources disagree (the value
--                        is then null, never picked); tiers / source_ids /
--                        claim_ids / flags are the dataset's ';'-joined text
--   dataset_updated_at   timestamp.json's updated_at of the load that last
--                        wrote the row; rows older than the latest load are
--                        deleted after it (the table mirrors the release)
--
-- nhl_contract_seasons: one row per (contract_key, season). player_id and
-- team are copied from the contract so a team's cap hits per season can be
-- summed without a join. `clause` is the dataset's free-text clause label
-- for that season ('NMC', 'M-NTC', '10 team M-NTC'); the structured version
-- is nhl_contract_clauses.
--
-- nhl_contract_clauses: one row per (contract_key, kind, season_from).
--   kind        'NMC' | 'NTC' | 'M-NTC' | 'M-NMC'
--   season_from / season_to   null on some tier-D rows (the post gave no
--               seasons) -- hence NULLS NOT DISTINCT on the key, so such a
--               row upserts onto itself instead of duplicating every night
--   list_size / list_type     modified clauses: '10 team' lists etc.
--
-- nhl_contract_gaps: gaps.json -- current roster players the dataset has no
-- public figure for, so the Worker can say "no public figure" rather than
-- show nothing. One row per player_id; a closed gap disappears on the next
-- load.
--   gap     'no_contract' (no row at all) | 'no_money' (row, no aav) |
--           'no_structure' (money, no per-season rows)
--   status  'has_contract' | 'older_rows_only' | 'in_raw_store' | 'absent'
--   *_hint  the dataset's latest matching ESPN / NHL.com / PuckPedia text
--
-- nhl_cap_limits: one row per season the NHL has announced (2026-27 only
-- today: $104M ceiling, $77M floor, $850K minimum). nhl_contracts.py
-- upserts CAP_LIMITS each load; later seasons are added when published.
--
-- nhl_contracts_loads: one row per successful load (append-only). The
-- pipeline reads the latest row and skips when the dataset's updated_at and
-- the current season both match it.
--
-- Not computable from these tables yet: cap space. Retained salary, buyouts
-- and LTIR are not in the dataset, and coverage of current rosters is
-- partial (92% of players have a row, 82% a cap hit, 2026-10-23).

create table if not exists public.nhl_contracts (
  contract_key         text primary key,
  player_id            bigint not null,
  player_name          text,
  team                 text,
  signed_at            date,
  signing_date_stated  boolean,
  contract_type        text,
  term_years           integer,
  aav_usd              bigint,
  cap_hit_usd          bigint,
  total_usd            bigint,
  nhl_salary_usd       bigint,
  minors_salary_usd    bigint,
  one_way              boolean,
  start_season         text,
  end_season           text,
  expiry_status        text,
  expiry_type          text check (expiry_type in ('UFA', 'RFA')),
  expiry_type_derived  boolean not null default false,
  is_current           boolean not null default false,
  years_left           integer,
  agent                text,
  re_signed            boolean,
  conflicts            text,
  n_sources            integer,
  best_tier            text,
  tiers                text,
  source_ids           text,
  claim_ids            text,
  flags                text,
  dataset_updated_at   timestamptz not null,
  created_at           timestamptz not null default now()
);
create index if not exists nhl_contracts_team_current_idx
  on public.nhl_contracts (team, is_current);
create index if not exists nhl_contracts_player_idx
  on public.nhl_contracts (player_id);
create index if not exists nhl_contracts_updated_idx
  on public.nhl_contracts (dataset_updated_at);

create table if not exists public.nhl_contract_seasons (
  id                     bigint generated always as identity primary key,
  contract_key           text not null,
  season                 text not null,
  player_id              bigint not null,
  team                   text,
  cap_hit_usd            bigint,
  aav_usd                bigint,
  base_salary_usd        bigint,
  signing_bonus_usd      bigint,
  performance_bonus_usd  bigint,
  total_salary_usd       bigint,
  minors_salary_usd      bigint,
  one_way                boolean,
  clause                 text,
  conflicts              text,
  n_sources              integer,
  best_tier              text,
  source_ids             text,
  dataset_updated_at     timestamptz not null,
  created_at             timestamptz not null default now(),
  constraint nhl_contract_seasons_key unique (contract_key, season)
);
create index if not exists nhl_contract_seasons_team_season_idx
  on public.nhl_contract_seasons (team, season);
create index if not exists nhl_contract_seasons_player_idx
  on public.nhl_contract_seasons (player_id);
create index if not exists nhl_contract_seasons_updated_idx
  on public.nhl_contract_seasons (dataset_updated_at);

create table if not exists public.nhl_contract_clauses (
  id                  bigint generated always as identity primary key,
  contract_key        text not null,
  player_id           bigint not null,
  kind                text not null,
  season_from         text,
  season_to           text,
  list_size           integer,
  list_type           text,
  conflicts           text,
  n_sources           integer,
  best_tier           text,
  source_ids          text,
  dataset_updated_at  timestamptz not null,
  created_at          timestamptz not null default now(),
  -- NULLS NOT DISTINCT (Postgres 15+): a null season_from is one key, not a
  -- new row every load. nhl_contracts.py upserts on these three columns.
  constraint nhl_contract_clauses_key unique nulls not distinct (contract_key, kind, season_from)
);
create index if not exists nhl_contract_clauses_player_idx
  on public.nhl_contract_clauses (player_id);
create index if not exists nhl_contract_clauses_updated_idx
  on public.nhl_contract_clauses (dataset_updated_at);

create table if not exists public.nhl_contract_gaps (
  player_id           bigint primary key,
  player_name         text,
  team                text,
  gap                 text not null,
  status              text,
  last_contract_end   text,
  espn_hint           text,
  forge_hint          text,
  bsky_hint           text,
  checked_season      text,
  dataset_updated_at  timestamptz not null,
  created_at          timestamptz not null default now()
);
create index if not exists nhl_contract_gaps_team_idx
  on public.nhl_contract_gaps (team);

create table if not exists public.nhl_cap_limits (
  season       text primary key,
  cap_ceiling  bigint not null,
  cap_floor    bigint not null,
  min_salary   bigint not null,
  updated_at   timestamptz not null default now()
);

create table if not exists public.nhl_contracts_loads (
  id                      bigint generated always as identity primary key,
  dataset_updated_at      timestamptz not null,
  season                  text not null,
  dataset_version         text,
  loaded_at               timestamptz not null default now(),
  n_contracts             integer,
  n_seasons               integer,
  n_clauses               integer,
  n_gaps                  integer,
  n_dropped_no_player_id  integer,
  n_duplicate_keys        integer,
  constraint nhl_contracts_loads_key unique (dataset_updated_at)
);

alter table public.nhl_contracts enable row level security;
alter table public.nhl_contract_seasons enable row level security;
alter table public.nhl_contract_clauses enable row level security;
alter table public.nhl_contract_gaps enable row level security;
alter table public.nhl_cap_limits enable row level security;
alter table public.nhl_contracts_loads enable row level security;

-- Public read (CC0 data), service-role writes -- same posture as
-- nhl_transactions. The loads table is readable too so the Worker can show
-- "figures as of <dataset_updated_at>".
create policy "anon can read nhl_contracts" on public.nhl_contracts
  for select to anon using (true);
create policy "anon can read nhl_contract_seasons" on public.nhl_contract_seasons
  for select to anon using (true);
create policy "anon can read nhl_contract_clauses" on public.nhl_contract_clauses
  for select to anon using (true);
create policy "anon can read nhl_contract_gaps" on public.nhl_contract_gaps
  for select to anon using (true);
create policy "anon can read nhl_cap_limits" on public.nhl_cap_limits
  for select to anon using (true);
create policy "anon can read nhl_contracts_loads" on public.nhl_contracts_loads
  for select to anon using (true);
