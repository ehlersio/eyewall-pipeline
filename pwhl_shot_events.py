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
  ("penaltyshot" events intentionally NOT parsed here — no coordinates exist
   for them at all, make or miss; see pwhl_penalty_shots.py, Session 42)

Coordinate transform:
  Raw values observed: xLocation 63-537, yLocation ~13-290
  Canvas estimated ~600 x 300 px. Set TRANSFORM_DEBUG=1 to print stats for calibration.

--- gameSummary merge (added Session 34; extended Session 41) ------------
After shot events are ingested for a game, a second fetch against
statviewfeed/gameSummary pulls periods[].goals[], which carries data the PBP
feed doesn't have: real assists (full player objects), and ground-truth
per-goal flags (isPowerPlay, isShortHanded, isEmptyNet, isGameWinningGoal,
isPenaltyShot, isInsuranceGoal — the last two added Session 41, confirmed
present on every goal via a live pull against game 326).
Each gameSummary goal is matched to its existing pwhl_shot_events row on
(game_id, event_type='goal', period_id, time_seconds, team_id, shooter_id) —
the same key components already used for shot-event dedup, minus x_raw/y_raw
— and that row is UPDATEd in place with assist1_id, assist2_id,
is_power_play, is_short_handed, is_empty_net, is_game_winning_goal,
is_penalty_shot, is_insurance_goal.

This does NOT replace pwhl_shot_events' own goal rows or its dedup key;
gameSummary has no shot x/y coordinates, so it only supplements existing rows.

--- penalty shots moved out entirely (Session 42) -----------------------
Penalty shots (make or miss) are NOT ingested here or into pwhl_shot_events
at all — see pwhl_penalty_shots.py, sourced from gameSummary's own
penaltyShots key instead (which has misses too, unlike periods[].goals[]).
extract_gamesummary_goals() below explicitly skips any goal with
isPenaltyShot=true rather than trying to match it against a shot_events row
that will never exist (penalty shots have no shot coordinates at all,
confirmed Session 42, and pwhl_shot_events is fundamentally a coordinate-
based shot-map table). Practical effect: is_penalty_shot on this table's
existing rows will only ever read false going forward — it's likely dead
weight now, left in place rather than dropped this session (see CLAUDE.md).

Run modes:
  python pwhl_shot_events.py                  # ingest current season, merge gameSummary for newly-ingested games
  python pwhl_shot_events.py 5                 # specific season_id
  python pwhl_shot_events.py --backfill-goals  # merge gameSummary onto ALREADY-ingested goal rows missing it
  python pwhl_shot_events.py --backfill-goals 5   # backfill a specific season_id
  python pwhl_shot_events.py --game 261        # single game_id (debug -- ingest + merge just this game)
  TRANSFORM_DEBUG=1 python pwhl_shot_events.py 5
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


def _resolve_season_type(season_id: str) -> str | None:
    """SEASON_TYPE_MAP first (holds a deliberate manual correction for
    season "2" — see CLAUDE.md's "Known open items" before ever touching
    that), then get_season_type() as a live fallback for any season_id
    this module has no hardcoded entry for. Returns None, not a guessed
    "regular", if neither source recognizes the id."""
    return SEASON_TYPE_MAP.get(season_id) or get_season_type(season_id)


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

# Same period-id normalisation used by pwhl_pbp_events.py — kept as a local
# copy here (both modules parse the same feed independently, no shared import).
PERIOD_MAP = {"OT1": 4, "OT2": 5, "OT3": 6, "SO": 7}

PIPELINE = "pwhl_shot_events"


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


def _hockeytech_get(view: str, game_id: int):
    """Shared fetch for any statviewfeed view keyed on game_id. Returns the
    parsed JSON (list or dict, whatever the view returns) or None on failure.
    Mirrors fetch_pbp's retry/JSONP-unwrap logic exactly."""
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


def fetch_pbp(game_id: int) -> list | None:
    """Fetch play-by-play events for a single game."""
    data = _hockeytech_get("gameCenterPlayByPlay", game_id)
    return data if isinstance(data, list) else None


