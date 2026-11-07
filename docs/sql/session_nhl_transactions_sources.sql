-- nhl_transactions / trades: where each row and its date come from -- for
-- wiki_trades.py (Wikipedia's per-season NHL transactions lists). Run this
-- in the Supabase SQL editor BEFORE deploying wiki_trades.py, and before the
-- Worker change that selects these columns.
--
-- Why: ESPN's feed (transactions.py) leaves out whole trades (Kane and
-- Ekholm, Feb 2023; Guentzel and Lindholm, 2024; Nelson, 2025), and the
-- dates it gives for entries it dates 2021-01-01 to 2024-06-19 run about 5
-- days early (transactions.ESPN_EARLY_DATES). wiki_trades.py adds the
-- missing trades as their own rows and corrects ESPN's dates where a
-- Wikipedia trade row (with its NHL.com / team / media citation) matches.
--
-- nhl_transactions.source       'espn' (transactions.py) | 'wikipedia' (wiki_trades.py)
-- nhl_transactions.source_url   the citation behind a Wikipedia row, or
--                               behind a corrected ESPN date
-- nhl_transactions.date_status  null       -- as the source gives it
--                               'corrected' -- an ESPN date replaced by the
--                                  matching Wikipedia trade's (espn_date
--                                  still holds what ESPN said)
--                               'espn_early' -- an ESPN date in its known
--                                  early window that nothing confirmed
-- nhl_transactions.assets       a Wikipedia row's assets as structured data
--                               ({partner, via, received, sent}, trade_parse
--                               shapes); trade_trees.py reads them instead of
--                               parsing the text. null for ESPN rows.
-- trades.date_status / source_url  the same, per trade (trade_trees.py):
--                               'espn_early' only when every side is.

alter table public.nhl_transactions
  add column if not exists source      text not null default 'espn',
  add column if not exists source_url  text,
  add column if not exists date_status text,
  add column if not exists assets      jsonb;

alter table public.nhl_transactions
  drop constraint if exists nhl_transactions_source_check,
  add constraint nhl_transactions_source_check check (source in ('espn', 'wikipedia')),
  drop constraint if exists nhl_transactions_date_status_check,
  add constraint nhl_transactions_date_status_check
    check (date_status is null or date_status in ('corrected', 'espn_early'));

alter table public.trades
  add column if not exists date_status text,
  add column if not exists source_url  text;

-- RLS: unchanged. Both tables already have RLS on with an anon read policy
-- (session_nhl_transactions.sql, session_trade_trees.sql); new columns are
-- covered by the existing table-level policies. Standing audit (CLAUDE.md)
-- afterwards should still return zero rows.
