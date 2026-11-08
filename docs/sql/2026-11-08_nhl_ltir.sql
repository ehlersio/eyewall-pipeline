-- NHL LTIR -- one table for nhl_contracts.py (2026-11-08), which mirrors the
-- open nhl-contracts dataset's new ltir.json (nhl-contracts #12): one row per
-- long-term injured reserve stint, so the Worker can show a team's LTIR
-- players and an upper bound on its LTIR relief beside the cap space
-- estimate. Run in the Supabase SQL editor, then the CLAUDE.md RLS audit
-- query. Until this has been run the nightly stage's nhl_ltir upsert fails
-- and run.py's stage isolation records it; the tables before it in the load
-- still load.
--
-- One row per (player_key, team, first_seen):
--   player_id, player_name   the player; player_id null for older players, or a
--                            surname PuckPedia wrote that matched two players
--   player_key               the NHL id as text, else the normalized name (like
--                            nhl_dead_cap), so an id-less row upserts onto itself
--   team                     the team the source names (app tri-codes)
--   season                   cap season of since / first_seen (July 1 boundary)
--   since, since_basis       placement date: ESPN's transaction ('espn_transactions')
--                            when there is one, else PuckPedia's post
--                            ('bluesky_puckpedia'); null when no source dates it
--   first_seen               earliest date any source has the player on LTIR
--   until, until_basis       the end: 'espn_transactions' / 'bluesky_puckpedia'
--                            (an activation), 'espn_injuries' (ESPN's list dropped
--                            him), 'later_team' (a later source has him on LTIR
--                            with another team); null while open
--   active                   on ESPN's IR-LT list with that team now, or open with
--                            its last mention in the current season
--   stated_relief_usd, stated_at, stated_source
--                            the relief PuckPedia stated with the placement, as
--                            written. One post placing two players states one
--                            figure for both: sum a team once per stated_source
--   sources                  ';'-joined dataset source ids
--   conflicts                'espn_not_listed' | 'espn_status:IR' | 'team:A|B', ';'-joined
--   flags                    'activation_from_ir', 'placement_not_found',
--                            'end_not_found', 'retroactive', 'offseason',
--                            'stated_relief_shared', 'name_unresolved'
--   dataset_updated_at       the load that last wrote the row; older rows are
--                            deleted after each load (the table mirrors the release)

create table if not exists public.nhl_ltir (
  player_key          text not null,
  team                text not null,
  first_seen          date not null,
  player_id           bigint,
  player_name         text not null,
  season              text not null,
  since               date,
  since_basis         text check (since_basis in ('espn_transactions', 'bluesky_puckpedia')),
  until               date,
  until_basis         text check (until_basis in ('espn_transactions', 'bluesky_puckpedia',
                                                  'espn_injuries', 'later_team')),
  active              boolean not null default false,
  stated_relief_usd   bigint,
  stated_at           date,
  stated_source       text,
  sources             text,
  conflicts           text,
  flags               text,
  dataset_updated_at  timestamptz not null,
  created_at          timestamptz not null default now(),
  primary key (player_key, team, first_seen)
);
create index if not exists nhl_ltir_team_active_idx on public.nhl_ltir (team, active);
create index if not exists nhl_ltir_player_idx on public.nhl_ltir (player_id);
create index if not exists nhl_ltir_updated_idx on public.nhl_ltir (dataset_updated_at);

alter table public.nhl_ltir enable row level security;
create policy "anon can read nhl_ltir" on public.nhl_ltir
  for select to anon using (true);

alter table public.nhl_contracts_loads add column if not exists n_ltir integer;
