-- game_shot_xg + {pwhl,ahl,echl}_game_shot_xg -- each team's expected goals
-- per game from EyeWall's own shot models (game_shot_xg.py). Run this in
-- the Supabase SQL editor before deploying game_shot_xg.py; until then the
-- stage logs one error and writes nothing.
--
-- Not MoneyPuck: game_xg holds MoneyPuck's numbers and stays app-only.
-- These tables are what the public API serves
-- (eyewall/eyewall-api-band-xg-scope-2026-11-18.md).
--
--   model: which model scored the row.
--     NHL  logistic-YYYY-MM-DD  nhl_xg_model.py, the coefficients'
--          generated date (nhl_xg_model.json). A refit changes it and the
--          stage rescores every game.
--     PWHL/AHL/ECHL  distance-bands-season-fit  hockeytech_shot_xg.py's
--          three distance bands, each band's goal rate over that season's
--          own goals and shots on goal, so a season's xG equals its goals.
--   attempts: attempts that carry xG. NHL: unblocked (goals, shots on goal,
--     missed shots). HockeyTech: goals and shots on goal (the AHL/ECHL
--     feeds have nothing else, and PWHL blocked shots can carry the wrong
--     team). Shootout attempts and penalty shots are never counted.
--   situation (NHL only): 'all', or '5v5' (situation_code 1551).

create table if not exists public.game_shot_xg (
  game_id bigint not null,
  season integer not null,
  game_type integer not null,
  game_date date,
  team text not null,
  opponent text not null,
  is_home boolean not null,
  situation text not null check (situation in ('all', '5v5')),
  attempts integer not null,
  goals integer not null,
  xg numeric(7, 3) not null,
  model text not null,
  updated_at timestamptz not null default now(),
  primary key (game_id, team, situation)
);

create index if not exists game_shot_xg_season on public.game_shot_xg (season, game_type);
create index if not exists game_shot_xg_date on public.game_shot_xg (game_date);

alter table public.game_shot_xg enable row level security;
create policy "anon can read game_shot_xg" on public.game_shot_xg
  for select to anon using (true);

-- One table per HockeyTech league, like their other per-game tables.
do $$
declare lg text;
begin
  foreach lg in array array['pwhl', 'ahl', 'echl'] loop
    execute format($f$
      create table if not exists public.%1$s_game_shot_xg (
        game_id bigint not null,
        season_id bigint not null,
        season_type text not null,
        game_date date,
        team_id bigint not null,
        opponent_id bigint not null,
        is_home boolean not null,
        attempts integer not null,
        goals integer not null,
        xg numeric(7, 3) not null,
        model text not null,
        updated_at timestamptz not null default now(),
        primary key (game_id, team_id)
      )$f$, lg);
    execute format('create index if not exists %1$s_game_shot_xg_season on public.%1$s_game_shot_xg (season_id)', lg);
    execute format('create index if not exists %1$s_game_shot_xg_date on public.%1$s_game_shot_xg (game_date)', lg);
    execute format('alter table public.%1$s_game_shot_xg enable row level security', lg);
    execute format('create policy "anon can read %1$s_game_shot_xg" on public.%1$s_game_shot_xg for select to anon using (true)', lg);
  end loop;
end $$;

-- Afterwards, run the RLS audit query in this repo's CLAUDE.md: none of
-- the four tables should appear in its output. Then backfill:
--   python game_shot_xg.py --season 20222023   (and each later season)
--   python game_shot_xg.py --league pwhl|ahl|echl [season_id]
