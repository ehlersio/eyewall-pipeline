"""
hockeytech_goalie_percentiles.py -- goalie GSAx proxy and danger-zone save
percentages, with percentile ranks, for every HockeyTech league (PWHL, AHL,
ECHL). pwhl_goalie_percentiles.py is a thin wrapper.

The NHL /goalie-analytics shape -- GSAx, GSAx rate, 5v5 SV%, high- and
medium-danger SV%, PK SV% -- from the shots each goalie faced:
  gsax       xG of every shot faced (goals included, valued by location)
             minus goals allowed; positive = saving more than expected
  HD/MD SV%  save % on shots from the <=15 / 15-30 unit distance buckets
             (the same buckets as hockeytech_shot_xg.py)
  5v5/PK SV% save % with no penalty running / with the goalie's team short
Pool: goalies with at least MIN_GP games (as the skater side); goalies below
it get no row.

PWHL (League.toi_rates) -- unchanged from pwhl_goalie_percentiles.py,
  pinned by test_pwhl_percentiles_characterization.py. Goals carry their
  goalie_id; shots faced are goal + shot (a blocked shot never reaches the
  goalie); bucket values are the calibrated DANGER_XG_FACED; the GSAx rate
  is per 60 minutes of pwhl_goalie_seasons.toi; strength comes from
  pwhl_pbp_events penalty windows (pwhl_strength_state.py). Merged onto
  pwhl_goalie_seasons.

AHL/ECHL -- written to {league}_goalie_percentiles with the same columns
  the PWHL route reads off pwhl_goalie_seasons, plus rate_basis_per_gp =
  true: gsax_per60 / pct_gsax60 hold the per-game-played rate (gsax / gp),
  matching the skater side, where there is no TOI at all.
  * Bucket values come from the same season's own faced shots
    (hockeytech_shot_xg.bucket_rates), so league GSAx sums to zero.
  * Their goal rows carry no goalie (hockeytech_shot_events.py: only the
    `shot` twin of a goal names him, and that twin isn't stored). A goal is
    credited to the defending team's only goalie in {league}_goalie_game_box
    who allowed a goal that game; with two such goalies (a mid-game change),
    to the goalie the scoring team's nearest shot in the same period faced.
    Empty-net goals, shootout attempts and penalty shots aren't shots faced.
  * 5v5 and PK SV% stay NULL: shot rows carry no strength state, and their
    penalties aren't ingested yet (the PBP has them -- a
    {league}_pbp_events port would unlock both).
  Migration: docs/2026-10-07_hockeytech_percentiles.sql; until it's been
  run the upsert fails, is logged once, and nothing else breaks.

Run modes (AHL/ECHL; PWHL runs through pwhl_goalie_percentiles.py):
    python hockeytech_goalie_percentiles.py ahl          # current season
    python hockeytech_goalie_percentiles.py echl 78      # specific season_id
"""

import argparse
import logging
import os
from collections import defaultdict

from dotenv import load_dotenv
from supabase import create_client

from hockeytech_leagues import League
from hockeytech_percentiles import build_sorted_pool, percentile_rank
from hockeytech_shot_xg import (
    LEAGUES,
    TableWriter,
    bucket_rates,
    danger_bucket,
    fetch_located_attempts,
    resolve_season,
)
from pipeline_common import select_all

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

MIN_GP = 10  # see module docstring

# PWHL: each bucket's goal rate over goal + shot events in pwhl_shot_events
# (seasons 1/3/5/6/8/9, re-checked 2026-09): high 592/4001 = 0.148, medium
# 503/4978 = 0.101, low 390/9038 = 0.0432. hockeytech_shot_xg.DANGER_XG's
# all-attempts rates include blocked shots in the denominator and would
# understate a shot that got through.
DANGER_XG_FACED = {
    "high": 0.148,
    "medium": 0.101,
    "low": 0.043,
}

# A goalie only ever faces shots that reach him -- "goal" and "shot". A
# blocked shot never does (counting it inflated PWHL shots faced by ~20%
# once, caught 2026-08 against pwhl_goalie_seasons.sv_pct).
GOALIE_FACED_TYPES = ("goal", "shot")

METRICS = ("gsax", "gsax60", "ev_sv", "hd_sv", "md_sv", "pk_sv")


def _danger_bucket(x, y) -> str:
    return danger_bucket(x, y)


def _shot_xg(event_type: str, x, y) -> float:
    """PWHL per-shot xG faced: the bucket's DANGER_XG_FACED value. A goal is
    valued by its location, not 1.0 (that made GSAx "xG of the saves",
    always positive -- fixed 2026-09)."""
    return DANGER_XG_FACED[_danger_bucket(x, y)]


