"""
pwhl_shot_events.py — PWHL shot event pipeline module

Fetches play-by-play for all completed PWHL games via the gameCenterPlayByPlay
endpoint and extracts shot events (shots, blocked_shots) with coordinates.

Endpoint: statviewfeed / gameCenterPlayByPlay on feed/index.php
Response: flat list of {event, details} dicts

Event types captured:
  shot         — details.shooter, goalie, xLocation, yLocation, shotType, shotQuality, isGoal
  blocked_shot — details.shooter, blocker, goalie, xLocation, yLocation, shotType, shotQuality
  (goal events skipped — coordinates duplicated on shot event with isGoal=true)

Coordinate transform:
  Raw values observed: xLocation 63-537, yLocation ~13-290
  Canvas estimated ~600 x 300 px. Set TRANSFORM_DEBUG=1 to print stats for calibration.

Usage:
    python pwhl_shot_events.py              # current season (PWHL_SEASON)
    python pwhl_shot_events.py 5            # specific season_id
    TRANSFORM_DEBUG=1 python pwhl_shot_events.py 5
"""

import json
import logging
import os
import sys
import time
from datetime import UTC, datetime

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
PWHL_SEASON = os.environ.get("PWHL_SEASON", "8")
TRANSFORM_DEBUG = os.environ.get("TRANSFORM_DEBUG", "0") == "1"

# Note: uses feed/index.php not feed/ -- required for gameCenterPlayByPlay
HOCKEYTECH_BASE = "https://lscluster.hockeytech.com/feed/index.php"
HOCKEYTECH_KEY = "446521baf8c38984"
CLIENT_CODE = "pwhl"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.thepwhl.com/",
}

SEASON_TYPE_MAP = {
    "1": "regular",
    "2": "showcase",
    "3": "playoffs",
    "4": "preseason",
    "5": "regular",
    "6": "playoffs",
    "7": "preseason",
    "8": "regular",
    "9": "playoffs",
}

# Coordinate transform constants
# Observed raw ranges from game 213: xLocation 63-537, yLocation 13-290
# Canvas estimated 600 x 300 px. Nets at approx x=75 (left) and x=525 (right).
CANVAS_W = 600.0
CANVAS_H = 300.0

SHOT_QUALITY_MAP = {
    "Quality goal": 5,
    "Quality on net": 1,
    "Non quality on net": 2,
    "Quality blocked": 8,
    "Non quality blocked": 7,
    "Non quality goal": 6,
}


def transform_coords(x_raw: int, y_raw: int, is_home: bool, period: int) -> tuple:
    """Transform HockeyTech pixel coords to NHL rink coords.
    Home attacks right (positive x) in odd periods, left in even periods.
    All shots normalised to attacking direction (positive x = attacking zone).
    """
    if x_raw is None or y_raw is None:
        return None, None
    x_norm = (x_raw / CANVAS_W - 0.5) * 200
    y_norm = (y_raw / CANVAS_H - 0.5) * 85
    home_attacks_right = period % 2 == 1
    attacking_right = home_attacks_right if is_home else not home_attacks_right
    if not attacking_right:
        x_norm = -x_norm
        y_norm = -y_norm
    return round(x_norm, 2), round(y_norm, 2)


