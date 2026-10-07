"""
hockeytech_percentiles.py -- position-split (F/D) skater percentile ranks for
every HockeyTech league (PWHL, AHL, ECHL); the HockeyTech analogue of
moneypuck.py's NHL `pct_*` columns. pwhl_percentiles.py is a thin wrapper.

Four categories, the ones the ingested data supports in every league:
  pct_goals      goals, as a rate
  pct_a1         primary assists ({league}_shot_events.assist1_id on goal
                 rows), as a rate
  pct_penalties  -PIM, as a rate (negative PIM = good, as moneypuck.py)
  pct_finishing  finishing (goals - xG, hockeytech_shot_xg.py), as a rate

Pools: skaters with at least MIN_GP games (same value as moneypuck.py's NHL
MIN_GP), split by position -- forwards against forwards, defence against
defence. Goalies get none here (hockeytech_goalie_percentiles.py).

Rates differ by league (League.toi_rates, hockeytech_leagues.py):

PWHL (toi_rates=True) -- per 60 minutes of ice time
  (pwhl_player_seasons.toi_per_game x gp, from pwhl_stats.py
  --toi-rollup-only), pct_* merged onto pwhl_player_seasons. Unchanged from
  pwhl_percentiles.py; pinned by test_pwhl_percentiles_characterization.py.
  Primary assists are counted per player (a traded player's whole-season
  count on each team row), as they always were.

AHL/ECHL (toi_rates=False) -- per game played: their box scores have no
  skater TOI at all (hockeytech_game_boxscore.py). Rows go to
  {league}_player_percentiles, one per {league}_player_seasons row with a
  F/D position, with the same columns the PWHL percentile route reads off
  pwhl_player_seasons (toi_per_game stays NULL -- there is none) and
  rate_basis_per_gp = true, so the Worker can label the rates per game.
  Primary assists and finishing are per (player, team), so a traded
  player's rate on each team row only counts what he did there. Position
  comes from {league}_players.position (C/LW/RW -> F, LD/RD/D -> D).
  Migration: docs/2026-10-07_hockeytech_percentiles.sql; until it's been run
  the upsert fails, is logged once, and nothing else breaks.

Run after the stats sweep, the shot-event ingest and hockeytech_shot_xg.py
(the ahl/echl nightlies do).

Run modes (AHL/ECHL; PWHL runs through pwhl_percentiles.py):
    python hockeytech_percentiles.py ahl          # current season
    python hockeytech_percentiles.py echl 78      # specific season_id
"""

import argparse
import logging
import os
from collections import defaultdict

from dotenv import load_dotenv
from supabase import create_client

from hockeytech_leagues import League
from hockeytech_shot_xg import LEAGUES, TableWriter, resolve_season
from pipeline_common import select_all

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

MIN_GP = 10  # see module docstring

# HockeyTech's granular roster positions -> the F/D pools (same groups
# hockeytech_game_boxscore.py's POSITION_GROUP_MAP uses).
POSITION_GROUP = {
    "C": "F",
    "LW": "F",
    "RW": "F",
    "F": "F",
    "LD": "D",
    "RD": "D",
    "D": "D",
    "G": "G",
}


# ── Pure math helpers (ported from moneypuck.py) ─────────────────────────


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


def per_gp(value, gp) -> float | None:
    """value per game played; None without games (not a 0 rate)."""
    if value is None or not gp:
        return None
    return value / gp


# ── Data loading ──────────────────────────────────────────────────────────


def _fetch_player_seasons(lg: League, sb, season_id: str, season_type: str, columns: str) -> list:
    """All {league}_player_seasons rows for this season/type. Bounded by
    league roster size -- OFFSET pagination."""
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table(f"{lg.key}_player_seasons")
            .select(columns)
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