def _toi_minutes(toi_str: str | None) -> float | None:
    """{league}_goalie_seasons.toi is an "MM:SS" string (e.g. "1643:15").
    Total minutes as a float, or None if missing/malformed."""
    if not toi_str or ":" not in toi_str:
        return None
    try:
        m, s = toi_str.split(":")
        return int(m) + int(s) / 60
    except ValueError:
        return None


def sv_pct(shots: int, goals: int) -> float | None:
    return None if shots == 0 else round(1 - goals / shots, 4)


def _new_agg() -> dict:
    return {
        "shots": 0,
        "goals": 0,
        "xg": 0.0,
        "ev_shots": 0,
        "ev_goals": 0,
        "pk_shots": 0,
        "pk_goals": 0,
        "danger": defaultdict(lambda: {"shots": 0, "goals": 0}),
    }


def _ranked_rows(metrics: dict) -> dict:
    """{key: {pct_*}} -- each metric ranked against the qualified pool."""
    pools = {m: build_sorted_pool(list(metrics), lambda k, m=m: metrics[k][m]) for m in METRICS}
    return {
        k: {f"pct_{m}": percentile_rank(v[m], pools[m]) for m in METRICS}
        for k, v in metrics.items()
    }


# ── PWHL (per 60) ─────────────────────────────────────────────────────────


def _fetch_shot_events_against(lg: League, sb, season_id: str, season_type: str) -> list:
    """Every goal/shot this season with a goalie_id, keyset-paged on id."""
    rows = []
    last_id = 0
    while True:
        batch = (
            sb.table(f"{lg.key}_shot_events")
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


def _fetch_goalie_seasons(
    lg: League, sb, season_id: str, season_type: str, columns: str = "player_id,team_id,gp,toi"
) -> list:
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table(f"{lg.key}_goalie_seasons")
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


def active_penalized_teams(game_windows: dict, game_id, period_id, time_seconds: int) -> set:
    """Every team_id with an active penalty window at this exact moment.
    Empty set = 5v5. A non-empty set containing some team OTHER than the
    shooter's own means the goalie's team is shorthanded -- in a 2-team game
    the only other team that can be penalized is the goalie's, and
    coincidental minors never build a window (pwhl_strength_state.py)."""
    return {
        team_id
        for team_id, p_period, start, end in game_windows.get(game_id, [])
        if p_period == period_id and start <= time_seconds < end
    }


def _compute_toi_league(lg: League, sb, season_id: str, season_type: str) -> None:
    # PWHL only: its penalties are in pwhl_pbp_events.
    from pwhl_strength_state import get_penalties_for_season
    from pwhl_strength_state import penalty_window as _penalty_window

    goalie_seasons = _fetch_goalie_seasons(lg, sb, season_id, season_type)
    if not goalie_seasons:
        log.warning(f"  No {lg.key}_goalie_seasons rows for season {season_id}/{season_type}")
        return

    events = _fetch_shot_events_against(lg, sb, season_id, season_type)
    if not events:
        log.warning(
            f"  No {lg.key}_shot_events rows with a goalie_id for season "
            f"{season_id}/{season_type} — nothing to compute"
        )
        return

    penalties = get_penalties_for_season(sb, int(season_id), season_type)
    game_windows = defaultdict(list)
    for p in penalties:
        w = _penalty_window(p)
        if w is None:  # outside regulation (OT) — see pwhl_strength_state.py
            continue
        period_id, start, end = w
        game_windows[p["game_id"]].append((p["team_id"], period_id, start, end))

    agg: dict = defaultdict(_new_agg)
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
                a["pk_shots"] += 1
                a["pk_goals"] += 1 if is_goal else 0

    gp_by_player = {r["player_id"]: r.get("gp") or 0 for r in goalie_seasons}
    team_by_player = {r["player_id"]: r["team_id"] for r in goalie_seasons}
    toi_by_player = {r["player_id"]: _toi_minutes(r.get("toi")) for r in goalie_seasons}

    qualified_ids = [pid for pid, gp in gp_by_player.items() if gp >= MIN_GP and pid in agg]
    log.info(
        f"  Pool: {len(qualified_ids)} goalies (min {MIN_GP} GP, of {len(goalie_seasons)} total)"
    )

    metrics = {}
    for pid in qualified_ids:
        a = agg[pid]
        gsax = round(a["xg"] - a["goals"], 3)
        toi = toi_by_player.get(pid)
        metrics[pid] = {
            "gsax": gsax,
            "gsax60": round(gsax / (toi / 60), 3) if toi else None,
            "ev_sv": sv_pct(a["ev_shots"], a["ev_goals"]),
            "hd_sv": sv_pct(a["danger"]["high"]["shots"], a["danger"]["high"]["goals"]),
            "md_sv": sv_pct(a["danger"]["medium"]["shots"], a["danger"]["medium"]["goals"]),
            "pk_sv": sv_pct(a["pk_shots"], a["pk_goals"]),
        }
    ranks = _ranked_rows(metrics)

    updates = []
    for pid in qualified_ids:
        m = metrics[pid]
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
                **ranks[pid],
            }
        )

    log.info(f"  Computed percentiles for {len(updates)} goalies")
    _upsert_onto_goalie_seasons(lg, sb, updates)


