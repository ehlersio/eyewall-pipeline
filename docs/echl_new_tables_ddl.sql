-- ECHL pipeline foundation — new tables.
-- Run this in the Supabase SQL editor before the echl-nightly.yml workflow
-- (or a manual `python echl_stats.py` / `python echl_shot_events.py` /
-- `python echl_penalty_shots.py`) is run for the first time. This repo has
-- no migration tooling — schema changes are applied directly in Supabase.
--
-- Straight mirror of docs/ahl_new_tables_ddl.sql (ahl_ -> echl_ renamed
-- throughout) -- ECHL is the same HockeyTech/LeagueStat vendor as AHL,
-- confirmed live 2026-08-30 to have the identical data-shape ceiling (no
-- shift data, no hit/faceoff/blocked_shot event types) -- see
-- ECHL_BUILD_BRIEF.md / AHL_ECHL_HOCKEYTECH_API.md for the original
-- investigation. Dedicated echl_* tables, not a shared schema with
-- ahl_*/pwhl_*/nhl_* tables and a league discriminator column, same
-- reasoning as AHL's own tables.

create table public.echl_players (
  player_id bigint primary key,
  first_name text,
  last_name text,
  position text,
  shoots text,
  height_inches integer,
  weight_lbs integer,
  birth_date date,
  birth_place text,
  jersey_number integer,
  team_id bigint,
  updated_at timestamptz not null default now()
);

alter table public.echl_players enable row level security;
create policy "public read access" on public.echl_players
  for select using (true);

create table public.echl_player_seasons (
  id bigint generated always as identity primary key,
  player_id bigint not null references public.echl_players(player_id),
  team_id bigint,
  season_id bigint not null,
  season_type text not null,
  gp integer default 0,
  goals integer default 0,
  assists integer default 0,
  points integer default 0,
  plus_minus integer default 0,
  pim integer default 0,
  shots integer default 0,
  pp_goals integer default 0,
  sh_goals integer default 0,
  -- No shot_pct/pp_assists/sh_assists columns: confirmed absent from
  -- ECHL's players view entirely, same as AHL.
  updated_at timestamptz not null default now(),
  constraint echl_player_seasons_natural_key
    unique (player_id, team_id, season_id, season_type)
);

create index echl_player_seasons_season_idx on public.echl_player_seasons (season_id);

alter table public.echl_player_seasons enable row level security;
create policy "public read access" on public.echl_player_seasons
  for select using (true);

create table public.echl_goalie_seasons (
  id bigint generated always as identity primary key,
  player_id bigint not null references public.echl_players(player_id),
  team_id bigint,
  season_id bigint not null,
  season_type text not null,
  gp integer default 0,
  wins integer default 0,
  losses integer default 0,
  ot_losses integer default 0,
  shots_against integer default 0,
  saves integer default 0,
  goals_against integer default 0,
  sv_pct numeric,
  gaa numeric,
  shutouts integer default 0,
  toi text,                      -- HockeyTech reports "minutes_played" as MM:SS-style text
  updated_at timestamptz not null default now(),
  constraint echl_goalie_seasons_natural_key
    unique (player_id, team_id, season_id, season_type)
);

create index echl_goalie_seasons_season_idx on public.echl_goalie_seasons (season_id);

alter table public.echl_goalie_seasons enable row level security;
create policy "public read access" on public.echl_goalie_seasons
  for select using (true);

create table public.echl_team_seasons (
  id bigint generated always as identity primary key,
  team_id bigint not null,
  season_id bigint not null,
  season_type text not null,
  gp integer default 0,
  wins integer default 0,
  losses integer default 0,
  ot_losses integer default 0,
  shootout_losses integer default 0,   -- ECHL reports these separately, same as AHL
  points integer default 0,
  goals_for integer default 0,
  goals_against integer default 0,
  pp_pct numeric,
  pk_pct numeric,
  pp_goals integer default 0,
  pp_opportunities integer default 0,
  pk_goals_against integer default 0,
  times_shorthanded integer default 0,
  sh_goals_for integer default 0,
  sh_goals_against integer default 0,
  updated_at timestamptz not null default now(),
  constraint echl_team_seasons_natural_key
    unique (team_id, season_id, season_type)
);

alter table public.echl_team_seasons enable row level security;
create policy "public read access" on public.echl_team_seasons
  for select using (true);

