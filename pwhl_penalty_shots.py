"""
pwhl_penalty_shots.py — PWHL penalty shot pipeline module (Session 42)

Fetches gameSummary's penaltyShots.homeTeam[]/visitingTeam[] for each
completed PWHL game and writes one row per penalty shot (make or miss) to
pwhl_penalty_shots.

Why gameSummary's penaltyShots key, not the PBP "penaltyshot" event or
periods[].goals[]:
  - PBP's "penaltyshot" event (gameCenterPlayByPlay) carries the same
    shooter/goalie/team/period/time/isGoal fields but a thinner team object
    (shooter_team: only id/abbreviation populated) and a different field
    name (shooter_team.id, not shooterTeamId like shot/blocked_shot events
    use) -- gameSummary's version is the richer, more consistent source.
  - gameSummary's periods[].goals[] (already consumed by pwhl_shot_events.py)
    only has GOALS -- a missed penalty shot isn't a goal, so it's invisible
    there entirely.
  - gameSummary's penaltyShots key has BOTH makes and misses, already split
    by home/visiting team, with a fully-resolved team object. Confirmed via
    live pulls (Session 42, scanning all 329 completed games' raw PBP): 9
    games had a penalty-shot event, only 1 was a goal (game 277) -- misses
    dominate 8-to-1, so a makes-only source would badly undercount this.

Confirmed (Session 42): NO coordinate data exists for penalty shots at all,
on either a make or a miss -- this table intentionally has no x/y columns,
and these events are NOT written to pwhl_shot_events (which is a
coordinate-based shot-map table; penalty shots don't belong there). This
means every historical/future penalty-shot GOAL would otherwise show up as
a permanent false-positive "unmatched" warning in pwhl_shot_events'
gameSummary merge -- extract_gamesummary_goals() in pwhl_shot_events.py was
updated (Session 42) to skip is_penalty_shot goals rather than try to match
them against a row that will never exist.

Run modes:
  python pwhl_penalty_shots.py                  # ingest current season, mark no-penalty-shot games skipped
  python pwhl_penalty_shots.py 5                 # specific season_id
  python pwhl_penalty_shots.py --game 277        # single game_id (debug -- ingest just this game)
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

from season_lookup import get_season_type

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
PWHL_SEASON = os.environ.get("PWHL_SEASON", "8")

HOCKEYTECH_BASE = "https://lscluster.hockeytech.com/feed/index.php"
HOCKEYTECH_KEY = "446521baf8c38984"
CLIENT_CODE = "pwhl"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.thepwhl.com/",
}

# Same manual-correction-first-then-live-fallback pattern as
# pwhl_shot_events.py/pwhl_pbp_events.py -- see CLAUDE.md's "Known open
# items" (season "2") before ever touching this map directly.
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

# Same normalisation used by pwhl_shot_events.py/pwhl_pbp_events.py --
# kept as a local copy here, both modules parse the same feed independently.
PERIOD_MAP = {"OT1": 4, "OT2": 5, "OT3": 6, "SO": 7}

PIPELINE = "pwhl_penalty_shots"


def _resolve_season_type(season_id: str) -> str | None:
    return SEASON_TYPE_MAP.get(season_id) or get_season_type(season_id)


def _hockeytech_get(view: str, game_id: int):
    """Shared fetch for any statviewfeed view keyed on game_id. Mirrors
    pwhl_shot_events.py's _hockeytech_get exactly -- kept as an independent
    copy rather than a cross-import, matching this codebase's existing
    convention of not coupling pipeline modules that each parse the same
    feed independently (see pwhl_game_boxscore.py's docstring)."""
    for attempt in range(3):
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
                continue
            text = r.text.strip()
            if "(" in text:
                text = text[text.index("(") + 1 : text.rindex(")")]
            data = json.loads(text)
            if isinstance(data, dict) and "error" in data:
                log.warning(f"    {view} {game_id} error: {data['error']}")
                return None
            return data
        except Exception as e:
            log.warning(f"    {view} {game_id} attempt {attempt + 1}: {e}")
        if attempt < 2:
            time.sleep(2**attempt)
    return None


def fetch_game_summary(game_id: int) -> dict | None:
    data = _hockeytech_get("gameSummary", game_id)
    return data if isinstance(data, dict) else None


def _parse_bool(val) -> bool:
    """HockeyTech booleans are inconsistently encoded across views/fields
    (real JSON booleans in some places, "true"/"false" or "1"/"0" strings
    in others -- see pwhl_shot_events.py's _gs_parse_bool docstring for the
    confirmed case that bit this codebase before). isGoal was observed as a
    real JSON boolean on every penalty shot pulled this session, but handle
    string/bool explicitly anyway rather than assume that holds forever."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes")
    return bool(val)


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
    """'MM:SS' -> elapsed seconds. Same convention confirmed throughout
    this codebase (docs/hockeytech-api-notes.md) -- already elapsed, not
    countdown."""
    try:
        parts = str(time_str or "0:00").split(":")
        return int(parts[0]) * 60 + int(parts[-1])
    except Exception:
        return 0


def extract_penalty_shots(game_summary: dict) -> list[dict]:
    """Flatten gameSummary's penaltyShots.homeTeam[]/visitingTeam[] into
    insert-ready dicts. Confirmed shape (Session 42, live pulls): each entry
    has shooter/goalie (full player objects), shooter_team (full team
    object), period, time, isGoal -- no coordinates on either a make or a
    miss."""
    out = []
    ps = game_summary.get("penaltyShots") or {}
    for side in ("homeTeam", "visitingTeam"):
        for shot in ps.get(side) or []:
            shooter = shot.get("shooter") or {}
            goalie = shot.get("goalie") or {}
            team = shot.get("shooter_team") or {}

            try:
                team_id = int(team.get("id"))
                player_id = int(shooter.get("id"))
            except (TypeError, ValueError):
                log.warning(f"    penaltyShots entry missing team/shooter id, skipping: {shot}")
                continue

            goalie_id = None
            if goalie.get("id") is not None:
                try:
                    goalie_id = int(goalie["id"])
                except (TypeError, ValueError):
                    goalie_id = None

            period_id = _parse_period((shot.get("period") or {}).get("id"))
            if period_id is None:
                log.warning(f"    penaltyShots entry missing period, skipping: {shot}")
                continue

            out.append(
                {
                    "team_id": team_id,
                    "player_id": player_id,
                    "goalie_id": goalie_id,
                    "period_id": period_id,
                    "time_seconds": _parse_time(shot.get("time")),
                    "is_goal": _parse_bool(shot.get("isGoal", False)),
                }
            )
    return out


def get_completed_games(sb, season_id: str) -> list:
    result = (
        sb.table("pwhl_game_log")
        .select("game_id")
        .eq("season_id", int(season_id))
        .eq("game_state", "Final")
        .execute()
    )
    return result.data or []


def get_skipped_games(sb) -> set:
    result = sb.table("pwhl_skipped_games").select("game_id").eq("pipeline", PIPELINE).execute()
    return {r["game_id"] for r in (result.data or [])}


def get_processed_games(sb, season_id: str) -> set:
    result = (
        sb.table("pwhl_penalty_shots").select("game_id").eq("season_id", int(season_id)).execute()
    )
    return {r["game_id"] for r in (result.data or [])}


def mark_skipped(sb, game_id: int, reason: str) -> None:
    sb.table("pwhl_skipped_games").upsert(
        {
            "game_id": game_id,
            "pipeline": PIPELINE,
            "reason": reason,
            "skipped_at": datetime.now(UTC).isoformat(),
        },
        on_conflict="game_id,pipeline",
    ).execute()


def ingest_game(sb, gid: int, season_id: str, season_type: str) -> int:
    """Fetch gameSummary, extract penalty shots, upsert. Returns the number
    of rows upserted (0 if none found or fetch failed). Games with zero
    penalty shots are the overwhelming majority (9/329 historically) and are
    marked skipped so nightly runs don't keep re-fetching gameSummary for
    them forever."""
    gs = fetch_game_summary(gid)
    if gs is None:
        log.warning("    gameSummary fetch failed -- skipping")
        mark_skipped(sb, gid, "no_gamesummary")
        return 0

    shots = extract_penalty_shots(gs)
    if not shots:
        mark_skipped(sb, gid, "no_penalty_shots")
        return 0

    player_ids = {s["player_id"] for s in shots} | {s["goalie_id"] for s in shots if s["goalie_id"]}
    if player_ids:
        existing = (
            sb.table("pwhl_players").select("player_id").in_("player_id", list(player_ids)).execute()
        )
        existing_ids = {r["player_id"] for r in (existing.data or [])}
        missing = player_ids - existing_ids
        if missing:
            stubs = [
                {"player_id": pid, "updated_at": datetime.now(UTC).isoformat()} for pid in missing
            ]
            sb.table("pwhl_players").upsert(stubs, on_conflict="player_id").execute()
            log.info(f"    Inserted {len(missing)} unknown player stubs: {missing}")

    rows = [
        {
            "game_id": gid,
            "season_id": int(season_id),
            "season_type": season_type,
            **s,
        }
        for s in shots
    ]

    sb.table("pwhl_penalty_shots").upsert(
        rows,
        on_conflict="game_id,team_id,player_id,period_id,time_seconds",
    ).execute()

    goals = sum(1 for r in rows if r["is_goal"])
    log.info(f"    {len(rows)} penalty shot(s) upserted ({goals} goal(s))")
    return len(rows)


def run(season_id: str | None = None) -> None:
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping run"
        )
        return

    log.info(f"=== PWHL Penalty Shots -- season {season_id} ({season_type}) ===")
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
        total += ingest_game(sb, gid, season_id, season_type)
        time.sleep(0.5)

    log.info(f"=== PWHL Penalty Shots complete -- {total} row(s) upserted ===")


def run_single_game(game_id: int) -> None:
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    result = (
        sb.table("pwhl_game_log").select("game_id,season_id").eq("game_id", game_id).limit(1).execute()
    )
    if not result.data:
        log.error(f"game_id {game_id} not found in pwhl_game_log")
        return

    season_id = str(result.data[0]["season_id"])
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        raise ValueError(
            f"Unknown season_id {season_id} for game {game_id} — not found in HockeyTech bootstrap data"
        )

    log.info(f"=== PWHL Penalty Shots -- single game {game_id} (season {season_id}) ===")
    ingest_game(sb, game_id, season_id, season_type)
    log.info("=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PWHL penalty shot pipeline")
    parser.add_argument("season", nargs="?", default=None, help="Season ID (e.g. 5, 8, 9)")
    parser.add_argument("--game", type=int, default=None, help="Single game_id (debug)")
    args = parser.parse_args()

    if args.game is not None:
        run_single_game(args.game)
    else:
        run(args.season)
