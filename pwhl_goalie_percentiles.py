"""
pwhl_goalie_percentiles.py — PWHL goalie percentile pipeline module

Computes GSAX-proxy + danger-zone/situational save% percentiles for PWHL
goalies, the goalie-side analogue of pwhl_percentiles.py (skaters). Mirrors
NHL's /goalie-analytics 6-category shape (GSAX, GSAX/60, 5v5 SV%, HD SV%,
MD SV%, PK SV%) as closely as the data allows — all six turned out to be
buildable from data already being ingested, confirmed live 2026-08 before
writing this:

  - GSAX-proxy: uses the same 3-bucket danger-zone xG proxy pwhl_shot_xg.py
    already computes for shooters (independent copy here, same convention
    every other module touching this feed follows — see that module's
    docstring for why cross-importing feed-parsing/derived-math logic
    isn't done in this codebase), applied to shots AGAINST a goalie
    instead of shots BY a shooter. gsax = xg_against - actual_goals_against
    (positive = saving more than expected). Only "goal"/"shot" event types
    count as a shot the goalie actually faced -- "blocked_shot" is
    deliberately excluded (see GOALIE_FACED_TYPES below), unlike
    pwhl_shot_xg.py's shooter-side pool which correctly includes it. A
    first version of this module wrongly included blocked shots here,
    inflating both shots-faced and xG-against with events that never
    reached the goalie at all (caught live 2026-08 by comparing this
    module's own shot count against pwhl_goalie_seasons.sv_pct's implied
    shot count -- was off by ~20%, exactly blocked_shot's share of the
    feed).
    KNOWN LIMITATION, not fixed here: absolute GSAX magnitude runs high
    relative to what an NHL reader would expect (e.g. +40 to +75 for
    strong current-season-8 starters, vs NHL's typical -15 to +25 range)
    -- the DANGER_XG bucket values (0.20/0.07/0.03) are ported verbatim
    from NHL's own calibration (rapm.py), not independently validated
    against PWHL's actual shot-danger distribution, which skews notably
    higher-danger by this proxy's straight-line-distance classification
    (confirmed live: ~23% of shots bucket "high" vs NHL's typical
    ~10-15%). This is the same already-shipped limitation
    pwhl_shot_xg.py's skater-side `finishing` metric carries (also
    NHL-calibrated buckets, also unvalidated for PWHL) -- staying
    consistent with that precedent rather than attempting a new,
    unvalidated PWHL-specific recalibration here. The RELATIVE ranking
    (percentiles) should still be meaningful even if the absolute number
    reads large, since every goalie is scored against the same bucket
    values.
  - GSAX/60: pwhl_goalie_seasons.toi (season-total, an "MM:SS" string from
    HockeyTech's own minutes_played field — see pwhl_stats.py's
    fetch_goalie_stats) is reliably populated for every goalie with a
    season row — confirmed live 2026-08 (all 20 current-season-8 goalie
    rows have it). No new TOI source needed, just a string parse.
  - 5v5 SV% / PK SV%: reuses pwhl_strength_state.py's penalty-window logic
    (the same machinery pwhl_milestones.py's SH-goal detection and
    pwhl_stats.py's run_team_shot_totals_5v5 already use), applied
    per-shot instead of per-goal/per-team. "PK for the goalie" = the
    OTHER team in that shot's game (i.e. not the shooter's own team_id)
    has an active penalty window at that shot's moment — this doesn't
    require resolving the goalie's own team_id via game_log home/away:
    in a 2-team game, the only team that could be penalized besides the
    shooter's own IS the goalie's, and pwhl_strength_state.py already
    excludes coincidental/offsetting penalties from its windows entirely
    (only genuine one-sided is_power_play=True penalties build a window),
    so "some window is active for a team other than the shooter's" is
    unambiguous.
  - HD/MD SV%: same 3-bucket distance thresholds as the xG proxy (high
    <=15, medium <=30, low >30 units from goal — low has no dedicated
    output category, matching NHL's own tile set which also has no LD
    SV%, just 5v5/HD/MD/PK).

MIN_GP = 10, matching pwhl_percentiles.py's skater convention — confirmed
against real season 8 data (2026-08): 11 of 20 goalies with a season row
qualify at this threshold, a workable pool for a 0-100 percentile scale
(smaller than skaters' ~168, proportional to a much smaller position group
in a 12-team league). Goalies below MIN_GP simply get no percentile row —
same "gsax stays null, route filters it out" convention NHL's own
moneypuck.py/goalie_seasons uses (see eyewall-poller's /goalie-analytics
select=...&gsax=not.is.null).

Schema note: pwhl_goalie_seasons already had placeholder gsax/
gsax_percentile columns from an earlier, never-completed attempt at this
feature (both 100% NULL, confirmed live 2026-08). gsax_percentile broke
this repo's own pct_* naming convention (pwhl_player_seasons.pct_goals/
pct_a1/pct_penalties/pct_finishing) — renamed to pct_gsax as part of this
change, safe since the column had zero real data in it. See PR description
for the exact ALTER TABLE (this repo has no migration tooling).

Run modes:
    python pwhl_goalie_percentiles.py                  # current season (PWHL_SEASON)
    python pwhl_goalie_percentiles.py 8                 # specific season_id
"""

