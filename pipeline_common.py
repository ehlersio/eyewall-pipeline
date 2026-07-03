"""
pipeline_common.py — shared helpers for EyeWall Analytics pipeline scripts.

Centralizes what was duplicated across draft_ingest.py and milestones.py:
Supabase client creation, the NHL API GET helper, and logging setup.
Import from here rather than redefining these in a new script.

Usage:
    from pipeline_common import get_supabase, nhl_get, get_logger

    log = get_logger(__name__)
    sb = get_supabase()
    data = nhl_get("/draft/picks/2026/all")
"""

import logging
import os

import requests
from dotenv import load_dotenv
from supabase import create_client
from supabase.lib.client_options import ClientOptions

load_dotenv()

NHL_BASE = "https://api-web.nhle.com/v1"

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

_LOGGING_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    """One shared logging format/level across all pipeline scripts."""
    global _LOGGING_CONFIGURED
    if not _LOGGING_CONFIGURED:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )
        _LOGGING_CONFIGURED = True
    return logging.getLogger(name)


def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions())


def nhl_get(path: str) -> dict:
    """GET against api-web.nhle.com/v1, path should start with '/'."""
    url = f"{NHL_BASE}{path}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()
