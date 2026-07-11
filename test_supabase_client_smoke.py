"""
test_supabase_client_smoke.py — real (non-mocked) Supabase client construction.

Every other test in this suite mocks create_client() (see
test_pwhl_pbp_events_season.py), which gave a false-green signal on the
2.3.4 -> 2.31.0 supabase-ecosystem Dependabot bump: the mocked suite passed
even though every real create_client(options=ClientOptions(...)) call in
this repo raised AttributeError at runtime (see db.py's import comment for
the story). create_client() doesn't hit the network on its own — it just
builds the postgrest/storage/auth/functions/realtime sub-clients — so this
stays a fast, offline check while still exercising the real client
construction path future supabase-ecosystem bumps need to prove out.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import warnings

import db
import tankathon_ingest


def test_get_client_constructs_without_raising():
    client = db.get_client()
    assert client is not None
    assert client.table("pwhl_game_log") is not None


def test_tankathon_get_supabase_constructs_without_deprecation_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        client = tankathon_ingest.get_supabase()
        # .table(...) forces lazy postgrest sub-client construction — the
        # deprecation only fires there, not at create_client() itself.
        assert client.table("draft_pick_order_2026") is not None
