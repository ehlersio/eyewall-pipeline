"""
ahl_shot_events.py — AHL shot event pipeline module

Fetches play-by-play for all completed AHL games via the
gameCenterPlayByPlay endpoint and extracts shot attempts and goals with
coordinates. Structurally mirrors pwhl_shot_events.py, but the AHL/ECHL
PBP schema has a real, confirmed difference from PWHL's that simplifies
this module considerably — see docs/hockeytech-ahl-api-notes.md for the
full investigation.

Endpoint: statviewfeed / gameCenterPlayByPlay on feed/index.php
Response: flat list of {event, details} dicts

Event types captured:
  shot  — details.shooter, goalie, xLocation, yLocation, shotType,
          shotQuality, isGoal (only ingested here when isGoal is falsy —
          see below)
  goal  — a DISTINCT event type from `shot`, unlike PWHL (PWHL's PBP only
          has goals embedded in `shot` events with isGoal=true — confirmed
          absent as its own event type in docs/hockeytech-api-notes.md).
          AHL/ECHL's `goal` event already carries assists[], properties
          (isPowerPlay/isShortHanded/isEmptyNet/isPenaltyShot/
          isInsuranceGoal/isGameWinningGoal), and plus_players/
          minus_players DIRECTLY — no separate gameSummary fetch/merge
          step is needed here, unlike pwhl_shot_events.py's
          merge_game_summary(). Confirmed across 16+ real games (both AHL
          and ECHL) that `shot`-with-isGoal=true and `goal` describe the
          same goal (near-exact 1:1 counts), with `goal` being a strict
          superset — so `shot` rows are only ingested when NOT a goal,
          and `goal` rows are ingested separately. This is a clean
          disjoint pair, unlike PWHL's overlapping shot+isGoal scheme —
          no dedup logic is needed here.

  (blocked_shot, hit, faceoff: confirmed NOT present in AHL/ECHL's PBP at
   all — not charted for this league, not a parsing gap. Do not add
   handling for these without first confirming they've actually started
   appearing in a live pull.)
  (penaltyshot events intentionally NOT parsed here — no coordinates exist
   for them, make or miss; see ahl_penalty_shots.py)

Coordinate transform: reuses PWHL's CANVAS_W/CANVAS_H=600x300 and
transform_coords() fold unmodified — an 8-game AHL sample landed in the
same x/y range as PWHL's own sample (see reference doc). Do one visual
overlay check against a real rink before fully trusting this on a shot
map — numeric range matching alone doesn't catch an axis swap.

Run modes:
  python ahl_shot_events.py                  # ingest current season
  python ahl_shot_events.py 90                # specific season_id
  python ahl_shot_events.py --game 1028362    # single game_id (debug)
"""

import argparse
import json
import logging
import os
import time
from datetime import UTC, datetime

import requests
from dotenv import load_dotenv
from supabase import create_client

from ahl_stats import resolve_current_season, resolve_season_type
from pipeline_common import FetchError

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TRANSFORM_DEBUG = os.environ.get("TRANSFORM_DEBUG", "0") == "1"

HOCKEYTECH_BASE = "https://lscluster.hockeytech.com/feed/index.php"
HOCKEYTECH_KEY = "ccb91f29d6744675"
CLIENT_CODE = "ahl"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://theahl.com/",
}

# Same canvas PWHL uses — see module docstring.
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

PERIOD_MAP = {"OT1": 4, "OT2": 5, "OT3": 6, "SO": 7}

PIPELINE = "ahl_shot_events"


def transform_coords(x_raw: int, y_raw: int, is_home: bool, period: int) -> tuple:
    """Unmodified copy of pwhl_shot_events.py's transform -- same canvas,
    same home-attacks-right-in-odd-periods fold. See module docstring for
    why this is expected to transfer directly."""
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


def _period_id(period_raw) -> int:
    period_raw = str(period_raw or "1")
    if period_raw in PERIOD_MAP:
        return PERIOD_MAP[period_raw]
    try:
        return int(period_raw)
    except ValueError:
        return 4  # unrecognized non-numeric label -- treat as first OT


def _time_seconds(time_str: str) -> int:
    try:
        parts = (time_str or "0:00").split(":")
        return int(parts[0]) * 60 + int(parts[-1])
    except Exception:
        return 0


