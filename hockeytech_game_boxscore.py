"""
hockeytech_game_boxscore.py -- AHL/ECHL per-game, per-player box scores.
Shared by ahl_game_boxscore.py and echl_game_boxscore.py; every entry point
takes the league config (hockeytech_leagues.py) first.

Fetches statviewfeed/gameSummary (homeTeam/visitingTeam skaters[]/goalies[])
for each completed game and writes one row per player per game to
{league}_skater_game_box / {league}_goalie_game_box -- the per-game
granularity {league}_player_seasons doesn't have (player popups' recent
form and per-game log, the box-score popup). Same gameSummary shape as
PWHL's pwhl_game_boxscore.py.

Real difference from PWHL, confirmed live in both leagues (AHL game 1028992,
ECHL game 24296): every skater's hits/faceoffAttempts/faceoffWins/
blockedShots/toi reads exactly 0 / "0:00" regardless of real ice time. Those
fields aren't ingested at all (no columns for them) rather than stored as a
fabricated zero. Goalie timeOnIce/shotsAgainst/goalsAgainst/saves are real
and kept.
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

# Same granular-code set PWHL's gameSummary uses (C/LW/RW/F -> F,
# LD/RD/D -> D, G -> G).
POSITION_GROUP_MAP = {
    "C": "F",
    "LW": "F",
    "RW": "F",
    "F": "F",
    "LD": "D",
    "RD": "D",
    "D": "D",
    "G": "G",
}


def _pipeline(lg: League) -> str:
    return f"{lg.key}_game_boxscore"


def _hockeytech_get(lg: League, view: str, game_id: int):
    """Fetch any statviewfeed view keyed on game_id."""
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(
                HOCKEYTECH_BASE,
                params={
                    "feed": "statviewfeed",
                    "view": view,
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
                log.warning(f"    {view} {game_id} status {r.status_code}")
                last_err = f"status {r.status_code}"
                continue
            data = json.loads(strip_jsonp(r.text.strip()))
            if isinstance(data, dict) and "error" in data:
                log.warning(f"    {view} {game_id} error: {data['error']}")
                return None
            return data
        except Exception as e:
            log.warning(f"    {view} {game_id} attempt {attempt + 1}: {e}")
            last_err = str(e)
        if attempt < 2:
            time.sleep(2**attempt)
    raise FetchError(f"{view} {game_id}: failed after 3 attempts ({last_err})")


def fetch_game_summary(lg: League, game_id: int) -> dict | None:
    data = _hockeytech_get(lg, "gameSummary", game_id)
    return data if isinstance(data, dict) else None


def _to_int(val, default=0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _to_bool_flag(val) -> bool:
    return bool(_to_int(val, 0))


def _parse_toi(toi_str) -> int | None:
    """'MM:SS' -> elapsed seconds, or None if missing/unparseable."""
    if not toi_str:
        return None
    try:
        parts = str(toi_str).split(":")
        return int(parts[0]) * 60 + int(parts[-1])
    except (ValueError, IndexError):
        return None


def _resolve_position(position_raw: str | None) -> tuple[str | None, str | None]:
    if not position_raw:
        return None, None
    group = POSITION_GROUP_MAP.get(position_raw)
    if group is None:
        log.warning(f"    Unrecognized position code '{position_raw}' -- storing raw only")
    return position_raw, group


def _extract_skaters(
    team: dict, team_id: int, game_id: int, season_id: str, season_type: str
) -> list[dict]:
    rows = []
    for sk in team.get("skaters") or []:
        info = sk.get("info") or {}
        stats = sk.get("stats") or {}
        pid = info.get("id")
        if pid is None:
            continue
        position_raw, position_group = _resolve_position(info.get("position"))
        rows.append(
            {
                "game_id": game_id,
                "player_id": _to_int(pid),
                "team_id": team_id,
                "season_id": int(season_id),
                "season_type": season_type,
                "position_raw": position_raw,
                "position_group": position_group,
                "jersey_number": _to_int(info.get("jerseyNumber"), None) or None,
                "starting": _to_bool_flag(sk.get("starting")),
                "status": sk.get("status") or "",
                "goals": _to_int(stats.get("goals")),
                "assists": _to_int(stats.get("assists")),
                "points": _to_int(stats.get("points")),
                "penalty_minutes": _to_int(stats.get("penaltyMinutes")),
                "plus_minus": _to_int(stats.get("plusMinus")),
                "shots": _to_int(stats.get("shots")),
                # No hits/faceoff/blocked-shots/toi columns -- always 0 in
                # this feed (see module docstring).
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
    return rows


def _extract_goalies(
    team: dict, team_id: int, game_id: int, season_id: str, season_type: str
) -> list[dict]:
    rows = []
    for g in team.get("goalies") or []:
        info = g.get("info") or {}
        stats = g.get("stats") or {}
        pid = info.get("id")
        if pid is None:
            continue
        rows.append(
            {
                "game_id": game_id,
                "player_id": _to_int(pid),
                "team_id": team_id,
                "season_id": int(season_id),
                "season_type": season_type,
                "jersey_number": _to_int(info.get("jerseyNumber"), None) or None,
                "starting": _to_bool_flag(g.get("starting")),
                "status": g.get("status") or "",
                "goals": _to_int(stats.get("goals")),
                "assists": _to_int(stats.get("assists")),
                "points": _to_int(stats.get("points")),
                "penalty_minutes": _to_int(stats.get("penaltyMinutes")),
                "toi_seconds": _parse_toi(stats.get("timeOnIce")),
                "shots_against": _to_int(stats.get("shotsAgainst")),
                "goals_against": _to_int(stats.get("goalsAgainst")),
                "saves": _to_int(stats.get("saves")),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
    return rows


def extract_boxscore(
    game_summary: dict, game_id: int, season_id: str, season_type: str
) -> tuple[list[dict], list[dict]]:
    """Returns (skater_rows, goalie_rows) across both teams."""
    skater_rows = []
    goalie_rows = []
    for side in ("homeTeam", "visitingTeam"):
        team = game_summary.get(side) or {}
        team_id = (team.get("info") or {}).get("id")
        if team_id is None:
            continue
        team_id = _to_int(team_id)
        skater_rows.extend(_extract_skaters(team, team_id, game_id, season_id, season_type))
        goalie_rows.extend(_extract_goalies(team, team_id, game_id, season_id, season_type))
    return skater_rows, goalie_rows


def _upsert_player_stubs(lg: League, sb, player_ids: set[int]) -> None:
    if not player_ids:
        return
    players = f"{lg.key}_players"
    existing = sb.table(players).select("player_id").in_("player_id", list(player_ids)).execute()
    existing_ids = {r["player_id"] for r in (existing.data or [])}
    missing = player_ids - existing_ids
    if missing:
        stubs = [{"player_id": pid, "updated_at": datetime.now(UTC).isoformat()} for pid in missing]
        sb.table(players).upsert(stubs, on_conflict="player_id").execute()
        log.info(f"    Inserted {len(missing)} unknown player stubs: {missing}")


def _mark_skipped(lg: League, sb, gid: int, reason: str) -> None:
    sb.table(f"{lg.key}_skipped_games").upsert(
        {
            "game_id": gid,
            "pipeline": _pipeline(lg),
            "reason": reason,
            "skipped_at": datetime.now(UTC).isoformat(),
        },
        on_conflict="game_id,pipeline",
    ).execute()


def ingest_game(lg: League, sb, gid: int, season_id: str, season_type: str) -> tuple[int, int]:
    """Fetch gameSummary and upsert both box-score tables for one game.
    Returns (skater_rows, goalie_rows) upserted -- (0, 0) if skipped."""
    gs = fetch_game_summary(lg, gid)
    if gs is None:
        log.warning("    No gameSummary -- skipping")
        _mark_skipped(lg, sb, gid, "no_gamesummary")
        return 0, 0

    skater_rows, goalie_rows = extract_boxscore(gs, gid, season_id, season_type)
    if not skater_rows and not goalie_rows:
        log.info("    No skater/goalie rows found")
        _mark_skipped(lg, sb, gid, "no_boxscore_rows")
        return 0, 0

    player_ids = {r["player_id"] for r in skater_rows} | {r["player_id"] for r in goalie_rows}
    _upsert_player_stubs(lg, sb, player_ids)

    for i in range(0, len(skater_rows), 200):
        sb.table(f"{lg.key}_skater_game_box").upsert(
            skater_rows[i : i + 200], on_conflict="game_id,player_id"
        ).execute()
    for i in range(0, len(goalie_rows), 200):
        sb.table(f"{lg.key}_goalie_game_box").upsert(
            goalie_rows[i : i + 200], on_conflict="game_id,player_id"
        ).execute()

    log.info(f"    {len(skater_rows)} skater row(s), {len(goalie_rows)} goalie row(s) upserted")
    return len(skater_rows), len(goalie_rows)


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
                sb.table(f"{lg.key}_skater_game_box")
                .select("game_id")
                .eq("season_id", int(season_id))
            )
        )
    }


def run(lg: League, season_id: str | None = None) -> None:
    if season_id:
        season_type = hockeytech_stats.resolve_season_type(lg, season_id)
    else:
        current = hockeytech_stats.resolve_current_season(lg)
        season_id = str(current["season_id"])
        season_type = current["season_type"]

    log.info(f"=== {lg.label} Game Boxscore -- season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    completed = get_completed_games(lg, sb, season_id)
    skipped = get_skipped_games(lg, sb)
    processed = get_processed_games(lg, sb, season_id)
    todo = [g for g in completed if g["game_id"] not in skipped and g["game_id"] not in processed]

    log.info(
        f"  {len(completed)} completed, {len(processed)} processed, "
        f"{len(skipped)} skipped, {len(todo)} to process"
    )

    for i, game in enumerate(todo):
        gid = game["game_id"]
        log.info(f"  [{i + 1}/{len(todo)}] game {gid}")
        try:
            ingest_game(lg, sb, gid, season_id, season_type)
        except FetchError as e:
            log.warning(f"    Fetch failed for game {gid}, skipping: {e}")
        except Exception:
            log.exception(f"    CRASHED on game {gid}, skipping")
        time.sleep(0.5)

    log.info(f"=== {lg.label} Game Boxscore complete ===")


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

    row = result.data[0]
    season_id = str(row["season_id"])
    season_type = hockeytech_stats.resolve_season_type(lg, season_id)

    log.info(f"=== {lg.label} Game Boxscore -- single game {game_id} (season {season_id}) ===")
    ingest_game(lg, sb, game_id, season_id, season_type)
    log.info("=== Done ===")


def main(lg: League) -> None:
    parser = argparse.ArgumentParser(description=f"{lg.label} per-game player box score")
    parser.add_argument(
        "season", nargs="?", default=None, help=f"Season ID (e.g. {lg.season_examples})"
    )
    parser.add_argument(
        "--game",
        type=int,
        default=None,
        help="Single game_id (debug -- ingest just this game)",
    )
    args = parser.parse_args()

    if args.game is not None:
        run_single_game(lg, args.game)
    else:
        run(lg, args.season)
