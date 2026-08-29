"""
ahl_game_boxscore.py — AHL per-game, per-player box score pipeline module

Fetches the statviewfeed/gameSummary box score (homeTeam/visitingTeam
skaters[]/goalies[]) for each completed AHL game and writes one row per
player per game to ahl_skater_game_box / ahl_goalie_game_box. Structurally
mirrors PWHL's pwhl_game_boxscore.py (same vendor, same gameSummary
homeTeam/visitingTeam.skaters[]/goalies[].info/stats shape, confirmed live
2026-08-29 against game 1028992), but with real, confirmed field
differences below.

Purpose (same as PWHL's): ahl_player_seasons/ahl_goalie_seasons only carry
season-aggregate totals -- this table fills the per-game granularity gap
those don't have (needed for AHLPlayerPopup's Recent Form / per-game log
and for AHLGameStatsPopup's box score).

Real difference from PWHL, confirmed live against game 1028992 (a real,
completed, meaningful game -- not a data-entry gap): every skater's
`hits`, `faceoffAttempts`, `faceoffWins`, `blockedShots`, and `toi` field
in gameSummary reads exactly 0 / "0:00" regardless of how much that player
actually played (e.g. a skater with a recorded shot and assist still
shows toi: "0:00") -- consistent with docs/hockeytech-ahl-api-notes.md's
existing finding that AHL's PBP has no hit/faceoff event types at all.
These fields are NOT ingested here at all (no columns for them on
ahl_skater_game_box) rather than stored as a fabricated always-zero value
-- same "don't add a column that would always read NULL/0" principle
docs/ahl_new_tables_ddl.sql already applies to ahl_player_seasons.

Goalie fields are NOT affected by this gap -- timeOnIce/shotsAgainst/
goalsAgainst/saves are all real per-game numbers (confirmed: game 1028992,
Levi 59:49 TOI / 32 SA / 5 GA / 27 SV, a materially different, real line
from his season aggregate), so ahl_goalie_game_box keeps them.

Season resolution: imports resolve_current_season/resolve_season_type
from ahl_stats.py directly (see that module's docstring for why this
doesn't go through season_lookup.py's Worker pattern yet).

Run modes:
  python ahl_game_boxscore.py                  # ingest current season
  python ahl_game_boxscore.py 90                # specific season_id
  python ahl_game_boxscore.py --game 1028992    # single game_id (debug)
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

HOCKEYTECH_BASE = "https://lscluster.hockeytech.com/feed/index.php"
HOCKEYTECH_KEY = "ccb91f29d6744675"
CLIENT_CODE = "ahl"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://theahl.com/",
}

# Same granular-code set PWHL's gameSummary uses (C/LW/RW/F -> F,
# LD/RD/D -> D, G -> G) -- confirmed live against game 1028992's own
# skaters (C/D/LW/RW/F all seen; LD/RD not seen in this one game but kept
# for parity with PWHL's map since HockeyTech is the same vendor).
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

PIPELINE = "ahl_game_boxscore"


def _hockeytech_get(view: str, game_id: int):
    """Shared fetch for any statviewfeed view keyed on game_id. Mirrors
    ahl_shot_events.py's fetch_pbp/pwhl_game_boxscore.py's _hockeytech_get."""
    last_err = None
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
                last_err = f"status {r.status_code}"
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
            last_err = str(e)
        if attempt < 2:
            time.sleep(2**attempt)
    raise FetchError(f"{view} {game_id}: failed after 3 attempts ({last_err})")


def fetch_game_summary(game_id: int) -> dict | None:
    data = _hockeytech_get("gameSummary", game_id)
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
                # No hits/faceoff_attempts/faceoff_wins/blocked_shots/
                # toi_seconds columns -- confirmed always 0/"0:00" for
                # every skater regardless of real ice time (see module
                # docstring). Not ingested at all, not stored as a
                # fabricated zero.
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
                # No faceoff_attempts/faceoff_wins -- same always-zero gap
                # as skaters, and not a meaningful goalie stat regardless.
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


def _upsert_player_stubs(sb, player_ids: set[int]) -> None:
    if not player_ids:
        return
    existing = (
        sb.table("ahl_players").select("player_id").in_("player_id", list(player_ids)).execute()
    )
    existing_ids = {r["player_id"] for r in (existing.data or [])}
    missing = player_ids - existing_ids
    if missing:
        stubs = [{"player_id": pid, "updated_at": datetime.now(UTC).isoformat()} for pid in missing]
        sb.table("ahl_players").upsert(stubs, on_conflict="player_id").execute()
        log.info(f"    Inserted {len(missing)} unknown player stubs: {missing}")


def ingest_game(sb, gid: int, season_id: str, season_type: str) -> tuple[int, int]:
    """Fetch gameSummary and upsert both box-score tables for one game.
    Returns (skater_rows_upserted, goalie_rows_upserted) -- (0, 0) if
    skipped for any reason."""
    gs = fetch_game_summary(gid)
    if gs is None:
        log.warning("    No gameSummary -- skipping")
        sb.table("ahl_skipped_games").upsert(
            {
                "game_id": gid,
                "pipeline": PIPELINE,
                "reason": "no_gamesummary",
                "skipped_at": datetime.now(UTC).isoformat(),
            },
            on_conflict="game_id,pipeline",
        ).execute()
        return 0, 0

    skater_rows, goalie_rows = extract_boxscore(gs, gid, season_id, season_type)
    if not skater_rows and not goalie_rows:
        log.info("    No skater/goalie rows found")
        sb.table("ahl_skipped_games").upsert(
            {
                "game_id": gid,
                "pipeline": PIPELINE,
                "reason": "no_boxscore_rows",
                "skipped_at": datetime.now(UTC).isoformat(),
            },
            on_conflict="game_id,pipeline",
        ).execute()
        return 0, 0

    player_ids = {r["player_id"] for r in skater_rows} | {r["player_id"] for r in goalie_rows}
    _upsert_player_stubs(sb, player_ids)

    for i in range(0, len(skater_rows), 200):
        sb.table("ahl_skater_game_box").upsert(
            skater_rows[i : i + 200], on_conflict="game_id,player_id"
        ).execute()
    for i in range(0, len(goalie_rows), 200):
        sb.table("ahl_goalie_game_box").upsert(
            goalie_rows[i : i + 200], on_conflict="game_id,player_id"
        ).execute()

    log.info(f"    {len(skater_rows)} skater row(s), {len(goalie_rows)} goalie row(s) upserted")
    return len(skater_rows), len(goalie_rows)


def get_completed_games(sb, season_id: str) -> list:
    result = (
        sb.table("ahl_game_log")
        .select("game_id")
        .eq("season_id", int(season_id))
        .eq("game_state", "Final")
        .execute()
    )
    return result.data or []


def get_skipped_games(sb, pipeline: str) -> set:
    result = sb.table("ahl_skipped_games").select("game_id").eq("pipeline", pipeline).execute()
    return {r["game_id"] for r in (result.data or [])}


def get_processed_games(sb, season_id: str) -> set:
    result = (
        sb.table("ahl_skater_game_box").select("game_id").eq("season_id", int(season_id)).execute()
    )
    return {r["game_id"] for r in (result.data or [])}


def run(season_id: str | None = None) -> None:
    if season_id:
        season_type = resolve_season_type(season_id)
    else:
        current = resolve_current_season()
        season_id = str(current["season_id"])
        season_type = current["season_type"]

    log.info(f"=== AHL Game Boxscore -- season {season_id} ({season_type}) ===")
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
        log.info(f"  [{i + 1}/{len(todo)}] game {gid}")
        try:
            ingest_game(sb, gid, season_id, season_type)
        except FetchError as e:
            log.warning(f"    Fetch failed for game {gid}, skipping: {e}")
        except Exception:
            log.exception(f"    CRASHED on game {gid}, skipping")
        time.sleep(0.5)

    log.info("=== AHL Game Boxscore complete ===")


def run_single_game(game_id: int) -> None:
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    result = (
        sb.table("ahl_game_log")
        .select("game_id,season_id")
        .eq("game_id", game_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        log.error(f"game_id {game_id} not found in ahl_game_log")
        return

    row = result.data[0]
    season_id = str(row["season_id"])
    season_type = resolve_season_type(season_id)

    log.info(f"=== AHL Game Boxscore -- single game {game_id} (season {season_id}) ===")
    ingest_game(sb, game_id, season_id, season_type)
    log.info("=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AHL per-game player box score")
    parser.add_argument("season", nargs="?", default=None, help="Season ID (e.g. 90, 92, 94)")
    parser.add_argument(
        "--game",
        type=int,
        default=None,
        help="Single game_id (debug -- ingest just this game)",
    )
    args = parser.parse_args()

    if args.game is not None:
        run_single_game(args.game)
    else:
        run(args.season)
