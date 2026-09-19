-- PWHL Elo tables for hockeytech_elo.py -- same shape and posture as
-- docs/hockeytech_elo_ddl.sql (AHL/ECHL), keyed by HockeyTech team_id.
-- Run in the Supabase SQL editor before the PWHL Elo step runs or
-- eyewall-poller's /pwhl/prediction reads pwhl_team_elo_ratings.

create table if not exists public.pwhl_team_elo_ratings (like public.ahl_team_elo_ratings including all);
create table if not exists public.pwhl_game_win_probs (like public.ahl_game_win_probs including all);

alter table public.pwhl_team_elo_ratings enable row level security;
alter table public.pwhl_game_win_probs enable row level security;

create policy "anon can read pwhl_team_elo_ratings" on public.pwhl_team_elo_ratings for select to anon using (true);
create policy "anon can read pwhl_game_win_probs" on public.pwhl_game_win_probs for select to anon using (true);
