-- Delete the 149 player_seasons rows filed under 2024-25 that hold another
-- season's numbers. Run in the Supabase SQL editor.
--
-- Why: until #169 (2026-09-28), moneypuck.py fetched the live season's
-- MoneyPuck file whatever season it was run for. A 2024-25 run on
-- 2026-06-10 read 2025-26's file and upserted its analytics under 2024-25,
-- creating game_type 2 rows for 149 players who hadn't played in 2024-25
-- at all (Jonathan Toews, Gabriel Landeskog, ...). Found 2026-09-30:
-- every one has no box score for 2024-25, was last written 2026-06-10, and
-- its rate columns are identical to that player's 2025-26 row. They made
-- those players look like they played a 2024-25 season they didn't.
-- moneypuck.py now only fills rows nhl_stats.py created, so nothing
-- recreates them.

-- Check first: expect 149, all last written 2026-06-10.
select count(*), min(updated_at)::date, max(updated_at)::date
from public.player_seasons
where season = 20242025 and game_type = 2
  and games_played is null and goals is null and assists is null and points is null;

-- Then delete. Only rows with no box score at all.
delete from public.player_seasons
where season = 20242025 and game_type = 2
  and games_played is null and goals is null and assists is null and points is null
  and shots is null and toi_per_game is null;
