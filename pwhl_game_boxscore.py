"""
pwhl_game_boxscore.py — PWHL per-game, per-player box score pipeline module

Fetches the statviewfeed/gameSummary box score (homeTeam/visitingTeam
skaters[]/goalies[]) for each completed PWHL game and writes one row per
player per game to pwhl_skater_game_box / pwhl_goalie_game_box.

This is a separate table pair from pwhl_shot_events' gameSummary merge
(pwhl_shot_events.py) -- that module reads periods[].goals[] for per-goal
assist/situational flags; this one reads homeTeam/visitingTeam.skaters[]/
goalies[] for full per-game stat lines (TOI, hits, blocked shots, faceoffs,
etc.) that don't exist anywhere else in the pipeline. Confirmed via live
pulls (Session 41, games 261/326) that pwhl_player_seasons/pwhl_goalie_seasons
(season aggregates from the league leaderboard view) hardcode toi_per_game
and gw_goals as unavailable -- this table fills that gap at per-game
granularity instead.

Endpoint: statviewfeed / gameSummary on feed/index.php, param game_id
(same endpoint pwhl_shot_events.py already uses for its goal merge; the
fetch is pwhl_common.fetch_game_summary(), shared by all five PWHL
per-game modules).

Position taxonomy:
  gameSummary's skaters[].info.position uses granular HockeyTech codes
  (C, LW, RW, LD, RD, D -- confirmed via live sampling across 9 games /
  3 seasons, no other codes seen). pwhl_players.position uses a broader
  F/D/G taxonomy and is NOT touched by this module -- both the raw and
  mapped position are stored locally on these tables instead:
    C, LW, RW        -> F
    LD, RD, D        -> D
    G                -> G
  An unrecognized code is stored as-is in position_raw with position_group
  left NULL and a warning logged, rather than silently guessed.

Run modes:
  python pwhl_game_boxscore.py                  # ingest current season
  python pwhl_game_boxscore.py 5                # specific season_id
  python pwhl_game_boxscore.py --game 326       # single game_id (debug)
"""

import argparse
import logging
import os
import time
from datetime import UTC, datetime

from dotenv import load_dotenv
from supabase import create_client

import pwhl_common
from pipeline_common import FetchError
from pwhl_common import fetch_game_summary, get_skipped_games
from season_lookup import get_pwhl_season, get_season_type

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
# Live-resolved via Worker; falls back to PWHL_SEASON env var (or "8") — see
# season_lookup.get_pwhl_season(). Not os.environ.get("PWHL_SEASON", "8"):
# that only applies its default when the key is absent, not when it's set
# to an empty string (the Session 30 bug).
PWHL_SEASON = str(get_pwhl_season()["season_id"])

# Same base/auth pattern pwhl_shot_events.py uses for gameSummary.
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


def _resolve_season_type(season_id: str) -> str | None:
    """SEASON_TYPE_MAP first (holds a deliberate manual correction for
    season "2" — see CLAUDE.md's "Known open items"), then get_season_type()
    as a live fallback. Returns None, not a guessed "regular", if neither
    source recognizes the id."""
    return SEASON_TYPE_MAP.get(season_id) or get_season_type(season_id)


# Granular HockeyTech position code -> broad F/D/G group.
# Confirmed via live sampling, Session 41: an initial 9-game sample (across
# seasons 1/5/8, 353 rows) missed the plain "F" code entirely -- it only
# surfaced once the full season 1 backfill ran (72 games), a reminder that
# small samples of this API can under-represent real code variety.
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

PIPELINE = "pwhl_game_boxscore"


