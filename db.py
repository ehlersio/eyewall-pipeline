"""
Supabase client shared across pipeline modules.
"""

import os

from dotenv import load_dotenv
from supabase import Client, create_client
from supabase.lib.client_options import ClientOptions

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
NHL_SEASON = int(os.environ.get("NHL_SEASON", "20252026"))
PRIMARY_TEAM_ABBR = os.environ.get("PRIMARY_TEAM_ABBR", "CAR")


def get_client() -> Client:
    return create_client(
        SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(postgrest_client_timeout=120)
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