def fetch_pbp(game_id: int, retries: int = 3) -> list | None:
    """Fetch play-by-play events for a single game. Raises FetchError
    after exhausting retries -- a genuine fetch failure, distinct from a
    game legitimately having no PBP (an empty list is a normal response,
    not an error)."""
    last_err = None
    for attempt in range(retries):
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
                last_err = f"status {r.status_code}"
            else:
                text = r.text.strip()
                if "(" in text:
                    text = text[text.index("(") + 1 : text.rindex(")")]
                data = json.loads(text)
                if isinstance(data, dict) and "error" in data:
                    log.warning(f"    PBP {game_id} error: {data['error']}")
                    return None
                return data if isinstance(data, list) else None
        except Exception as e:
            log.warning(f"    PBP {game_id} attempt {attempt + 1}: {e}")
            last_err = str(e)
        if attempt < retries - 1:
            time.sleep(2**attempt)
    raise FetchError(f"PBP {game_id}: failed after {retries} attempts ({last_err})")


def _parse_bool(properties: dict, key: str) -> bool:
    return str(properties.get(key, "0")) == "1"


def parse_pbp(
    game_id: int, season_id: str, season_type: str, home_team_id: int, events: list
) -> list:
    rows = []
    debug_coords = [] if TRANSFORM_DEBUG else None

    for ev in events:
        if not isinstance(ev, dict):
            continue
        event_type = ev.get("event", "")
        d = ev.get("details", {})

        if event_type == "shot":
            if bool(d.get("isGoal", False)):
                continue  # the parallel `goal` event owns this one -- see module docstring
            x_raw, y_raw = d.get("xLocation"), d.get("yLocation")
            if x_raw is None or y_raw is None:
                continue
            period = _period_id((d.get("period") or {}).get("id"))
            team_id = int(d.get("shooterTeamId") or 0) or None
            is_home = team_id == home_team_id if team_id else False
            shooter = d.get("shooter") or {}
            goalie = d.get("goalie") or {}
            x_norm, y_norm = transform_coords(int(x_raw), int(y_raw), is_home, period)
            if TRANSFORM_DEBUG:
                debug_coords.append((x_raw, y_raw, x_norm, y_norm))

            rows.append(
                {
                    "game_id": game_id,
                    "season_id": int(season_id),
                    "season_type": season_type,
                    "event_type": "shot",
                    "period_id": period,
                    "time_seconds": _time_seconds(d.get("time")),
                    "team_id": team_id,
                    "shooter_id": int(shooter["id"]) if shooter.get("id") else None,
                    "goalie_id": int(goalie["id"]) if goalie.get("id") else None,
                    "shot_type": d.get("shotType", ""),
                    "quality": SHOT_QUALITY_MAP.get(d.get("shotQuality", ""), 0),
                    "x_raw": int(x_raw),
                    "y_raw": int(y_raw),
                    "x_norm": x_norm,
                    "y_norm": y_norm,
                    "is_home": is_home,
                }
            )

        elif event_type == "goal":
            x_raw, y_raw = d.get("xLocation"), d.get("yLocation")
            period = _period_id((d.get("period") or {}).get("id"))
            team = d.get("team") or {}
            team_id = int(team["id"]) if team.get("id") else None
            is_home = team_id == home_team_id if team_id else False
            scorer = d.get("scoredBy") or {}
            assists = d.get("assists") or []
            props = d.get("properties") or {}
            x_norm = y_norm = None
            if x_raw is not None and y_raw is not None:
                x_norm, y_norm = transform_coords(int(x_raw), int(y_raw), is_home, period)

            rows.append(
                {
                    "game_id": game_id,
                    "season_id": int(season_id),
                    "season_type": season_type,
                    "event_type": "goal",
                    "period_id": period,
                    "time_seconds": _time_seconds(d.get("time")),
                    "team_id": team_id,
                    "shooter_id": int(scorer["id"]) if scorer.get("id") else None,
                    "goalie_id": None,  # not carried on the goal event itself
                    "shot_type": "",
                    "quality": SHOT_QUALITY_MAP.get("Quality goal", 5),
                    "x_raw": int(x_raw) if x_raw is not None else None,
                    "y_raw": int(y_raw) if y_raw is not None else None,
                    "x_norm": x_norm,
                    "y_norm": y_norm,
                    "is_home": is_home,
                    "game_goal_id": int(d["game_goal_id"]) if d.get("game_goal_id") else None,
                    "assist1_id": int(assists[0]["id"]) if len(assists) > 0 else None,
                    "assist2_id": int(assists[1]["id"]) if len(assists) > 1 else None,
                    "is_power_play": _parse_bool(props, "isPowerPlay"),
                    "is_short_handed": _parse_bool(props, "isShortHanded"),
                    "is_empty_net": _parse_bool(props, "isEmptyNet"),
                    "is_penalty_shot": _parse_bool(props, "isPenaltyShot"),
                    "is_insurance_goal": _parse_bool(props, "isInsuranceGoal"),
                    "is_game_winning_goal": _parse_bool(props, "isGameWinningGoal"),
                }
            )

    if TRANSFORM_DEBUG and debug_coords:
        x_raws = [c[0] for c in debug_coords]
        y_raws = [c[1] for c in debug_coords]
        log.info(
            f"  [DEBUG] x_raw: {min(x_raws)}-{max(x_raws)}, y_raw: {min(y_raws)}-{max(y_raws)}"
        )

    return rows


