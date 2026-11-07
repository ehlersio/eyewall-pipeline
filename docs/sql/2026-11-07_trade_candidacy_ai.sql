-- trade_candidacy_ai -- one EyeWall AI paragraph per tiered trade candidate
-- and locale (ai_trade_candidacy.py, trade fit scope §5.5), shown in the NHL
-- player popup under Candidate signals. Run this in the Supabase SQL editor
-- before deploying ai_trade_candidacy.py.
--
-- Written only from the player's trade_candidacy row (signals, clause) and
-- his best trade_fit reasons. Like trade_candidacy, the table is emptied
-- outside Jan 1 - the trade deadline, and a player who loses his tier loses
-- his paragraph.
--
--   locale: en | fr
--   input_hash: sha256 of what the paragraph was written from (the worded
--               facts, locale, prompt version, model). An unchanged hash
--               means no model call the next night.
--   model: the OpenRouter model that wrote it (ai_client.MODEL)
create table if not exists public.trade_candidacy_ai (
  player_id bigint not null,
  locale text not null check (locale in ('en', 'fr')),
  season integer not null,
  paragraph text not null,
  input_hash text not null,
  model text not null,
  generated_at timestamptz not null,
  primary key (player_id, locale)
);

alter table public.trade_candidacy_ai enable row level security;

-- Public read-only data, same posture as trade_candidacy
-- (docs/sql/2026-11-07_trade_candidacy.sql). Writes stay service-role-only;
-- ai_trade_candidacy.py uses SUPABASE_SERVICE_KEY, which bypasses RLS.
create policy "anon can read trade_candidacy_ai" on public.trade_candidacy_ai
  for select to anon using (true);

-- Afterwards, run the RLS audit query in this repo's CLAUDE.md: the table
-- should not appear in its output.
