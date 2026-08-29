-- AHL per-game player box score — new tables for ahl_game_boxscore.py.
-- Run this in the Supabase SQL editor before ahl_game_boxscore.py's first
-- run (manual `python ahl_game_boxscore.py 90` for a backfill, or the
-- nightly workflow). This repo has no migration tooling — same
-- direct-in-Supabase convention as docs/ahl_new_tables_ddl.sql.
--
-- Mirrors PWHL's pwhl_skater_game_box/pwhl_goalie_game_box shape, with
-- one deliberate difference: no hits/faceoff_attempts/faceoff_wins/
-- blocked_shots/skater toi_seconds columns. Confirmed live 2026-08-29
-- (game 1028992) that every skater's hits/faceoffAttempts/faceoffWins/
-- blockedShots/toi field in AHL's gameSummary reads exactly 0/"0:00"
-- regardless of real ice time -- consistent with the existing
-- hockeytech-ahl-api-notes.md finding that AHL's PBP has no hit/faceoff
-- event types at all. Not ingesting these at all rather than storing a
-- fabricated always-zero value, same principle ahl_player_seasons already
-- applies. Goalie timeOnIce/shotsAgainst/goalsAgainst/saves ARE real
-- per-game numbers (confirmed: Levi 59:49 TOI / 32 SA / 5 GA / 27 SV in
-- the same game) and are kept.

create table public.ahl_skater_game_box (
  game_id bigint not null,
  player_id bigint not null references public.ahl_players(player_id),
  team_id bigint not null,
  season_id bigint not null,
  season_type text not null,
  position_raw text,
  position_group text,          -- F/D/G, derived from position_raw -- see
                                 -- ahl_game_boxscore.py's POSITION_GROUP_MAP
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

create index ahl_skater_game_box_season_idx on public.ahl_skater_game_box (season_id);
create index ahl_skater_game_box_player_idx on public.ahl_skater_game_box (player_id);

alter table public.ahl_skater_game_box enable row level security;
create policy "public read access" on public.ahl_skater_game_box
  for select using (true);

create table public.ahl_goalie_game_box (
  game_id bigint not null,
  player_id bigint not null references public.ahl_players(player_id),
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

create index ahl_goalie_game_box_season_idx on public.ahl_goalie_game_box (season_id);
create index ahl_goalie_game_box_player_idx on public.ahl_goalie_game_box (player_id);

alter table public.ahl_goalie_game_box enable row level security;
create policy "public read access" on public.ahl_goalie_game_box
  for select using (true);
