-- team_needs -- each NHL team's lineup slots, the players in them with their
-- ratings, and each slot's league rank (team_needs.py). Run this in the
-- Supabase SQL editor before deploying team_needs.py.
--
-- One current set per team: team_needs.py upserts every team's rows each
-- night and deletes whatever it didn't write (generated_at older than the
-- run), so there is no history here.
--
-- slot: F_TOP6_C, F_TOP6_W, F_BOT6_C, F_BOT6_W, D_TOP4, D_BOT2, G_1A, G_1B
-- players: [{id, name, pos, shoots, rating, rating_basis, gp, filled,
--            games_started (goalies)}], rating = WAR per 82 (skaters) or
--            GSAx per 60 (goalies); rating_basis 'season' | 'last_season' |
--            null (unrated)
-- strength: mean rating of the rated players, null unless at least
--           two-thirds are rated
-- league_rank: 1 = strongest among the league_teams teams with a strength;
--              need = bottom third of them
-- right_shots: right-shot defensemen in D_TOP4 (null for other slots)
-- basis: projected_lines' basis ('last_game' | 'preseason') for skater
--        slots, 'goalie_starts' for goalie slots
create table if not exists public.team_needs (
  team text not null,
  season integer not null,
  slot text not null check (
    slot in ('F_TOP6_C', 'F_TOP6_W', 'F_BOT6_C', 'F_BOT6_W', 'D_TOP4', 'D_BOT2', 'G_1A', 'G_1B')
  ),
  players jsonb not null,
  n_players integer not null,
  n_rated integer not null,
  strength double precision,
  weakest_id bigint,
  weakest_rating double precision,
  league_rank integer,
  league_teams integer not null,
  need boolean not null default false,
  right_shots integer,
  basis text,
  basis_game_id bigint,
  generated_at timestamptz not null,
  primary key (team, slot)
);

alter table public.team_needs enable row level security;

-- Public read-only data, same posture as projected_lines
-- (docs/sql/projected_lines_create.sql). Writes stay service-role-only;
-- team_needs.py uses SUPABASE_SERVICE_KEY, which bypasses RLS.
create policy "anon can read team_needs" on public.team_needs
  for select to anon using (true);

-- Afterwards, run the RLS audit query in this repo's CLAUDE.md: this table
-- should NOT appear in its output.
