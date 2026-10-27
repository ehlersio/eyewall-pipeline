-- game_type on line_combinations and special_teams_units.
-- Run in the Supabase SQL editor before merging the pipeline and poller
-- PRs for step 4 of the game-type split. This repo has no migration tooling.
--
-- Why: line_combinations.py and special_teams.py built a season's lines
-- and PP/PK units from every game of the season in game_log -- preseason
-- and playoffs included. On 2026-09-29 all 224 of 2026-27's
-- line_combinations rows and 127 special_teams_units were built from
-- preseason games alone. Lines are now built per game type (2 regular
-- season, 3 playoffs; preseason never), each stored under its own
-- game_type.
--
-- Both tables are small (a few hundred rows a season), and adding a
-- column with a constant default is metadata-only in Postgres 11+, so
-- nothing is rewritten. Existing rows get game_type 2; the next nightly
-- rebuilds them from regular-season games (or clears them for a team that
-- hasn't played one yet).

alter table public.line_combinations
  add column if not exists game_type smallint not null default 2
  check (game_type in (2, 3));

comment on column public.line_combinations.game_type is
  'NHL gameType the units were built from: 2 regular season, 3 playoffs.';

-- source: a playoff slot the playoff games can't fill yet is filled from
-- the same season's regular-season units, tagged 'regular_season'.
alter table public.line_combinations
  drop constraint if exists line_combinations_source_check;
alter table public.line_combinations
  add constraint line_combinations_source_check
  check (source in ('current', 'prior_season', 'regular_season'));

-- line_combinations also has a unique index on (season, team, unit_type,
-- rank), created outside this repo's SQL files. With playoff units stored
-- alongside the regular season's, a team's playoff Line 1 collides with
-- its regular-season Line 1 (23505, found rebuilding 2023-24..2025-26 on
-- 2026-09-29), so game_type joins the key. Dropped as a constraint or as a
-- plain index, whichever it is.
alter table public.line_combinations drop constraint if exists line_combinations_unit_idx;
drop index if exists public.line_combinations_unit_idx;
create unique index line_combinations_unit_idx
  on public.line_combinations (season, team, game_type, unit_type, rank);

alter table public.special_teams_units
  add column if not exists game_type smallint not null default 2
  check (game_type in (2, 3));

comment on column public.special_teams_units.game_type is
  'NHL gameType the units were inferred from: 2 regular season, 3 playoffs.';

-- special_teams.py upserts on (team, season, unit_type, unit_number); it
-- now needs game_type in that key. Drop whichever unique constraint or
-- index covers exactly those four columns, then add the new one.
do $$
declare
  c record;
begin
  for c in
    select con.conname
    from pg_constraint con
    where con.conrelid = 'public.special_teams_units'::regclass
      and con.contype = 'u'
      and (
        select array_agg(att.attname::text order by att.attname)
        from unnest(con.conkey) k
        join pg_attribute att on att.attrelid = con.conrelid and att.attnum = k
      ) = array['season', 'team', 'unit_number', 'unit_type']
  loop
    execute format('alter table public.special_teams_units drop constraint %I', c.conname);
    raise notice 'dropped constraint %', c.conname;
  end loop;
  for c in
    select i.relname
    from pg_index x
    join pg_class i on i.oid = x.indexrelid
    where x.indrelid = 'public.special_teams_units'::regclass
      and x.indisunique and not x.indisprimary
      and not exists (select 1 from pg_constraint where conindid = x.indexrelid)
      and (
        select array_agg(att.attname::text order by att.attname)
        from unnest(x.indkey) k
        join pg_attribute att on att.attrelid = x.indrelid and att.attnum = k
      ) = array['season', 'team', 'unit_number', 'unit_type']
  loop
    execute format('drop index public.%I', c.relname);
    raise notice 'dropped index %', c.relname;
  end loop;
end $$;

alter table public.special_teams_units
  add constraint special_teams_units_team_season_game_type_unit_key
  unique (team, season, game_type, unit_type, unit_number);

-- Check: both columns exist, and the new key is the only unique one
-- besides the primary key.
--
-- select conname, pg_get_constraintdef(oid)
-- from pg_constraint
-- where conrelid in ('public.special_teams_units'::regclass, 'public.line_combinations'::regclass)
-- order by 1;
