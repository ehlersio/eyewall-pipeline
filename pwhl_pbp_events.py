"""
pwhl_pbp_events.py — PWHL play-by-play event pipeline module

Fetches gameCenterPlayByPlay for all completed PWHL games and extracts
non-shot PBP events (hits, penalties, faceoffs, goalie changes).
Shot events with coordinates are handled by pwhl_shot_events.py.

Event types captured:
  hit          — player, onPlayer, xLocation, yLocation
  penalty      — takenBy, servedBy, minutes, isPowerPlay, isBench, description
  faceoff      — homePlayer, visitingPlayer, homeWin, xLocation, yLocation
  goalie_change — goalie, team

Shot/goal/blocked_shot events are intentionally skipped — owned by pwhl_shot_events.py.

Usage:
    python pwhl_pbp_events.py              # current season (PWHL_SEASON)
    python pwhl_pbp_events.py 6            # specific season_id
    python pwhl_pbp_events.py 6 --force    # re-ingest all games in season
    python pwhl_pbp_events.py --game 213   # single game (for debugging)
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

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL         = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
PWHL_SEASON          = os.environ.get("PWHL_SEASON", "8")

HOCKEYTECH_BASE = "https://lscluster.hockeytech.com/feed/index.php"
HOCKEYTECH_KEY  = "446521baf8c38984"
CLIENT_CODE     = "pwhl"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json",
    "Referer":    "https://www.thepwhl.com/",
}

SEASON_TYPE_MAP = {
    "1": "regular", "2": "showcase", "3": "playoffs",
    "4": "preseason", "5": "regular", "6": "playoffs",
    "7": "preseason", "8": "regular", "9": "playoffs",
}

# Event types we own — shots/goals handled by pwhl_shot_events.py
OWNED_TYPES = {"hit", "penalty", "faceoff", "goalie_change"}
SKIP_TYPES  = {
    "shot", "blocked_shot", "goal",
    "stoppage", "period_start", "period_end", "game_end", "shootout",
}

PIPELINE = "pwhl_pbp_events"


# ── HockeyTech fetch (mirrors pwhl_shot_events.py exactly) ───────────────────

def fetch_pbp(game_id: int) -> list | None:
    """Fetch play-by-play events for a single game. Returns list or None."""
    for attempt in range(3):
        try:
            r = requests.get(HOCKEYTECH_BASE, params={
                "feed":        "statviewfeed",
                "view":        "gameCenterPlayByPlay",
                "game_id":     str(game_id),
                "key":         HOCKEYTECH_KEY,
                "client_code": CLIENT_CODE,
                "lang":        "en",
                "league_id":   "",
            }, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                log.warning(f"    PBP {game_id} status {r.status_code}")
                continue
            text = r.text.strip()
            # HockeyTech wraps some responses as JSONP: callback(...)
            if "(" in text:
                text = text[text.index("(") + 1:text.rindex(")")]
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "error" in data:
                log.warning(f"    PBP {game_id} error: {data['error']}")
                return None
        except Exception as e:
            log.warning(f"    PBP {game_id} attempt {attempt + 1}: {e}")
        if attempt < 2:
            time.sleep(2 ** attempt)
    return None


# ── Coord helpers ─────────────────────────────────────────────────────────────

def _safe_float(val) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _parse_time(time_str: str | None) -> int | None:
    """Convert 'MM:SS' string to total seconds elapsed in period."""
    if not time_str:
        return None
    try:
        parts = str(time_str).strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(parts[0])
    except (ValueError, AttributeError):
        return None


def _period_id(period_raw) -> int | None:
    """Normalise period identifier to integer (OT1→4, OT2→5, SO→7)."""
    if period_raw is None:
        return None
    period_map = {"OT1": 4, "OT2": 5, "OT3": 6, "SO": 7}
    s = str(period_raw)
    if s in period_map:
        return period_map[s]
    # period_raw may be a dict with an 'id' key (same as pwhl_shot_events.py)
    return _safe_int(period_raw)


# ── Event parsers ─────────────────────────────────────────────────────────────

def _parse_hit(d: dict, game_id: int, season_id: int, season_type: str,
               period: int | None, time_seconds: int | None, team_id: int | None) -> dict:
    player  = d.get("player")  or {}
    on_plyr = d.get("onPlayer") or {}
    return {
        "game_id":               game_id,
        "season_id":             season_id,
        "season_type":           season_type,
        "event_type":            "hit",
        "period_id":             period,
        "time_seconds":          time_seconds,
        "team_id":               team_id,
        "player_id":             _safe_int(player.get("id")),
        "player_name":           (player.get("name") or "").strip() or None,
        "secondary_player_id":   _safe_int(on_plyr.get("id")),
        "secondary_player_name": (on_plyr.get("name") or "").strip() or None,
        "description":           None,
        "is_power_play":         False,
        "is_bench_penalty":      False,
        "penalty_minutes":       None,
        "x_location":            _safe_float(d.get("xLocation")),
        "y_location":            _safe_float(d.get("yLocation")),
    }


def _parse_penalty(d: dict, game_id: int, season_id: int, season_type: str,
                   period: int | None, time_seconds: int | None, team_id: int | None) -> dict:
    taken_by  = d.get("takenBy")  or {}
    served_by = d.get("servedBy") or {}
    return {
        "game_id":               game_id,
        "season_id":             season_id,
        "season_type":           season_type,
        "event_type":            "penalty",
        "period_id":             period,
        "time_seconds":          time_seconds,
        "team_id":               team_id,
        "player_id":             _safe_int(taken_by.get("id")),
        "player_name":           (taken_by.get("name") or "").strip() or None,
        "secondary_player_id":   _safe_int(served_by.get("id")),
        "secondary_player_name": (served_by.get("name") or "").strip() or None,
        "description":           (d.get("description") or "").strip() or None,
        "is_power_play":         bool(d.get("isPowerPlay", False)),
        "is_bench_penalty":      bool(d.get("isBench", False)),
        "penalty_minutes":       _safe_int(d.get("minutes")),
        "x_location":            None,
        "y_location":            None,
    }


def _parse_faceoff(d: dict, game_id: int, season_id: int, season_type: str,
                   period: int | None, time_seconds: int | None,
                   home_team_id: int, away_team_id: int | None = None,
                   player_team_map: dict | None = None) -> dict:
    # NOTE: HockeyTech's homeWin field is unreliable (often always True).
    # Instead derive winning team from the winning player's team via player_team_map.
    # player_team_map: { player_id (int) -> team_id (int) }
    home_player  = d.get("homePlayer")      or {}
    visit_player = d.get("visitingPlayer")  or {}

    # Determine winner: HockeyTech marks winner differently per game version.
    # Try homeWin first; if player_team_map available, override with roster lookup.
    home_win_flag = str(d.get("homeWin", "0")).strip() == "1"
    winner = home_player  if home_win_flag else visit_player
    loser  = visit_player if home_win_flag else home_player

    # Resolve winner's team from roster if available — more reliable than homeWin
    winner_pid = _safe_int(winner.get("id"))
    if player_team_map and winner_pid:
        winner_team = player_team_map.get(winner_pid)
        if winner_team in (home_team_id, away_team_id):
            # Confirmed by roster — use this
            team_id = winner_team
        else:
            # Not in roster (opponent player or unknown) — fall back to homeWin
            team_id = home_team_id if home_win_flag else (away_team_id or None)
    else:
        team_id = home_team_id if home_win_flag else (away_team_id or None)

    return {
        "game_id":               game_id,
        "season_id":             season_id,
        "season_type":           season_type,
        "event_type":            "faceoff",
        "period_id":             period,
        "time_seconds":          time_seconds,
        "team_id":               team_id,
        "player_id":             winner_pid,
        "player_name":           (winner.get("name") or "").strip() or None,
        "secondary_player_id":   _safe_int(loser.get("id")),
        "secondary_player_name": (loser.get("name") or "").strip() or None,
        "description":           None,
        "is_power_play":         False,
        "is_bench_penalty":      False,
        "penalty_minutes":       None,
        "x_location":            _safe_float(d.get("xLocation")),
        "y_location":            _safe_float(d.get("yLocation")),
    }


def _parse_goalie_change(d: dict, game_id: int, season_id: int, season_type: str,
                         period: int | None, time_seconds: int | None,
                         team_id: int | None) -> dict:
    goalie = d.get("goalie") or {}
    return {
        "game_id":               game_id,
        "season_id":             season_id,
        "season_type":           season_type,
        "event_type":            "goalie_change",
        "period_id":             period,
        "time_seconds":          time_seconds,
        "team_id":               team_id,
        "player_id":             _safe_int(goalie.get("id")),
        "player_name":           (goalie.get("name") or "").strip() or None,
        "secondary_player_id":   None,
        "secondary_player_name": None,
        "description":           None,
        "is_power_play":         False,
        "is_bench_penalty":      False,
        "penalty_minutes":       None,
        "x_location":            None,
        "y_location":            None,
    }


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_pbp(game_id: int, season_id: str, season_type: str,
              home_team_id: int, events: list, away_team_id: int | None = None,
              player_team_map: dict | None = None) -> list:
    rows    = []
    unknown = []

    for ev in events:
        if not isinstance(ev, dict):
            continue

        event_type = ev.get("event", "")
        if event_type in SKIP_TYPES:
            continue

        d = ev.get("details") or ev  # some feeds inline details at top level

        # Period and time — may live in details or at top level
        period_raw   = (d.get("period") or {}).get("id") if isinstance(d.get("period"), dict) else d.get("period")
        period       = _period_id(period_raw)
        time_seconds = _parse_time(d.get("time") or d.get("clock"))

        # Team — hits/penalties carry teamId; faceoffs use home/visiting pattern
        team_id = _safe_int(d.get("teamId") or d.get("team_id"))

        if event_type == "hit":
            rows.append(_parse_hit(d, game_id, int(season_id), season_type,
                                   period, time_seconds, team_id))
        elif event_type == "penalty":
            rows.append(_parse_penalty(d, game_id, int(season_id), season_type,
                                       period, time_seconds, team_id))
        elif event_type == "faceoff":
            rows.append(_parse_faceoff(d, game_id, int(season_id), season_type,
                                       period, time_seconds, home_team_id, away_team_id,
                                       player_team_map))
        elif event_type == "goalie_change":
            rows.append(_parse_goalie_change(d, game_id, int(season_id), season_type,
                                             period, time_seconds, team_id))
        elif event_type not in SKIP_TYPES:
            unknown.append(event_type)

    if unknown:
        unique_unknown = sorted(set(unknown))
        log.info(f"    Unknown event types (not ingested): {unique_unknown}")

    return rows


# ── Supabase helpers (match pwhl_shot_events.py patterns) ─────────────────────

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
    result = (
        sb.table("pwhl_skipped_games")
        .select("game_id")
        .eq("pipeline", PIPELINE)
        .execute()
    )
    return {r["game_id"] for r in (result.data or [])}


def get_processed_games(sb, season_id: str) -> set:
    result = (
        sb.table("pwhl_pbp_events")
        .select("game_id")
        .eq("season_id", int(season_id))
        .execute()
    )
    return {r["game_id"] for r in (result.data or [])}


def mark_skipped(sb, game_id: int, reason: str) -> None:
    sb.table("pwhl_skipped_games").upsert({
        "game_id":    game_id,
        "pipeline":   PIPELINE,
        "reason":     reason,
        "skipped_at": datetime.now(UTC).isoformat(),
    }, on_conflict="game_id,pipeline").execute()


# ── Ingest one game ───────────────────────────────────────────────────────────

def ingest_game(sb, gid: int, home_team_id: int, season_id: str, season_type: str, away_team_id: int | None = None) -> int:
    events = fetch_pbp(gid)
    if not events:
        log.warning("    No PBP — skipping")
        mark_skipped(sb, gid, "no_pbp")
        return 0

    # Build player→team map from both teams' rosters for accurate faceoff winner resolution
    player_team_map: dict[int, int] = {}
    team_ids = [t for t in (home_team_id, away_team_id) if t]
    if team_ids:
        try:
            roster_res = sb.table("pwhl_players")                 .select("player_id,team_id")                 .in_("team_id", team_ids)                 .execute()
            for p in (roster_res.data or []):
                if p.get("player_id") and p.get("team_id"):
                    player_team_map[p["player_id"]] = p["team_id"]
        except Exception as e:
            log.warning(f"    Could not fetch player roster for team map: {e}")

    rows = parse_pbp(gid, season_id, season_type, home_team_id, events, away_team_id, player_team_map)
    if not rows:
        log.info("    No owned PBP events")
        mark_skipped(sb, gid, "no_pbp_events")
        return 0

    # Delete any existing rows for this game before inserting (force re-run safe)
    sb.table("pwhl_pbp_events").delete().eq("game_id", gid).execute()

    for j in range(0, len(rows), 200):
        sb.table("pwhl_pbp_events").insert(rows[j:j + 200]).execute()

    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["event_type"]] = by_type.get(r["event_type"], 0) + 1
    summary = " ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
    log.info(f"    {len(rows)} rows inserted ({summary})")
    return len(rows)


# ── Entry point ───────────────────────────────────────────────────────────────

def run(season_id: str | None = None, force: bool = False, single_game: int | None = None) -> None:
    season_id   = season_id or PWHL_SEASON
    season_type = SEASON_TYPE_MAP.get(season_id, "regular")

    log.info(f"=== PWHL PBP Events -- season {season_id} ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    if single_game is not None:
        result = (
            sb.table("pwhl_game_log")
            .select("home_team_id")
            .eq("game_id", single_game)
            .limit(1)
            .execute()
        )
        if not result.data:
            log.error(f"game_id {single_game} not found in pwhl_game_log")
            return
        home_id = result.data[0]["home_team_id"] or 0
        log.info(f"Single-game mode: game {single_game}")
        ingest_game(sb, single_game, home_id, season_id, season_type)
        return

    completed = get_completed_games(sb, season_id)
    skipped   = get_skipped_games(sb)
    processed = get_processed_games(sb, season_id) if not force else set()

    todo = [g for g in completed
            if g["game_id"] not in skipped and g["game_id"] not in processed]

    log.info(f"  {len(completed)} completed, {len(processed)} processed, "
             f"{len(skipped)} skipped, {len(todo)} to process")

    total_rows = 0
    for i, game in enumerate(todo):
        gid     = game["game_id"]
        home_id = game["home_team_id"] or 0
        away_id = game["away_team_id"] or None
        log.info(f"  [{i + 1}/{len(todo)}] game {gid}")
        total_rows += ingest_game(sb, gid, home_id, season_id, season_type, away_id)
        time.sleep(0.5)

    log.info(f"=== PWHL PBP Events complete — {total_rows} total rows ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest PWHL PBP events")
    parser.add_argument("season",   nargs="?", default=None, help="Season ID (e.g. 6, 7, 8)")
    parser.add_argument("--force",  action="store_true",     help="Re-ingest already-processed games")
    parser.add_argument("--game",   type=int,  default=None, help="Single game_id (debug)")
    args = parser.parse_args()
    run(season_id=args.season, force=args.force, single_game=args.game)