def _upsert_onto_goalie_seasons(lg: League, sb, updates: list) -> None:
    """Merge-upsert onto {league}_goalie_seasons, tolerant of the columns
    not existing in the live schema: log loudly and skip."""
    if not updates:
        return
    try:
        for i in range(0, len(updates), 200):
            chunk = updates[i : i + 200]
            sb.table(f"{lg.key}_goalie_seasons").upsert(
                chunk, on_conflict="player_id,team_id,season_id,season_type"
            ).execute()
        log.info(f"  {len(updates)} goalie season rows updated with percentiles")
    except Exception as e:
        log.error(
            f"  Goalie percentile upsert FAILED — likely missing columns on "
            f"{lg.key}_goalie_seasons: {type(e).__name__}: {e}"
        )


# ── AHL/ECHL (per game played) ───────────────────────────────────────────


def _goal_goalie(goal: dict, candidates: list, shots: list):
    """The goalie a goal (no goalie_id) was scored on. `candidates`: the
    defending team's goalies who allowed a goal that game per the box score;
    `shots`: the scoring team's shots that game as (period, time, goalie),
    sorted. One candidate settles it. Otherwise the goalie the nearest shot
    faced -- the latest one at or before the goal in the same period, else
    the next one after it in that period, else the nearest in the game --
    limited to the candidates when there are any."""
    if len(candidates) == 1:
        return candidates[0]
    usable = [s for s in shots if not candidates or s[2] in candidates]
    if not usable:
        return None
    at = (goal.get("period_id") or 0, goal.get("time_seconds") or 0)
    before = [s for s in usable if (s[0], s[1]) <= at]
    after = [s for s in usable if (s[0], s[1]) > at]
    same_before = [s for s in before if s[0] == at[0]]
    same_after = [s for s in after if s[0] == at[0]]
    for pick in (same_before[-1:], same_after[:1], before[-1:], after[:1]):
        if pick:
            return pick[0][2]
    return None


def attribute_shots_faced(events: list, games: list, box: list) -> list:
    """(goalie_id, defending team_id, event) for every located goal/shot a
    goalie faced. Shots name their goalie; goals are credited by
    _goal_goalie(). Empty-net goals and events whose game or team can't be
    placed are dropped."""
    sides = {g["game_id"]: (g.get("home_team_id"), g.get("away_team_id")) for g in games}
    conceded: dict = defaultdict(list)
    for b in box:
        if (b.get("goals_against") or 0) > 0:
            conceded[(b["game_id"], b["team_id"])].append(b["player_id"])
    shots: dict = defaultdict(list)
    for e in events:
        if e["event_type"] == "shot" and e.get("goalie_id") is not None:
            shots[(e["game_id"], e.get("team_id"))].append(
                (e.get("period_id") or 0, e.get("time_seconds") or 0, e["goalie_id"])
            )
    for v in shots.values():
        v.sort()

    faced = []
    for e in events:
        side = sides.get(e["game_id"])
        team = e.get("team_id")
        if not side or team not in side:
            continue
        defending = side[1] if team == side[0] else side[0]
        if e["event_type"] == "shot":
            goalie = e.get("goalie_id")
        elif e.get("is_empty_net"):
            continue
        else:
            goalie = e.get("goalie_id") or _goal_goalie(
                e,
                sorted(conceded.get((e["game_id"], defending), [])),
                shots.get((e["game_id"], team), []),
            )
        if goalie is not None:
            faced.append((goalie, defending, e))
    return faced


