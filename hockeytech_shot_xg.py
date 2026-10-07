"""
hockeytech_shot_xg.py -- shot-location xG proxy per shooter, for every
HockeyTech league (PWHL, AHL, ECHL). pwhl_shot_xg.py is a thin wrapper.

Same idea in every league: each shot attempt is worth its distance bucket's
goal rate (<=15 / <=30 / >30 units from the goal line centre, the buckets
rapm.py uses for NHL), xg_for is the sum over a shooter's attempts -- goals
included, valued by location like any other attempt -- and
finishing = goals - xg_for (positive = scoring above what shot location
alone predicts).

Where the leagues differ (League.toi_rates, hockeytech_leagues.py):

PWHL (toi_rates=True) -- unchanged from pwhl_shot_xg.py, pinned by
  test_pwhl_percentiles_characterization.py. Attempts are goal / shot /
  blocked_shot (no missed shots in the feed). Bucket values are the
  calibrated DANGER_XG constants below. xg_for/finishing are merged onto
  pwhl_player_seasons for (player, team) rows that already exist, skipping
  shooters with more than one team row (pwhl_shot_events' team_id can be
  the opponent's on blocked shots there).

AHL/ECHL (toi_rates=False) -- attempts are goal / shot only: their
  play-by-play has no blocked or missed shots at all
  (hockeytech_shot_events.py). Bucket values are computed from the same
  season's own events every run (goals / attempts per bucket over every
  located attempt), so league xG equals league goals by construction and
  nothing is borrowed from another league's calibration. Shootout attempts
  (period 7) and penalty shots are left out. Rows go to their own table,
  {league}_player_xg, one per (player, team): shot rows' team_id is the
  shooter's own team in these feeds (shooterTeamId / the goal's team), so a
  traded player's attempts split by team cleanly. Migration:
  docs/2026-10-07_hockeytech_percentiles.sql; until it has been run the
  upsert fails and is logged once, nothing else breaks.

Run modes (AHL/ECHL; PWHL runs through pwhl_shot_xg.py):
    python hockeytech_shot_xg.py ahl            # current season
    python hockeytech_shot_xg.py echl 78        # specific season_id
"""

import argparse
import logging
import math
import os
from collections import defaultdict

from dotenv import load_dotenv
from supabase import create_client

import hockeytech_stats
from hockeytech_leagues import AHL, ECHL, League

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

LEAGUES = {lg.key: lg for lg in (AHL, ECHL)}

# PWHL's 3-bucket values: each bucket's observed goal rate over every shot
# attempt (goal/shot/blocked_shot) in pwhl_shot_events, seasons 1/3/5/6/8/9
# (24,885 attempts, re-checked 2026-09): high 592/4211 = 0.1406, medium
# 503/6186 = 0.0813, low 390/14488 = 0.0269. Summed over those attempts
# they give 1,486 xG for 1,485 actual goals. test_pwhl_shot_xg.py pins
# that. AHL/ECHL compute theirs per season instead (bucket_rates()).
DANGER_XG = {
    "high": 0.141,
    "medium": 0.081,
    "low": 0.027,
}

# PWHL's event_type vocabulary for shot attempts (no "missed_shot" value in
# that feed). AHL/ECHL rows only ever use the first two.
REAL_SHOT_TYPES = ("goal", "shot", "blocked_shot")
AHL_ECHL_SHOT_TYPES = ("goal", "shot")

SHOOTOUT_PERIOD = 7  # hockeytech_shot_events.PERIOD_MAP["SO"]


def danger_bucket(x, y) -> str:
    """Distance from the goal (rink coords: goal at x=+-89, centre y=0) to
    bucket -- the same thresholds rapm.py uses for NHL."""
    dist = math.sqrt((abs(x) - 89) ** 2 + (y or 0) ** 2)
    if dist <= 15:
        return "high"
    if dist <= 30:
        return "medium"
    return "low"


def shot_xg(event_type: str, x, y) -> float:
    """PWHL's per-attempt xG: the attempt's bucket value from DANGER_XG, 0
    for anything that isn't a shot attempt. A goal is valued by its location,
    not 1.0: this feeds a goals-minus-expected metric, so a goal has to carry
    the same pre-shot value as a save would."""
    if event_type not in REAL_SHOT_TYPES:
        return 0.0
    return DANGER_XG[danger_bucket(x, y)]


