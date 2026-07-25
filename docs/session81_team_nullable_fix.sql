-- Session 81 (follow-up #2) — team must be nullable now that moneypuck.py
-- no longer writes it.
--
-- After the dedupe constraint fix (session81_dedupe_constraint_fix.sql)
-- landed and moneypuck.py stopped including `team` in its upsert payload
-- (it's nhl_stats.py's column to own, see that commit), a live smoke test
-- against production surfaced this: `team` is NOT NULL on both tables.
-- Postgres validates NOT NULL constraints on an `INSERT ... ON CONFLICT DO
-- UPDATE` statement's attempted row BEFORE it evaluates the conflict --
-- so ANY upsert omitting `team` fails with 23502 outright, even one that
-- would have resolved to a harmless update on an already-existing row.
-- Confirmed live on both tables (2026-07-25); the failed statements rolled
-- back cleanly, no partial rows or data loss.
--
-- Fix: make `team` nullable on both tables. Safe because nhl_stats.py
-- (which runs first every night) always supplies a real team value on
-- INSERT; moneypuck.py's upserts either match that existing row (UPDATE
-- path -- `team` isn't in its payload, so it's left untouched) or, in the
-- rare case no row exists yet, insert with team=NULL (degrades
-- gracefully -- same tolerance-for-partial-data philosophy used
-- throughout this pipeline, not a new pattern).

alter table public.player_seasons
  alter column team drop not null;

alter table public.goalie_seasons
  alter column team drop not null;