def _to_int(val, default=0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _to_bool_flag(val) -> bool:
    """starting/status come through as 0/1 ints (not the "true"/"false" or
    "1"/"0" string encodings seen on goal properties elsewhere in this
    payload) -- confirmed via live pull, games 261/326."""
    return bool(_to_int(val, 0))


def _parse_toi(toi_str) -> int | None:
    """'MM:SS' -> elapsed seconds, or None if missing/unparseable. Same
    convention as pwhl_shot_events.py's _gs_parse_time, but returns None
    (not 0) on a missing value -- a skater who didn't play should be
    distinguishable from one with 0:00 TOI."""
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
                "faceoff_attempts": _to_int(stats.get("faceoffAttempts")),
                "faceoff_wins": _to_int(stats.get("faceoffWins")),
                "shots": _to_int(stats.get("shots")),
                "hits": _to_int(stats.get("hits")),
                "blocked_shots": _to_int(stats.get("blockedShots")),
                "toi_seconds": _parse_toi(stats.get("toi")),
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
                "plus_minus": _to_int(stats.get("plusMinus")),
                "faceoff_attempts": _to_int(stats.get("faceoffAttempts")),
                "faceoff_wins": _to_int(stats.get("faceoffWins")),
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


def _upsert_player_stubs(sb, player_ids: set[int]) -> None:
    if not player_ids:
        return
    existing = (
        sb.table("pwhl_players").select("player_id").in_("player_id", list(player_ids)).execute()
    )
    existing_ids = {r["player_id"] for r in (existing.data or [])}
    missing = player_ids - existing_ids
    if missing:
        stubs = [{"player_id": pid, "updated_at": datetime.now(UTC).isoformat()} for pid in missing]
        sb.table("pwhl_players").upsert(stubs, on_conflict="player_id").execute()
        log.info(f"    Inserted {len(missing)} unknown player stubs: {missing}")


def ingest_game(sb, gid: int, season_id: str, season_type: str) -> tuple[int, int]:
    """Fetch gameSummary and upsert both box-score tables for one game.
    Returns (skater_rows_upserted, goalie_rows_upserted) -- (0, 0) if
    skipped for any reason."""
    gs = fetch_game_summary(gid)
    if gs is None:
        log.warning("    No gameSummary -- skipping")
        pwhl_common.mark_skipped(sb, gid, PIPELINE, "no_gamesummary")
        return 0, 0

    skater_rows, goalie_rows = extract_boxscore(gs, gid, season_id, season_type)
    if not skater_rows and not goalie_rows:
        log.info("    No skater/goalie rows found")
        pwhl_common.mark_skipped(sb, gid, PIPELINE, "no_boxscore_rows")
        return 0, 0

    player_ids = {r["player_id"] for r in skater_rows} | {r["player_id"] for r in goalie_rows}
    _upsert_player_stubs(sb, player_ids)

    for i in range(0, len(skater_rows), 200):
        sb.table("pwhl_skater_game_box").upsert(
            skater_rows[i : i + 200], on_conflict="game_id,player_id"
        ).execute()
    for i in range(0, len(goalie_rows), 200):
        sb.table("pwhl_goalie_game_box").upsert(
            goalie_rows[i : i + 200], on_conflict="game_id,player_id"
        ).execute()

    log.info(f"    {len(skater_rows)} skater row(s), {len(goalie_rows)} goalie row(s) upserted")
    return len(skater_rows), len(goalie_rows)


def get_completed_games(sb, season_id: str) -> list:
    return pwhl_common.get_completed_games(sb, season_id, columns="game_id")


def get_processed_games(sb, season_id: str) -> set:
    return pwhl_common.get_processed_games(sb, "pwhl_skater_game_box", season_id)


def run(season_id: str | None = None) -> None:
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping run"
        )
        return

    log.info(f"=== PWHL Game Boxscore -- season {season_id} ({season_type}) ===")
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
            # pwhl_common.hockeytech_get() raises after exhausting retries instead
            # of swallowing to None -- without this, one bad game's fetch
            # failure would crash the entire sweep instead of being skipped.
            log.warning(f"    Fetch failed for game {gid}, skipping: {e}")
        except Exception:
            log.exception(f"    CRASHED on game {gid}, skipping")
        time.sleep(0.5)

    log.info("=== PWHL Game Boxscore complete ===")


def run_single_game(game_id: int) -> None:
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    result = (
        sb.table("pwhl_game_log")
        .select("game_id,season_id")
        .eq("game_id", game_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        log.error(f"game_id {game_id} not found in pwhl_game_log")
        return

    row = result.data[0]
    season_id = str(row["season_id"])
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        raise ValueError(
            f"Unknown season_id {season_id} for game {game_id} — "
            "not found in HockeyTech bootstrap data"
        )

    log.info(f"=== PWHL Game Boxscore -- single game {game_id} (season {season_id}) ===")
    ingest_game(sb, game_id, season_id, season_type)
    log.info("=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PWHL per-game player box score")
    parser.add_argument("season", nargs="?", default=None, help="Season ID (e.g. 5, 8, 9)")
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
