-- French/English localization for pre-game predictions -- the same Track B
-- change docs/session_locale_trackb_b0_schema.sql made to the other AI
-- narrative tables, applied to game_predictions (which that pass missed).
-- Run in the Supabase SQL editor BEFORE merging the ai_predictions.py
-- change (its upsert targets game_id,locale) and the eyewall-poller change
-- (its /game-predictions read filters on locale).
--
-- Adds `locale` (existing rows default to 'en') and widens the upsert
-- conflict key from (game_id) to (game_id, locale), so an English and a
-- French prediction can coexist for the same game.

-- STEP 0 (read-only) -- confirm the real name of the existing unique
-- constraint on game_id. The DROP below assumes Postgres's standard
-- auto-name for an unnamed inline unique(game_id); if this returns a
-- different name, substitute it before running STEP 2. (If game_id is
-- unique through a standalone UNIQUE INDEX instead, it shows up in the
-- second query -- drop that index by name instead.)
select conname, pg_get_constraintdef(oid) as definition
from pg_constraint
where conrelid = 'public.game_predictions'::regclass and contype = 'u';

select indexname, indexdef
from pg_indexes
where schemaname = 'public' and tablename = 'game_predictions';

-- ── STEP 1: add the column ──────────────────────────────────────────────
alter table public.game_predictions
  add column if not exists locale text not null default 'en'
  check (locale in ('en', 'fr'));

-- ── STEP 2: widen the conflict key ──────────────────────────────────────
alter table public.game_predictions
  drop constraint if exists game_predictions_game_id_key;
alter table public.game_predictions
  add constraint game_predictions_game_id_locale_key unique (game_id, locale);

-- STEP 3 (read-only) -- should list only game_predictions_game_id_locale_key
-- (plus the primary key under contype 'p', not shown here).
select conname, pg_get_constraintdef(oid) as definition
from pg_constraint
where conrelid = 'public.game_predictions'::regclass and contype = 'u';
