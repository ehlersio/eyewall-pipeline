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
_hockeytech_seasons_cache: dict = {}  # league -> season list, or _FETCH_FAILED — see get_hockeytech_seasons()

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


def _pwhl_season_entry(raw) -> dict | None:
    """One PWHL season object from /config/seasons ({seasonId, seasonType,
    startYear, startDate}) -> {'season_id', 'season_type', 'start_year',
    'start_date'}, or None for null/malformed. start_date can be None (an
    override or an older cached Worker answer may not carry it)."""
    try:
        return {
            "season_id": int(raw["seasonId"]),
            "season_type": raw["seasonType"],
            "start_year": int(raw["startYear"]),
            "start_date": raw.get("startDate"),
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def get_pwhl_next_season() -> dict | None:
    """The upcoming PWHL regular season -- the one the Worker's 14-day
    lookahead is still holding back (seasons.js pickPWHLSeasonContext()) --
    as {'season_id', 'season_type', 'start_year', 'start_date',
    'preseason'}, where 'preseason' is the same shape or None. None when
    there isn't one, or when the Worker can't say (unreachable, or a
    version that predates `pwhl.next`)."""
    try:
        pwhl = _fetch_config()["pwhl"]
        raw = pwhl["next"]
    except FetchError as e:
        print(f"  WARNING: {e}")
        return None
    except (KeyError, TypeError):
        return None
    entry = _pwhl_season_entry(raw)
    if entry is not None:
        entry["preseason"] = _pwhl_season_entry(raw.get("preseason"))
    return entry


def get_pwhl_upcoming_seasons() -> list[dict] | None:
    """Every PWHL season besides the current one whose schedule the nightly
    run should ingest: the current season's preseason, the next regular
    season and the next season's preseason (from /config/seasons'
    pwhl.preseason / pwhl.next / pwhl.next.preseason), each as
    {'season_id', 'season_type', 'start_year', 'start_date'}, preseason
    first. [] when there are none.

    None -- "we don't know", not "there are none" -- when the Worker is
    unreachable or doesn't serve these fields yet; callers should treat
    that as a failure to report, not as an empty list."""
    try:
        pwhl = _fetch_config()["pwhl"]
        current_id = int(pwhl["seasonId"])
        raw_next = pwhl["next"]
        raw_pre = pwhl["preseason"]
    except FetchError as e:
        print(f"  WARNING: {e}")
        return None
    except (KeyError, TypeError, ValueError):
        return None
    raw = [raw_pre]
    if isinstance(raw_next, dict):
        raw += [raw_next.get("preseason"), raw_next]
    seasons, seen = [], {current_id}
    for entry in map(_pwhl_season_entry, raw):
        if entry is not None and entry["season_id"] not in seen:
            seen.add(entry["season_id"])
            seasons.append(entry)
    return seasons


def get_pwhl_season_start_date(season_id: str | int) -> str | None:
    """HockeyTech's start_date ("YYYY-MM-DD") for a PWHL season the Worker's
    /config/seasons describes -- the current one, its preseason, the next
    one and its preseason. None for any other season, or when the Worker
    is unreachable."""
    try:
        pwhl = _fetch_config()["pwhl"]
    except (FetchError, KeyError, TypeError) as e:
        if isinstance(e, FetchError):
            print(f"  WARNING: {e}")
        return None
    if not isinstance(pwhl, dict):
        return None
    nxt = pwhl.get("next") if isinstance(pwhl.get("next"), dict) else {}
    for raw in (pwhl, pwhl.get("preseason"), nxt, nxt.get("preseason")):
        if isinstance(raw, dict) and str(raw.get("seasonId")) == str(season_id):
            start = raw.get("startDate")
            return start if isinstance(start, str) and start else None
    return None


def get_hockeytech_season(league: str, default_season_id: int) -> dict:
    """Returns {'season_id': int, 'season_type': str} for "ahl" or "echl".

    The Worker resolves it (seasons.js resolveAHLSeason/resolveECHLSeason:
    the most recent career="1" season that has already started, not simply
    the max season_id, with a manual KV override). Falls back to the
    {LEAGUE}_SEASON env var, then default_season_id, as a regular season if
    the Worker is unreachable or has no entry for the league. Uses `or`, as
    get_pwhl_season() does, so an empty-string secret doesn't crash int().
    """
    fallback = {
        "season_id": int(os.environ.get(f"{league.upper()}_SEASON") or default_season_id),
        "season_type": "regular",
    }
    try:
        config = _fetch_config()
    except FetchError as e:
        print(f"  WARNING: {e} — using env var fallback")
        return fallback
    try:
        entry = config[league]
        return {"season_id": int(entry["seasonId"]), "season_type": entry["seasonType"]}
    except (KeyError, TypeError, ValueError):
        return fallback


def get_hockeytech_seasons(league: str) -> list[dict] | None:
    """Every season the Worker lists for "ahl" or "echl", current and
    historical, from GET /config/seasons/{league}-seasons:
    [{'seasonId', 'seasonName', 'seasonType', 'startYear', 'startDate',
    'endDate'}]. The Worker derives seasonType from HockeyTech's own seasons
    feed (seasons.js), the same answer the frontend gets.

    None if the Worker is unreachable or returns something that isn't a
    list -- callers decide what that means (hockeytech_stats assumes a
    regular season and a wide date window). Cached per league for the rest
    of the process, a failure included, so a run doesn't retry a Worker
    that's already down.
    """
    cached = _hockeytech_seasons_cache.get(league)
    if cached is _FETCH_FAILED:
        return None
    if cached is not None:
        return cached
    try:
        r = requests.get(f"{WORKER_BASE}/config/seasons/{league}-seasons", timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        seasons = r.json()
        if not isinstance(seasons, list):
            raise ValueError(f"expected a list, got {type(seasons).__name__}")
    except Exception as e:
        print(f"  WARNING: season_lookup could not load {league} seasons from Worker ({e})")
        _hockeytech_seasons_cache[league] = _FETCH_FAILED
        return None
    _hockeytech_seasons_cache[league] = seasons
    return seasons


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


# PWHL season_id -> season_type, consulted before anything live. Holds the
# manual corrections the live data would get wrong:
#   "2"  -- HockeyTech's bootstrap calls it "2024 Preseason"; it was the
#           2023 showcase (9 games, 2023-12-04..07, before the inaugural
#           regular season) -- CLAUDE.md, "PWHL season 2 is the 2023
#           showcase". Keep "showcase".
#   "10" -- HockeyTech names it "2026-27 Pre-Season" (hyphenated), which the
#           Worker's deriveSeasonType() reads as "regular" (checked 2026-09).
# Every other past id is here too, so a resolve never needs the network for
# them. Was copied into nine pwhl_* modules (some without "10"); one copy
# since 2026-10.
PWHL_SEASON_TYPE_MAP = {
    "1": "regular",  # 2024 Regular Season (inaugural)
    "2": "showcase",  # 2023 showcase -- see above
    "3": "playoffs",  # 2024 Playoffs
    "4": "preseason",  # 2024-25 Preseason
    "5": "regular",  # 2024-25 Regular Season
    "6": "playoffs",  # 2025 Playoffs
    "7": "preseason",  # 2025-26 Preseason
    "8": "regular",  # 2025-26 Regular Season
    "9": "playoffs",  # 2025-26 Playoffs
    "10": "preseason",  # 2026-27 Preseason -- see above
}


def resolve_pwhl_season_type(season_id: str | int) -> str | None:
    """season_type for any PWHL season_id: PWHL_SEASON_TYPE_MAP first, then
    the Worker's current season (get_pwhl_season(), the same answer
    pwhl_stats.py's SEASON_TYPE_MAP.setdefault used to add), then the full
    bootstrap list (get_season_type()). None, not a guessed "regular", when
    nothing knows the id: unattended sweeps log and skip, --game paths
    raise (CLAUDE.md, "Arbitrary season_id -> season_type lookup")."""
    sid = str(season_id)
    if sid in PWHL_SEASON_TYPE_MAP:
        return PWHL_SEASON_TYPE_MAP[sid]
    current = get_pwhl_season()
    if str(current["season_id"]) == sid:
        return current["season_type"]
    return get_season_type(sid)
