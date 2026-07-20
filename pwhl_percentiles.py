"""
pwhl_percentiles.py — PWHL skater percentile pipeline module

Computes position-split (F/D) percentile ranks for PWHL skaters, the PWHL-
native analogue of moneypuck.py's `pct_*` columns on NHL's `player_seasons`.
Standalone (not a parametrized moneypuck.py) rather than folding this into
that module: MoneyPuck is an NHL-only external feed, so there's no shared
feed-fetch/parsing logic to parametrize over here — this module's only
inputs are already-ingested EyeWall tables (pwhl_player_seasons,
pwhl_players, pwhl_shot_events), not a second external CSV/API source.

Depends on two other nightly steps having already run for this season/type:
  - pwhl_stats.py's compute_toi_per_game() (piece 1) — populates
    pwhl_player_seasons.toi_per_game, needed to turn season totals into
    per-60 rates.
  - pwhl_shot_xg.py's compute_shooter_xg() (piece 2) — populates
    pwhl_player_seasons.finishing, needed for pct_finishing.
See pwhl-nightly.yml for the enforced ordering.

percentile_rank()/build_sorted_pool() are ported from moneypuck.py's
identical functions rather than imported from there. Reasoning: this
codebase's established convention (documented in pwhl_shot_events.py's
PERIOD_MAP comment, pwhl_game_boxscore.py's _hockeytech_get docstring, and
now pwhl_shot_xg.py's shot_xg()) is that pipeline modules keep independent
copies of logic rather than cross-import, specifically for feed-parsing
code. percentile_rank() itself is pure math, not feed-specific, and in
principle could live in a shared module -- but no such "generic pipeline
math utils" module exists yet (pipeline_common.py is explicitly scoped to
"what db.py doesn't [already provide]": HTTP helpers and logging, not math
helpers), and moneypuck.py has never imported anything PWHL-side either.
Introducing shared-module coupling in one direction only, for ~15 lines of
pure math, seemed like a bigger footprint than porting it — flagged here in
case a future session wants to hoist it out once a real shared-utils module
exists.

Categories computed (4, matching what's actually buildable from ingested
data — no MoneyPuck-equivalent play-by-play xG model exists for PWHL):
  pct_goals      — goals per 60 minutes of average TOI
  pct_a1         — primary assists per 60 (pwhl_shot_events.assist1_id on
                   goal rows -- PWHL's gameSummary merge tracks assist1 vs
                   assist2 distinctly, confirmed via pwhl_shot_events.py)
  pct_penalties  — -PIM per 60 (negative PIM = good, mirrors moneypuck.py's
                   NHL pct_penalties exactly)
  pct_finishing  — piece 2's season-total `finishing` (goals - xg_for),
                   rated per 60

Position split: pwhl_players.position (F/D/G broad taxonomy, written by
pwhl_stats.py's fetch_roster()/fetch_skater_stats()) — NOT
pwhl_skater_game_box.position_group, which is a separate, more granular,
per-game-box-specific field. Goalies (position == "G") get no percentiles,
same as NHL.

MIN_GP = 10, same value as moneypuck.py's NHL MIN_GP. A prior investigation
this session found PWHL currently has ~168 GP>=10-qualified skaters (100 F /
68 D) for the current season -- a much smaller pool than NHL's, but still
workable for a 0-100 percentile scale; using a smaller threshold wasn't
judged necessary for v1, and NHL's own value is a reasonable, well-
understood starting point rather than an arbitrary NHL-specific artifact
(unlike the TOI-minute floor below).

Deliberately NOT ported: NHL's 250/330 TOI-minute display-gating floor
(icetime >= 300 in moneypuck.py's qualified-pool filter, similar spirit).
Those thresholds were derived from NHL's specific TOI distribution and
would be meaningless copy-pasted onto a league with different game lengths/
roster sizes/usage patterns. Deriving a PWHL-native equivalent (e.g. p10 of
the GP-qualified pool's own TOI distribution) is the right way to add one,
but is out of scope for this v1 pass -- shipping without a TOI floor and
flagging it here as a known gap, rather than silently skipping it.

KNOWN DATA GAP: pct_a1/pct_finishing depend on pwhl_shot_events having real
rows for a season/type -- season 9 (2025-26 Playoffs) currently has zero
(see pwhl_shot_xg.py's module docstring). This module detects that and
leaves both percentiles NULL for that season/type rather than computing a
misleading percentile from an all-zero pool.

Run modes:
    python pwhl_percentiles.py                  # current season (PWHL_SEASON)
    python pwhl_percentiles.py 8                # specific season_id
"""

