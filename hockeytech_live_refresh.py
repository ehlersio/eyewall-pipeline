"""
hockeytech_live_refresh.py -- frequent refresh of {league}_game_log's
live-volatile fields (game_state, game_status_code, home_score, away_score)
for games in a narrow window around today. Shared by ahl_live_refresh.py
and echl_live_refresh.py.

Why this exists: the nightly {league}_stats.py ingest writes the whole
season once a day, and nothing updates game_state/scores again until the
following night -- so a game happening today would sit at the last nightly
snapshot and the Worker's live-game detection (/{league}/today, polled every
minute) could never see it live. This is a narrow, fast refresh of just those
4 columns, run from live-score-refresh.yml; nothing else {league}_stats.py
owns (rosters, season stats, the full schedule) is touched.
"""

import logging
import os

import requests
from dotenv import load_dotenv
from supabase import create_client

from hockeytech_leagues import HOCKEYTECH_BASE, League

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


def fetch_scorebar(lg: League) -> list[dict]:
    """feed=modulekit&view=scorebar, +/-1 day around today -- enough to catch
    a game that started yesterday and isn't marked Final yet (a late finish
    crossing midnight ET), without pulling the whole season the way
    {league}_stats.py's fetch_game_log() does."""
    params = {
        "feed": "modulekit",
        "view": "scorebar",
        "numberofdaysback": "1",
        "numberofdaysahead": "1",
        "limit": "100",
        "league_id": lg.league_id,
        "key": lg.hockeytech_key,
        "client_code": lg.key,
        "site_id": lg.site_id,
        "lang": "en",
    }
    r = requests.get(HOCKEYTECH_BASE, params=params, headers=lg.headers, timeout=15)
    r.raise_for_status()
    return r.json().get("SiteKit", {}).get("Scorebar", [])


def main(lg: League) -> None:
    try:
        games = fetch_scorebar(lg)
    except Exception as e:
        log.warning(f"Scorebar fetch failed: {e}")
        return

    log.info(f"Fetched {len(games)} games in the +/-1 day window")
    if not games:
        return

    rows = []
    for g in games:
        gid = g.get("ID")
        if not gid:
            continue
        # GameStatus: 1=scheduled (GameStatusString is then the scheduled
        # clock time, e.g. "7:00PM", not a state word), 4=final. 2/3
        # unconfirmed -- the Worker treats "not 1, not 4" as live.
        status_code = int(g["GameStatus"]) if g.get("GameStatus") not in (None, "") else None
        rows.append(
            {
                "game_id": int(gid),
                "game_state": g.get("GameStatusString", "") or "",
                "game_status_code": status_code,
                "home_score": int(g.get("HomeGoals", 0) or 0),
                "away_score": int(g.get("VisitorGoals", 0) or 0),
            }
        )

    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    total = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i : i + 200]
        try:
            sb.table(f"{lg.key}_game_log").upsert(chunk, on_conflict="game_id").execute()
            total += len(chunk)
        except Exception as e:
            # A game_id not yet in {league}_game_log (brand new, before the
            # next nightly stats run inserts its full row) fails this
            # partial-column upsert on NOT NULL columns it doesn't set --
            # rare, self-heals the same day, not worth failing the run.
            log.warning(f"  Chunk upsert failed ({len(chunk)} rows): {e}")
    log.info(f"Refreshed game_state/game_status_code/scores for {total} games")
