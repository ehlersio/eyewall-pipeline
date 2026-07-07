"""
pwhl_goal_on_ice.py — PWHL goal-level on-ice roster pipeline module (Session 42)

Fetches gameSummary's periods[].goals[].plus_players[]/minus_players[] for
each completed PWHL game and writes one row per (game_goal_id, player_id) to
pwhl_goal_on_ice — the full on-ice skater roster (by team) at the moment of
each goal. Independent of the shift-derivation approach the rest of the
pipeline relies on elsewhere, and independent of the WAR/RAPM blocker
(HockeyTech's PBP still exposes no player_change shift events) — this data
is goal-scoped, not continuous shift tracking, so it does NOT change that
blocker's calculus. It's a much coarser signal (only captures on-ice
composition at goal instants, which are rare relative to total ice time)
and should not be treated as a substitute for real shift data if a future
session is tempted to use it that way for line combinations or on-ice
shot-rate stats.

Data shape confirmed live (Session 42, game 277 and others): plus_players[]
= on-ice skaters for the team that SCORED; minus_players[] = on-ice skaters
for the team that CONCEDED. Not the traditional individual plus-minus
convention by name, but empirically validated (below) to reproduce it
exactly once the right goals are excluded.

Convention validated against gameSummary's own skaters[].stats.plusMinus
(Session 42, 11 games / 416 player-game rows, matched 416/416): summing
on_ice_for (+1) / not on_ice_for (-1) across all goals EXCEPT power-play
goals reproduces HockeyTech's own plusMinus field exactly. Short-handed,
empty-net, AND penalty-shot goals all count -- only power-play goals are
excluded. This module stores is_power_play/is_short_handed/is_empty_net/
is_penalty_shot on every row so any consumer can apply that filter (or a
different one) without joining back to pwhl_shot_events.

Run modes:
  python pwhl_goal_on_ice.py                  # ingest current season
  python pwhl_goal_on_ice.py 5                 # specific season_id
  python pwhl_goal_on_ice.py --game 277        # single game_id (debug)
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

PIPELINE = "pwhl_goal_on_ice"


def _resolve_season_type(season_id: str) -> str | None:
    return SEASON_TYPE_MAP.get(season_id) or get_season_type(season_id)


def _hockeytech_get(view: str, game_id: int):
    """Independent copy of the shared fetch helper -- see
    pwhl_penalty_shots.py's identical docstring for why this isn't a
    cross-import."""
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
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes")
    return bool(val)


def extract_goal_on_ice(game_summary: dict, home_team_id: int, away_team_id: int) -> list[dict]:
    """Flatten periods[].goals[].plus_players[]/minus_players[] into
    insert-ready dicts (missing game_id/season_id/season_type, filled in by
    the caller). plus_players -> on_ice_for=True, team_id=scoring team;
    minus_players -> on_ice_for=False, team_id=the OTHER team (derived from
    home/away, since minus_players itself carries no team field)."""
    out = []
    for period in game_summary.get("periods") or []:
        for goal in period.get("goals") or []:
            team = goal.get("team") or {}
            try:
                scoring_team_id = int(team.get("id"))
            except (TypeError, ValueError):
                continue

            try:
                game_goal_id = int(goal.get("game_goal_id"))
            except (TypeError, ValueError):
                log.warning(f"    goal missing game_goal_id, skipping: {goal.get('team')}")
                continue

            if scoring_team_id == home_team_id:
                opposing_team_id = away_team_id
            elif scoring_team_id == away_team_id:
                opposing_team_id = home_team_id
            else:
                opposing_team_id = None
                log.warning(
                    f"    goal {game_goal_id}: scoring team {scoring_team_id} matches neither "
                    f"home {home_team_id} nor away {away_team_id}"
                )

            props = goal.get("properties") or {}
            flags = {
                "is_power_play": _parse_bool(props.get("isPowerPlay", False)),
                "is_short_handed": _parse_bool(props.get("isShortHanded", False)),
                "is_empty_net": _parse_bool(props.get("isEmptyNet", False)),
                "is_penalty_shot": _parse_bool(props.get("isPenaltyShot", False)),
            }

            for pl in goal.get("plus_players") or []:
                try:
                    pid = int(pl.get("id"))
                except (TypeError, ValueError):
                    continue
                out.append(
                    {
                        "game_goal_id": game_goal_id,
                        "scoring_team_id": scoring_team_id,
                        "player_id": pid,
                        "team_id": scoring_team_id,
                        "on_ice_for": True,
                        **flags,
                    }
                )

            if opposing_team_id is None:
                minus = goal.get("minus_players") or []
                if minus:
                    log.warning(
                        f"    goal {game_goal_id}: skipping {len(minus)} minus_players -- "
                        "couldn't resolve opposing team_id"
                    )
                continue

            for pl in goal.get("minus_players") or []:
                try:
                    pid = int(pl.get("id"))
                except (TypeError, ValueError):
                    continue
                out.append(
                    {
                        "game_goal_id": game_goal_id,
                        "scoring_team_id": scoring_team_id,
                        "player_id": pid,
                        "team_id": opposing_team_id,
                        "on_ice_for": False,
                        **flags,
                    }
                )
    return out


def get_completed_games(sb, season_id: str) -> list:
    result = (
        sb.table("pwhl_game_log")
        .select("game_id,home_team_id,away_team_id")
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
        sb.table("pwhl_goal_on_ice").select("game_id").eq("season_id", int(season_id)).execute()
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


def ingest_game(sb, gid: int, home_id: int, away_id: int, season_id: str, season_type: str) -> int:
    gs = fetch_game_summary(gid)
    if gs is None:
        log.warning("    gameSummary fetch failed -- skipping")
        mark_skipped(sb, gid, "no_gamesummary")
        return 0

    entries = extract_goal_on_ice(gs, home_id, away_id)
    if not entries:
        mark_skipped(sb, gid, "no_goals")
        return 0

    player_ids = {e["player_id"] for e in entries}
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
                {"player_id": pid, "updated_at": datetime.now(UTC).isoformat()} for pid in missing
            ]
            sb.table("pwhl_players").upsert(stubs, on_conflict="player_id").execute()
            log.info(f"    Inserted {len(missing)} unknown player stubs: {missing}")

    rows = [
        {
            "game_id": gid,
            "season_id": int(season_id),
            "season_type": season_type,
            **e,
        }
        for e in entries
    ]

    for j in range(0, len(rows), 200):
        sb.table("pwhl_goal_on_ice").upsert(
            rows[j : j + 200],
            on_conflict="game_goal_id,player_id",
        ).execute()

    log.info(
        f"    {len(rows)} on-ice row(s) upserted across {len({r['game_goal_id'] for r in rows})} goal(s)"
    )
    return len(rows)


def run(season_id: str | None = None) -> None:
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping run"
        )
        return

    log.info(f"=== PWHL Goal On-Ice -- season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    completed = get_completed_games(sb, season_id)
    skipped = get_skipped_games(sb)
    processed = get_processed_games(sb, season_id)
    todo = [g for g in completed if g["game_id"] not in skipped and g["game_id"] not in processed]

    log.info(
        f"  {len(completed)} completed, {len(processed)} processed, "
        f"{len(skipped)} skipped, {len(todo)} to process"
    )

    total = 0
    for i, game in enumerate(todo):
        gid = game["game_id"]
        home_id = game["home_team_id"] or 0
        away_id = game["away_team_id"] or 0
        log.info(f"  [{i + 1}/{len(todo)}] game {gid}")
        total += ingest_game(sb, gid, home_id, away_id, season_id, season_type)
        time.sleep(0.5)

    log.info(f"=== PWHL Goal On-Ice complete -- {total} row(s) upserted ===")


def run_single_game(game_id: int) -> None:
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    result = (
        sb.table("pwhl_game_log")
        .select("game_id,home_team_id,away_team_id,season_id")
        .eq("game_id", game_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        log.error(f"game_id {game_id} not found in pwhl_game_log")
        return

    row = result.data[0]
    home_id = row["home_team_id"] or 0
    away_id = row["away_team_id"] or 0
    season_id = str(row["season_id"])
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        raise ValueError(
            f"Unknown season_id {season_id} for game {game_id} — not found in HockeyTech bootstrap data"
        )

    log.info(f"=== PWHL Goal On-Ice -- single game {game_id} (season {season_id}) ===")
    ingest_game(sb, game_id, home_id, away_id, season_id, season_type)
    log.info("=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PWHL goal-level on-ice roster pipeline")
    parser.add_argument("season", nargs="?", default=None, help="Season ID (e.g. 5, 8, 9)")
    parser.add_argument("--game", type=int, default=None, help="Single game_id (debug)")
    args = parser.parse_args()

    if args.game is not None:
        run_single_game(args.game)
    else:
        run(args.season)
