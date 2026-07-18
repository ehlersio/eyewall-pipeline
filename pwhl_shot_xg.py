"""
pwhl_shot_xg.py — PWHL shot-based xG proxy pipeline module

Computes a per-shooter, per-season xG proxy from pwhl_shot_events, using the
same distance-bucket approach rapm.py uses for its NHL RAPM regression
target (rapm.py's own shot_xg()) — implemented here as an INDEPENDENT COPY,
not a cross-import. pwhl_shot_events.py's module docstring documents this
codebase's existing convention of not coupling pipeline modules that each
parse/consume the same feed independently (see e.g. its PERIOD_MAP comment:
"kept as a local copy here ... no shared import"; pwhl_game_boxscore.py's
module docstring says the same about _hockeytech_get). rapm.py's shot_xg()
is also NHL-specific (its docstring/usage assumes NHL event-type strings),
so this needed its own PWHL-vocabulary version regardless of import style.

PWHL event_type vocabulary (verified against pwhl_shot_events.py's
parse_pbp(), which is the only place that writes this column): exactly
three real values — "goal", "shot" (on-target, unblocked), "blocked_shot".
There is NO "missed_shot" value — HockeyTech's PWHL feed has no missed-shot
data at all (same gap pwhl_stats.py's run_team_shot_totals() docstring
notes: "No missed shots in HockeyTech data, so FF is SOG-based Fenwick
proxy"). So "real shot attempts" here is all three existing event types,
not a 4-way goal/on-target/missed/blocked split some plans assume.

pwhl_shot_events.x_norm/y_norm are already in NHL rink-coordinate units
(pwhl_shot_events.py's transform_coords(): x in [-100, 100], y in
[-42.5, 42.5], attacking net at x=+89-ish) — confirmed live this session,
no rescaling needed before reusing rapm.py's distance thresholds as-is.

xg_for  = sum of shot_xg() over the shooter's shot attempts this season.
goals   = count of event_type == "goal" for that shooter.
finishing = goals - xg_for (positive = scoring above what shot quality/
volume alone predicts; mirrors moneypuck.py's NHL finishing() metric,
season-total here rather than per-60 — pwhl_percentiles.py rates it using
piece 1's toi_per_game before taking a percentile).

KNOWN DATA GAP (confirmed live this session): pwhl_shot_events has ZERO
rows for season 9 (2025-26 Playoffs) — the PBP shot-event ingest was never
run for that season's 13 Final games (distinct from the box-score backfill,
which WAS done separately for season 9). compute_shooter_xg() detects "no
shot rows for this season/type" and returns without upserting anything
rather than crashing or writing nonsense — xg_for/finishing simply stay
NULL for that season until someone runs a manual
`python pwhl_shot_events.py 9` backfill (a call for the site owner to make,
not automated here).

Run modes:
    python pwhl_shot_xg.py                  # current season (PWHL_SEASON)
    python pwhl_shot_xg.py 8                # specific season_id
"""

import logging
import math
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv
from supabase import create_client

from season_lookup import get_pwhl_season, get_season_type

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
# Live-resolved via Worker; falls back to PWHL_SEASON env var (or "8") — see
# season_lookup.get_pwhl_season().
PWHL_SEASON = str(get_pwhl_season()["season_id"])

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
    """SEASON_TYPE_MAP first, then get_season_type() as a live fallback for
    any season_id this module has no hardcoded entry for. Returns None, not
    a guessed "regular", if neither source recognizes the id — same
    convention as pwhl_stats.py/pwhl_shot_events.py."""
    return SEASON_TYPE_MAP.get(season_id) or get_season_type(season_id)


# Same 3-bucket danger-zone xG proxy rapm.py uses for NHL (its shot_xg()) —
# independent copy, see module docstring for why.
DANGER_XG = {
    "high": 0.20,
    "medium": 0.07,
    "low": 0.03,
}

# PWHL's real event_type vocabulary for shot attempts (see module docstring
# — there is no "missed_shot" value in this feed).
REAL_SHOT_TYPES = ("goal", "shot", "blocked_shot")


def shot_xg(event_type: str, x, y) -> float:
    """Approximate xG from shot location and event type. Own copy of
    rapm.py's shot_xg(), against PWHL's event_type vocabulary instead of
    NHL's — see module docstring."""
    if event_type == "goal":
        return 1.0
    if event_type not in ("shot", "blocked_shot"):
        return 0.0
    # Use distance from goal (rink coords: goal at x=+-89, centre y=0) —
    # same convention rapm.py uses for NHL, valid here since
    # pwhl_shot_events.x_norm/y_norm are already in the same coordinate
    # system (see module docstring).
    dist = math.sqrt((abs(x) - 89) ** 2 + (y or 0) ** 2)
    if dist <= 15:
        return DANGER_XG["high"]
    if dist <= 30:
        return DANGER_XG["medium"]
    return DANGER_XG["low"]


def _fetch_shot_events(sb, season_id: str, season_type: str) -> list:
    """Keyset-paginated fetch of this season's real shot attempts.
    PostgREST silently caps any single query at 1000 rows regardless of
    .limit() — same gotcha pwhl_stats.py's run_team_shot_totals() docstring
    documents, same keyset-on-id pattern used there and in
    moneypuck.py::fetch_all_keyset."""
    rows = []
    last_id = 0
    while True:
        batch = (
            sb.table("pwhl_shot_events")
            .select("id,shooter_id,event_type,x_norm,y_norm")
            .eq("season_id", int(season_id))
            .eq("season_type", season_type)
            .in_("event_type", list(REAL_SHOT_TYPES))
            .gt("id", last_id)
            .order("id")
            .limit(999)
            .execute()
            .data
        )
        if not batch:
            break
        rows.extend(batch)
        last_id = batch[-1]["id"]
        if len(batch) < 999:
            break
    return rows


