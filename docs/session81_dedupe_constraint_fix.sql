-- Session 81 (follow-up, found while backfilling the percentile/hits work)
-- — fixes a real production data-integrity bug: player_seasons and
-- goalie_seasons both upserted on a conflict key that included `team`,
-- but nhl_stats.py and moneypuck.py disagree on what a traded player's
-- `team` value looks like (nhl_stats.py's NHL API field is a possibly
-- comma-joined trade-history string like "VAN,SJS"; MoneyPuck's CSV only
-- ever has their current team, e.g. "SJS"). Every time those two values
-- didn't match, the upsert couldn't find the existing row and created a
-- second one instead — one row ends up with real box-score stats and no
-- analytics, the other with WAR/percentiles and no box-score stats.
--
-- Confirmed in production (2026-07-24): 338 duplicate
-- (player_id, season, game_type) pairs in player_seasons (81 in the
-- current 2025-26 season, 257 accumulated in 2024-25 over many nightly
-- runs — some players forked into 3+ rows as MoneyPuck's own team
-- snapshot changed run to run), plus 2 in goalie_seasons. All were
-- 2-clean-cases (exactly one row per group had games_played set) — no
-- ambiguous cases needed manual review. Already merged and deleted via a
-- one-off script before this DDL was written; this file only handles the
-- schema change so it can't recur. nhl_stats.py/moneypuck.py's upsert
-- conflict keys were updated in the same commit to
-- `player_id,season,game_type` (team dropped) — this DDL must be applied
-- BEFORE that code runs again, or every upsert will error with
-- "there is no unique or exclusion constraint matching the ON CONFLICT
-- specification".

-- ── Step 1: find the existing constraint names ───────────────────────
-- Run this first and read off the actual names — Supabase/Postgres
-- auto-names a UNIQUE(...) constraint predictably
-- (`<table>_<col1>_..._key`) but don't assume; confirm before dropping.
select conname, conrelid::regclass as table_name, pg_get_constraintdef(oid) as definition
from pg_constraint
where conrelid in ('public.player_seasons'::regclass, 'public.goalie_seasons'::regclass)
  and contype = 'u';

-- ── Step 2: drop the old team-inclusive constraints ──────────────────
-- Replace <player_seasons_constraint_name> / <goalie_seasons_constraint_name>
-- with whatever Step 1 actually returned.
alter table public.player_seasons
  drop constraint <player_seasons_constraint_name>;

alter table public.goalie_seasons
  drop constraint <goalie_seasons_constraint_name>;

-- ── Step 3: add the corrected constraints (team excluded) ────────────
alter table public.player_seasons
  add constraint player_seasons_player_id_season_game_type_key
  unique (player_id, season, game_type);

alter table public.goalie_seasons
  add constraint goalie_seasons_player_id_season_game_type_key
  unique (player_id, season, game_type);

-- ── Step 4: sanity check — should return zero rows ────────────────────
select player_id, season, game_type, count(*)
from public.player_seasons
group by player_id, season, game_type
having count(*) > 1;

select player_id, season, game_type, count(*)
from public.goalie_seasons
group by player_id, season, game_type
having count(*) > 1;
