-- Session 42 — new tables for penalty shots + goal-level on-ice roster data.
-- Run this in the Supabase SQL editor. This file is a reference copy, not
-- executed by any pipeline code (this repo has no migration tooling —
-- schema changes are applied directly in Supabase, same as every existing
-- pwhl_* table).

-- ── Item A: penalty shots (their own table, sourced from gameSummary's
-- penaltyShots.homeTeam[]/visitingTeam[], not the PBP "penaltyshot" event —
-- gameSummary's penaltyShots already includes misses, which periods[].goals[]
-- can't, and has cleanly-shaped team objects the PBP event doesn't).
create table public.pwhl_penalty_shots (
  id bigint generated always as identity primary key,
  game_id bigint not null,
  season_id bigint not null,
  season_type text,
  team_id bigint not null,       -- shooter's team
  player_id bigint not null,     -- shooter
  goalie_id bigint,
  period_id bigint not null,
  time_seconds bigint not null,
  is_goal boolean not null,
  created_at timestamptz not null default now(),
  constraint pwhl_penalty_shots_natural_key
    unique (game_id, team_id, player_id, period_id, time_seconds)
);

create index pwhl_penalty_shots_game_id_idx on public.pwhl_penalty_shots (game_id);
create index pwhl_penalty_shots_player_id_idx on public.pwhl_penalty_shots (player_id);

alter table public.pwhl_penalty_shots enable row level security;
create policy "public read access" on public.pwhl_penalty_shots
  for select using (true);

-- ── Item B: goal-level on-ice roster, normalized one row per
-- (game_goal_id, player_id). Sourced from gameSummary's
-- periods[].goals[].plus_players[]/minus_players[]. Situational flags are
-- copied onto each row so consumers don't need to join back to
-- pwhl_shot_events just to exclude power-play goals (the empirically
-- confirmed rule for reproducing HockeyTech's own plusMinus box-score
-- field: exclude power-play goals only; short-handed, empty-net, and
-- penalty-shot goals all count).
create table public.pwhl_goal_on_ice (
  id bigint generated always as identity primary key,
  game_id bigint not null,
  season_id bigint not null,
  season_type text,
  game_goal_id bigint not null,
  scoring_team_id bigint not null,  -- the team that scored this goal
  player_id bigint not null,
  team_id bigint not null,          -- this player's own team
  on_ice_for boolean not null,      -- true = plus_players (own team scored), false = minus_players (own team conceded)
  is_power_play boolean,
  is_short_handed boolean,
  is_empty_net boolean,
  is_penalty_shot boolean,
  created_at timestamptz not null default now(),
  constraint pwhl_goal_on_ice_natural_key unique (game_goal_id, player_id)
);

create index pwhl_goal_on_ice_game_id_idx on public.pwhl_goal_on_ice (game_id);
create index pwhl_goal_on_ice_player_id_idx on public.pwhl_goal_on_ice (player_id);

alter table public.pwhl_goal_on_ice enable row level security;
create policy "public read access" on public.pwhl_goal_on_ice
  for select using (true);
