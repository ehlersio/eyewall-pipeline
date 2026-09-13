-- game_win_probs + prediction_scorecard -- new tables for win_probs.py and
-- prediction_scorecard.py. Run this in the Supabase SQL editor before
-- deploying either.
--
-- game_win_probs: each morning, the pre-game Elo win probability for every
-- NHL regular-season/playoff game today and tomorrow (win_probs.py). Same
-- model and inputs as the Worker's /prediction/analyze and the game
-- preview's win bar: team_elo_ratings + elo.expected_prob(), home advantage
-- unless the schedule marks the game neutral-site. A game's row is
-- rewritten each run until it starts, so what remains is the last pre-game
-- number -- the public scorecard only ever grades predictions published
-- before puck drop.
--
-- prediction_scorecard: one row per (model, kind, period), rebuilt nightly
-- by prediction_scorecard.py.
--   model  'game_winner' | 'starting_goalie' | 'playoff_odds'
--   kind   'live'     -- graded predictions that were published beforehand
--          'backtest' -- the model replayed on past seasons, labeled as such
--   period e.g. '2026-27', or '2023-24 to 2025-26' for a backtest
--   status 'ok' | 'pending' (nothing graded yet -- e.g. playoff odds before
--          the regular season ends)
--   baseline (jsonb)    { name, accuracy, brier } -- the simple rule to beat
--   calibration (jsonb) [{ bucket, n, predicted, actual }] -- 10 buckets
--   recent (jsonb)      live only: the latest graded predictions
-- Plain probabilities -- no betting framing wherever this is shown.

create table if not exists public.game_win_probs (
  id bigint generated always as identity primary key,
  season        integer not null,
  game_id       bigint not null,
  game_date     date not null,
  game_type     integer not null,
  home_team     text not null,
  away_team     text not null,
  neutral       boolean not null default false,
  home_rating   double precision not null,
  away_rating   double precision not null,
  home_win_prob double precision not null,
  run_date      date not null,
  updated_at    timestamptz not null default now(),
  constraint game_win_probs_game_key unique (game_id)
);

create index if not exists game_win_probs_season_idx
  on public.game_win_probs (season, game_date);

create table if not exists public.prediction_scorecard (
  id bigint generated always as identity primary key,
  model        text not null,
  kind         text not null check (kind in ('live', 'backtest')),
  period       text not null,
  status       text not null default 'ok',
  n            integer not null default 0,
  accuracy     double precision,
  brier        double precision,
  log_loss     double precision,
  baseline     jsonb,
  calibration  jsonb not null default '[]'::jsonb,
  recent       jsonb not null default '[]'::jsonb,
  note         text,
  updated_at   timestamptz not null default now(),
  constraint prediction_scorecard_key unique (model, kind, period)
);

alter table public.game_win_probs enable row level security;
alter table public.prediction_scorecard enable row level security;

-- Read-only public content, same posture as playoff_odds / goalie_start_probs.
-- Writes stay service-role-only (the pipeline uses SUPABASE_SERVICE_KEY,
-- which bypasses RLS) -- no INSERT/UPDATE/DELETE policy for anon.
create policy "anon can read game_win_probs" on public.game_win_probs
  for select to anon using (true);
create policy "anon can read prediction_scorecard" on public.prediction_scorecard
  for select to anon using (true);

-- Standing RLS audit (see CLAUDE.md) -- run afterwards; zero rows back = clean:
--
-- select t.schemaname, t.tablename,
--   case when t.rowsecurity = false then 'RLS DISABLED...'
--        when count(p.policyname) = 0 then 'RLS ENABLED, ZERO POLICIES...' end as risk,
--   t.rowsecurity as rls_enabled, count(p.policyname) as policy_count
-- from pg_tables t
-- left join pg_policies p on p.schemaname=t.schemaname and p.tablename=t.tablename
-- where t.schemaname = 'public'
-- group by t.schemaname, t.tablename, t.rowsecurity
-- having t.rowsecurity = false or count(p.policyname) = 0
-- order by risk, t.tablename;
