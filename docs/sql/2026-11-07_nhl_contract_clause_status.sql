-- NHL clause status -- one table for nhl_contracts.py (2026-11-07), which
-- mirrors the open nhl-contracts dataset's new clause_status.json
-- (nhl-contracts #10): per contract-season, whether a trade clause is in
-- effect, none, or not known, so the app can say "No trade clause" instead
-- of "clause data not reported" where a source settles it. Run in the
-- Supabase SQL editor, then the CLAUDE.md RLS audit query. Until this has
-- been run the nightly stage's nhl_contract_clause_status upsert fails and
-- run.py's stage isolation records it; the tables before it in the load
-- still load.
--
-- One row per (contract_key, season):
--   status   'clause' | 'none' | 'none_likely' | 'unknown'
--   basis    'source'                    a clause row in nhl_contract_clauses covers the season
--            'stated'                    a PuckPedia post says no trade protection
--            'capfriendly_2024'          a blank Clause cell on an archived CapFriendly page
--            'breakdown_without_clause'  (none_likely) a PuckPedia year-by-year breakdown
--                                        that names no clause for the season
--            'ineligible'                applied here, not in the dataset: the player is
--                                        under 25 on the June 30 before the season, so no
--                                        clause can be in effect (CBA 11.8: clauses only
--                                        take effect in UFA years; accrued seasons start
--                                        at 18). Only replaces 'unknown' / 'none_likely'
--            null                        (unknown)
--   source_ids   the dataset's ';'-joined sources the answer rests on
--   dataset_updated_at   the load that last wrote the row; older rows are
--                deleted after each load (the table mirrors the release)

create table if not exists public.nhl_contract_clause_status (
  contract_key        text not null,
  season              text not null,
  player_id           bigint not null,
  status              text not null check (status in ('clause', 'none', 'none_likely', 'unknown')),
  basis               text check (basis in ('source', 'stated', 'capfriendly_2024',
                                            'breakdown_without_clause', 'ineligible')),
  source_ids          text,
  dataset_updated_at  timestamptz not null,
  created_at          timestamptz not null default now(),
  primary key (contract_key, season)
);
create index if not exists nhl_contract_clause_status_player_idx
  on public.nhl_contract_clause_status (player_id, season);
create index if not exists nhl_contract_clause_status_updated_idx
  on public.nhl_contract_clause_status (dataset_updated_at);

alter table public.nhl_contract_clause_status enable row level security;
create policy "anon can read nhl_contract_clause_status" on public.nhl_contract_clause_status
  for select to anon using (true);
