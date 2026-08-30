#!/usr/bin/env python3
"""
pwhl_live_refresh.py — Lightweight, frequent refresh of pwhl_game_log's
live-volatile fields (game_state, game_status_code, home_score,
away_score) for games in a narrow window around today.

Why this exists: pwhl_stats.py's full nightly ingest runs once (3:20 AM
ET) and writes the whole season including future/scheduled games — but
nothing updates game_state/scores again until the FOLLOWING night. A
game happening today would sit at whatever status the last nightly
snapshot showed for the entire day, even after it goes live or
finishes, which means the Worker's /pwhl/today live-game detection
(polled every minute via wrangler's cron) can never actually see a
live game. This script is the fix: a narrow, fast, frequent-safe
refresh of just those 4 columns, run via a tight cron
(live-score-refresh.yml) — nothing else pwhl_stats.py owns (rosters,
season stats, full schedule) is touched here.

Deliberately uses feed=modulekit&view=scorebar, NOT the
feed=statviewfeed&view=schedule view pwhl_stats.py's own
fetch_game_log() uses -- confirmed live 2026-08-29 that schedule's row
shape has no numeric status-code field at all (only the string
game_status), while scorebar has both GameStatusString AND a numeric
GameStatus. pwhl_stats.py itself is NOT switched to scorebar (out of
scope, would risk the working nightly job for no benefit it needs) --
game_status_code on pwhl_game_log is populated ONLY by this script.

Usage:
    python pwhl_live_refresh.py
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
HOCKEYTECH_KEY = "446521baf8c38984"
CLIENT_CODE = "pwhl"
SITE_ID = "0"
LEAGUE_ID = ""

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.thepwhl.com/",
}


def fetch_scorebar() -> list[dict]:
    """feed=modulekit&view=scorebar, +/-1 day around today -- see module
    docstring for why this view instead of pwhl_stats.py's own
    statviewfeed&view=schedule. Confirmed live 2026-08-29."""
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
        # GameStatus: numeric companion to GameStatusString, same
        # confirmed convention as ahl_live_refresh.py -- 1=scheduled,
        # 4=final confirmed live; 2/3 unconfirmed (no in-progress game
        # observed this build). The Worker treats "not 1, not 4" as live.
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
            sb.table("pwhl_game_log").upsert(chunk, on_conflict="game_id").execute()
            total += len(chunk)
        except Exception as e:
            # A game_id not yet in pwhl_game_log fails this partial-column
            # upsert on NOT NULL columns it doesn't set -- rare and
            # self-heals same-day, not worth failing the whole run over.
            log.warning(f"  Chunk upsert failed ({len(chunk)} rows): {e}")
    log.info(f"Refreshed game_state/game_status_code/scores for {total} games")


if __name__ == "__main__":
    main()
