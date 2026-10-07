"""
pwhl_common.py -- the HockeyTech per-game fetch and the game-queue helpers
shared by the PWHL per-game modules: pwhl_shot_events.py,
pwhl_pbp_events.py, pwhl_goal_on_ice.py, pwhl_penalty_shots.py and
pwhl_game_boxscore.py.

Each of those used to carry its own copy of all of this -- the client
config, the retrying statviewfeed GET, and the completed / skipped /
processed / mark-skipped queries (audit 2026-10-06, pipeline F-24). The
copies were identical apart from the table each one counts as "processed",
which is now a parameter. Each module keeps its own thin wrappers under the
old names and signatures, so callers and tests are unchanged.

The client config is hockeytech_leagues.PWHL's (key, client code, headers)
and the JSONP unwrap is hockeytech_leagues.strip_jsonp, as the AHL/ECHL
modules use (2026-10). fetch_pbp() is the one play-by-play fetch for
pwhl_shot_events.py and pwhl_pbp_events.py, which had two copies that
disagreed about an odd-shaped response.

Per-game queue, for every module:
    todo = completed games (pwhl_game_log, game_state "Final")
           - games already in the module's own table ("processed")
           - games in pwhl_skipped_games for the module's pipeline name
"""

import json
import logging
import time
from datetime import UTC, datetime

import requests

from hockeytech_leagues import HOCKEYTECH_BASE, PWHL, strip_jsonp
from pipeline_common import FetchError, select_all

log = logging.getLogger(__name__)

# HOCKEYTECH_BASE is feed/index.php, not feed/ -- required for
# gameCenterPlayByPlay.
HOCKEYTECH_KEY = PWHL.hockeytech_key
CLIENT_CODE = PWHL.key
HEADERS = PWHL.headers

ATTEMPTS = 3


def hockeytech_get(view: str, game_id: int, expect: type | None = None):
    """GET a statviewfeed view keyed on game_id (gameSummary,
    gameCenterPlayByPlay) and return the parsed JSON.

    Returns None if the API answered with an error payload -- a legitimate
    "no data for this view/game", not a fetch failure. Raises FetchError
    after 3 failed attempts (non-200, unparseable body, network error), with
    a 1 s then 2 s backoff after exceptions; a non-200 retries at once.

    `expect`: when set, a response of another type that isn't an error
    payload counts as a failed attempt too. pwhl_pbp_events.py's fetch_pbp
    always behaved this way (list or retry); the other modules take any
    shape and check it themselves, so they leave it unset.

    The body is unwrapped only if it really is JSONP-wrapped
    (hockeytech_leagues.strip_jsonp: the feed answers "(...)"). Until
    2026-10 the PWHL copies sliced from the first "(" to the last ")"
    whatever the body was, which corrupts plain JSON with a "(" in a value
    -- the bug that once broke AHL rosters.
    """
    last_err = None
    for attempt in range(ATTEMPTS):
        try:
            r = requests.get(
                HOCKEYTECH_BASE,
                params={
                    "feed": "statviewfeed",
                    "view": view,
                    "game_id": str(game_id),
                    "key": HOCKEYTECH_KEY,
                    "client_code": CLIENT_CODE,
                    "lang": "en",
                    "league_id": "",
                },
                headers=HEADERS,
                timeout=20,
            )
            if r.status_code != 200:
                log.warning(f"    {view} {game_id} status {r.status_code}")
                last_err = f"status {r.status_code}"
                continue
            data = json.loads(strip_jsonp(r.text.strip()))
            if expect is not None and isinstance(data, expect):
                return data
            if isinstance(data, dict) and "error" in data:
                log.warning(f"    {view} {game_id} error: {data['error']}")
                return None
            if expect is None:
                return data
            last_err = f"unexpected {type(data).__name__} response"
        except Exception as e:
            log.warning(f"    {view} {game_id} attempt {attempt + 1}: {e}")
            last_err = str(e)
        if attempt < ATTEMPTS - 1:
            time.sleep(2**attempt)
    raise FetchError(f"{view} {game_id}: failed after {ATTEMPTS} attempts ({last_err})")


def fetch_pbp(game_id: int) -> list | None:
    """Play-by-play events for one game: a list, or None when HockeyTech
    answers with an error payload (no data for this game -- the caller marks
    it skipped). Anything else is a failed attempt: retried, then FetchError,
    so the game is retried next run rather than marked skipped.
    pwhl_shot_events.py's copy used to turn an odd-shaped response into None
    straight away, which marked the game skipped for good."""
    return hockeytech_get("gameCenterPlayByPlay", game_id, expect=list)


def fetch_game_summary(game_id: int) -> dict | None:
    """The gameSummary box score for one game (top-level keys: details,
    homeTeam, visitingTeam, periods, ...; see docs/hockeytech-api-notes.md),
    or None when HockeyTech has none. Raises FetchError like
    hockeytech_get()."""
    data = hockeytech_get("gameSummary", game_id)
    return data if isinstance(data, dict) else None


def get_completed_games(
    sb, season_id: str, columns: str = "game_id,home_team_id,away_team_id"
) -> list:
    """A season's finished games from pwhl_game_log."""
    return select_all(
        lambda: (
            sb.table("pwhl_game_log")
            .select(columns)
            .eq("season_id", int(season_id))
            .eq("game_state", "Final")
        )
    )


def get_skipped_games(sb, pipeline: str) -> set:
    """game_ids pwhl_skipped_games holds for this pipeline."""
    return {
        r["game_id"]
        for r in select_all(
            lambda: sb.table("pwhl_skipped_games").select("game_id").eq("pipeline", pipeline)
        )
    }


def get_processed_games(sb, table: str, season_id: str) -> set:
    """game_ids already in `table` for a season."""
    return {
        r["game_id"]
        for r in select_all(
            lambda: sb.table(table).select("game_id").eq("season_id", int(season_id))
        )
    }


def mark_skipped(sb, game_id: int, pipeline: str, reason: str) -> None:
    """Record that `pipeline` found nothing to ingest for this game, so its
    sweep stops retrying it."""
    sb.table("pwhl_skipped_games").upsert(
        {
            "game_id": game_id,
            "pipeline": pipeline,
            "reason": reason,
            "skipped_at": datetime.now(UTC).isoformat(),
        },
        on_conflict="game_id,pipeline",
    ).execute()
