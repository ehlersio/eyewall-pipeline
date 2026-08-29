-- AHL pipeline foundation — new tables.
-- Run this in the Supabase SQL editor before the ahl-nightly.yml workflow
-- (or a manual `python ahl_stats.py` / `python ahl_shot_events.py` /
-- `python ahl_penalty_shots.py`) is run for the first time. This repo has
-- no migration tooling — schema changes are applied directly in Supabase,
-- same as every existing pwhl_* table (see docs/session42_new_tables.sql
-- for the precedent this mirrors).
--
-- Dedicated ahl_* tables, not a shared schema with pwhl_*/nhl_* tables and
-- a league discriminator column — deliberate choice (see
-- AHL_BUILD_BRIEF.md): AHL's real data shape has no shift/TOI/attempts-
-- based-Corsi/faceoff/hits data at all (confirmed during investigation,
-- see docs/hockeytech-ahl-api-notes.md), so forcing it into PWHL's schema
-- would mean permanently-null columns on every row. No ahl_teams table:
-- PWHL doesn't have one either in this pipeline — team display metadata
-- lives in the frontend, not here; ahl_stats.py hardcodes TEAM_ID_MAP the
-- same way pwhl_stats.py does.

create table public.ahl_players (
  player_id bigint primary key,
  first_name text,
  last_name text,
  position text,
  shoots text,
  height_inches integer,
  weight_lbs integer,           -- real data for AHL, unlike PWHL (always "0" there,
                                 -- deliberately not ingested in pwhl_players)
  birth_date date,
  birth_place text,
  jersey_number integer,
  team_id bigint,
  updated_at timestamptz not null default now()
);

alter table public.ahl_players enable row level security;
create policy "public read access" on public.ahl_players
  for select using (true);

create table public.ahl_player_seasons (
  id bigint generated always as identity primary key,
  player_id bigint not null references public.ahl_players(player_id),
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
  -- AHL's players view entirely, not just occasionally null (see
  -- docs/hockeytech-ahl-api-notes.md) — don't add columns that would
  -- always read NULL.
  updated_at timestamptz not null default now(),
  constraint ahl_player_seasons_natural_key
    unique (player_id, team_id, season_id, season_type)
);

create index ahl_player_seasons_season_idx on public.ahl_player_seasons (season_id);

alter table public.ahl_player_seasons enable row level security;
create policy "public read access" on public.ahl_player_seasons
  for select using (true);

create table public.ahl_goalie_seasons (
  id bigint generated always as identity primary key,
  player_id bigint not null references public.ahl_players(player_id),
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
  toi text,                      -- HockeyTech reports "minutes_played" as MM:SS-style text, same
                                  -- as pwhl_goalie_seasons.toi -- not parsed to seconds here
  updated_at timestamptz not null default now(),
  constraint ahl_goalie_seasons_natural_key
    unique (player_id, team_id, season_id, season_type)
);

create index ahl_goalie_seasons_season_idx on public.ahl_goalie_seasons (season_id);

alter table public.ahl_goalie_seasons enable row level security;
create policy "public read access" on public.ahl_goalie_seasons
  for select using (true);

create table public.ahl_team_seasons (
  id bigint generated always as identity primary key,
  team_id bigint not null,
  season_id bigint not null,
  season_type text not null,
  gp integer default 0,
  wins integer default 0,
  losses integer default 0,
  ot_losses integer default 0,
  shootout_losses integer default 0,   -- AHL reports these separately, unlike PWHL's
                                        -- single combined non_reg_losses
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
  constraint ahl_team_seasons_natural_key
    unique (team_id, season_id, season_type)
);

alter table public.ahl_team_seasons enable row level security;
create policy "public read access" on public.ahl_team_seasons
  for select using (true);

create table public.ahl_game_log (
  game_id bigint primary key,
  season_id bigint not null,
  game_date date,
  home_team_id bigint,
  away_team_id bigint,
  home_score integer,
  away_score integer,
  game_state text,               -- e.g. "Final" -- no ot/shootout boolean columns:
                                  -- not cleanly available from the scorebar view AHL uses
                                  -- (a different view than PWHL's schedule view — see
                                  -- docs/hockeytech-ahl-api-notes.md), left out rather
                                  -- than guessed at
  venue_name text,
  venue_city text,
  updated_at timestamptz not null default now()
);

create index ahl_game_log_season_idx on public.ahl_game_log (season_id);
create index ahl_game_log_state_idx on public.ahl_game_log (game_state);

alter table public.ahl_game_log enable row level security;
create policy "public read access" on public.ahl_game_log
  for select using (true);

-- One row per shot ATTEMPT-ON-GOAL (event_type='shot') or GOAL
-- (event_type='goal') with coordinates. Unlike pwhl_shot_events, AHL's
-- feed has no blocked_shot events at all (confirmed absent, see reference
-- doc) — this table only ever has these two event_types. Goal rows carry
-- assist/situational-flag columns populated directly from the PBP `goal`
-- event (no separate gameSummary merge needed, unlike PWHL) — shot rows
-- leave them NULL.
create table public.ahl_shot_events (
  id bigint generated always as identity primary key,
  game_id bigint not null references public.ahl_game_log(game_id),
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
  constraint ahl_shot_events_natural_key
    unique (game_id, event_type, period_id, time_seconds, team_id, shooter_id)
);

create index ahl_shot_events_game_id_idx on public.ahl_shot_events (game_id);
create index ahl_shot_events_shooter_id_idx on public.ahl_shot_events (shooter_id);
create index ahl_shot_events_season_idx on public.ahl_shot_events (season_id);

alter table public.ahl_shot_events enable row level security;
create policy "public read access" on public.ahl_shot_events
  for select using (true);

-- Mirrors pwhl_penalty_shots exactly (same shape, same reasoning: no
-- coordinates exist for penalty shots, make or miss).
create table public.ahl_penalty_shots (
  id bigint generated always as identity primary key,
  game_id bigint not null references public.ahl_game_log(game_id),
  season_id bigint not null,
  season_type text,
  team_id bigint not null,
  player_id bigint not null,
  goalie_id bigint,
  period_id bigint not null,
  time_seconds bigint not null,
  is_goal boolean not null,
  created_at timestamptz not null default now(),
  constraint ahl_penalty_shots_natural_key
    unique (game_id, team_id, player_id, period_id, time_seconds)
);

create index ahl_penalty_shots_game_id_idx on public.ahl_penalty_shots (game_id);
create index ahl_penalty_shots_player_id_idx on public.ahl_penalty_shots (player_id);

alter table public.ahl_penalty_shots enable row level security;
create policy "public read access" on public.ahl_penalty_shots
  for select using (true);

-- Mirrors pwhl_skipped_games -- shared "why didn't this game get
-- processed" tracking across ahl_shot_events.py / ahl_penalty_shots.py, so
-- a nightly run doesn't keep re-fetching a game with genuinely no data.
create table public.ahl_skipped_games (
  game_id bigint not null,
  pipeline text not null,
  reason text,
  skipped_at timestamptz not null default now(),
  constraint ahl_skipped_games_natural_key unique (game_id, pipeline)
);

alter table public.ahl_skipped_games enable row level security;
create policy "public read access" on public.ahl_skipped_games
  for select using (true);
