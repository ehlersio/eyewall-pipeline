-- Combined Prediction Calibration — Part B: players.team column.
-- Run this in the Supabase SQL editor. This file is a reference copy, not
-- executed by any pipeline code (this repo has no migration tooling --
-- schema changes are applied directly in Supabase, same as every existing
-- pwhl_*/team_seasons/player_seasons column addition).

-- Why: there was previously no way to answer "which team is player X on
-- right now" anywhere in this codebase. `players` had no team column,
-- `player_seasons` for the upcoming season is empty until real stats
-- exist (confirmed empty for 20262027 while writing this), and
-- nhl_stats.py::fetch_roster(team, season) knew the team while fetching
-- but discarded it before the players upsert. This blocked the
-- roster-continuity signal (COMBINED_CALIBRATION_IMPLEMENTATION.md Part B)
-- and the regime check (Part C) from having anything to read.
--
-- nhl_stats.py::fetch_roster now attaches `team` to every player row it
-- returns, so this rides the existing nightly ingestion -- no new job,
-- no separate backfill script required. First nightly run after this
-- column exists populates it for all 32 teams' current rosters.
--
-- Timing: land this (and run one nightly cycle, manual or scheduled)
-- before the 2026-27 preseason opener (2026-09-29) so Part C has a
-- populated column to test against, not the week of.
alter table public.players
  add column team text;
