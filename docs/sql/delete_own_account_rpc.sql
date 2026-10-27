-- delete_own_account() -- in-app account deletion (App Store Guideline
-- 5.1.1(v): apps that let users create an account must let them delete it
-- from inside the app). Run this in the Supabase SQL editor -- this repo
-- has no migration tooling, same convention as every other docs/*.sql file
-- here.
--
-- Called by eyewall-analytics' AuthContext.deleteAccount() as
-- supabase.rpc('delete_own_account') with the signed-in user's own JWT.
-- security definer so it can delete from auth.users, which the
-- authenticated role can't touch directly; auth.uid() scopes it to the
-- caller, so there's no argument for a client to tamper with.
--
-- Everything user-owned cascades from auth.users: user_preferences and
-- trivia_answers both reference auth.users(id) on delete cascade (see
-- session90_user_preferences_table.sql / session92_trivia_tables.sql), and
-- Supabase's own auth.identities/auth.sessions/auth.refresh_tokens cascade
-- too. Push subscriptions live in the Worker's KV keyed by endpoint/device
-- token and were never linked to an account, so nothing else needs cleanup.
--
-- A Postgres function rather than an eyewall-poller route on purpose: the
-- Worker only holds the publishable key, so a route would need the
-- service-role key added as a new Worker secret just for this one call.
create or replace function public.delete_own_account()
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid uuid := auth.uid();
begin
  if uid is null then
    raise exception 'not signed in' using errcode = '42501';
  end if;
  delete from auth.users where id = uid;
end;
$$;

-- Postgres grants EXECUTE to PUBLIC by default, and Supabase's default
-- privileges grant it to anon as well -- lock it to signed-in users only.
revoke execute on function public.delete_own_account() from public, anon;
grant execute on function public.delete_own_account() to authenticated;

-- Verify: prosecdef should be true, and proacl should list authenticated
-- (and postgres/service_role) but not anon or an empty-grantee PUBLIC entry.
--
-- select proname, prosecdef, proacl from pg_proc where proname = 'delete_own_account';
