"""
season_lookup.py — resolves the current season for both leagues from the
Worker's live /config/seasons endpoint (see seasons.js), falling back to
env vars if the Worker is unreachable.

The Worker is the single source of truth for "what season is it right
now" — this module does not re-implement that resolution logic, it just
reads the answer. If the Worker is down or times out, every function here
degrades gracefully to today's env-var-based behavior rather than
crashing a pipeline run.
"""

import os

import requests

WORKER_BASE = "https://eyewall-poller.billowing-queen-bf23.workers.dev"
TIMEOUT_SECONDS = 10

_cache = None  # populated on first call, reused for the rest of this process


def _fetch_config() -> dict | None:
    global _cache
    if _cache is not None:
        return _cache
    try:
        r = requests.get(f"{WORKER_BASE}/config/seasons", timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        _cache = r.json()
    except Exception as e:
        print(f"  WARNING: season_lookup could not reach Worker ({e}) — using env var fallbacks")
        _cache = {}
    return _cache


def get_nhl_season() -> int:
    """Returns e.g. 20252026.

    Falls back to the NHL_SEASON env var (or 20252026 if that's also
    unset) if the Worker is unreachable or returns something unexpected.
    """
    fallback = int(os.environ.get("NHL_SEASON", "20252026"))
    config = _fetch_config()
    try:
        return int(config["nhl"]["seasonId"])
    except (KeyError, TypeError, ValueError):
        return fallback


def get_pwhl_season() -> dict:
    """Returns {'season_id': int, 'season_type': str, 'start_year': int}.

    Falls back to the PWHL_SEASON env var (or "8") plus a conservative
    regular/2025 guess for type/year if the Worker is unreachable.
    Mirrors pwhl_stats.py's existing `or` (not .get's default) handling
    so an empty-string secret doesn't crash int().
    """
    fallback = {
        "season_id": int(os.environ.get("PWHL_SEASON") or "8"),
        "season_type": "regular",
        "start_year": 2025,
    }
    config = _fetch_config()
    try:
        pwhl = config["pwhl"]
        return {
            "season_id": int(pwhl["seasonId"]),
            "season_type": pwhl["seasonType"],
            "start_year": int(pwhl["startYear"]),
        }
    except (KeyError, TypeError, ValueError):
        return fallback
