"""
pipeline_common.py — shared helpers NOT already covered by db.py.

db.py already provides the Supabase client (get_client), NHL_SEASON, and
PRIMARY_TEAM_ABBR — import those directly from db.py, don't duplicate them
here. This module only adds what db.py doesn't: NHL API and HockeyTech
statviewfeed GET helpers and shared logging setup.

Usage:
    from db import get_client
    from pipeline_common import get_logger, nhl_get

    log = get_logger(__name__)
    sb = get_client()
    data = nhl_get("/draft/picks/2026/all")
"""

import json
import logging
import time

import requests

NHL_BASE = "https://api-web.nhle.com/v1"

_LOGGING_CONFIGURED = False

log = logging.getLogger(__name__)


class FetchError(Exception):
    """Raised by HTTP-fetch helpers on failure, instead of swallowing to a
    falsy value (None/[]). Network flakiness is fine to absorb, but "no data
    exists" and "the fetch broke" collapsing into the same falsy return left
    callers unable to tell the two apart. Callers catch this explicitly and
    decide what to do (skip an item, abort a stage, etc.) -- the helper
    itself no longer makes that call silently."""


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


def hockeytech_statview_get(
    base: str, auth: dict, params: dict, headers: dict, retries: int = 3
) -> list | dict:
    """GET a HockeyTech feed=statviewfeed view and return the parsed response.

    `auth` is the league's key/client_code/site_id/league_id; `params` names
    the view and its arguments. Retries with backoff and raises FetchError
    after `retries` attempts. Shared by pwhl_stats.py and hockeytech_stats.py
    (AHL/ECHL) through their own ht_get() wrappers.

    statviewfeed responses really are JSONP-wrapped, so this strips from the
    first "(" to the last ")". Don't reuse it for modulekit views, which are
    plain JSON that can contain parentheses (see hockeytech_stats.py's
    _modulekit_get()).
    """
    p = {"feed": "statviewfeed", **auth, "lang": "en"}
    p.update(params)

    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(base, params=p, headers=headers, timeout=20)
            if r.status_code == 200:
                text = r.text.strip()
                if "(" in text:
                    text = text[text.index("(") + 1 : text.rindex(")")]
                return json.loads(text)
            log.warning(f"HT {p.get('view')} status {r.status_code} (attempt {attempt + 1})")
            last_err = f"status {r.status_code}"
        except Exception as e:
            log.warning(f"HT {p.get('view')} error: {e} (attempt {attempt + 1})")
            last_err = str(e)
        if attempt < retries - 1:
            time.sleep(2**attempt)
    raise FetchError(f"HT {p.get('view')}: failed after {retries} attempts ({last_err})")