def _fetch_positions(lg: League, sb, player_ids: list) -> dict:
    """player_id -> {league}_players.position, in chunked .in_() lookups."""
    positions: dict = {}
    ids = list(player_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        batch = (
            sb.table(f"{lg.key}_players")
            .select("player_id,position")
            .in_("player_id", chunk)
            .execute()
        ).data
        for r in batch or []:
            positions[r["player_id"]] = r.get("position")
    return positions


def _fetch_primary_assists(lg: League, sb, season_id: str, season_type: str, by_team: bool):
    """Primary-assist counts this season/type, keyed by player_id (or by
    (player_id, team_id) when `by_team`; a goal row's team_id is the scoring
    team, the assister's own). Returns (counts, has_data): has_data is False
    when the season has no shot-event rows at all, so callers can leave
    pct_a1 null instead of ranking everyone on an all-zero pool."""
    probe = (
        sb.table(f"{lg.key}_shot_events")
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
    columns = "id,assist1_id,team_id" if by_team else "id,assist1_id"
    while True:
        batch = (
            sb.table(f"{lg.key}_shot_events")
            .select(columns)
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
            counts[(r["assist1_id"], r["team_id"]) if by_team else r["assist1_id"]] += 1
        last_id = batch[-1]["id"]
        if len(batch) < 999:
            break
    return dict(counts), True


def _fetch_xg(lg: League, sb, season_id: str, season_type: str) -> dict:
    """(player_id, team_id) -> {league}_player_xg row (AHL/ECHL). Empty if
    the table doesn't exist yet -- finishing then stays null."""
    try:
        rows = select_all(
            lambda: (
                sb.table(f"{lg.key}_player_xg")
                .select("player_id,team_id,xg_for,finishing")
                .eq("season_id", int(season_id))
                .eq("season_type", season_type)
            ),
            order="player_id",
        )
    except Exception as e:
        log.warning(
            f"  Could not read {lg.key}_player_xg ({type(e).__name__}: {e}) -- "
            "pct_finishing stays null"
        )
        return {}
    return {(r["player_id"], r["team_id"]): r for r in rows}


# ── PWHL (per 60) ─────────────────────────────────────────────────────────


def _compute_toi_league(lg: League, sb, season_id: str, season_type: str) -> None:
    seasons = _fetch_player_seasons(
        lg,
        sb,
        season_id,
        season_type,
        "player_id,team_id,gp,goals,assists,pim,toi_per_game,finishing",
    )
    if not seasons:
        log.warning(f"  No {lg.key}_player_seasons rows for season {season_id}/{season_type}")
        return

    positions = _fetch_positions(lg, sb, [r["player_id"] for r in seasons])
    a1_totals, has_shot_data = _fetch_primary_assists(lg, sb, season_id, season_type, False)
    if not has_shot_data:
        log.warning(
            f"  No {lg.key}_shot_events rows for season {season_id}/{season_type} — "
            "pct_a1 will stay null for this season/type"
        )

    def total_toi_seconds(row) -> int:
        # toi_per_game is a Postgres bigint -- PostgREST serializes bigint/
        # numeric columns as JSON strings, unlike the plain `integer`
        # columns (gp, goals, ...). Cast explicitly: `toi * gp` on an
        # unconverted string is string repetition (caught 2026-07-20).
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
        # numeric column -- string from PostgREST, cast as above.
        fin = row.get("finishing")
        if fin is None:
            return None
        return per60(float(fin), total_toi_seconds(row))

    rates = {"goals": goals60, "a1": a1_60, "penalties": penalties60, "finishing": finishing60}
    updates = []
    for row, pct in _rank(seasons, positions, rates):
        updates.append(
            {
                "player_id": row["player_id"],
                "team_id": row["team_id"],
                "season_id": int(season_id),
                "season_type": season_type,
                **pct,
            }
        )

    log.info(f"  Computed percentiles for {len(updates)} skaters")
    _upsert_onto_player_seasons(lg, sb, updates)


def _rank(seasons: list, positions: dict, rates: dict):
    """(row, {pct_<key>: rank}) for every F/D row, ranked against the
    MIN_GP-qualified rows of the same position."""
    qualified = [r for r in seasons if (r.get("gp") or 0) >= MIN_GP]
    pools = {}
    for pos in ("F", "D"):
        group = [r for r in qualified if positions.get(r["player_id"]) == pos]
        pools[pos] = {key: build_sorted_pool(group, fn) for key, fn in rates.items()}
    log.info(
        f"  Pool: {sum(1 for r in qualified if positions.get(r['player_id']) == 'F')} forwards, "
        f"{sum(1 for r in qualified if positions.get(r['player_id']) == 'D')} defensemen "
        f"(min {MIN_GP} GP)"
    )

    for row in seasons:
        pos = positions.get(row["player_id"])
        if pos not in ("F", "D"):
            continue  # goalies / unknown position get no percentiles
        yield (
            row,
            {f"pct_{key}": percentile_rank(fn(row), pools[pos][key]) for key, fn in rates.items()},
        )


def _upsert_onto_player_seasons(lg: League, sb, updates: list) -> None:
    """Merge-upsert pct_* onto {league}_player_seasons, tolerant of those
    columns not existing in the live schema: log loudly and skip rather
    than crash the whole nightly run."""
    if not updates:
        return
    try:
        for i in range(0, len(updates), 200):
            chunk = updates[i : i + 200]
            sb.table(f"{lg.key}_player_seasons").upsert(
                chunk, on_conflict="player_id,team_id,season_id,season_type"
            ).execute()
        log.info(f"  {len(updates)} player season rows updated with percentiles")
    except Exception as e:
        log.error(
            f"  Percentile upsert FAILED — likely missing columns on "
            f"{lg.key}_player_seasons: {type(e).__name__}: {e}"
        )


# ── AHL/ECHL (per game played) ───────────────────────────────────────────


def _compute_per_gp_league(lg: League, sb, season_id: str, season_type: str) -> None:
    seasons = _fetch_player_seasons(
        lg, sb, season_id, season_type, "player_id,team_id,gp,goals,pim"
    )
    # team_id is part of the target's key, and NULL never matches one.
    seasons = [r for r in seasons if r.get("team_id") is not None]
    if not seasons:
        log.warning(f"  No {lg.key}_player_seasons rows for season {season_id}/{season_type}")
        return

    raw_positions = _fetch_positions(lg, sb, [r["player_id"] for r in seasons])
    positions = {pid: POSITION_GROUP.get((p or "").upper()) for pid, p in raw_positions.items()}
    a1_totals, has_shot_data = _fetch_primary_assists(lg, sb, season_id, season_type, True)
    if not has_shot_data:
        log.warning(
            f"  No {lg.key}_shot_events rows for season {season_id}/{season_type} — "
            "pct_a1 stays null"
        )
    xg = _fetch_xg(lg, sb, season_id, season_type)

    def key(row):
        return (row["player_id"], row["team_id"])

    def finishing(row):
        fin = (xg.get(key(row)) or {}).get("finishing")
        return float(fin) if fin is not None else None

    def goals_gp(row):
        return per_gp(row.get("goals") or 0, row.get("gp"))

    def a1_gp(row):
        if not has_shot_data:
            return None
        return per_gp(a1_totals.get(key(row), 0), row.get("gp"))

    def penalties_gp(row):
        rate = per_gp(row.get("pim") or 0, row.get("gp"))
        return -rate if rate is not None else None

    def finishing_gp(row):
        return per_gp(finishing(row), row.get("gp"))

    rates = {"goals": goals_gp, "a1": a1_gp, "penalties": penalties_gp, "finishing": finishing_gp}
    rows = []
    for row, pct in _rank(seasons, positions, rates):
        x = xg.get(key(row)) or {}
        rows.append(
            {
                "player_id": row["player_id"],
                "team_id": row["team_id"],
                "season_id": int(season_id),
                "season_type": season_type,
                "gp": row.get("gp") or 0,
                "xg_for": float(x["xg_for"]) if x.get("xg_for") is not None else None,
                "finishing": finishing(row),
                **pct,
                "rate_basis_per_gp": True,
            }
        )
    rows.sort(key=lambda r: (r["player_id"], r["team_id"] or 0))
    log.info(f"  Computed percentiles for {len(rows)} skaters")
    writer = TableWriter(
        sb, f"{lg.key}_player_percentiles", "player_id,team_id,season_id,season_type"
    )
    n = writer.write(rows)
    log.info(f"  {n} {lg.key}_player_percentiles rows upserted")


def compute_percentiles(lg: League, sb, season_id: str, season_type: str) -> None:
    log.info(f"Computing {lg.label} skater percentiles (season {season_id}, {season_type})...")
    if lg.toi_rates:
        _compute_toi_league(lg, sb, season_id, season_type)
    else:
        _compute_per_gp_league(lg, sb, season_id, season_type)


def run(lg: League, season_id: str | None = None) -> None:
    season_id, season_type = resolve_season(lg, season_id)
    log.info(f"=== {lg.label} skater percentiles — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    compute_percentiles(lg, sb, season_id, season_type)
    log.info(f"=== {lg.label} skater percentiles complete ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="AHL/ECHL skater percentiles (per game played)")
    parser.add_argument("league", choices=sorted(LEAGUES))
    parser.add_argument("season_id", nargs="?", default=None)
    args = parser.parse_args()
    run(LEAGUES[args.league], args.season_id or None)


if __name__ == "__main__":
    main()
