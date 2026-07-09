"""
Supabase client shared across pipeline modules.
"""

import os

import httpx
from dotenv import load_dotenv

# Import ClientOptions from the package root, not from supabase.lib.client_options.
# The submodule path resolves to a non-public base class missing a `storage`
# attribute create_client() expects, raising AttributeError at call time (not
# at import time). The root import resolves to SyncClientOptions, which is
# what create_client() actually wants. Confirmed intentional (not a bug) by
# supabase-py maintainers: https://github.com/supabase/supabase-py/issues/1306
# — still true as of 2.31.0, don't "clean up" this import back to the submodule.
from supabase import Client, ClientOptions, create_client

from season_lookup import get_nhl_season

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
NHL_SEASON = get_nhl_season()  # live-resolved via Worker; falls back to NHL_SEASON env var
PRIMARY_TEAM_ABBR = os.environ.get("PRIMARY_TEAM_ABBR", "CAR")


def get_client() -> Client:
    # httpx_client (not postgrest_client_timeout) — supabase-py's own glue code
    # unconditionally forwards postgrest_client_timeout to a deprecated
    # SyncPostgrestClient(timeout=...) constructor arg internally (confirmed via
    # 2.31.0's supabase/_sync/client.py:_init_postgrest_client); passing a
    # pre-built httpx client bypasses that path entirely, timeout still applies.
    return create_client(
        SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(httpx_client=httpx.Client(timeout=120))
    )


def upsert(client: Client, table: str, rows: list, conflict: str):
    """Upsert rows into table, handling conflicts on the given column(s)."""
    if not rows:
        return
    # Supabase upsert in batches of 500
    for i in range(0, len(rows), 500):
        batch = rows[i : i + 500]
        client.table(table).upsert(batch, on_conflict=conflict).execute()
    print(f"  OK {table}: {len(rows)} rows upserted")
