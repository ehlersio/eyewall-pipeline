-- Delete the goalie_seasons rows that only ever held a preseason QS%.
-- Run in the Supabase SQL editor AFTER moneypuck.py has been re-run for
-- 2024-25, 2025-26 and 2026-27 with game-type filtering (#170 and the
-- game-type split's step 2), which clears their qs/qs_pct.
--
-- Why: run_goalie_qs() used to count every start in shot_events by season,
-- preseason included, and upsert qs/qs_pct on (player_id, season,
-- game_type 2). For a goalie who played preseason but no regular-season
-- game, that created a game_type 2 row nhl_stats.py never made: no box
-- score, no GSAX, only a QS%. 184 of them on 2026-09-29 (95 in 2026-27,
-- 49 in 2025-26, 40 in 2024-25); 50 also had a team, nothing else did.
-- They make a goalie look like he played a regular season he didn't.
-- run_goalie_qs() now counts regular-season starts only, so nothing
-- recreates them.

-- Check first: expect 184 rows, all game_type 2, in those three seasons,
-- and qs_pct NULL on every one (if not, the recompute hasn't run yet).
select season, game_type, count(*), count(qs_pct) as with_qs_pct
from public.goalie_seasons
where games_played is null
group by 1, 2
order by 1, 2;

-- Then delete. Every condition must hold: no box score, no QS, no GSAX or
-- save% of any kind, no percentile.
delete from public.goalie_seasons
where games_played is null
  and games_started is null and wins is null and losses is null and ot_losses is null
  and shots_against is null and saves is null and goals_against is null
  and sv_pct is null and gaa is null and shutouts is null and toi is null
  and qs is null and qs_pct is null
  and gsax is null and gsax_per60 is null
  and ev_sv_pct is null and hd_sv_pct is null and md_sv_pct is null and pk_sv_pct is null
  and pct_gsax is null and pct_gsax60 is null
  and pct_ev_sv is null and pct_hd_sv is null and pct_md_sv is null and pct_pk_sv is null;