def fetch_pbp(game_id: int) -> list | None:
    """Fetch play-by-play events for a single game."""
    for attempt in range(3):
        try:
            r = requests.get(
                HOCKEYTECH_BASE,
                params={
                    "feed": "statviewfeed",
                    "view": "gameCenterPlayByPlay",
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
                log.warning(f"    PBP {game_id} status {r.status_code}")
                continue
            text = r.text.strip()
            if "(" in text:
                text = text[text.index("(") + 1 : text.rindex(")")]
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "error" in data:
                log.warning(f"    PBP {game_id} error: {data['error']}")
                return None
        except Exception as e:
            log.warning(f"    PBP {game_id} attempt {attempt + 1}: {e}")
        if attempt < 2:
            time.sleep(2**attempt)
    return None


def get_completed_games(sb, season_id: str) -> list:
    result = (
        sb.table("pwhl_game_log")
        .select("game_id,home_team_id,away_team_id")
        .eq("season_id", int(season_id))
        .eq("game_state", "Final")
        .execute()
    )
    return result.data or []


def get_skipped_games(sb, pipeline: str) -> set:
    result = sb.table("pwhl_skipped_games").select("game_id").eq("pipeline", pipeline).execute()
    return {r["game_id"] for r in (result.data or [])}


def get_processed_games(sb, season_id: str) -> set:
    result = (
        sb.table("pwhl_shot_events").select("game_id").eq("season_id", int(season_id)).execute()
    )
    return {r["game_id"] for r in (result.data or [])}


def parse_pbp(
    game_id: int, season_id: str, season_type: str, home_team_id: int, events: list
) -> list:
    rows = []
    debug_coords = [] if TRANSFORM_DEBUG else None

    for ev in events:
        if not isinstance(ev, dict):
            continue
        event_type = ev.get("event", "")
        if event_type not in ("shot", "blocked_shot"):
            continue  # goal coords duplicated on shot event with isGoal=true

        d = ev.get("details", {})
        x_raw = d.get("xLocation")
        y_raw = d.get("yLocation")
        if x_raw is None or y_raw is None:
            continue

        period_raw = (d.get("period") or {}).get("id", "1") or "1"
        period_map = {"OT1": 4, "OT2": 5, "OT3": 6, "SO": 7}
        period = (
            period_map.get(str(period_raw)) or int(period_raw)
            if str(period_raw).isdigit()
            else period_map.get(str(period_raw), 4)
        )
        team_id = int(d.get("shooterTeamId") or 0) or None
        is_home = (team_id == home_team_id) if team_id else False

        shooter = d.get("shooter") or {}
        goalie = d.get("goalie") or {}
        blocker = d.get("blocker") or {}
        shooter_id = int(shooter.get("id") or 0) or None
        goalie_id = int(goalie.get("id") or 0) or None
        blocker_id = int(blocker.get("id") or 0) or None

        shot_type = d.get("shotType", "")
        quality = SHOT_QUALITY_MAP.get(d.get("shotQuality", ""), 0)
        is_goal = bool(d.get("isGoal", False))

        try:
            parts = (d.get("time") or "0:00").split(":")
            time_seconds = int(parts[0]) * 60 + int(parts[-1])
        except Exception:
            time_seconds = 0

        x_norm, y_norm = transform_coords(int(x_raw), int(y_raw), is_home, period)

        if TRANSFORM_DEBUG and debug_coords is not None:
            debug_coords.append((x_raw, y_raw, x_norm, y_norm))

        rows.append(
            {
                "game_id": game_id,
                "season_id": int(season_id),
                "event_type": "goal" if is_goal else event_type,
                "period_id": period,
                "time_seconds": time_seconds,
                "team_id": team_id,
                "shooter_id": shooter_id,
                "goalie_id": goalie_id,
                "blocker_id": blocker_id,
                "shot_type": shot_type,
                "quality": quality,
                "x_raw": int(x_raw),
                "y_raw": int(y_raw),
                "x_norm": x_norm,
                "y_norm": y_norm,
                "is_home": is_home,
                "situation_code": "5v5",  # TODO: derive from penalty log
                "season_type": season_type,
            }
        )

    if TRANSFORM_DEBUG and debug_coords:
        x_raws = [c[0] for c in debug_coords]
        y_raws = [c[1] for c in debug_coords]
        x_norms = [c[2] for c in debug_coords if c[2] is not None]
        y_norms = [c[3] for c in debug_coords if c[3] is not None]
        log.info(
            f"  [DEBUG] x_raw: {min(x_raws)}-{max(x_raws)}, y_raw: {min(y_raws)}-{max(y_raws)}"
        )
        log.info(
            f"  [DEBUG] x_norm: {min(x_norms):.1f}-{max(x_norms):.1f}, y_norm: {min(y_norms):.1f}-{max(y_norms):.1f}"
        )

    return rows


def run(season_id: str | None = None) -> None:
    season_id = season_id or PWHL_SEASON
    season_type = SEASON_TYPE_MAP.get(season_id, "regular")
    pipeline = "pwhl_shot_events"

    log.info(f"=== PWHL Shot Events -- season {season_id} ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    completed = get_completed_games(sb, season_id)
    skipped = get_skipped_games(sb, pipeline)
    processed = get_processed_games(sb, season_id)
    todo = [g for g in completed if g["game_id"] not in skipped and g["game_id"] not in processed]

    log.info(
        f"  {len(completed)} completed, {len(processed)} processed, "
        f"{len(skipped)} skipped, {len(todo)} to process"
    )

    for i, game in enumerate(todo):
        gid = game["game_id"]
        home_id = game["home_team_id"] or 0
        log.info(f"  [{i + 1}/{len(todo)}] game {gid}")

        events = fetch_pbp(gid)
        if not events:
            log.warning("    No PBP -- skipping")
            sb.table("pwhl_skipped_games").upsert(
                {
                    "game_id": gid,
                    "pipeline": pipeline,
                    "reason": "no_pbp",
                    "skipped_at": datetime.now(UTC).isoformat(),
                },
                on_conflict="game_id,pipeline",
            ).execute()
            continue

        rows = parse_pbp(gid, season_id, season_type, home_id, events)
        if not rows:
            log.info("    No shot events")
            sb.table("pwhl_skipped_games").upsert(
                {
                    "game_id": gid,
                    "pipeline": pipeline,
                    "reason": "no_shots",
                    "skipped_at": datetime.now(UTC).isoformat(),
                },
                on_conflict="game_id,pipeline",
            ).execute()
            continue

        # Upsert any unknown players referenced in shot events
        # (some shooters in blocked_shot events have null names)
        player_ids = set()
        for row in rows:
            for fld in ("shooter_id", "goalie_id", "blocker_id"):
                if row.get(fld):
                    player_ids.add(row[fld])
        if player_ids:
            existing = (
                sb.table("pwhl_players")
                .select("player_id")
                .in_("player_id", list(player_ids))
                .execute()
            )
            existing_ids = {r["player_id"] for r in (existing.data or [])}
            missing = player_ids - existing_ids
            if missing:
                stubs = [
                    {"player_id": pid, "updated_at": datetime.now(UTC).isoformat()}
                    for pid in missing
                ]
                sb.table("pwhl_players").upsert(stubs, on_conflict="player_id").execute()
                log.info(f"    Inserted {len(missing)} unknown player stubs: {missing}")

        # Deduplicate within this game before upserting — two events can share
        # the same (game_id, event_type, period_id, time_seconds, team_id,
        # shooter_id) if a player has two distinct shots in the same recorded
        # second. x_raw/y_raw are folded into the key to disambiguate: a real
        # duplicate (re-parsed same event) will have identical coordinates and
        # still collapse correctly, while two genuinely different shots will
        # almost always differ in location.
        seen = set()
        deduped = []
        for row in rows:
            key = (
                row["game_id"],
                row["event_type"],
                row["period_id"],
                row["time_seconds"],
                row["team_id"],
                row["shooter_id"],
                row["x_raw"],
                row["y_raw"],
            )
            if key not in seen:
                seen.add(key)
                deduped.append(row)
        if len(deduped) < len(rows):
            log.info(f"    Deduplicated {len(rows) - len(deduped)} duplicate events")

        for j in range(0, len(deduped), 200):
            sb.table("pwhl_shot_events").upsert(
                deduped[j : j + 200],
                on_conflict="game_id,event_type,period_id,time_seconds,team_id,shooter_id,x_raw,y_raw",
            ).execute()

        goals = sum(1 for r in rows if r["event_type"] == "goal")
        log.info(f"    {len(rows)} events upserted ({goals} goals)")
        time.sleep(0.5)

    log.info("=== PWHL Shot Events complete ===")


if __name__ == "__main__":
    season_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run(season_arg)
