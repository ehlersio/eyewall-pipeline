-- NHL contracts: bought-out contracts -- two columns on nhl_contracts for
-- nhl_contracts.py (2026-10-25), which mirrors the open nhl-contracts
-- dataset's new contract columns of the same names. Run in the Supabase SQL
-- editor before the next nightly. Until this has been run the nightly
-- stage's nhl_contracts upsert fails (it writes both columns) and run.py's
-- stage isolation records it. No new table, so no RLS change: the
-- existing anon read policy on nhl_contracts covers the new columns.
--
--   bought_out           true when the contract was bought out. Its
--                        end_season is then the last season the player was
--                        paid under it (the dataset shortens the span), so
--                        is_current / years_left / expiry_type follow the
--                        paid span, not the signed one
--   end_season_signed    'YYYY-YY' the contract was originally signed to;
--                        null when it was not bought out, and on every row
--                        loaded from a release before the dataset added the
--                        column (which reads as bought_out = false)
--
-- The buyout's cap charges (the seasons after the paid span, and the
-- reduced charges during it) are in nhl_dead_cap with kind = 'buyout'
-- (docs/sql/2026-10-25_nhl_dead_cap.sql), not on the contract row.

alter table public.nhl_contracts add column if not exists bought_out boolean not null default false;
alter table public.nhl_contracts add column if not exists end_season_signed text;
