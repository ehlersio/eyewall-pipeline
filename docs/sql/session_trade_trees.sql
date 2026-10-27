-- trades + trade_assets -- new tables for trade_trees.py. Run this in the
-- Supabase SQL editor before deploying it.
--
-- trade_trees.py rebuilds both nightly from nhl_transactions (ESPN's
-- free-text feed, parsed by trade_parse.py): each trade's two per-team
-- entries paired into one trade, every player / pick / right / future
-- consideration as a row, picks resolved against draft_pick_history (the
-- NHL's own pick records), and each received asset linked to the next trade
-- its new team sent it on in (next_trade_id -- the tree's edges). Read by the
-- Worker's /trades/tree route.
--
-- trades.trade_id is a stable hash of the source entries (trade_trees.trade_id).
-- trade_assets.pick_note: null when resolved, else 'future' (not drafted
-- yet), 'not_traced' (e.g. a conditional pick that was deferred or never
-- conveyed), or 'several_possible'. Nothing is guessed.

create table if not exists public.trades (
  id bigint generated always as identity primary key,
  trade_id      text not null,
  tx_date       date not null,
  season        integer,
  teams         text[] not null,
  via           text[] not null default '{}',
  source_tx_ids bigint[] not null,
  descriptions  text[] not null,
  updated_at    timestamptz not null default now(),
  constraint trades_trade_id_key unique (trade_id)
);

create index if not exists trades_source_tx_ids_idx on public.trades using gin (source_tx_ids);
create index if not exists trades_tx_date_idx on public.trades (tx_date desc);

create table if not exists public.trade_assets (
  id bigint generated always as identity primary key,
  trade_id            text not null references public.trades (trade_id) on delete cascade,
  idx                 integer not null,
  tx_date             date not null,
  from_team           text,
  to_team             text,
  asset_type          text not null,  -- player | pick | future_considerations | cash | unknown
  player_name         text,
  player_key          text,           -- injuries.normalize_name(player_name), for linking
  player_id           bigint,         -- only when the name matches exactly one player
  position            text,
  rights              boolean not null default false,
  pick_year           integer,
  pick_round          integer,
  pick_conditional    boolean,
  pick_overall        integer,        -- as ESPN stated it ("No. 162")
  pick_original_team  text,
  pick_raw            text,
  pairing_uncertain   boolean not null default false,
  resolved_year       integer,
  resolved_overall    integer,
  drafted_player_name text,
  drafted_player_id   bigint,
  pick_chain          text[],
  pick_note           text,
  next_trade_id       text,
  updated_at          timestamptz not null default now(),
  constraint trade_assets_key unique (trade_id, idx)
);

create index if not exists trade_assets_trade_idx on public.trade_assets (trade_id);
create index if not exists trade_assets_player_key_idx on public.trade_assets (player_key);

alter table public.trades enable row level security;
alter table public.trade_assets enable row level security;

-- Read-only public content, same posture as nhl_transactions / draft_pick_history.
-- Writes stay service-role-only (trade_trees.py uses SUPABASE_SERVICE_KEY,
-- which bypasses RLS) -- no INSERT/UPDATE/DELETE policy for anon.
create policy "anon can read trades" on public.trades
  for select to anon using (true);
create policy "anon can read trade_assets" on public.trade_assets
  for select to anon using (true);

-- Standing RLS audit (see CLAUDE.md) -- run afterwards; zero rows back = clean:
--
-- select t.schemaname, t.tablename,
--   case when t.rowsecurity = false then 'RLS DISABLED...'
--        when count(p.policyname) = 0 then 'RLS ENABLED, ZERO POLICIES...' end as risk,
--   t.rowsecurity as rls_enabled, count(p.policyname) as policy_count
-- from pg_tables t
-- left join pg_policies p on p.schemaname=t.schemaname and p.tablename=t.tablename
-- where t.schemaname = 'public'
-- group by t.schemaname, t.tablename, t.rowsecurity
-- having t.rowsecurity = false or count(p.policyname) = 0
-- order by risk, t.tablename;