import logging
import math
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv
from supabase import create_client

from pwhl_strength_state import get_penalties_for_season
from pwhl_strength_state import penalty_window as _penalty_window
from season_lookup import get_pwhl_season, get_season_type

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
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

MIN_GP = 10  # see module docstring

# Same 3-bucket danger-zone xG proxy pwhl_shot_xg.py uses — independent
# copy, see that module's docstring for why this codebase doesn't
# cross-import feed-derived math between pipeline modules.
DANGER_XG = {
    "high": 0.20,
    "medium": 0.07,
    "low": 0.03,
}

# A goalie only ever faces shots that actually reach them -- "goal" and
# "shot" (on-target, unblocked). Deliberately narrower than
# pwhl_shot_xg.py's shooter-side REAL_SHOT_TYPES, which correctly INCLUDES
# blocked_shot (a blocked attempt still reflects the shooter's own shot
# quality/opportunity creation). A blocked shot never reaches the goalie
# at all -- it's stopped by a teammate defender in front of them -- so
# counting it as a "shot faced" would inflate both shots-faced and
# xG-against with events the goalie had literally zero chance to save or
# allow. Caught live 2026-08 by comparing this module's own shot-count
# output against pwhl_goalie_seasons.sv_pct's implied shot count before
# the fix (was off by ~20%, exactly blocked_shot's share of the feed).
GOALIE_FACED_TYPES = ("goal", "shot")


def _resolve_season_type(season_id: str) -> str | None:
    return SEASON_TYPE_MAP.get(season_id) or get_season_type(season_id)


# ── Pure math helpers (ported from pwhl_percentiles.py / moneypuck.py) ────


def percentile_rank(value, sorted_pool: list) -> int | None:
    """Binary search percentile — O(log n). Identical to
    pwhl_percentiles.py's/moneypuck.py's percentile_rank()."""
    if not sorted_pool or value is None:
        return None
    lo, hi = 0, len(sorted_pool)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_pool[mid] < value:
            lo = mid + 1
        else:
            hi = mid
    return round(lo / len(sorted_pool) * 100)


def build_sorted_pool(goalies: list, fn) -> list:
    vals = [fn(g) for g in goalies]
    vals = [v for v in vals if v is not None]
    return sorted(vals)


def _danger_bucket(x, y) -> str:
    """Same distance-from-goal thresholds as pwhl_shot_xg.py's shot_xg()
    — rink coords, goal at x=+-89, centre y=0."""
    dist = math.sqrt((abs(x) - 89) ** 2 + (y or 0) ** 2)
    if dist <= 15:
        return "high"
    if dist <= 30:
        return "medium"
    return "low"


def _shot_xg(event_type: str, x, y) -> float:
    """Own copy of pwhl_shot_xg.py's shot_xg() — see module docstring.
    Narrower than the original: only ever called with GOALIE_FACED_TYPES
    ("goal"/"shot") here, never "blocked_shot" (see that constant's
    comment for why)."""
    if event_type == "goal":
        return 1.0
    return DANGER_XG[_danger_bucket(x, y)]


def _toi_minutes(toi_str: str | None) -> float | None:
    """pwhl_goalie_seasons.toi is an "MM:SS" string (e.g. "1643:15") — see
    module docstring. Returns total minutes as a float, or None if
    missing/malformed (sorts/divides-by never happen against a bad value)."""
    if not toi_str or ":" not in toi_str:
        return None
    try:
        m, s = toi_str.split(":")
        return int(m) + int(s) / 60
    except ValueError:
        return None