def _compute_per_gp_league(lg: League, sb, season_id: str, season_type: str) -> None:
    goalie_seasons = _fetch_goalie_seasons(lg, sb, season_id, season_type, "player_id,team_id,gp")
    if not goalie_seasons:
        log.warning(f"  No {lg.key}_goalie_seasons rows for season {season_id}/{season_type}")
        return

    events = fetch_located_attempts(
        lg, sb, season_id, season_type, "game_id,team_id,goalie_id,time_seconds,is_empty_net"
    )
    if not events:
        log.warning(f"  No located shot events for season {season_id}/{season_type}")
        return
    games = select_all(
        lambda: (
            sb.table(f"{lg.key}_game_log")
            .select("game_id,home_team_id,away_team_id")
            .eq("season_id", int(season_id))
        )
    )
    box = select_all(
        lambda: (
            sb.table(f"{lg.key}_goalie_game_box")
            .select("game_id,player_id,team_id,goals_against")
            .eq("season_id", int(season_id))
        )
    )

    faced = attribute_shots_faced(events, games, box)
    rates = bucket_rates([e for _, _, e in faced])
    unplaced = sum(1 for e in events if e["event_type"] == "goal" and not e.get("is_empty_net"))
    unplaced -= sum(1 for _, _, e in faced if e["event_type"] == "goal")
    if unplaced:
        log.info(f"  {unplaced} goal(s) with no goalie to credit left out")

    agg: dict = defaultdict(_new_agg)
    for goalie, team, e in faced:
        is_goal = e["event_type"] == "goal"
        bucket = danger_bucket(e["x_norm"], e["y_norm"])
        a = agg[(goalie, team)]
        a["shots"] += 1
        a["goals"] += 1 if is_goal else 0
        a["xg"] += rates[bucket]
        a["danger"][bucket]["shots"] += 1
        a["danger"][bucket]["goals"] += 1 if is_goal else 0

    gp = {
        (r["player_id"], r["team_id"]): r.get("gp") or 0
        for r in goalie_seasons
        if r.get("team_id") is not None
    }
    qualified = sorted(k for k, n in gp.items() if n >= MIN_GP and k in agg)
    log.info(f"  Pool: {len(qualified)} goalies (min {MIN_GP} GP, of {len(goalie_seasons)} total)")

    metrics = {}
    for k in qualified:
        a = agg[k]
        gsax = round(a["xg"] - a["goals"], 3)
        metrics[k] = {
            "gsax": gsax,
            "gsax60": round(gsax / gp[k], 3),
            "ev_sv": None,
            "hd_sv": sv_pct(a["danger"]["high"]["shots"], a["danger"]["high"]["goals"]),
            "md_sv": sv_pct(a["danger"]["medium"]["shots"], a["danger"]["medium"]["goals"]),
            "pk_sv": None,
        }
    ranks = _ranked_rows(metrics)

    rows = []
    for k in qualified:
        m = metrics[k]
        rows.append(
            {
                "player_id": k[0],
                "team_id": k[1],
                "season_id": int(season_id),
                "season_type": season_type,
                "gp": gp[k],
                "gsax": m["gsax"],
                "gsax_per60": m["gsax60"],
                "ev_sv_pct": None,
                "hd_sv_pct": m["hd_sv"],
                "md_sv_pct": m["md_sv"],
                "pk_sv_pct": None,
                **ranks[k],
                "rate_basis_per_gp": True,
            }
        )
    log.info(f"  Computed percentiles for {len(rows)} goalies")
    writer = TableWriter(
        sb, f"{lg.key}_goalie_percentiles", "player_id,team_id,season_id,season_type"
    )
    n = writer.write(rows)
    log.info(f"  {n} {lg.key}_goalie_percentiles rows upserted")


def compute_goalie_percentiles(lg: League, sb, season_id: str, season_type: str) -> None:
    log.info(f"Computing {lg.label} goalie percentiles (season {season_id}, {season_type})...")
    if lg.toi_rates:
        _compute_toi_league(lg, sb, season_id, season_type)
    else:
        _compute_per_gp_league(lg, sb, season_id, season_type)


def run(lg: League, season_id: str | None = None) -> None:
    season_id, season_type = resolve_season(lg, season_id)
    log.info(f"=== {lg.label} goalie percentiles — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    compute_goalie_percentiles(lg, sb, season_id, season_type)
    log.info(f"=== {lg.label} goalie percentiles complete ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="AHL/ECHL goalie percentiles (per game played)")
    parser.add_argument("league", choices=sorted(LEAGUES))
    parser.add_argument("season_id", nargs="?", default=None)
    args = parser.parse_args()
    run(LEAGUES[args.league], args.season_id or None)


if __name__ == "__main__":
    main()
