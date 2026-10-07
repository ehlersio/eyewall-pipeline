-- AHL/ECHL goal-level on-ice rosters (2026-10-07, audit 2026-10-06 Phase 3
-- contract C6). Run in the Supabase SQL editor before
-- `python hockeytech_goal_on_ice.py ahl|echl` (ahl-/echl-nightly.yml); until
-- then that step logs one error and skips.
--
-- Same shape as pwhl_goal_on_ice (docs/session42_new_tables.sql): one row per
-- (game_goal_id, player_id) from gameSummary's
-- periods[].goals[].plus_players[]/minus_players[]. game_goal_id is the id
-- gameSummary and the play-by-play both use for the goal. eyewall-poller's
-- /{league}/game-box reads these rows by game_id for its `goals` array.

create table if not exists public.ahl_goal_on_ice (
  id bigint generated always as identity primary key,
  game_id bigint not null,
  season_id bigint not null,
  season_type text,
  game_goal_id bigint not null,
  scoring_team_id bigint not null,  -- the team that scored this goal
  player_id bigint not null,
  team_id bigint not null,          -- this player's own team
  on_ice_for boolean not null,      -- true = plus_players (own team scored), false = minus_players
  is_power_play boolean,
  is_short_handed boolean,
  is_empty_net boolean,
  is_penalty_shot boolean,
  created_at timestamptz not null default now(),
  constraint ahl_goal_on_ice_natural_key unique (game_goal_id, player_id)
);
create index if not exists ahl_goal_on_ice_game_id_idx on public.ahl_goal_on_ice (game_id);
create index if not exists ahl_goal_on_ice_player_id_idx on public.ahl_goal_on_ice (player_id);

create table if not exists public.echl_goal_on_ice (like public.ahl_goal_on_ice including all);

alter table public.ahl_goal_on_ice enable row level security;
alter table public.echl_goal_on_ice enable row level security;
create policy "public read access" on public.ahl_goal_on_ice for select using (true);
create policy "public read access" on public.echl_goal_on_ice for select using (true);
