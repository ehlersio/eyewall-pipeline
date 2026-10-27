-- Session 66 — PWHL percentile parity v1 (TOI rollup + shot-xG proxy +
-- percentile computation).
-- Run this in the Supabase SQL editor. This file is a reference copy, not
-- executed by any pipeline code (this repo has no migration tooling --
-- schema changes are applied directly in Supabase, same as every existing
-- pwhl_*/team_seasons/player_seasons column addition -- see e.g.
-- session57_new_columns.sql).

-- ── Piece 1: TOI rollup ────────────────────────────────────────────────
-- toi_per_game ALREADY EXISTS on pwhl_player_seasons (previously hardcoded
-- None by pwhl_stats.py's fetch_skater_stats()) -- no DDL needed for it.
-- pwhl_stats.py's new compute_toi_per_game() populates it from
-- pwhl_skater_game_box.toi_seconds. Nothing to run for this piece.

-- ── Piece 2: shot-based xG proxy (pwhl_shot_xg.py) ──────────────────────
-- xg_for: season-total distance-bucket xG proxy (rapm.py's NHL approach,
--   ported to PWHL's own event_type vocabulary -- see pwhl_shot_xg.py).
-- finishing: goals - xg_for (positive = scoring above shot quality/volume
--   alone would predict).
alter table public.pwhl_player_seasons
  add column xg_for numeric,
  add column finishing numeric;

-- ── Piece 3: percentiles (pwhl_percentiles.py) ──────────────────────────
-- pct_goals / pct_a1 / pct_penalties / pct_finishing: 0-100 percentile
-- rank within the player's position group (F or D) among GP>=10 qualified
-- skaters this season/type. See pwhl_percentiles.py's module docstring for
-- the full reasoning (MIN_GP choice, no TOI-minute floor in v1, the
-- season-9-playoffs data gap).
alter table public.pwhl_player_seasons
  add column pct_goals integer,
  add column pct_a1 integer,
  add column pct_penalties integer,
  add column pct_finishing integer;

-- ── Known gap, not fixed by this migration ──────────────────────────────
-- pwhl_shot_events has ZERO rows for season 9 (2025-26 Playoffs) as of this
-- session -- the PBP shot-event ingest was never run for that season's 13
-- Final games (distinct from the box-score backfill, which was done
-- separately). xg_for/finishing/pct_a1/pct_finishing will read NULL for
-- every season-9-playoffs row until a manual backfill is run:
--     python pwhl_shot_events.py 9
-- This is a call for the site owner to make (a live-production ingest run
-- for a season/type not otherwise in scope for this PR) -- not run here.
