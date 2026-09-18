-- projected_lines -- each NHL team's projected forward lines and D pairs for
-- its NEXT game (projected_lines.py). Run this in the Supabase SQL editor
-- before deploying projected_lines.py.
--
-- One current projection per team: projected_lines.py deletes a team's rows
-- and re-inserts them every run, so there is no history here. Grading past
-- projections is the backtests' job (docs/projected_lines_backtest_results.md);
-- a live track record would be a separate table written before each game.
--
-- basis:
--   'last_game'  groupings from the team's last regular-season game
--   'preseason'  groupings pooled over this season's preseason games
-- basis_game_id is the game the groupings were last drawn from; basis_games
-- is how many games they're drawn from (1 for last_game).
-- filled_ids lists players who were NOT in the basis lineup (filling an
-- injured / departed player's spot) -- the least certain part of a
-- projection, for the UI to mark.
create table if not exists public.projected_lines (
  team text not null,
  season integer not null,
  unit_type text not null check (unit_type in ('F', 'D')),
  rank integer not null,
  player_ids bigint[] not null,
  names text[] not null,
  positions text[] not null,
  filled_ids bigint[] not null default '{}',
  basis text not null check (basis in ('last_game', 'preseason')),
  basis_game_id bigint,
  basis_games integer not null,
  generated_at timestamptz not null default now(),
  primary key (team, unit_type, rank)
);

alter table public.projected_lines enable row level security;

-- Public read-only data, same posture as team_elo_ratings
-- (docs/team_elo_ratings_create.sql). Writes stay service-role-only;
-- projected_lines.py uses SUPABASE_SERVICE_KEY, which bypasses RLS.
create policy "anon can read projected_lines" on public.projected_lines
  for select to anon using (true);

-- Afterwards, run the RLS audit query in this repo's CLAUDE.md: this table
-- should NOT appear in its output.