import logging
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


def _resolve_season_type(season_id: str) -> str | None:
    return SEASON_TYPE_MAP.get(season_id) or get_season_type(season_id)


# ── Pure math helpers (ported from moneypuck.py — see module docstring) ──


def percentile_rank(value, sorted_pool: list) -> int | None:
    """Binary search percentile — O(log n). Identical to moneypuck.py's
    percentile_rank()."""
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


def build_sorted_pool(players: list, fn) -> list:
    """Identical to moneypuck.py's build_sorted_pool()."""
    vals = [fn(p) for p in players]
    vals = [v for v in vals if v is not None]
    return sorted(vals)


def per60(value, total_toi_seconds) -> float | None:
    """value per 60 minutes, given a total (not per-game) TOI in seconds.
    None if TOI is missing/zero -- distinguishes "genuinely 0 rate" from
    "no ice time to rate against"."""
    if not total_toi_seconds:
        return None
    return value / total_toi_seconds * 3600


# ── Data loading ──────────────────────────────────────────────────────────


def _fetch_player_seasons(sb, season_id: str, season_type: str) -> list:
    """All pwhl_player_seasons rows for this season/type. Bounded by league
    roster size (a few hundred rows/season) -- OFFSET pagination, same
    reasoning as moneypuck.py's RAPM-values load."""
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table("pwhl_player_seasons")
            .select("player_id,team_id,gp,goals,assists,pim,toi_per_game,finishing")
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


