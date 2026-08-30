"""
echl_penalty_shots.py — ECHL penalty shot pipeline module

Fetches play-by-play for each completed ECHL game and extracts the
`penaltyshot` event (make or miss) into echl_penalty_shots. Structurally
mirrors ahl_penalty_shots.py -- confirmed live 2026-08-30 (games 24320,
24340) that ECHL's `penaltyshot` event has the identical shape AHL's
does: fully-resolved `shooter_team`, full shooter/goalie player objects,
no coordinates.

Same reasoning as ahl_penalty_shots.py for why this reads the PBP event
directly rather than gameSummary's penaltyShots[] key the way PWHL's
pipeline does -- see that module's docstring.

This module does its own independent PBP fetch rather than importing
echl_shot_events.fetch_pbp, matching this codebase's existing convention.

Run modes:
  python echl_penalty_shots.py                  # ingest current season
  python echl_penalty_shots.py 73                # specific season_id
  python echl_penalty_shots.py --game 24320      # single game_id (debug)
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

from echl_stats import resolve_current_season, resolve_season_type
from pipeline_common import FetchError

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

HOCKEYTECH_BASE = "https://lscluster.hockeytech.com/feed/index.php"
HOCKEYTECH_KEY = "2c2b89ea7345cae8"
CLIENT_CODE = "echl"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://echl.com/",
}

PERIOD_MAP = {"OT1": 4, "OT2": 5, "OT3": 6, "SO": 7}
PIPELINE = "echl_penalty_shots"


def _hockeytech_get_pbp(game_id: int):
    """Same JSONP-unwrap hardening as the other new echl_*.py modules --
    only strip parens when the response actually starts/ends with them."""
    last_err = None
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
                last_err = f"status {r.status_code}"
                continue
            text = r.text.strip()
            if text.startswith("(") and text.endswith(")"):
                text = text[1:-1]
            data = json.loads(text)
            if isinstance(data, dict) and "error" in data:
                log.warning(f"    PBP {game_id} error: {data['error']}")
                return None
            return data if isinstance(data, list) else None
        except Exception as e:
            log.warning(f"    PBP {game_id} attempt {attempt + 1}: {e}")
            last_err = str(e)
        if attempt < 2:
            time.sleep(2**attempt)
    raise FetchError(f"PBP {game_id}: failed after 3 attempts ({last_err})")


def _parse_period(period_raw) -> int | None:
    if period_raw is None:
        return None
    s = str(period_raw)
    if s in PERIOD_MAP:
        return PERIOD_MAP[s]
    try:
        return int(s)
    except ValueError:
        return None


def _parse_time(time_str) -> int:
    try:
        parts = str(time_str or "0:00").split(":")
        return int(parts[0]) * 60 + int(parts[-1])
    except Exception:
        return 0


def extract_penalty_shots(events: list) -> list[dict]:
    out = []
    for ev in events:
        if not isinstance(ev, dict) or ev.get("event") != "penaltyshot":
            continue
        d = ev.get("details", {})
        shooter = d.get("shooter") or {}
        goalie = d.get("goalie") or {}
        team = d.get("shooter_team") or {}

        try:
            team_id = int(team.get("id"))
            player_id = int(shooter.get("id"))
        except (TypeError, ValueError):
            log.warning(f"    penaltyshot event missing team/shooter id, skipping: {d}")
            continue

        goalie_id = None
        if goalie.get("id") is not None:
            try:
                goalie_id = int(goalie["id"])
            except (TypeError, ValueError):
                goalie_id = None

        period_id = _parse_period((d.get("period") or {}).get("id"))
        if period_id is None:
            log.warning(f"    penaltyshot event missing period, skipping: {d}")
            continue

        out.append(
            {
                "team_id": team_id,
                "player_id": player_id,
                "goalie_id": goalie_id,
                "period_id": period_id,
                "time_seconds": _parse_time(d.get("time")),
                "is_goal": bool(d.get("isGoal", False)),
            }
        )
    return out


def get_completed_games(sb, season_id: str) -> list:
    result = (
        sb.table("echl_game_log")
        .select("game_id")
        .eq("season_id", int(season_id))
        .eq("game_state", "Final")
        .execute()
    )
    return result.data or []


def get_skipped_games(sb) -> set:
    result = sb.table("echl_skipped_games").select("game_id").eq("pipeline", PIPELINE).execute()
    return {r["game_id"] for r in (result.data or [])}


def get_processed_games(sb, season_id: str) -> set:
    result = (
        sb.table("echl_penalty_shots").select("game_id").eq("season_id", int(season_id)).execute()
    )
    return {r["game_id"] for r in (result.data or [])}


def mark_skipped(sb, game_id: int, reason: str) -> None:
    sb.table("echl_skipped_games").upsert(
        {
            "game_id": game_id,
            "pipeline": PIPELINE,
            "reason": reason,
            "skipped_at": datetime.now(UTC).isoformat(),
        },
        on_conflict="game_id,pipeline",
    ).execute()


def ingest_game(sb, gid: int, season_id: str, season_type: str) -> int:
    events = _hockeytech_get_pbp(gid)
    if events is None:
        log.warning("    PBP fetch failed -- skipping")
        mark_skipped(sb, gid, "no_pbp")
        return 0

    shots = extract_penalty_shots(events)
    if not shots:
        mark_skipped(sb, gid, "no_penalty_shots")
        return 0

    player_ids = {s["player_id"] for s in shots} | {s["goalie_id"] for s in shots if s["goalie_id"]}
    if player_ids:
        existing = (
            sb.table("echl_players")
            .select("player_id")
            .in_("player_id", list(player_ids))
            .execute()
        )
        existing_ids = {r["player_id"] for r in (existing.data or [])}
        missing = player_ids - existing_ids
        if missing:
            stubs = [
                {"player_id": pid, "updated_at": datetime.now(UTC).isoformat()} for pid in missing
            ]
            sb.table("echl_players").upsert(stubs, on_conflict="player_id").execute()
            log.info(f"    Inserted {len(missing)} unknown player stubs: {missing}")

    rows = [
        {"game_id": gid, "season_id": int(season_id), "season_type": season_type, **s}
        for s in shots
    ]

    sb.table("echl_penalty_shots").upsert(
        rows,
        on_conflict="game_id,team_id,player_id,period_id,time_seconds",
    ).execute()

    goals = sum(1 for r in rows if r["is_goal"])
    log.info(f"    {len(rows)} penalty shot(s) upserted ({goals} goal(s))")
    return len(rows)


def run(season_id: str | None = None) -> None:
    if season_id:
        season_type = resolve_season_type(season_id)
    else:
        current = resolve_current_season()
        season_id = str(current["season_id"])
        season_type = current["season_type"]

    log.info(f"=== ECHL Penalty Shots -- season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    completed = get_completed_games(sb, season_id)
    skipped = get_skipped_games(sb)
    processed = get_processed_games(sb, season_id)
    todo = [
        g["game_id"]
        for g in completed
        if g["game_id"] not in skipped and g["game_id"] not in processed
    ]

    log.info(
        f"  {len(completed)} completed, {len(processed)} processed, "
        f"{len(skipped)} skipped, {len(todo)} to process"
    )

    total = 0
    for i, gid in enumerate(todo):
        log.info(f"  [{i + 1}/{len(todo)}] game {gid}")
        try:
            total += ingest_game(sb, gid, season_id, season_type)
        except FetchError as e:
            log.warning(f"    Fetch failed for game {gid}, skipping: {e}")
        except Exception:
            log.exception(f"    CRASHED on game {gid}, skipping")
        time.sleep(0.5)

    log.info(f"=== ECHL Penalty Shots complete -- {total} row(s) upserted ===")


def run_single_game(game_id: int) -> None:
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    result = (
        sb.table("echl_game_log")
        .select("game_id,season_id")
        .eq("game_id", game_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        log.error(f"game_id {game_id} not found in echl_game_log")
        return
    season_id = str(result.data[0]["season_id"])
    season_type = resolve_season_type(season_id)

    log.info(f"=== ECHL Penalty Shots -- single game {game_id} (season {season_id}) ===")
    ingest_game(sb, game_id, season_id, season_type)
    log.info("=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ECHL penalty shot pipeline")
    parser.add_argument("season", nargs="?", default=None)
    parser.add_argument("--game", type=int, default=None)
    args = parser.parse_args()

    if args.game is not None:
        run_single_game(args.game)
    else:
        run(args.season)
