"""
pipeline_common.py — shared helpers NOT already covered by db.py.

db.py already provides the Supabase client (get_client), NHL_SEASON, and
PRIMARY_TEAM_ABBR — import those directly from db.py, don't duplicate them
here. This module only adds what db.py doesn't: an NHL API GET helper and
shared logging setup.

Usage:
    from db import get_client
    from pipeline_common import get_logger, nhl_get

    log = get_logger(__name__)
    sb = get_client()
    data = nhl_get("/draft/picks/2026/all")
"""

import logging

import requests

NHL_BASE = "https://api-web.nhle.com/v1"

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


def nhl_get(path: str) -> dict:
    """GET against api-web.nhle.com/v1, path should start with '/'."""
    url = f"{NHL_BASE}{path}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()
