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


# Indirection so tests can skip the backoff without patching time.sleep for
# every module.
_sleep = time.sleep

NHL_HEADERS = {"User-Agent": "EyeWall-Analytics/1.0 (eyewallanalytics.com)"}

# Worth another try: rate limiting and server-side errors. Any other 4xx
# (a 404 for a game that doesn't exist yet, say) fails at once.
NHL_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def nhl_get(
    path_or_url: str,
    params: dict | None = None,
    *,
    headers: dict | None = None,
    timeout: float = 20,
    retries: int = 3,
) -> dict:
    """GET an NHL API endpoint and return its JSON.

    `path_or_url` is either a path under api-web.nhle.com/v1 (starting with
    '/') or a full URL (nhl_stats/shot_events/shift_data/zone_starts/
    game_scoring/line_combinations pass full URLs, some on api.nhle.com).

    Retries timeouts, connection errors and 429/5xx up to `retries` attempts
    with 1 s, 2 s backoff, the same shape as hockeytech_statview_get(), so
    one 502 no longer fails a whole stage (audit 2026-10-06 F-12). Raises
    FetchError when the attempts run out, on any other HTTP error, or on a
    body that isn't JSON.
    """
    url = path_or_url if path_or_url.startswith("http") else f"{NHL_BASE}{path_or_url}"
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers or NHL_HEADERS, params=params, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
        else:
            if r.status_code in NHL_RETRY_STATUSES:
                last_err = f"HTTP {r.status_code}"
            else:
                try:
                    r.raise_for_status()
                    return r.json()
                except (requests.HTTPError, ValueError) as e:
                    raise FetchError(f"NHL GET failed: {url} -- {e}") from e
        log.warning(f"NHL GET {url}: {last_err} (attempt {attempt + 1}/{retries})")
        if attempt < retries - 1:
            _sleep(2**attempt)
    raise FetchError(f"NHL GET failed: {url} -- {last_err} after {retries} attempts")


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


class OnRosterMarker:
    """Keeps {league}_players.on_roster in step with one run's roster feed.

    For each team whose roster came back, write() upserts its rows with
    on_roster = true, then sets on_roster = false on that team's other rows
    in one update (players released, sent down or traded, whose team_id still
    points here because nothing has moved them). Teams whose roster fetch
    failed or came back empty are left alone. Readers (eyewall-poller's
    Roster tab and call-up watch) hide only on_roster = false, so a NULL
    (never marked) still shows.

    The column comes from docs/2026-10-06_on_roster.sql, which the owner
    runs. Until then the first write fails with PostgREST's missing-column
    error: that is logged once, and this run (and every later one until the
    column exists) upserts the rows without on_roster, as before.

    `enabled=False` (an explicit-season backfill, whose roster is not
    today's) skips the marking entirely.
    """

    def __init__(self, sb, table: str, enabled: bool = True):
        self.sb = sb
        self.table = table
        self.enabled = enabled

    def write(self, team_id: int, rows: list[dict], upsert) -> int:
        """`upsert(rows) -> int` writes rows to self.table (the module's own
        upsert_chunk); returns what it returns."""
        if not rows or not self.enabled:
            return upsert(rows)
        try:
            n = upsert([{**r, "on_roster": True} for r in rows])
        except Exception as e:
            if "on_roster" not in str(e):
                raise
            log.warning(
                f"{self.table}.on_roster is missing (run docs/2026-10-06_on_roster.sql); "
                f"writing rosters without it this run: {e}"
            )
            self.enabled = False
            return upsert(rows)
        ids = ",".join(str(r["player_id"]) for r in rows)
        (
            self.sb.table(self.table)
            .update({"on_roster": False})
            .eq("team_id", team_id)
            .filter("player_id", "not.in", f"({ids})")
            .execute()
        )
        return n


def select_all(
    build, order: str = "game_id", page_size: int = 1000, max_pages: int = 1000
) -> list[dict]:
    """Every row a Supabase query matches, not just the first page.

    PostgREST returns at most the project's row cap (1,000 today, 999 at one
    point -- see moneypuck.py) per request, and a bare .execute() silently
    truncates anything past it. That capped the per-game pipelines' "which
    games are completed / already processed / skipped" lookups, leaving
    later games never ingested and making every run re-ingest games it had
    already done (2026-09). This pages with .range() over a stable .order()
    until a page comes back empty, so it's right whatever the cap is.

    `build` returns a fresh query builder on each call (builders are
    mutable), e.g.
        select_all(lambda: sb.table("ahl_game_log").select("game_id").eq("season_id", 90))

    Raises RuntimeError after `max_pages` non-empty pages (a million rows at
    the default) rather than looping forever on a builder that ignores
    .range() -- no season's table is anywhere near that.
    """
    rows, offset = [], 0
    for _ in range(max_pages):
        page = build().order(order).range(offset, offset + page_size - 1).execute().data or []
        if not page:
            return rows
        rows.extend(page)
        offset += len(page)
    raise RuntimeError(
        f"select_all: still getting rows after {max_pages} pages -- is .range() ignored?"
    )


# NHL gameType values, as the NHL API and game_log.game_type use them.
NHL_PRESEASON = 1
NHL_REGULAR_SEASON = 2
NHL_PLAYOFFS = 3


def nhl_game_type(game_id) -> int | None:
    """The NHL gameType encoded in an NHL game id, or None if `game_id`
    isn't one.

    NHL game ids are YYYYTTNNNN: the season's start year, the two-digit game
    type, then the game number -- 2026010010 is a 2026-27 preseason game,
    2026020001 the regular season's first, 2025030111 a playoff game.

    shot_events, shift_events, zone_starts and game_xg hold every season's
    preseason and playoff games alongside the regular season's, and each has
    a `game_type` PostgREST computed field that decodes game_id the same way
    (docs/game_type_column.sql). Filter season-scoped reads of them on that
    column in the query rather than decoding rows here: before it existed,
    61 preseason games became 2026-27's regular-season Corsi (team_seasons,
    game_type 2) before a regular-season game had been played.
    """
    try:
        gid = int(game_id)
    except (TypeError, ValueError):
        return None
    if not 1_000_000_000 <= gid <= 9_999_999_999:
        return None
    return gid // 10_000 % 100
