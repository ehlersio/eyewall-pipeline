-- trade_candidacy -- each NHL player's candidate signals and tier
-- (trade_candidacy.py, Phase 4 of the trade fit scope). Run this in the
-- Supabase SQL editor before deploying trade_candidacy.py.
--
-- One current set: trade_candidacy.py upserts every player in the
-- population each night and deletes what it didn't write (generated_at
-- older than the run). Outside Jan 1 - trade deadline it empties the table,
-- and the app hides the section and the Deadline board.
--
--   tier: several | some | null (no label). Only with at least one signal
--         and a public cap hit.
--   rank: 1..n among tiered players by the model, null when untiered
--   signals: [{code, ...params}] -- expiring, outside_playoffs, toi_down
--            codes the app words
--   model_p: the frozen model's output. NOT for display: the backtest
--            didn't calibrate well enough to show a percentage
--            (docs/trade_candidacy_backtest_results.md). Kept to rank and
--            to check the model against this season's real trades later.
--   clause_kind: NMC | M-NMC | NTC | M-NTC | null (none on record -- the
--                dataset can't tell "none" from "not reported")
create table if not exists public.trade_candidacy (
  player_id bigint primary key,
  season integer not null,
  name text,
  team text not null,
  position text not null,
  games_played integer not null,
  tier text check (tier in ('several', 'some')),
  rank integer,
  signals jsonb not null,
  model_p double precision not null,
  cap_hit bigint,
  years_left integer,
  expiry_type text,
  expiry_type_derived boolean not null default false,
  extended boolean not null default false,
  clause_kind text,
  clause_list_size integer,
  as_of date not null,
  deadline date not null,
  generated_at timestamptz not null
);

create index if not exists trade_candidacy_tier_rank on public.trade_candidacy (tier, rank);

alter table public.trade_candidacy enable row level security;

-- Public read-only data, same posture as trade_fit
-- (docs/sql/2026-11-06_trade_fit.sql). Writes stay service-role-only;
-- trade_candidacy.py uses SUPABASE_SERVICE_KEY, which bypasses RLS.
create policy "anon can read trade_candidacy" on public.trade_candidacy
  for select to anon using (true);

-- Afterwards, run the RLS audit query in this repo's CLAUDE.md: the table
-- should not appear in its output.
