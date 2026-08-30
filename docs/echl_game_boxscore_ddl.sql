-- ECHL per-game player box score — new tables for echl_game_boxscore.py.
-- Run this in the Supabase SQL editor before echl_game_boxscore.py's
-- first run. This repo has no migration tooling — same direct-in-
-- Supabase convention as docs/echl_new_tables_ddl.sql.
--
-- Straight mirror of docs/ahl_game_boxscore_ddl.sql (ahl_ -> echl_
-- renamed throughout). Same deliberate difference from PWHL: no
-- hits/faceoff_attempts/faceoff_wins/blocked_shots/skater toi_seconds
-- columns. Confirmed live 2026-08-30 (game 24296) that every skater's
-- hits/faceoffAttempts/faceoffWins/blockedShots/toi field in ECHL's
-- gameSummary reads exactly 0/"0:00" regardless of real ice time -- same
-- wall as AHL. Goalie timeOnIce/shotsAgainst/goalsAgainst/saves ARE real
-- per-game numbers (confirmed: Hunter Jones 59:16 TOI / 28 SA / 5 GA /
-- 23 SV in the same game) and are kept.

create table public.echl_skater_game_box (
  game_id bigint not null,
  player_id bigint not null references public.echl_players(player_id),
  team_id bigint not null,
  season_id bigint not null,
  season_type text not null,
  position_raw text,
  position_group text,          -- F/D/G, derived from position_raw -- see
                                 -- echl_game_boxscore.py's POSITION_GROUP_MAP
  jersey_number integer,
  starting boolean default false,
  status text default '',
  goals integer default 0,
  assists integer default 0,
  points integer default 0,
  penalty_minutes integer default 0,
  plus_minus integer default 0,
  shots integer default 0,
  updated_at timestamptz not null default now(),
  primary key (game_id, player_id)
);

create index echl_skater_game_box_season_idx on public.echl_skater_game_box (season_id);
create index echl_skater_game_box_player_idx on public.echl_skater_game_box (player_id);

alter table public.echl_skater_game_box enable row level security;
create policy "public read access" on public.echl_skater_game_box
  for select using (true);

create table public.echl_goalie_game_box (
  game_id bigint not null,
  player_id bigint not null references public.echl_players(player_id),
  team_id bigint not null,
  season_id bigint not null,
  season_type text not null,
  jersey_number integer,
  starting boolean default false,
  status text default '',
  goals integer default 0,
  assists integer default 0,
  points integer default 0,
  penalty_minutes integer default 0,
  toi_seconds integer,          -- real data, unlike the skater table --
                                 -- see module docstring
  shots_against integer default 0,
  goals_against integer default 0,
  saves integer default 0,
  updated_at timestamptz not null default now(),
  primary key (game_id, player_id)
);

create index echl_goalie_game_box_season_idx on public.echl_goalie_game_box (season_id);
create index echl_goalie_game_box_player_idx on public.echl_goalie_game_box (player_id);

alter table public.echl_goalie_game_box enable row level security;
create policy "public read access" on public.echl_goalie_game_box
  for select using (true);