# ── Data loading ────────────────────────────────────────────────────────


def _fetch_shot_events_against(sb, season_id: str, season_type: str) -> list:
    """Keyset-paginated fetch of every real shot attempt this season with a
    goalie_id attached (i.e. every shot faced by some goalie) — same
    "PostgREST silently caps at 1000 rows regardless of .limit()" gotcha
    and keyset-on-id pattern every other module touching this feed uses."""
    rows = []
    last_id = 0
    while True:
        batch = (
            sb.table("pwhl_shot_events")
            .select("id,game_id,team_id,goalie_id,event_type,x_norm,y_norm,period_id,time_seconds")
            .eq("season_id", int(season_id))
            .eq("season_type", season_type)
            .in_("event_type", list(GOALIE_FACED_TYPES))
            .not_.is_("goalie_id", "null")
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


def _fetch_goalie_seasons(sb, season_id: str, season_type: str) -> list:
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table("pwhl_goalie_seasons")
            .select("player_id,team_id,gp,toi")
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
    return rows


# ── Strength-state classification ──────────────────────────────────────


def active_penalized_teams(game_windows: dict, game_id, period_id, time_seconds: int) -> set:
    """Every team_id with an active penalty window at this exact moment.
    Empty set = 5v5. A non-empty set containing some team OTHER than the
    shooter's own team_id means the goalie's team (the shot's defending
    side) is shorthanded -- see module docstring's "PK for the goalie"
    explanation for why this doesn't need the goalie's own team_id
    resolved via game_log home/away."""
    return {
        team_id
        for team_id, p_period, start, end in game_windows.get(game_id, [])
        if p_period == period_id and start <= time_seconds < end
    }


# ── Main computation ───────────────────────────────────────────────────


def compute_goalie_percentiles(sb, season_id: str, season_type: str) -> None:
    log.info(f"Computing PWHL goalie percentiles (season {season_id}, {season_type})...")

    goalie_seasons = _fetch_goalie_seasons(sb, season_id, season_type)
    if not goalie_seasons:
        log.warning(f"  No pwhl_goalie_seasons rows for season {season_id}/{season_type}")
        return

    events = _fetch_shot_events_against(sb, season_id, season_type)
    if not events:
        log.warning(
            f"  No pwhl_shot_events rows with a goalie_id for season {season_id}/{season_type} — "
            "nothing to compute (expected for season 9 playoffs; see pwhl_shot_xg.py's docstring)"
        )
        return

    # game_windows[game_id] = [(penalized_team_id, period_id, start, end), ...]
    # Same construction as pwhl_stats.py's run_team_shot_totals_5v5.
    penalties = get_penalties_for_season(sb, int(season_id), season_type)
    game_windows = defaultdict(list)
    for p in penalties:
        w = _penalty_window(p)
        if w is None:  # outside regulation (OT) — see pwhl_strength_state.py
            continue
        period_id, start, end = w
        game_windows[p["game_id"]].append((p["team_id"], period_id, start, end))

    # ── Aggregate per goalie ─────────────────────────────────────────────
    agg: dict = defaultdict(
        lambda: {
            "shots": 0,
            "goals": 0,
            "xg": 0.0,
            "ev_shots": 0,
            "ev_goals": 0,
            "pk_shots": 0,
            "pk_goals": 0,
            "danger": defaultdict(lambda: {"shots": 0, "goals": 0}),
        }
    )

    for e in events:
        gid = e["goalie_id"]
        period_id = e.get("period_id")
        time_seconds = e.get("time_seconds") or 0
        shooter_team = e.get("team_id")
        is_goal = e["event_type"] == "goal"
        xg = _shot_xg(e["event_type"], e.get("x_norm") or 0, e.get("y_norm") or 0)
        bucket = _danger_bucket(e.get("x_norm") or 0, e.get("y_norm") or 0)

        a = agg[gid]
        a["shots"] += 1
        a["goals"] += 1 if is_goal else 0
        a["xg"] += xg
        a["danger"][bucket]["shots"] += 1
        a["danger"][bucket]["goals"] += 1 if is_goal else 0

        if period_id in (1, 2, 3):
            penalized = active_penalized_teams(game_windows, e["game_id"], period_id, time_seconds)
            if not penalized:
                a["ev_shots"] += 1
                a["ev_goals"] += 1 if is_goal else 0
            elif shooter_team is not None and any(t != shooter_team for t in penalized):
                # The team penalized is NOT the shooter's own team -> the
                # shooter's opponent (the goalie's own team) is shorthanded.
                a["pk_shots"] += 1
                a["pk_goals"] += 1 if is_goal else 0

    # ── Derive rate stats, gated on MIN_GP ───────────────────────────────
    gp_by_player = {r["player_id"]: r.get("gp") or 0 for r in goalie_seasons}
    team_by_player = {r["player_id"]: r["team_id"] for r in goalie_seasons}
    toi_by_player = {r["player_id"]: _toi_minutes(r.get("toi")) for r in goalie_seasons}

    def sv_pct(shots, goals):
        return None if shots == 0 else round(1 - goals / shots, 4)

    qualified_ids = [pid for pid, gp in gp_by_player.items() if gp >= MIN_GP and pid in agg]
    log.info(
        f"  Pool: {len(qualified_ids)} goalies (min {MIN_GP} GP, of {len(goalie_seasons)} total)"
    )

    goalie_metrics = {}
    for pid in qualified_ids:
        a = agg[pid]
        gsax = round(a["xg"] - a["goals"], 3)
        toi = toi_by_player.get(pid)
        gsax60 = round(gsax / (toi / 60), 3) if toi else None
        goalie_metrics[pid] = {
            "gsax": gsax,
            "gsax60": gsax60,
            "ev_sv": sv_pct(a["ev_shots"], a["ev_goals"]),
            "hd_sv": sv_pct(a["danger"]["high"]["shots"], a["danger"]["high"]["goals"]),
            "md_sv": sv_pct(a["danger"]["medium"]["shots"], a["danger"]["medium"]["goals"]),
            "pk_sv": sv_pct(a["pk_shots"], a["pk_goals"]),
        }

    pools = {
        key: build_sorted_pool(qualified_ids, lambda pid, k=key: goalie_metrics[pid][k])
        for key in ("gsax", "gsax60", "ev_sv", "hd_sv", "md_sv", "pk_sv")
    }

    updates = []
    for pid in qualified_ids:
        m = goalie_metrics[pid]
        updates.append(
            {
                "player_id": pid,
                "team_id": team_by_player.get(pid),
                "season_id": int(season_id),
                "season_type": season_type,
                "gsax": m["gsax"],
                "gsax_per60": m["gsax60"],
                "ev_sv_pct": m["ev_sv"],
                "hd_sv_pct": m["hd_sv"],
                "md_sv_pct": m["md_sv"],
                "pk_sv_pct": m["pk_sv"],
                "pct_gsax": percentile_rank(m["gsax"], pools["gsax"]),
                "pct_gsax60": percentile_rank(m["gsax60"], pools["gsax60"]),
                "pct_ev_sv": percentile_rank(m["ev_sv"], pools["ev_sv"]),
                "pct_hd_sv": percentile_rank(m["hd_sv"], pools["hd_sv"]),
                "pct_md_sv": percentile_rank(m["md_sv"], pools["md_sv"]),
                "pct_pk_sv": percentile_rank(m["pk_sv"], pools["pk_sv"]),
            }
        )

    log.info(f"  Computed percentiles for {len(updates)} goalies")
    _upsert_defensive(sb, updates)


def _upsert_defensive(sb, updates: list) -> None:
    """Merge-upsert onto pwhl_goalie_seasons, tolerant of the new columns
    not existing yet in the live schema (this repo has no migration
    tooling — see PR description for the exact ALTER TABLE). If the upsert
    400s because a column is missing, log loudly and skip rather than
    crash the whole nightly run."""
    if not updates:
        return
    try:
        for i in range(0, len(updates), 200):
            chunk = updates[i : i + 200]
            sb.table("pwhl_goalie_seasons").upsert(
                chunk, on_conflict="player_id,team_id,season_id,season_type"
            ).execute()
        log.info(f"  {len(updates)} goalie season rows updated with percentiles")
    except Exception as e:
        log.error(
            f"  Goalie percentile upsert FAILED — likely missing columns on "
            f"pwhl_goalie_seasons (see PR description for required ALTER "
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
    log.info(f"=== PWHL goalie percentiles — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    compute_goalie_percentiles(sb, season_id, season_type)
    log.info("=== PWHL goalie percentiles complete ===")


if __name__ == "__main__":
    args = sys.argv[1:]
    run(args[0] if args else None)