def fetch_game_summary(game_id: int) -> dict | None:
    """Fetch the gameSummary box score for a single game. Returns the raw
    dict (top-level keys: details, homeTeam, visitingTeam, periods, ...)
    or None on failure. See module docstring / docs/hockeytech-api-notes.md
    for the confirmed shape."""
    data = _hockeytech_get("gameSummary", game_id)
    return data if isinstance(data, dict) else None


def _gs_period_id(period_raw) -> int | None:
    """Normalise gameSummary's period.id the same way PBP period ids are
    normalised (OT1->4, OT2->5, OT3->6, SO->7, else int(id))."""
    if period_raw is None:
        return None
    s = str(period_raw)
    if s in PERIOD_MAP:
        return PERIOD_MAP[s]
    try:
        return int(s)
    except ValueError:
        return None


def _gs_parse_time(time_str) -> int:
    """'MM:SS' -> elapsed seconds. Same convention as parse_pbp's time
    parsing (already elapsed, not countdown — confirmed in
    docs/hockeytech-api-notes.md)."""
    try:
        parts = str(time_str or "0:00").split(":")
        return int(parts[0]) * 60 + int(parts[-1])
    except Exception:
        return 0


def _gs_parse_bool(val) -> bool:
    """HockeyTech's gameSummary properties come through as strings, not
    JSON booleans -- confirmed 2026-07 via game 261, where a naive bool(val)
    marked every single goal true for every flag (isPowerPlay, isShortHanded,
    isEmptyNet, isGameWinningGoal all True on all 10 goals -- impossible,
    since bool("false") is True in Python for any non-empty string). The
    exact string encoding isn't even consistent: game 261 sent "true"/"false",
    while a later live pull (game 326, Session 41) sent "1"/"0" for the same
    properties on the same view. Handle string/bool/None explicitly instead
    of relying on Python truthiness or assuming one fixed encoding."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes")
    return bool(val)


def extract_gamesummary_goals(game_summary: dict) -> list[dict]:
    """Flatten periods[].goals[] into merge-ready dicts: the match key
    (period_id, time_seconds, team_id, shooter_id) plus the new columns to
    write. No coordinates here — this only supplements existing shot_events
    rows, never replaces them.

    Penalty-shot goals are deliberately excluded (Session 42) — they now
    live in pwhl_penalty_shots.py instead, sourced from gameSummary's own
    penaltyShots key (which also has misses, unlike this periods[].goals[]
    view). A penalty-shot goal is intentionally never inserted into
    pwhl_shot_events (it's a coordinate-based shot-map table; penalty shots
    have no coordinates at all), so without this skip, merge_game_summary()
    would log a permanent false-positive "unmatched" warning for every one,
    forever, instead of a real data-quality signal."""
    out = []
    for period in game_summary.get("periods") or []:
        period_id = _gs_period_id((period.get("info") or {}).get("id"))
        for goal in period.get("goals") or []:
            team = goal.get("team") or {}
            scored_by = goal.get("scoredBy") or {}
            assists = goal.get("assists") or []
            props = goal.get("properties") or {}

            team_id = team.get("id")
            shooter_id = scored_by.get("id")
            if team_id is None or shooter_id is None:
                continue

            try:
                team_id = int(team_id)
                shooter_id = int(shooter_id)
            except (TypeError, ValueError):
                continue

            assist1_id = None
            assist2_id = None
            if len(assists) >= 1 and assists[0]:
                try:
                    assist1_id = int(assists[0].get("id"))
                except (TypeError, ValueError):
                    assist1_id = None
            if len(assists) >= 2 and assists[1]:
                try:
                    assist2_id = int(assists[1].get("id"))
                except (TypeError, ValueError):
                    assist2_id = None

            is_penalty_shot = _gs_parse_bool(props.get("isPenaltyShot", False))
            if is_penalty_shot:
                # No pwhl_shot_events row will ever exist for this goal --
                # see docstring above. pwhl_penalty_shots.py owns it instead.
                continue

            out.append(
                {
                    "period_id": period_id,
                    "time_seconds": _gs_parse_time(goal.get("time")),
                    "team_id": team_id,
                    "shooter_id": shooter_id,
                    "game_goal_id": goal.get("game_goal_id"),
                    "assist1_id": assist1_id,
                    "assist2_id": assist2_id,
                    "is_power_play": _gs_parse_bool(props.get("isPowerPlay", False)),
                    "is_short_handed": _gs_parse_bool(props.get("isShortHanded", False)),
                    "is_empty_net": _gs_parse_bool(props.get("isEmptyNet", False)),
                    "is_game_winning_goal": _gs_parse_bool(props.get("isGameWinningGoal", False)),
                    "is_penalty_shot": is_penalty_shot,
                    "is_insurance_goal": _gs_parse_bool(props.get("isInsuranceGoal", False)),
                }
            )
    return out


def merge_game_summary(sb, game_id: int) -> tuple[int, int]:
    """Fetch gameSummary for game_id and UPDATE matching pwhl_shot_events
    goal rows with assist/situational data. Returns (matched, unmatched)
    counts. Safe to call repeatedly (idempotent UPDATEs)."""
    gs = fetch_game_summary(game_id)
    if gs is None:
        log.warning(f"    gameSummary {game_id} -- fetch failed, skipping merge")
        return 0, 0

    gs_goals = extract_gamesummary_goals(gs)
    if not gs_goals:
        log.info(f"    gameSummary {game_id} -- no goals found")
        return 0, 0

    matched = 0
    unmatched = 0
    for g in gs_goals:
        result = (
            sb.table("pwhl_shot_events")
            .update(
                {
                    "assist1_id": g["assist1_id"],
                    "assist2_id": g["assist2_id"],
                    "is_power_play": g["is_power_play"],
                    "is_short_handed": g["is_short_handed"],
                    "is_empty_net": g["is_empty_net"],
                    "is_game_winning_goal": g["is_game_winning_goal"],
                    "is_penalty_shot": g["is_penalty_shot"],
                    "is_insurance_goal": g["is_insurance_goal"],
                    "game_goal_id": g["game_goal_id"],
                }
            )
            .eq("game_id", game_id)
            .eq("event_type", "goal")
            .eq("period_id", g["period_id"])
            .eq("time_seconds", g["time_seconds"])
            .eq("team_id", g["team_id"])
            .eq("shooter_id", g["shooter_id"])
            .execute()
        )
        rows_hit = len(result.data or [])
        if rows_hit == 0:
            unmatched += 1
            log.warning(
                f"    gameSummary {game_id} -- no shot_events match for goal "
                f"period={g['period_id']} time={g['time_seconds']} "
                f"team={g['team_id']} shooter={g['shooter_id']}"
            )
        else:
            matched += rows_hit

    log.info(f"    gameSummary {game_id} -- {matched} goal row(s) merged, {unmatched} unmatched")
    return matched, unmatched


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


def get_games_missing_gamesummary(sb, season_id: str) -> set:
    """Games with goal rows already in pwhl_shot_events but never merged
    with gameSummary (is_power_play still NULL on at least one goal row).
    Used by --backfill-goals."""
    result = (
        sb.table("pwhl_shot_events")
        .select("game_id")
        .eq("season_id", int(season_id))
        .eq("event_type", "goal")
        .is_("is_power_play", "null")  # postgrest-py accepts "null" or None here
        .execute()
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


def ingest_game(sb, gid: int, home_id: int, season_id: str, season_type: str) -> int:
    """Fetch, parse, dedupe, and upsert shot events for one game, then run
    the gameSummary merge if any goals were found. Returns the number of
    shot-event rows upserted (0 if skipped for any reason). Shared by run()
    (bulk mode) and run_single_game() (--game debug mode)."""
    events = fetch_pbp(gid)
    if not events:
        log.warning("    No PBP -- skipping")
        sb.table("pwhl_skipped_games").upsert(
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
        sb.table("pwhl_skipped_games").upsert(
            {
                "game_id": gid,
                "pipeline": PIPELINE,
                "reason": "no_shots",
                "skipped_at": datetime.now(UTC).isoformat(),
            },
            on_conflict="game_id,pipeline",
        ).execute()
        return 0

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
                {"player_id": pid, "updated_at": datetime.now(UTC).isoformat()} for pid in missing
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

    if goals:
        merge_game_summary(sb, gid)

    return len(rows)


def run(season_id: str | None = None) -> None:
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        # Sweep mode processes many games in one unattended run — log
        # loudly and bail out of the whole run rather than crash it or
        # silently guess "regular" for a season we don't recognize.
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping run"
        )
        return

    log.info(f"=== PWHL Shot Events -- season {season_id} ({season_type}) ===")
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
        ingest_game(sb, gid, home_id, season_id, season_type)
        time.sleep(0.5)

    log.info("=== PWHL Shot Events complete ===")


def run_single_game(game_id: int) -> None:
    """--game debug mode: ingest (or re-ingest) shot events for exactly one
    game, then run the gameSummary merge, regardless of skip/processed
    state. Does NOT require game_state='Final' -- mirrors
    pwhl_pbp_events.py's --game behaviour, since you may want to spot-check
    a specific game while it's still in progress or re-test after a code
    change. Uses upsert throughout, so re-running is always safe."""
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    result = (
        sb.table("pwhl_game_log")
        .select("game_id,home_team_id,season_id")
        .eq("game_id", game_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        log.error(f"game_id {game_id} not found in pwhl_game_log")
        return

    row = result.data[0]
    home_id = row["home_team_id"] or 0
    season_id = str(row["season_id"])
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        # --game is a debug/spot-check tool run by a human watching the
        # output directly — loud failure (not a silent "regular" guess)
        # is correct here, unlike the unattended sweep path in run().
        raise ValueError(
            f"Unknown season_id {season_id} for game {game_id} — "
            "not found in HockeyTech bootstrap data"
        )

    log.info(f"=== PWHL Shot Events -- single game {game_id} (season {season_id}) ===")
    ingest_game(sb, game_id, home_id, season_id, season_type)
    log.info("=== Done ===")


def backfill_goals(season_id: str | None = None) -> None:
    """Merge gameSummary data onto goal rows that already exist in
    pwhl_shot_events from a previous run (predating this feature), rather
    than re-ingesting PBP. Targets rows where is_power_play IS NULL."""
    season_id = season_id or PWHL_SEASON
    log.info(f"=== PWHL gameSummary backfill -- season {season_id} ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    game_ids = sorted(get_games_missing_gamesummary(sb, season_id))
    log.info(f"  {len(game_ids)} game(s) with unmerged goal rows")

    total_matched = 0
    total_unmatched = 0
    for i, gid in enumerate(game_ids):
        log.info(f"  [{i + 1}/{len(game_ids)}] game {gid}")
        matched, unmatched = merge_game_summary(sb, gid)
        total_matched += matched
        total_unmatched += unmatched
        time.sleep(0.5)

    log.info(
        f"=== Backfill complete -- {total_matched} goal row(s) merged, "
        f"{total_unmatched} unmatched across {len(game_ids)} game(s) ==="
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PWHL shot events + gameSummary merge")
    parser.add_argument("season", nargs="?", default=None, help="Season ID (e.g. 5, 8, 9)")
    parser.add_argument(
        "--backfill-goals",
        action="store_true",
        help="Merge gameSummary onto already-ingested goal rows missing it, instead of ingesting new PBP",
    )
    parser.add_argument(
        "--game",
        type=int,
        default=None,
        help="Single game_id (debug -- ingest + merge just this game)",
    )
    args = parser.parse_args()

    if args.game is not None:
        run_single_game(args.game)
    elif args.backfill_goals:
        backfill_goals(args.season)
    else:
        run(args.season)
