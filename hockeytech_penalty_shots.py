"""
hockeytech_penalty_shots.py -- AHL/ECHL penalty shots (make or miss), from
play-by-play. Shared by ahl_penalty_shots.py and echl_penalty_shots.py;
every entry point takes the league config (hockeytech_leagues.py) first.

Reads the PBP `penaltyshot` event directly, unlike PWHL's
pwhl_penalty_shots.py (which uses gameSummary's penaltyShots[] because
PWHL's PBP version has a thinner team object). Here the event already has
makes and misses, a fully-resolved shooter_team, and full shooter/goalie
player objects -- confirmed live in both leagues. No coordinates exist for
penalty shots, so this table has no x/y columns and these events are NOT
written to {league}_shot_events.

Does its own PBP fetch rather than importing hockeytech_shot_events'
fetch_pbp -- this codebase's convention of pipeline modules parsing the same
feed independently. The extra per-game fetch is the same tradeoff
pwhl_penalty_shots.py accepts.
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

import hockeytech_stats
from hockeytech_leagues import HOCKEYTECH_BASE, League, strip_jsonp
from pipeline_common import FetchError, select_all

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

PERIOD_MAP = {"OT1": 4, "OT2": 5, "OT3": 6, "SO": 7}


def _pipeline(lg: League) -> str:
    return f"{lg.key}_penalty_shots"


def _hockeytech_get_pbp(lg: League, game_id: int):
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(
                HOCKEYTECH_BASE,
                params={
                    "feed": "statviewfeed",
                    "view": "gameCenterPlayByPlay",
                    "game_id": str(game_id),
                    "key": lg.hockeytech_key,
                    "client_code": lg.key,
                    "lang": "en",
                    "league_id": "",
                },
                headers=lg.headers,
                timeout=20,
            )
            if r.status_code != 200:
                log.warning(f"    PBP {game_id} status {r.status_code}")
                last_err = f"status {r.status_code}"
                continue
            data = json.loads(strip_jsonp(r.text.strip()))
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


def get_completed_games(lg: League, sb, season_id: str) -> list:
    return select_all(
        lambda: (
            sb.table(f"{lg.key}_game_log")
            .select("game_id")
            .eq("season_id", int(season_id))
            .eq("game_state", "Final")
        )
    )


def get_skipped_games(lg: League, sb) -> set:
    return {
        r["game_id"]
        for r in select_all(
            lambda: (
                sb.table(f"{lg.key}_skipped_games").select("game_id").eq("pipeline", _pipeline(lg))
            )
        )
    }


def get_processed_games(lg: League, sb, season_id: str) -> set:
    return {
        r["game_id"]
        for r in select_all(
            lambda: (
                sb.table(f"{lg.key}_penalty_shots")
                .select("game_id")
                .eq("season_id", int(season_id))
            )
        )
    }


def mark_skipped(lg: League, sb, game_id: int, reason: str) -> None:
    sb.table(f"{lg.key}_skipped_games").upsert(
        {
            "game_id": game_id,
            "pipeline": _pipeline(lg),
            "reason": reason,
            "skipped_at": datetime.now(UTC).isoformat(),
        },
        on_conflict="game_id,pipeline",
    ).execute()


def ingest_game(lg: League, sb, gid: int, season_id: str, season_type: str) -> int:
    events = _hockeytech_get_pbp(lg, gid)
    if events is None:
        log.warning("    PBP fetch failed -- skipping")
        mark_skipped(lg, sb, gid, "no_pbp")
        return 0

    shots = extract_penalty_shots(events)
    if not shots:
        mark_skipped(lg, sb, gid, "no_penalty_shots")
        return 0

    player_ids = {s["player_id"] for s in shots} | {s["goalie_id"] for s in shots if s["goalie_id"]}
    if player_ids:
        players = f"{lg.key}_players"
        existing = (
            sb.table(players).select("player_id").in_("player_id", list(player_ids)).execute()
        )
        existing_ids = {r["player_id"] for r in (existing.data or [])}
        missing = player_ids - existing_ids
        if missing:
            stubs = [
                {"player_id": pid, "updated_at": datetime.now(UTC).isoformat()} for pid in missing
            ]
            sb.table(players).upsert(stubs, on_conflict="player_id").execute()
            log.info(f"    Inserted {len(missing)} unknown player stubs: {missing}")

    rows = [
        {"game_id": gid, "season_id": int(season_id), "season_type": season_type, **s}
        for s in shots
    ]

    sb.table(f"{lg.key}_penalty_shots").upsert(
        rows,
        on_conflict="game_id,team_id,player_id,period_id,time_seconds",
    ).execute()

    goals = sum(1 for r in rows if r["is_goal"])
    log.info(f"    {len(rows)} penalty shot(s) upserted ({goals} goal(s))")
    return len(rows)


def run(lg: League, season_id: str | None = None) -> None:
    if season_id:
        season_type = hockeytech_stats.resolve_season_type(lg, season_id)
    else:
        current = hockeytech_stats.resolve_current_season(lg)
        season_id = str(current["season_id"])
        season_type = current["season_type"]

    log.info(f"=== {lg.label} Penalty Shots -- season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    completed = get_completed_games(lg, sb, season_id)
    skipped = get_skipped_games(lg, sb)
    processed = get_processed_games(lg, sb, season_id)
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
            total += ingest_game(lg, sb, gid, season_id, season_type)
        except FetchError as e:
            log.warning(f"    Fetch failed for game {gid}, skipping: {e}")
        except Exception:
            log.exception(f"    CRASHED on game {gid}, skipping")
        time.sleep(0.5)

    log.info(f"=== {lg.label} Penalty Shots complete -- {total} row(s) upserted ===")


def run_single_game(lg: League, game_id: int) -> None:
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    result = (
        sb.table(f"{lg.key}_game_log")
        .select("game_id,season_id")
        .eq("game_id", game_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        log.error(f"game_id {game_id} not found in {lg.key}_game_log")
        return
    season_id = str(result.data[0]["season_id"])
    season_type = hockeytech_stats.resolve_season_type(lg, season_id)

    log.info(f"=== {lg.label} Penalty Shots -- single game {game_id} (season {season_id}) ===")
    ingest_game(lg, sb, game_id, season_id, season_type)
    log.info("=== Done ===")


def main(lg: League) -> None:
    parser = argparse.ArgumentParser(description=f"{lg.label} penalty shot pipeline")
    parser.add_argument("season", nargs="?", default=None)
    parser.add_argument("--game", type=int, default=None)
    args = parser.parse_args()

    if args.game is not None:
        run_single_game(lg, args.game)
    else:
        run(lg, args.season)
