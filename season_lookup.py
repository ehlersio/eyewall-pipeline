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

from pipeline_common import FetchError

WORKER_BASE = "https://eyewall-poller.billowing-queen-bf23.workers.dev"
TIMEOUT_SECONDS = 10

_cache = None  # populated on first call, reused for the rest of this process
_season_types_cache: dict | None = None  # same pattern, separate endpoint — see get_season_type()

# Sentinels distinct from both None (unfetched) and {} (a genuinely empty but
# valid response) — mark "already tried this process, the Worker was down."
# _fetch_config()/_fetch_season_types() raise FetchError on every call once
# a sentinel is cached, without hitting the network again, preserving the
# original "at most one real fetch attempt per process" behavior now that
# failure is signaled by raising instead of by caching a falsy value.
_FETCH_FAILED = object()
_TYPES_FETCH_FAILED = object()


def _fetch_config() -> dict:
    global _cache
    if _cache is _FETCH_FAILED:
        raise FetchError("season_lookup: Worker unreachable (cached failure, not retrying)")
    if _cache is not None:
        return _cache
    try:
        r = requests.get(f"{WORKER_BASE}/config/seasons", timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        _cache = r.json()
        return _cache
    except Exception as e:
        _cache = _FETCH_FAILED
        raise FetchError(f"season_lookup could not reach Worker ({e})") from e


def get_nhl_season() -> int:
    """Returns e.g. 20252026.

    Falls back to the NHL_SEASON env var (or 20252026 if that's also
    unset) if the Worker is unreachable or returns something unexpected.
    """
    fallback = int(os.environ.get("NHL_SEASON", "20252026"))
    try:
        config = _fetch_config()
    except FetchError as e:
        print(f"  WARNING: {e} — using env var fallback")
        return fallback
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
    try:
        config = _fetch_config()
    except FetchError as e:
        print(f"  WARNING: {e} — using env var fallback")
        return fallback
    try:
        pwhl = config["pwhl"]
        return {
            "season_id": int(pwhl["seasonId"]),
            "season_type": pwhl["seasonType"],
            "start_year": int(pwhl["startYear"]),
        }
    except (KeyError, TypeError, ValueError):
        return fallback


def _fetch_season_types() -> dict:
    """Fetches the full PWHL season_id -> season_type map from the Worker's
    /config/seasons/pwhl-types endpoint. Cached for the rest of this
    process, same as _fetch_config() — a pipeline run is short-lived, so
    "once per process" is effectively as fresh as a real TTL would be
    here; no need to reimplement the Worker's 6hr KV TTL on this side.

    Unlike _fetch_config()'s fallback-laden callers, there IS no
    reasonable local fallback for "what type is this arbitrary season" —
    get_season_type() catches the FetchError this raises and returns None
    for the rest of this run instead of retrying a Worker that's already
    down (see _FETCH_FAILED-style sentinel caching above).
    """
    global _season_types_cache
    if _season_types_cache is _TYPES_FETCH_FAILED:
        raise FetchError(
            "season_lookup: Worker unreachable for season types (cached failure, not retrying)"
        )
    if _season_types_cache is not None:
        return _season_types_cache
    try:
        r = requests.get(f"{WORKER_BASE}/config/seasons/pwhl-types", timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        _season_types_cache = r.json()
        return _season_types_cache
    except Exception as e:
        _season_types_cache = _TYPES_FETCH_FAILED
        raise FetchError(f"season_lookup could not reach Worker for season types ({e})") from e


def get_season_type(season_id: str | int) -> str | None:
    """Return the season_type ("regular", "playoffs", "preseason", etc.)
    for an arbitrary PWHL season_id, per HockeyTech's own bootstrap data
    (proxied through the Worker's /config/seasons/pwhl-types endpoint).

    Returns None if season_id isn't present in that response, OR if the
    Worker couldn't be reached at all — both cases mean "we don't
    actually know," and callers should treat that as something to
    surface (log + skip, or raise), not as license to guess "regular".
    """
    try:
        types = _fetch_season_types()
    except FetchError as e:
        print(f"  WARNING: {e}")
        return None
    return types.get(str(season_id))
