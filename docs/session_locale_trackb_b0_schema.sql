-- French/English localization, Track B Phase B0 -- schema for AI-generated
-- narrative content (scouting blurbs, results-vs-process narratives, game
-- summaries, trivia). Run this in the Supabase SQL editor -- this repo has
-- no migration tooling, same convention as every other docs/*.sql file
-- here.
--
-- Adds a `locale` column to all 4 AI-narrative tables and widens each
-- table's upsert conflict key to include it, so an English and a French row
-- can coexist for the same (game/player, season, team, ...) tuple instead
-- of colliding on the old key. Existing rows default to locale='en' --
-- additive and backward-compatible. Nothing breaks until Phase B1 (this
-- repo's pipeline scripts) starts writing locale='fr' rows and Phase B2
-- (eyewall-poller) starts reading them by locale.
--
-- STEP 0 (run first, read-only) -- confirms the real name of each table's
-- existing unique constraint before you drop it. This repo's docs/*.sql
-- history only records player_narratives' (docs/session56_new_columns.sql,
-- a standalone UNIQUE INDEX, not a table constraint) and trivia_questions'
-- (docs/session92_trivia_tables.sql, an unnamed inline `unique(...)`, so
-- Postgres auto-generated its name). game_summaries and player_scouting
-- predate that convention -- their constraint names below are my best
-- guess at Postgres's standard auto-naming for an unnamed inline
-- unique(...) constraint (`<table>_<col1>_<col2>_..._key`). If this query
-- returns a different name for either one, substitute it into STEP 2 below
-- before running -- the DROP statements use `if exists` so a wrong guess
-- fails safely (no-op) rather than erroring, but the later ADD CONSTRAINT
-- would then fail on a leftover duplicate-columns conflict, so don't skip
-- this check.
select
  conrelid::regclass as table_name,
  conname as constraint_name,
  pg_get_constraintdef(oid) as definition
from pg_constraint
where conrelid in (
  'public.game_summaries'::regclass,
  'public.player_scouting'::regclass,
  'public.trivia_questions'::regclass
)
and contype = 'u';

-- ── STEP 1: add the column to all 4 tables ──────────────────────────────
alter table public.game_summaries
  add column locale text not null default 'en' check (locale in ('en', 'fr'));

alter table public.player_scouting
  add column locale text not null default 'en' check (locale in ('en', 'fr'));

alter table public.player_narratives
  add column locale text not null default 'en' check (locale in ('en', 'fr'));

alter table public.trivia_questions
  add column locale text not null default 'en' check (locale in ('en', 'fr'));

-- ── STEP 2: widen each table's conflict key to include locale ──────────

-- game_summaries: was (game_id, team) -- see ai_summaries.py's save_summary()
alter table public.game_summaries
  drop constraint if exists game_summaries_game_id_team_key;
alter table public.game_summaries
  add constraint game_summaries_game_id_team_locale_key unique (game_id, team, locale);

-- player_scouting: was (player_id, season, team) -- see ai_scouting.py's
-- upsert_scouting_blurb()
alter table public.player_scouting
  drop constraint if exists player_scouting_player_id_season_team_key;
alter table public.player_scouting
  add constraint player_scouting_player_id_season_team_locale_key
  unique (player_id, season, team, locale);

-- player_narratives: was a standalone UNIQUE INDEX named
-- player_narratives_conflict_key (docs/session56_new_columns.sql), not a
-- table constraint -- DROP INDEX, not DROP CONSTRAINT. Recreated under the
-- same name so ai_results_vs_process.py's on_conflict= string (which
-- matches by column list, not constraint name) keeps working unmodified
-- other than adding `locale` to that column list in code.
drop index if exists public.player_narratives_conflict_key;
create unique index player_narratives_conflict_key
  on public.player_narratives (player_id, season, team, narrative_type, locale);

-- trivia_questions: was (question_date, tier, sport, team) --
-- docs/session92_trivia_tables.sql's inline unique(...)
alter table public.trivia_questions
  drop constraint if exists trivia_questions_question_date_tier_sport_team_key;
alter table public.trivia_questions
  add constraint trivia_questions_question_date_tier_sport_team_locale_key
  unique (question_date, tier, sport, team, locale);

-- ── STEP 3 (verify) -- should show exactly 4 rows, each definition ending
-- in ", locale)". Compare against STEP 0's output if anything looks off.
select
  conrelid::regclass as table_name,
  conname as constraint_name,
  pg_get_constraintdef(oid) as definition
from pg_constraint
where conrelid in (
  'public.game_summaries'::regclass,
  'public.player_scouting'::regclass,
  'public.trivia_questions'::regclass
)
and contype = 'u'
union all
select
  'public.player_narratives'::regclass,
  indexname,
  indexdef
from pg_indexes
where schemaname = 'public' and tablename = 'player_narratives' and indexname = 'player_narratives_conflict_key';