create table public.echl_game_log (
  game_id bigint primary key,
  season_id bigint not null,
  game_date date,
  home_team_id bigint,
  away_team_id bigint,
  home_score integer,
  away_score integer,
  game_state text,               -- e.g. "Final"
  game_status_code integer,      -- HockeyTech's numeric GameStatus (1=scheduled,
                                  -- 4=final confirmed live) -- same field AHL/PWHL's
                                  -- live-score-refresh already relies on, included
                                  -- from day one here rather than added later
  venue_name text,
  venue_city text,
  updated_at timestamptz not null default now()
);

create index echl_game_log_season_idx on public.echl_game_log (season_id);
create index echl_game_log_state_idx on public.echl_game_log (game_state);

alter table public.echl_game_log enable row level security;
create policy "public read access" on public.echl_game_log
  for select using (true);

-- One row per shot ATTEMPT-ON-GOAL (event_type='shot') or GOAL
-- (event_type='goal') with coordinates. Same as AHL, ECHL's feed has no
-- blocked_shot events at all.
create table public.echl_shot_events (
  id bigint generated always as identity primary key,
  game_id bigint not null references public.echl_game_log(game_id),
  season_id bigint not null,
  season_type text not null,
  event_type text not null,      -- 'shot' | 'goal'
  period_id integer not null,
  time_seconds integer not null,
  team_id bigint,
  shooter_id bigint,
  goalie_id bigint,
  shot_type text,
  quality integer,
  x_raw integer,
  y_raw integer,
  x_norm numeric,
  y_norm numeric,
  is_home boolean,
  game_goal_id bigint,           -- goal rows only
  assist1_id bigint,             -- goal rows only
  assist2_id bigint,             -- goal rows only
  is_power_play boolean,         -- goal rows only
  is_short_handed boolean,       -- goal rows only
  is_empty_net boolean,          -- goal rows only
  is_penalty_shot boolean,       -- goal rows only
  is_insurance_goal boolean,     -- goal rows only
  is_game_winning_goal boolean,  -- goal rows only
  created_at timestamptz not null default now(),
  -- Includes x_raw/y_raw in the natural key from day one -- AHL's own
  -- pipeline hit a real Postgres 21000 conflict in production without
  -- this (two shots by the same shooter in the same recorded second),
  -- see docs/ahl_shot_events_constraint_fix.sql for that history. Built
  -- in correctly here rather than rediscovered.
  constraint echl_shot_events_natural_key
    unique (game_id, event_type, period_id, time_seconds, team_id, shooter_id, x_raw, y_raw)
);

create index echl_shot_events_game_id_idx on public.echl_shot_events (game_id);
create index echl_shot_events_shooter_id_idx on public.echl_shot_events (shooter_id);
create index echl_shot_events_season_idx on public.echl_shot_events (season_id);

alter table public.echl_shot_events enable row level security;
create policy "public read access" on public.echl_shot_events
  for select using (true);

-- Mirrors ahl_penalty_shots exactly (no coordinates exist for penalty
-- shots, make or miss).
create table public.echl_penalty_shots (
  id bigint generated always as identity primary key,
  game_id bigint not null references public.echl_game_log(game_id),
  season_id bigint not null,
  season_type text,
  team_id bigint not null,
  player_id bigint not null,
  goalie_id bigint,
  period_id bigint not null,
  time_seconds bigint not null,
  is_goal boolean not null,
  created_at timestamptz not null default now(),
  constraint echl_penalty_shots_natural_key
    unique (game_id, team_id, player_id, period_id, time_seconds)
);

create index echl_penalty_shots_game_id_idx on public.echl_penalty_shots (game_id);
create index echl_penalty_shots_player_id_idx on public.echl_penalty_shots (player_id);

alter table public.echl_penalty_shots enable row level security;
create policy "public read access" on public.echl_penalty_shots
  for select using (true);

-- Mirrors ahl_skipped_games -- shared "why didn't this game get
-- processed" tracking across echl_shot_events.py / echl_penalty_shots.py
-- / echl_game_boxscore.py.
create table public.echl_skipped_games (
  game_id bigint not null,
  pipeline text not null,
  reason text,
  skipped_at timestamptz not null default now(),
  constraint echl_skipped_games_natural_key unique (game_id, pipeline)
);

alter table public.echl_skipped_games enable row level security;
create policy "public read access" on public.echl_skipped_games
  for select using (true);
