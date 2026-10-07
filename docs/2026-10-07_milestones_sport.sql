-- AHL/ECHL milestones in the shared `milestones` table (2026-10-07, audit
-- 2026-10-06 Phase 3 contract C5). Run in the Supabase SQL editor before
-- hockeytech_milestones.py's first nightly run (ahl-/echl-nightly.yml); until
-- then its upsert fails, is logged once, and the run moves on.
--
-- `milestones` told leagues apart with is_pwhl alone. AHL/ECHL rows are
-- written with is_pwhl = false and sport = 'ahl' | 'echl', and `season` is
-- the HockeyTech season_id. NHL and PWHL rows are left as they are (sport
-- NULL): milestones.py / pwhl_milestones.py don't write it, and
-- eyewall-poller keeps filtering those two on is_pwhl (+ season), the
-- AHL/ECHL ones on sport=eq.X&season=eq.N. An NHL query (is_pwhl = false)
-- never picks up an AHL/ECHL row because their season ids (90-ish, 70-ish)
-- never equal an NHL season (20262027).
--
-- The existing unique key (game_id, player_id, milestone_type, event_key)
-- stays: game ids are disjoint across the leagues (NHL 10 digits, AHL
-- ~1,000,000, ECHL ~24,000, PWHL < 1,000).

alter table public.milestones add column if not exists sport text;

alter table public.milestones drop constraint if exists milestones_sport_check;
alter table public.milestones
  add constraint milestones_sport_check check (sport is null or sport in ('nhl', 'pwhl', 'ahl', 'echl'));

create index if not exists milestones_sport_season_idx
  on public.milestones (sport, season, game_date desc)
  where sport is not null;
