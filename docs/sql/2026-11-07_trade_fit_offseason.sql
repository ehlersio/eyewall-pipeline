-- trade_fit_players: phase + rights (trade fit scope Phase 2b, §3.4).
-- Run this in the Supabase SQL editor before deploying the trade_fit.py
-- that writes them (and before eyewall-poller's /trade-fit selects them).
--
--   phase: in_season (the season's first projection through the trade
--          deadline, this season's rules) | offseason (the day after the
--          deadline until the next season's first projection: next season's
--          signed rosters, cap and contenders). `season` is the season the
--          fit is for, so it's next season in the offseason.
--   rights: a pending UFA / RFA in the offseason, until June 30: his
--           contract ends this season and he has none for next, so only
--           his rights could be traded. cap_hit / years_left are null.
--
-- No new table, so no new policy: trade_fit_players' "anon can read" policy
-- (docs/sql/2026-11-06_trade_fit.sql) covers the new columns.
alter table public.trade_fit_players
  add column if not exists phase text not null default 'in_season'
    check (phase in ('in_season', 'offseason')),
  add column if not exists rights boolean not null default false;

-- rating_basis / team_needs.basis need no change: team_needs' new basis
-- 'next_season_roster' is free text, like 'last_game' / 'preseason'.
