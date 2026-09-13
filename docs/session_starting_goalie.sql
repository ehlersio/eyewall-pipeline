-- goalie_game_starts + goalie_start_probs -- new tables for goalie_starts.py
-- and starting_goalie.py. Run this in the Supabase SQL editor before
-- deploying either.
--
-- goalie_game_starts: every goalie who dressed in every NHL regular-season
-- and playoff game, from the NHL's own gamecenter boxscore
-- (playerByGameStats.{home,away}Team.goalies[] -- `starter` is the NHL's
-- flag, present back to at least 2023-24). Two rows per team per game
-- (starter + backup), occasionally three. The history the starting-goalie
-- model learns from, and the ground truth its predictions are scored on.
--
-- goalie_start_probs: for each upcoming regular-season game, each team's
-- candidate goalies and the probability each starts (sums to 1 per team per
-- game). Written nightly by starting_goalie.py; the row for a game is
-- rewritten each night until the game is played, so what's left afterwards
-- is the morning-of prediction -- scoreable against goalie_game_starts.
-- factors (jsonb): what drove it -- { share_last10, share_season,
-- started_last, back_to_back, days_rest, injury_status }.
-- Plain probabilities -- no betting framing anywhere this is shown.

create table if not exists public.goalie_game_starts (
  id bigint generated always as identity primary key,
  season      integer not null,
  game_id     bigint not null,
  game_date   date not null,
  game_type   integer not null,
  team        text not null,
  opponent    text not null,
  is_home     boolean not null,
  goalie_id   bigint not null,
  goalie_name text,
  started     boolean not null,
  toi_secs    integer,
  decision    text,
  updated_at  timestamptz not null default now(),
  constraint goalie_game_starts_key unique (game_id, goalie_id)
);

create index if not exists goalie_game_starts_team_idx
  on public.goalie_game_starts (team, season, game_date);

create table if not exists public.goalie_start_probs (
  id bigint generated always as identity primary key,
  season      integer not null,
  run_date    date not null,
  game_id     bigint not null,
  game_date   date not null,
  team        text not null,
  opponent    text not null,
  is_home     boolean not null,
  goalie_id   bigint not null,
  goalie_name text,
  start_prob  double precision not null,
  factors     jsonb,
  updated_at  timestamptz not null default now(),
  constraint goalie_start_probs_key unique (game_id, goalie_id)
);

create index if not exists goalie_start_probs_team_idx
  on public.goalie_start_probs (team, game_date);

alter table public.goalie_game_starts enable row level security;
alter table public.goalie_start_probs enable row level security;

-- Read-only public content, same posture as player_injuries / playoff_odds.
-- Writes stay service-role-only (the pipeline uses SUPABASE_SERVICE_KEY,
-- which bypasses RLS) -- no INSERT/UPDATE/DELETE policy for anon.
create policy "anon can read goalie_game_starts" on public.goalie_game_starts
  for select to anon using (true);
create policy "anon can read goalie_start_probs" on public.goalie_start_probs
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
