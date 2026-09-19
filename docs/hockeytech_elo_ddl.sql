-- AHL / ECHL Elo tables for hockeytech_elo.py. Run in the Supabase SQL
-- editor before deploying hockeytech_elo.py or the eyewall-poller
-- hockeytech.js change that reads {league}_team_elo_ratings.
--
-- Per-league tables rather than team_elo_ratings/game_win_probs: those are
-- keyed by NHL abbreviation, and AHL/ECHL codes collide with NHL ones (CHI,
-- COL, SJ, ...). These are keyed by HockeyTech team_id, like every other
-- {league}_* table.
--
-- {league}_team_elo_ratings: one row per team, fully recomputed each run
--   (hockeytech_elo.py replays 2023-24 onward). season_id is the latest
--   season the replay covered; games is how many games fed the rating.
-- {league}_game_win_probs: pre-game home win probability for today's and
--   tomorrow's games, rewritten each run until the game starts -- the row
--   that remains is the last pre-game number (same contract as
--   game_win_probs), for grading later.
--
-- Public read (numbers, not sensitive), service-role writes -- same posture
-- as team_elo_ratings. hockeytech_elo.py uses SUPABASE_SERVICE_KEY and
-- sets updated_at on every row it writes (an upsert doesn't re-apply the
-- column default).

create table if not exists public.ahl_team_elo_ratings (
  team_id    bigint primary key,
  season_id  bigint not null,
  rating     double precision not null default 1500,
  games      integer not null default 0,
  updated_at timestamptz not null default now()
);
create table if not exists public.echl_team_elo_ratings (like public.ahl_team_elo_ratings including all);

create table if not exists public.ahl_game_win_probs (
  game_id       bigint primary key,
  season_id     bigint not null,
  game_date     date not null,
  home_team_id  bigint not null,
  away_team_id  bigint not null,
  home_rating   double precision not null,
  away_rating   double precision not null,
  home_win_prob double precision not null,
  run_date      date not null,
  updated_at    timestamptz not null default now()
);
create index if not exists ahl_game_win_probs_date_idx on public.ahl_game_win_probs (game_date);
create table if not exists public.echl_game_win_probs (like public.ahl_game_win_probs including all);

alter table public.ahl_team_elo_ratings enable row level security;
alter table public.echl_team_elo_ratings enable row level security;
alter table public.ahl_game_win_probs enable row level security;
alter table public.echl_game_win_probs enable row level security;

create policy "anon can read ahl_team_elo_ratings" on public.ahl_team_elo_ratings for select to anon using (true);
create policy "anon can read echl_team_elo_ratings" on public.echl_team_elo_ratings for select to anon using (true);
create policy "anon can read ahl_game_win_probs" on public.ahl_game_win_probs for select to anon using (true);
create policy "anon can read echl_game_win_probs" on public.echl_game_win_probs for select to anon using (true);
