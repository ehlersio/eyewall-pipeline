#!/usr/bin/env python3
"""
echl_live_refresh.py — Lightweight, frequent refresh of echl_game_log's
live-volatile fields (game_state, game_status_code, home_score,
away_score) for games in a narrow window around today.

Mirrors ahl_live_refresh.py exactly -- see that file's module docstring
for the full "why this exists" explanation (echl_stats.py's nightly
ingest only writes these columns once a day, so a live game would sit
stale until the following night without this). Confirmed live
2026-08-30 that ECHL's own modulekit&view=scorebar has the identical
GameStatus/GameStatusString shape as AHL's (1=scheduled, GameStatusString
is the scheduled clock time in that state; 4=final; 2/3 unconfirmed, same
"not 1, not 4 = live" Worker-side treatment).

Usage:
    python echl_live_refresh.py
"""

import logging
import os

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

HOCKEYTECH_BASE = "https://lscluster.hockeytech.com/feed/index.php"
HOCKEYTECH_KEY = "2c2b89ea7345cae8"
CLIENT_CODE = "echl"
SITE_ID = "0"
LEAGUE_ID = "1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://echl.com/",
}


def fetch_scorebar() -> list[dict]:
    """feed=modulekit&view=scorebar, +/-1 day around today -- enough to
    catch a game that started yesterday and is still not yet marked
    Final, without pulling the whole season the way echl_stats.py's
    fetch_game_log() does. Gives GameStatus (numeric, see main()'s
    comment) alongside GameStatusString -- confirmed live 2026-08-30."""
    params = {
        "feed": "modulekit",
        "view": "scorebar",
        "numberofdaysback": "1",
        "numberofdaysahead": "1",
        "limit": "100",
        "league_id": LEAGUE_ID,
        "key": HOCKEYTECH_KEY,
        "client_code": CLIENT_CODE,
        "site_id": SITE_ID,
        "lang": "en",
    }
    r = requests.get(HOCKEYTECH_BASE, params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json().get("SiteKit", {}).get("Scorebar", [])


def main():
    try:
        games = fetch_scorebar()
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
        # GameStatus: numeric companion to GameStatusString. Confirmed
        # live: 1=scheduled (GameStatusString is the scheduled clock time
        # in this state, e.g. "7:00PM", not a state word -- string-only
        # matching can't tell this apart from an unrecognized live state),
        # 4=final. 2/3 unconfirmed (no in-progress game observed yet) --
        # the Worker treats "not 1, not 4" as live rather than guessing.
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
            sb.table("echl_game_log").upsert(chunk, on_conflict="game_id").execute()
            total += len(chunk)
        except Exception as e:
            # A game_id not yet in echl_game_log (brand new, before the
            # next nightly echl_stats.py run has inserted its full row)
            # fails this partial-column upsert on NOT NULL columns it
            # doesn't set -- rare and self-heals same-day, not worth
            # failing the whole run over.
            log.warning(f"  Chunk upsert failed ({len(chunk)} rows): {e}")
    log.info(f"Refreshed game_state/game_status_code/scores for {total} games")


if __name__ == "__main__":
    main()
