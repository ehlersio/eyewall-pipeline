-- Session 57 — NHL magic number / elimination calculations.
-- Run this in the Supabase SQL editor. This file is a reference copy, not
-- executed by any pipeline code (this repo has no migration tooling --
-- schema changes are applied directly in Supabase, same as every existing
-- pwhl_*/team_seasons/player_seasons column addition).

-- ── Standings enrichment (nhl_stats.py, regular season / game_type=2 only).
-- These come straight from the NHL standings/now endpoint, which
-- nhl_stats.py previously only read for L10 record and discarded
-- everything else (see fetch_standings(), formerly fetch_standings_l10()).
-- NULL for game_type=3 (playoffs) rows -- standings/now has no
-- playoff-bracket equivalent, and division/wildcard races don't apply once
-- a team is in the bracket.
alter table public.team_seasons
  add column division_abbrev text,
  add column conference_abbrev text,
  add column wildcard_sequence integer,
  add column regulation_wins integer,
  add column clinch_indicator text;

-- ── Magic number / elimination calc (playoff_race.py, new module, run
-- nightly right after nhl_stats.py). See playoff_race.py's module
-- docstring for the full V1 algorithm and its explicitly-flagged
-- simplifications (no tiebreak modeling, 82-game season assumption).
-- Regular season only, same scope as the columns above.
--
-- magic_number: points still needed to guarantee a playoff spot. NULL once
--   clinched or eliminated (no meaningful "points needed" number either
--   way at that point).
-- tragic_number: points of cushion remaining before mathematical
--   elimination. 0 = eliminated. Never NULL (well-defined at every game
--   state, including already-clinched teams).
-- clinched / eliminated: booleans, derived from the same calc. Once
--   clinch_indicator (above) is populated by the NHL API, treat it as
--   ground truth over these -- they're a pre-clinch estimate only. That
--   preference is a Worker/frontend display concern, not enforced here.
alter table public.team_seasons
  add column magic_number integer,
  add column tragic_number integer,
  add column clinched boolean,
  add column eliminated boolean;
