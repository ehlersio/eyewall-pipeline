-- Session 85 — add height_inches to pwhl_players
--
-- Backs PWHLPlayerPopup.jsx's Option A header reflow (see
-- PWHL_HEADER_AND_SHOTMAP_TEAMNAME_BRIEF.md). HockeyTech's PWHL roster feed
-- has a "height" field (e.g. "5'11") that pwhl_stats.py's fetch_roster() was
-- never ingesting -- now parsed into total inches by _parse_height_inches()
-- and written here. "weight" is deliberately NOT added: HockeyTech's PWHL
-- feed always returns "0" for it, not real data.
--
-- No migration tooling in this repo -- run this by hand in the Supabase SQL
-- editor before the next pwhl-nightly.yml run backfills the column.

alter table pwhl_players
  add column if not exists height_inches integer;