def _fetch_positions(sb, player_ids: list) -> dict:
    """player_id -> pwhl_players.position (F/D/G broad taxonomy). Chunked
    .in_() lookups -- player_ids can exceed a single query's practical IN-
    list size for a full league season."""
    positions: dict = {}
    ids = list(player_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        batch = (
            sb.table("pwhl_players").select("player_id,position").in_("player_id", chunk).execute()
        ).data
        for r in batch or []:
            positions[r["player_id"]] = r.get("position")
    return positions


def _fetch_primary_assist_totals(sb, season_id: str, season_type: str) -> tuple:
    """player_id -> count of primary assists (pwhl_shot_events.assist1_id on
    goal rows) this season/type. Returns (dict, has_data) -- has_data is
    False if there isn't a single pwhl_shot_events row for this season/type
    at all (the season-9-playoffs gap, see pwhl_shot_xg.py's docstring), so
    callers can tell "genuinely zero primary assists so far" apart from
    "no shot-event data exists yet" instead of treating an empty dict as if
    every player had 0 (which would rank everyone identically instead of
    leaving the percentile null)."""
    probe = (
        sb.table("pwhl_shot_events")
        .select("id")
        .eq("season_id", int(season_id))
        .eq("season_type", season_type)
        .limit(1)
        .execute()
        .data
    )
    if not probe:
        return {}, False

    counts: dict = defaultdict(int)
    last_id = 0
    while True:
        batch = (
            sb.table("pwhl_shot_events")
            .select("id,assist1_id")
            .eq("season_id", int(season_id))
            .eq("season_type", season_type)
            .eq("event_type", "goal")
            .not_.is_("assist1_id", "null")
            .gt("id", last_id)
            .order("id")
            .limit(999)
            .execute()
            .data
        )
        if not batch:
            break
        for r in batch:
            counts[r["assist1_id"]] += 1
        last_id = batch[-1]["id"]
        if len(batch) < 999:
            break
    return dict(counts), True


# ── Main computation ──────────────────────────────────────────────────────


def compute_percentiles(sb, season_id: str, season_type: str) -> None:
    log.info(f"Computing PWHL skater percentiles (season {season_id}, {season_type})...")

    seasons = _fetch_player_seasons(sb, season_id, season_type)
    if not seasons:
        log.warning(f"  No pwhl_player_seasons rows for season {season_id}/{season_type}")
        return

    positions = _fetch_positions(sb, [r["player_id"] for r in seasons])
    a1_totals, has_shot_data = _fetch_primary_assist_totals(sb, season_id, season_type)
    if not has_shot_data:
        log.warning(
            f"  No pwhl_shot_events rows for season {season_id}/{season_type} — "
            "pct_a1 will stay null for this season/type (expected for season "
            "9 playoffs as of this module's introduction; see "
            "pwhl_shot_xg.py's module docstring)"
        )

    # ── Per-player metric values ────────────────────────────────────────
    def total_toi_seconds(row) -> int:
        # toi_per_game is a Postgres bigint -- PostgREST serializes bigint/
        # numeric columns as JSON strings (precision safety), unlike the
        # plain `integer` columns (gp, goals, ...) this module reads
        # elsewhere. Cast explicitly: `toi * gp` on an unconverted string
        # is Python string-repetition, not multiplication, and blew up
        # downstream in per60() with "unsupported operand type(s) for /:
        # 'int' and 'str'" the first time this ever ran with a non-null
        # toi_per_game (caught 2026-07-20, once compute_toi_per_game()'s
        # own pagination bug was fixed and this path finally executed).
        toi = row.get("toi_per_game")
        gp = row.get("gp")
        if not toi or not gp:
            return 0
        return int(toi) * int(gp)

    def goals60(row):
        return per60(row.get("goals") or 0, total_toi_seconds(row))

    def a1_60(row):
        if not has_shot_data:
            return None
        a1 = a1_totals.get(row["player_id"], 0)
        return per60(a1, total_toi_seconds(row))

    def penalties60(row):
        tt = total_toi_seconds(row)
        if not tt:
            return None
        return -per60(row.get("pim") or 0, tt)

    def finishing60(row):
        # finishing is a Postgres `numeric` column -- same PostgREST
        # string-serialization behavior as toi_per_game above, cast for the
        # same reason.
        fin = row.get("finishing")
        if fin is None:
            return None
        return per60(float(fin), total_toi_seconds(row))

    # ── Qualified pools, split by position ──────────────────────────────
    qualified = [r for r in seasons if (r.get("gp") or 0) >= MIN_GP]
    fwds = [r for r in qualified if positions.get(r["player_id"]) == "F"]
    defs = [r for r in qualified if positions.get(r["player_id"]) == "D"]
    log.info(f"  Pool: {len(fwds)} forwards, {len(defs)} defensemen (min {MIN_GP} GP)")

    fwd_pools = {
        "goals": build_sorted_pool(fwds, goals60),
        "a1": build_sorted_pool(fwds, a1_60),
        "penalties": build_sorted_pool(fwds, penalties60),
        "finishing": build_sorted_pool(fwds, finishing60),
    }
    def_pools = {
        "goals": build_sorted_pool(defs, goals60),
        "a1": build_sorted_pool(defs, a1_60),
        "penalties": build_sorted_pool(defs, penalties60),
        "finishing": build_sorted_pool(defs, finishing60),
    }

    # ── Per-row percentiles ──────────────────────────────────────────────
    updates = []
    for row in seasons:
        pos = positions.get(row["player_id"])
        if pos not in ("F", "D"):
            continue  # goalies / unknown position get no percentiles

        pools = fwd_pools if pos == "F" else def_pools
        updates.append(
            {
                "player_id": row["player_id"],
                "team_id": row["team_id"],
                "season_id": int(season_id),
                "season_type": season_type,
                "pct_goals": percentile_rank(goals60(row), pools["goals"]),
                "pct_a1": percentile_rank(a1_60(row), pools["a1"]),
                "pct_penalties": percentile_rank(penalties60(row), pools["penalties"]),
                "pct_finishing": percentile_rank(finishing60(row), pools["finishing"]),
            }
        )

    log.info(f"  Computed percentiles for {len(updates)} skaters")
    _upsert_defensive(sb, updates)


def _upsert_defensive(sb, updates: list) -> None:
    """Merge-upsert pct_goals/pct_a1/pct_penalties/pct_finishing onto
    pwhl_player_seasons, tolerant of those columns not existing yet in the
    live schema (this repo has no migration tooling — see PR description
    for the exact ALTER TABLE). If the upsert 400s because a column is
    missing, log loudly and skip rather than crash the whole nightly run."""
    if not updates:
        return
    try:
        for i in range(0, len(updates), 200):
            chunk = updates[i : i + 200]
            sb.table("pwhl_player_seasons").upsert(
                chunk, on_conflict="player_id,team_id,season_id,season_type"
            ).execute()
        log.info(f"  {len(updates)} player season rows updated with percentiles")
    except Exception as e:
        log.error(
            f"  Percentile upsert FAILED — likely missing columns on "
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
    log.info(f"=== PWHL skater percentiles — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    compute_percentiles(sb, season_id, season_type)
    log.info("=== PWHL skater percentiles complete ===")


if __name__ == "__main__":
    args = sys.argv[1:]
    run(args[0] if args else None)