def _load_player_season_teams(sb, season_id: str, season_type: str) -> tuple:
    """Single fetch of pwhl_player_seasons' (player_id, team_id) rows for
    this season/type, reused for two purposes:
      - `existing`: the full (player_id, team_id) pair set, so upserts can
        be filtered to pairs fetch_skater_stats() already created (never
        silently INSERT a new, mostly-NULL season row — pwhl_player_seasons'
        other columns, gp/goals/etc., are always populated together by that
        function, and a stray rollup-only insert would leave those NULL).
      - `team_of`: player_id -> team_id, but ONLY for players with exactly
        one team row this season (the common case). pwhl_shot_events has no
        independent, trustworthy team-for-shooter signal beyond the shot's
        own team_id, which can reflect the *opponent's* team on
        blocked_shot rows in some HockeyTech feeds — using
        pwhl_player_seasons' own team_id instead sidesteps that risk.
        A shooter with multiple team rows this season (mid-season trade)
        has no single unambiguous team here and is skipped by
        compute_shooter_xg() — rare enough in a ~12-team league not to be
        worth more machinery for v1.
    Bounded by league roster size (a few hundred rows/season), same
    "OFFSET pagination is fine at this scale" reasoning moneypuck.py uses
    for its RAPM-values load.
    """
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table("pwhl_player_seasons")
            .select("player_id,team_id")
            .eq("season_id", int(season_id))
            .eq("season_type", season_type)
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not batch:
            break
        rows.extend(batch)
        offset += 1000
        if len(batch) < 1000:
            break

    existing = {(r["player_id"], r["team_id"]) for r in rows if r.get("team_id") is not None}

    counts: dict = defaultdict(int)
    team_of: dict = {}
    for r in rows:
        pid = r["player_id"]
        counts[pid] += 1
        team_of[pid] = r["team_id"]
    team_of = {pid: tid for pid, tid in team_of.items() if counts[pid] == 1}

    return existing, team_of


def compute_shooter_xg(sb, season_id: str, season_type: str) -> None:
    """Compute per-shooter xg_for/goals/finishing for one season/type and
    merge-upsert xg_for + finishing onto pwhl_player_seasons. See module
    docstring for the season-9-playoffs data gap this handles gracefully.
    """
    log.info(f"Computing shot-based xG proxy (season {season_id}, {season_type})...")

    events = _fetch_shot_events(sb, season_id, season_type)
    if not events:
        log.warning(
            f"  No shot events for season {season_id}/{season_type} — "
            "xg_for/finishing will stay null for this season/type (expected "
            "for season 9 playoffs as of this module's introduction; see "
            "module docstring)"
        )
        return

    totals: dict = defaultdict(lambda: {"xg": 0.0, "goals": 0})
    for e in events:
        sid = e.get("shooter_id")
        if sid is None:
            continue
        xg = shot_xg(e["event_type"], e.get("x_norm") or 0, e.get("y_norm") or 0)
        totals[sid]["xg"] += xg
        if e["event_type"] == "goal":
            totals[sid]["goals"] += 1

    existing, team_of = _load_player_season_teams(sb, season_id, season_type)

    updates = []
    skipped_no_team = 0
    skipped_no_row = 0
    for pid, t in totals.items():
        tid = team_of.get(pid)
        if tid is None:
            skipped_no_team += 1
            continue
        if (pid, tid) not in existing:
            skipped_no_row += 1
            continue
        xg_for = round(t["xg"], 3)
        updates.append(
            {
                "player_id": pid,
                "team_id": tid,
                "season_id": int(season_id),
                "season_type": season_type,
                "xg_for": xg_for,
                "finishing": round(t["goals"] - xg_for, 3),
            }
        )

    if skipped_no_team:
        log.info(
            f"  Skipped {skipped_no_team} shooter(s) with no unambiguous "
            "team this season (likely mid-season trades)"
        )
    if skipped_no_row:
        log.info(f"  Skipped {skipped_no_row} shooter(s) with no existing pwhl_player_seasons row")

    log.info(f"  Computed xG for {len(updates)} shooters")
    _upsert_defensive(sb, updates)


def _upsert_defensive(sb, updates: list) -> None:
    """Merge-upsert xg_for/finishing onto pwhl_player_seasons, tolerant of
    those columns not existing yet in the live schema. This repo has no
    migration tooling (see CLAUDE.md / this PR's description for the exact
    ALTER TABLE) — if the upsert 400s because a column is missing, log
    loudly and skip rather than crash the whole nightly run."""
    if not updates:
        return
    try:
        for i in range(0, len(updates), 200):
            chunk = updates[i : i + 200]
            sb.table("pwhl_player_seasons").upsert(
                chunk, on_conflict="player_id,team_id,season_id,season_type"
            ).execute()
        log.info(f"  {len(updates)} player season rows updated with xg_for/finishing")
    except Exception as e:
        log.error(
            f"  xg_for/finishing upsert FAILED — likely missing columns on "
            f"pwhl_player_seasons (see PR description for required ALTER "
            f"TABLE): {type(e).__name__}: {e}"
        )


def run(season_id: str | None = None) -> None:
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping run"
        )
        return
    log.info(f"=== PWHL shot-based xG proxy — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    compute_shooter_xg(sb, season_id, season_type)
    log.info("=== PWHL shot-based xG proxy complete ===")


if __name__ == "__main__":
    args = sys.argv[1:]
    run(args[0] if args else None)