def bucket_rates(events: list) -> dict:
    """Goal rate per distance bucket over `events` (each with event_type,
    x_norm, y_norm): goals / attempts. A bucket with no attempts is left out
    -- nothing is ever looked up in it. Summed over the same events the rates
    reproduce the goal count exactly, which is what makes this a fair
    expectation for the league and season they came from."""
    attempts: dict = defaultdict(int)
    goals: dict = defaultdict(int)
    for e in events:
        b = danger_bucket(e["x_norm"], e.get("y_norm"))
        attempts[b] += 1
        if e["event_type"] == "goal":
            goals[b] += 1
    return {b: goals[b] / n for b, n in attempts.items()}


def fetch_located_attempts(lg: League, sb, season_id: str, season_type: str, columns: str) -> list:
    """Every AHL/ECHL goal and shot this season/type that has a location,
    minus shootout attempts and penalty shots (neither is a shot in the run
    of play). Keyset-paged on id: PostgREST caps a response at 1,000 rows."""
    cols = f"id,event_type,x_norm,y_norm,period_id,is_penalty_shot,{columns}"
    rows, last_id = [], 0
    while True:
        batch = (
            sb.table(f"{lg.key}_shot_events")
            .select(cols)
            .eq("season_id", int(season_id))
            .eq("season_type", season_type)
            .in_("event_type", list(AHL_ECHL_SHOT_TYPES))
            .not_.is_("x_norm", "null")
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
    out = []
    for r in rows:
        if r.get("period_id") == SHOOTOUT_PERIOD or r.get("is_penalty_shot"):
            continue
        r["x_norm"] = float(r["x_norm"])  # numeric -> PostgREST sends a string
        r["y_norm"] = float(r["y_norm"]) if r.get("y_norm") is not None else 0.0
        out.append(r)
    return out


class TableWriter:
    """Upserts to a table the owner may not have created yet. The first
    failure is logged once and every later write is skipped for the rest of
    the run, so a missing migration costs one log line, not a crashed
    nightly or a log line per chunk."""

    def __init__(self, sb, table: str, conflict: str):
        self.sb = sb
        self.table = table
        self.conflict = conflict
        self.failed = False

    def write(self, rows: list) -> int:
        if self.failed or not rows:
            return 0
        try:
            for i in range(0, len(rows), 200):
                self.sb.table(self.table).upsert(
                    rows[i : i + 200], on_conflict=self.conflict
                ).execute()
        except Exception as e:
            self.failed = True
            log.error(
                f"  Upsert to {self.table} FAILED -- has "
                "docs/2026-10-07_hockeytech_percentiles.sql been run? Skipping it for "
                f"this run: {type(e).__name__}: {e}"
            )
            return 0
        return len(rows)


# ── PWHL (toi_rates) ─────────────────────────────────────────────────────


def _fetch_shot_events(lg: League, sb, season_id: str, season_type: str) -> list:
    """Keyset-paginated fetch of this season's real shot attempts (PWHL)."""
    rows = []
    last_id = 0
    while True:
        batch = (
            sb.table(f"{lg.key}_shot_events")
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


def _load_player_season_teams(lg: League, sb, season_id: str, season_type: str) -> tuple:
    """Single fetch of {league}_player_seasons' (player_id, team_id) rows for
    this season/type, reused for two purposes:
      - `existing`: the full (player_id, team_id) pair set, so upserts can
        be filtered to pairs the stats sweep already created (never
        silently INSERT a new, mostly-NULL season row).
      - `team_of`: player_id -> team_id, but ONLY for players with exactly
        one team row this season (the common case). pwhl_shot_events'
        team_id can reflect the *opponent's* team on blocked_shot rows in
        some HockeyTech feeds, so the season row's team is used instead; a
        shooter with multiple team rows (mid-season trade) is skipped.
    Bounded by league roster size -- OFFSET pagination is fine at this scale.
    """
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table(f"{lg.key}_player_seasons")
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


def _compute_toi_league(lg: League, sb, season_id: str, season_type: str) -> None:
    events = _fetch_shot_events(lg, sb, season_id, season_type)
    if not events:
        log.warning(
            f"  No shot events for season {season_id}/{season_type} — "
            "xg_for/finishing will stay null for this season/type"
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

    existing, team_of = _load_player_season_teams(lg, sb, season_id, season_type)

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
        log.info(
            f"  Skipped {skipped_no_row} shooter(s) with no existing {lg.key}_player_seasons row"
        )

    log.info(f"  Computed xG for {len(updates)} shooters")
    _upsert_onto_player_seasons(lg, sb, updates)


def _upsert_onto_player_seasons(lg: League, sb, updates: list) -> None:
    """Merge-upsert xg_for/finishing onto {league}_player_seasons, tolerant
    of those columns not existing in the live schema: log loudly and skip
    rather than crash the whole nightly run."""
    if not updates:
        return
    try:
        for i in range(0, len(updates), 200):
            chunk = updates[i : i + 200]
            sb.table(f"{lg.key}_player_seasons").upsert(
                chunk, on_conflict="player_id,team_id,season_id,season_type"
            ).execute()
        log.info(f"  {len(updates)} player season rows updated with xg_for/finishing")
    except Exception as e:
        log.error(
            f"  xg_for/finishing upsert FAILED — likely missing columns on "
            f"{lg.key}_player_seasons: {type(e).__name__}: {e}"
        )


# ── AHL/ECHL (per game played) ───────────────────────────────────────────


def _compute_per_gp_league(lg: League, sb, season_id: str, season_type: str) -> None:
    events = fetch_located_attempts(lg, sb, season_id, season_type, "shooter_id,team_id")
    if not events:
        log.warning(f"  No located shot events for season {season_id}/{season_type}")
        return

    rates = bucket_rates(events)
    log.info(
        "  Bucket goal rates from this season's "
        f"{len(events)} attempts: "
        + ", ".join(f"{b} {rates[b]:.4f}" for b in ("high", "medium", "low") if b in rates)
    )

    totals: dict = defaultdict(lambda: {"xg": 0.0, "goals": 0, "attempts": 0})
    for e in events:
        if e.get("shooter_id") is None or e.get("team_id") is None:
            continue
        t = totals[(e["shooter_id"], e["team_id"])]
        t["xg"] += rates[danger_bucket(e["x_norm"], e["y_norm"])]
        t["attempts"] += 1
        if e["event_type"] == "goal":
            t["goals"] += 1

    rows = []
    for (pid, tid), t in totals.items():
        xg_for = round(t["xg"], 3)
        rows.append(
            {
                "player_id": pid,
                "team_id": tid,
                "season_id": int(season_id),
                "season_type": season_type,
                "attempts": t["attempts"],
                "goals": t["goals"],
                "xg_for": xg_for,
                "finishing": round(t["goals"] - xg_for, 3),
            }
        )
    rows.sort(key=lambda r: (r["player_id"], r["team_id"]))
    writer = TableWriter(sb, f"{lg.key}_player_xg", "player_id,team_id,season_id,season_type")
    n = writer.write(rows)
    log.info(f"  {n} {lg.key}_player_xg rows upserted")


def compute_shooter_xg(lg: League, sb, season_id: str, season_type: str) -> None:
    """xg_for/finishing per shooter for one season/type -- see the module
    docstring for where each league's rows go."""
    log.info(f"Computing {lg.label} shot-based xG proxy (season {season_id}, {season_type})...")
    if lg.toi_rates:
        _compute_toi_league(lg, sb, season_id, season_type)
    else:
        _compute_per_gp_league(lg, sb, season_id, season_type)


def resolve_season(lg: League, season_id: str | None) -> tuple[str, str]:
    """(season_id, season_type) the AHL/ECHL way: an explicit id's type from
    the Worker's season list, else the live-resolved current season."""
    if season_id:
        return str(season_id), hockeytech_stats.resolve_season_type(lg, str(season_id))
    current = hockeytech_stats.resolve_current_season(lg)
    return str(current["season_id"]), current["season_type"]


def run(lg: League, season_id: str | None = None) -> None:
    season_id, season_type = resolve_season(lg, season_id)
    log.info(f"=== {lg.label} shot-based xG proxy — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    compute_shooter_xg(lg, sb, season_id, season_type)
    log.info(f"=== {lg.label} shot-based xG proxy complete ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="AHL/ECHL shot-location xG proxy")
    parser.add_argument("league", choices=sorted(LEAGUES))
    parser.add_argument("season_id", nargs="?", default=None)
    args = parser.parse_args()
    run(LEAGUES[args.league], args.season_id or None)


if __name__ == "__main__":
    main()
