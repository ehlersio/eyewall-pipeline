-- nhl_transactions -- new table for transactions.py (ESPN's NHL transactions feed).
-- Run this in the Supabase SQL editor before deploying transactions.py.
--
-- Source: site.api.espn.com/apis/site/v2/sports/hockey/nhl/transactions --
-- unofficial and undocumented, same tier as the injuries feed injuries.py
-- already reads. ESPN carries no structured player data: each entry is a
-- date, a team, and free text ("Acquired D Yegor Zamula from Philadelphia in
-- exchange for F Philip Tomasino."), and ~1 in 4 entries bundles several
-- moves into one description. So each ESPN entry is stored whole, tagged
-- with keyword categories (transactions.py's CATEGORIES) rather than split
-- into sentences -- sentence splitting breaks on "St. Louis", "J.J Moser",
-- "Sault Ste. Marie".
--
-- categories        -- every category the text matches (text[]), e.g.
--                      {trade,assignment} for a trade + AHL demotion entry
-- primary_category  -- one badge-worthy category by priority, or 'other'
-- counterparties    -- other NHL teams named in trade/waiver entries (app
--                      abbrevs). Pairing the two halves of a trade (each team
--                      posts its own side) is display logic, done by the
--                      Worker from (team, counterparties, tx_date).
-- season            -- NHL season the move belongs to, July 1 boundary
--                      (2026-07-01 onward -> 20262027).
-- dedupe_key        -- sha1(tx_date|espn_team_id|description): the nightly
--                      re-fetch of the whole calendar year upserts onto it.
--                      If ESPN later edits an entry's wording, the edited
--                      text lands as a second row (no stable ESPN id exists).

create table if not exists public.nhl_transactions (
  id bigint generated always as identity primary key,
  tx_date          date not null,
  season           integer not null,
  team             text not null,
  espn_team_id     integer,
  description      text not null,
  categories       text[] not null default '{}',
  primary_category text not null,
  counterparties   text[] not null default '{}',
  espn_date        timestamptz,
  dedupe_key       text not null,
  created_at       timestamptz not null default now(),
  constraint nhl_transactions_dedupe_key unique (dedupe_key)
);

create index if not exists nhl_transactions_date_idx on public.nhl_transactions (tx_date desc);
create index if not exists nhl_transactions_team_date_idx on public.nhl_transactions (team, tx_date desc);
create index if not exists nhl_transactions_counterparties_idx on public.nhl_transactions using gin (counterparties);

alter table public.nhl_transactions enable row level security;

-- Read-only public content, same posture as player_injuries. Writes stay
-- service-role-only (transactions.py uses SUPABASE_SERVICE_KEY, which
-- bypasses RLS) -- no INSERT/UPDATE/DELETE policy for anon.
create policy "anon can read nhl_transactions" on public.nhl_transactions
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