def ingest_game(sb, gid: int, home_id: int, season_id: str, season_type: str) -> int:
    events = fetch_pbp(gid)
    if not events:
        log.warning("    No PBP -- skipping")
        sb.table("ahl_skipped_games").upsert(
            {
                "game_id": gid,
                "pipeline": PIPELINE,
                "reason": "no_pbp",
                "skipped_at": datetime.now(UTC).isoformat(),
            },
            on_conflict="game_id,pipeline",
        ).execute()
        return 0

    rows = parse_pbp(gid, season_id, season_type, home_id, events)
    if not rows:
        log.info("    No shot events")
        sb.table("ahl_skipped_games").upsert(
            {
                "game_id": gid,
                "pipeline": PIPELINE,
                "reason": "no_shots",
                "skipped_at": datetime.now(UTC).isoformat(),
            },
            on_conflict="game_id,pipeline",
        ).execute()
        return 0

    # Upsert any unknown players referenced in shot/goal events (ahl_stats.py
    # covers roster + league-leaders players, but a call-up who never
    # appears in either can still take a shot).
    player_ids = set()
    for row in rows:
        for fld in ("shooter_id", "goalie_id", "assist1_id", "assist2_id"):
            if row.get(fld):
                player_ids.add(row[fld])
    if player_ids:
        existing = (
            sb.table("ahl_players").select("player_id").in_("player_id", list(player_ids)).execute()
        )
        existing_ids = {r["player_id"] for r in (existing.data or [])}
        missing = player_ids - existing_ids
        if missing:
            stubs = [
                {"player_id": pid, "updated_at": datetime.now(UTC).isoformat()} for pid in missing
            ]
            sb.table("ahl_players").upsert(stubs, on_conflict="player_id").execute()
            log.info(f"    Inserted {len(missing)} unknown player stubs: {missing}")

    for j in range(0, len(rows), 200):
        sb.table("ahl_shot_events").upsert(
            rows[j : j + 200],
            on_conflict="game_id,event_type,period_id,time_seconds,team_id,shooter_id",
        ).execute()

    goals = sum(1 for r in rows if r["event_type"] == "goal")
    log.info(f"    {len(rows)} events upserted ({goals} goals)")
    return len(rows)


def get_completed_games(sb, season_id: str) -> list:
    result = (
        sb.table("ahl_game_log")
        .select("game_id,home_team_id,away_team_id")
        .eq("season_id", int(season_id))
        .eq("game_state", "Final")
        .execute()
    )
    return result.data or []


def get_skipped_games(sb, pipeline: str) -> set:
    result = sb.table("ahl_skipped_games").select("game_id").eq("pipeline", pipeline).execute()
    return {r["game_id"] for r in (result.data or [])}


def get_processed_games(sb, season_id: str) -> set:
    result = sb.table("ahl_shot_events").select("game_id").eq("season_id", int(season_id)).execute()
    return {r["game_id"] for r in (result.data or [])}


def run(season_id: str | None = None) -> None:
    if season_id:
        season_type = resolve_season_type(season_id)
    else:
        current = resolve_current_season()
        season_id = str(current["season_id"])
        season_type = current["season_type"]

    log.info(f"=== AHL Shot Events -- season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    completed = get_completed_games(sb, season_id)
    skipped = get_skipped_games(sb, PIPELINE)
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
        try:
            ingest_game(sb, gid, home_id, season_id, season_type)
        except FetchError as e:
            log.warning(f"    Fetch failed for game {gid}, skipping: {e}")
        except Exception:
            log.exception(f"    CRASHED on game {gid}, skipping")
        time.sleep(0.5)

    log.info("=== AHL Shot Events complete ===")


def run_single_game(game_id: int) -> None:
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    result = (
        sb.table("ahl_game_log")
        .select("game_id,home_team_id,season_id")
        .eq("game_id", game_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        log.error(f"game_id {game_id} not found in ahl_game_log")
        return
    row = result.data[0]
    home_id = row["home_team_id"] or 0
    season_id = str(row["season_id"])
    season_type = resolve_season_type(season_id)

    log.info(f"=== AHL Shot Events -- single game {game_id} (season {season_id}) ===")
    ingest_game(sb, game_id, home_id, season_id, season_type)
    log.info("=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("season_id", nargs="?", default=None)
    parser.add_argument("--game", type=int, default=None)
    args = parser.parse_args()

    if args.game:
        run_single_game(args.game)
    else:
        run(args.season_id)
