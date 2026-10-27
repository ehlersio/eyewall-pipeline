-- line_combinations.source -- prior-season blend (Scouting tab lines).
-- Run this in the Supabase SQL editor before deploying the updated
-- line_combinations.py.
--
-- line_combinations.py previously only ever wrote units it inferred from
-- the CURRENT season's own shift data -- if a team's current-season sample
-- was too thin (early season) or empty (before a team's own opener, since
-- db.NHL_SEASON flips league-wide the moment ANY team's first game is
-- played, not per-team), the frontend either hid the section or showed a
-- partial 1-3-line list with no indication it was incomplete.
--
-- The pipeline now fills any rank slot current data can't cover from that
-- team's own last written prior-season units (filtered to players still on
-- the live roster). This column distinguishes the two so the frontend can
-- label carried-over lines differently from both live-inferred data and
-- the separate, hand-maintained staticLines.js fallback.
alter table public.line_combinations
  add column source text not null default 'current' check (source in ('current', 'prior_season'));

-- Existing rows all came from current-season clustering -- the default
-- backfills them correctly with no separate UPDATE needed.

-- No RLS changes needed -- Postgres RLS is row-level, not column-level,
-- and this table's existing policies already cover any column on it,
-- including this new one. (Still worth a quick look with the RLS audit
-- query in this repo's CLAUDE.md if you're unsure of this table's current
-- policy state -- it's cheap and this is exactly the kind of gap that
-- bit player_narratives silently once.)
