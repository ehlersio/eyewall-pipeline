"""
line_combinations.py — Infer forward lines and defence pairs for every NHL
team from shift co-occurrence.

For each team's games this season, finds which skaters shared 5v5 ice time
together, accumulates xGF/xGA during those shared shifts, and writes the top
line combinations to the `line_combinations` Supabase table.

Algorithm (per team):
  1. Load all of the team's 5v5 shift events for the season (team=<abbr>).
  2. Look up the team's game_ids for the season from `game_log`, then load
     5v5 shot events (situation_code='1551') for those games.
  3. Per game, find every pair of the team's skaters whose shifts overlapped,
     compute seconds of shared ice.
  4. Aggregate pairs across all games -> player_a, player_b, toi_secs_together.
  5. Cluster pairs into line triplets (forwards) and D pairs using a greedy
     TOI-weighted grouping: the two teammates each player spends the most time
     with form their "line unit".
  6. Compute xGF/xGA/xGF% for each unit using the shot events that fell within
     the shared shift windows.
  7. Enrich with player names from the `players` table.
  8. Upsert into `line_combinations`.

Scope:
  - All 32 NHL teams (loop; pass --team for a single team).
  - 5v5 only (situation_code = '1551').
  - Forwards only for line triplets; defenders for D pairs.
  - Minimum 60 seconds shared TOI to be counted as a pair (filters noise).
  - Minimum 300 seconds shared TOI for a unit to be surfaced in the UI.

Shot events are fetched by this team's own game_id list (via `game_log`),
not the `shot_events.car_game` flag -- that flag only ever marks games CAR
played in, so it can't be reused as a per-team filter (special_teams.py hit
this same trap: its per-team shot fetch is still silently CAR-scoped, see
that file's `fetch_pp_shots_for_team` docstring -- not fixed here, out of
scope for this change).

Usage:
  python line_combinations.py                # current season, all 32 teams
  python line_combinations.py 20242025        # specific season, all 32 teams
  python line_combinations.py --team CAR      # one team only
  python line_combinations.py --dry-run       # compute and print, skip DB writes
  python run.py lines                         # via orchestrator

Run order: after shift_data and shot_events (both must be populated).
"""

import argparse
import math
from collections import defaultdict

from db import NHL_SEASON, get_client

MIN_PAIR_SECS = 60  # ignore pairs with < 1 min shared ice (noise)
MIN_UNIT_SECS = 300  # a unit must have 5+ min together to surface in UI

ALL_TEAMS = [
    "ANA",
    "BOS",
    "BUF",
    "CAR",
    "CBJ",
    "CGY",
    "CHI",
    "COL",
    "DAL",
    "DET",
    "EDM",
    "FLA",
    "LAK",
    "MIN",
    "MTL",
    "NJD",
    "NSH",
    "NYI",
    "NYR",
    "OTT",
    "PHI",
    "PIT",
    "SEA",
    "SJS",
    "STL",
    "TBL",
    "TOR",
    "UTA",
    "VAN",
    "VGK",
    "WPG",
    "WSH",
]


# xG proxy (mirrors rapm.py — no MoneyPuck xG stored per shot event)
def shot_xg(event_type, x, y):
    if event_type == "goal":
        return 1.0
    if event_type not in ("shot-on-goal", "missed-shot", "blocked-shot"):
        return 0.0
    dist = math.sqrt((abs(x or 0) - 89) ** 2 + (y or 0) ** 2)
    if dist <= 15:
        return 0.20
    if dist <= 30:
        return 0.07
    return 0.03


def fetch_all(client, table, select, filters, page_size=999, cursor_col="id"):
    """Cursor-based (keyset) Supabase fetch.

    Uses `id > last_seen_id` + ORDER BY id instead of OFFSET pagination.
    OFFSET cost grows with page depth (Postgres has to scan and discard
    everything before the offset on every request), which is what caused
    a `57014 statement timeout` on this exact table once CAR's season
    shift_events grew past ~30 pages deep (see 2026-07-04 nightly failure).
    Keyset pagination keeps each page's cost flat regardless of depth.
    """
    rows, last_val = [], 0
    cols = select if cursor_col in select.split(",") else f"{cursor_col},{select}"
    while True:
        q = client.table(table).select(cols)
        for col, val in filters.items():
            if isinstance(val, list):
                q = q.in_(col, val)
            else:
                q = q.eq(col, val)
        batch = q.gt(cursor_col, last_val).order(cursor_col).limit(page_size).execute().data
        if not batch:
            break
        rows.extend(batch)
        last_val = batch[-1][cursor_col]
        if len(batch) < page_size:
            break
    return rows


def mmss_to_secs(tip):
    """'14:32' -> 872"""
    try:
        parts = (tip or "0:00").split(":")
        return int(parts[0]) * 60 + int(parts[1] if len(parts) > 1 else 0)
    except Exception:
        return 0


PERIOD_OFFSETS = {1: 0, 2: 1200, 3: 2400, 4: 3600, 5: 4800}


def shot_abs_secs(shot):
    period = shot.get("period", 1) or 1
    return PERIOD_OFFSETS.get(period, (period - 1) * 1200) + mmss_to_secs(
        shot.get("time_in_period")
    )


# ── Pair-level TOI computation ────────────────────────────────────────────────


def compute_pair_toi(game_shifts):
    """
    Given a list of shift rows for one game (all of one team's skaters),
    return dict: frozenset({pid_a, pid_b}) -> overlap_secs.

    Two shifts overlap when one starts before the other ends:
        overlap = min(end_a, end_b) - max(start_a, start_b)
    """
    pairs = defaultdict(float)
    n = len(game_shifts)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = game_shifts[i], game_shifts[j]
            if a["player_id"] == b["player_id"]:  # same player, different shift rows
                continue
            overlap = min(a["end_secs"], b["end_secs"]) - max(a["start_secs"], b["start_secs"])
            if overlap > 0:
                key = frozenset({a["player_id"], b["player_id"]})
                pairs[key] += overlap
    return pairs


# ── Shot attribution to pair windows ─────────────────────────────────────────


def attribute_shots_to_pairs(game_shots, game_shifts, pair_toi, team):
    """
    For each shot in a game, determine which of `team`'s pairs were on ice
    together and attribute xGF or xGA to them.

    Returns dict: frozenset({pid_a, pid_b}) -> {'xgf': float, 'xga': float}
    """
    team_shifts = list(game_shifts)  # already team-only

    pair_xg = defaultdict(lambda: {"xgf": 0.0, "xga": 0.0})

    for shot in game_shots:
        xg = shot_xg(shot["event_type"], shot.get("x"), shot.get("y"))
        if xg == 0:
            continue
        t = shot_abs_secs(shot)
        is_team = shot["team"] == team
        key = "xgf" if is_team else "xga"

        # Find team players on ice at this moment
        on_ice = [s["player_id"] for s in team_shifts if s["start_secs"] <= t <= s["end_secs"]]

        # Credit all pairs that were on ice together
        for i in range(len(on_ice)):
            for j in range(i + 1, len(on_ice)):
                pair = frozenset({on_ice[i], on_ice[j]})
                if pair in pair_toi:
                    pair_xg[pair][key] += xg

    return pair_xg


# ── Player position lookup ────────────────────────────────────────────────────


def fetch_player_positions(client, player_ids):
    """Returns dict: player_id -> {'name': str, 'position': str}"""
    result = {}
    ids = list(player_ids)
    for i in range(0, len(ids), 200):
        batch = ids[i : i + 200]
        rows = client.table("players").select("id,name,position").in_("id", batch).execute().data
        for r in rows:
            result[r["id"]] = {"name": r["name"], "position": r["position"]}
    return result


# ── Line/pair clustering ──────────────────────────────────────────────────────


def cluster_into_units(pair_toi, positions, min_unit_secs):
    """
    Greedy line clustering:
      1. Filter pairs to only same-class players (F-F or D-D).
      2. For each player, find their two highest-TOI linemates (forwards)
         or one highest-TOI partner (defenders).
      3. Deduplicate: a triplet {a,b,c} is the same line regardless of order.
      4. Keep units with combined pair TOI >= min_unit_secs.

    Returns list of dicts:
      { 'unit_type': 'F'|'D', 'players': [pid, ...], 'toi_secs': int }
    """
    # Separate forwards and defenders
    fwd_ids = {
        pid for pid, p in positions.items() if p["position"] in ("C", "L", "R", "LW", "RW", "F")
    }
    def_ids = {pid for pid, p in positions.items() if p["position"] in ("D",)}

    def is_fwd(pid):
        return pid in fwd_ids

    def is_def(pid):
        return pid in def_ids

    # Build per-player TOI-sorted partner list
    player_partners = defaultdict(list)  # pid -> [(toi, partner_pid)]
    for pair, toi in pair_toi.items():
        if toi < MIN_PAIR_SECS:
            continue
        a, b = tuple(pair)
        # Only same position class
        if (is_fwd(a) and is_fwd(b)) or (is_def(a) and is_def(b)):
            player_partners[a].append((toi, b))
            player_partners[b].append((toi, a))

    for pid in player_partners:
        player_partners[pid].sort(reverse=True)

    # ── Forward lines (triplets) ──────────────────────────────
    seen_triplets = set()
    forward_units = []

    fwd_by_toi = sorted(
        [
            (sum(t for t, _ in player_partners[pid]), pid)
            for pid in fwd_ids
            if pid in player_partners
        ],
        reverse=True,
    )

    for _, pid in fwd_by_toi:
        partners = [p for _, p in player_partners[pid][:2]]
        if len(partners) < 2:
            continue
        triplet = frozenset({pid, partners[0], partners[1]})
        if triplet in seen_triplets:
            continue
        seen_triplets.add(triplet)

        # Combined TOI = average of the three pair TOIs
        p1, p2, p3 = tuple(triplet)
        t12 = pair_toi.get(frozenset({p1, p2}), 0)
        t13 = pair_toi.get(frozenset({p1, p3}), 0)
        t23 = pair_toi.get(frozenset({p2, p3}), 0)
        unit_toi = min(t12, t13, t23)  # conservative: all three must have played together

        if unit_toi < min_unit_secs:
            continue

        forward_units.append(
            {
                "unit_type": "F",
                "players": sorted(triplet),
                "toi_secs": int(unit_toi),
            }
        )

    # Sort by TOI descending — Line 1 is top TOI triplet
    forward_units.sort(key=lambda u: u["toi_secs"], reverse=True)

    # ── Defence pairs ─────────────────────────────────────────
    seen_pairs = set()
    def_units = []

    for pid in def_ids:
        if pid not in player_partners:
            continue
        partners = player_partners[pid]
        if not partners:
            continue
        best_toi, best_partner = partners[0]
        if best_partner == pid:  # skip degenerate self-pairs
            continue
        pair = frozenset({pid, best_partner})
        if len(pair) < 2:  # guard: collapsed frozenset
            continue
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        if best_toi < min_unit_secs:
            continue
        members = sorted(pair)
        def_units.append(
            {
                "unit_type": "D",
                "players": members,
                "toi_secs": int(best_toi),
            }
        )

    def_units.sort(key=lambda u: u["toi_secs"], reverse=True)

    return forward_units[:4] + def_units[:3]  # top 4 lines + top 3 D pairs


# ── Main ──────────────────────────────────────────────────────────────────────


def run_team(client, team, season, dry_run=False):
    """Compute and (unless dry_run) write line combinations for one team.

    Returns the number of unit rows written (or that would be written under
    --dry-run), or None if there wasn't enough data to compute anything.
    """
    print(f"\n--- {team} ---")

    # 1. Load this team's shifts (situation column may be null; filter at shot level)
    raw_shifts = fetch_all(
        client,
        "shift_events",
        "game_id,player_id,team,start_secs,end_secs",
        {"season": season, "team": team},
    )
    print(f"  {len(raw_shifts):,} shift rows")
    if not raw_shifts:
        print("  no shift data — run shift_data.py first")
        return None

    # 2. Look up this team's game_ids for the season, then its 5v5 shot events.
    # game_log has one row per team per game, so filtering by team here gives
    # exactly the games this team played (home or away) — not shot_events.car_game,
    # which only ever flags CAR's own games (see module docstring).
    game_rows = fetch_all(
        client, "game_log", "game_id", {"season": season, "team": team}, cursor_col="game_id"
    )
    game_ids = [g["game_id"] for g in game_rows]
    if not game_ids:
        print("  no games in game_log — skip")
        return None

    raw_shots = fetch_all(
        client,
        "shot_events",
        "game_id,player_id,team,x,y,event_type,period,time_in_period,situation_code",
        {"season": season, "game_id": game_ids, "situation_code": "1551"},
    )
    print(f"  {len(raw_shots):,} 5v5 shot events")

    # 3. Index by game
    shifts_by_game = defaultdict(list)
    for s in raw_shifts:
        shifts_by_game[s["game_id"]].append(s)

    shots_by_game = defaultdict(list)
    for s in raw_shots:
        shots_by_game[s["game_id"]].append(s)

    # 4. Compute pair TOI and xG across all games
    all_pair_toi = defaultdict(float)
    all_pair_xg = defaultdict(lambda: {"xgf": 0.0, "xga": 0.0})

    games = sorted(shifts_by_game.keys())
    for game_id in games:
        game_shifts = shifts_by_game[game_id]
        game_shots = shots_by_game.get(game_id, [])

        pair_toi = compute_pair_toi(game_shifts)
        pair_xg = attribute_shots_to_pairs(game_shots, game_shifts, pair_toi, team)

        for pair, toi in pair_toi.items():
            all_pair_toi[pair] += toi
        for pair, xg in pair_xg.items():
            all_pair_xg[pair]["xgf"] += xg["xgf"]
            all_pair_xg[pair]["xga"] += xg["xga"]

    print(f"  {len(all_pair_toi):,} unique pairs found")

    # 5. Fetch player positions and names for all involved players
    all_player_ids = set()
    for pair in all_pair_toi:
        all_player_ids.update(pair)
    positions = fetch_player_positions(client, all_player_ids)

    # 6. Cluster into line units
    units = cluster_into_units(all_pair_toi, positions, MIN_UNIT_SECS)
    print(
        f"  {len(units)} units formed ({sum(1 for u in units if u['unit_type'] == 'F')} lines, "
        f"{sum(1 for u in units if u['unit_type'] == 'D')} D pairs)"
    )

    if not units:
        print("  no units met the minimum TOI threshold — check shift data coverage")
        return None

    # 7. Build upsert rows
    rows = []
    fwd_rank = 1
    def_rank = 1
    for unit in units:
        players = unit["players"]
        # xGF% = average of all pair xGF% within unit
        pair_keys = []
        if unit["unit_type"] == "F":
            a, b, c = players[0], players[1], players[2]
            pair_keys = [frozenset({a, b}), frozenset({a, c}), frozenset({b, c})]
            rank = fwd_rank
            fwd_rank += 1
            p3 = c
        else:
            a, b = players[0], players[1]
            pair_keys = [frozenset({a, b})]
            rank = def_rank
            def_rank += 1
            p3 = None

        total_xgf = sum(all_pair_xg[pk]["xgf"] for pk in pair_keys)
        total_xga = sum(all_pair_xg[pk]["xga"] for pk in pair_keys)
        total_xg = total_xgf + total_xga
        xgf_pct = round(total_xgf / total_xg, 4) if total_xg > 0.001 else None

        # Player names
        def name(pid):
            p = positions.get(pid, {})
            return p.get("name") or str(pid)

        def pos(pid):
            p = positions.get(pid, {})
            return p.get("position") or "?"

        rows.append(
            {
                "season": season,
                "team": team,
                "unit_type": unit["unit_type"],
                "rank": rank,
                "player_a": players[0],
                "player_b": players[1],
                "player_c": p3,
                "name_a": name(players[0]),
                "name_b": name(players[1]),
                "name_c": name(p3) if p3 else None,
                "pos_a": pos(players[0]),
                "pos_b": pos(players[1]),
                "pos_c": pos(p3) if p3 else None,
                "toi_secs": unit["toi_secs"],
                "xgf": round(total_xgf / len(pair_keys), 2),
                "xga": round(total_xga / len(pair_keys), 2),
                "xgf_pct": xgf_pct,
            }
        )
        label = f"Line {rank}" if unit["unit_type"] == "F" else f"D{rank}"
        names = f"{name(players[0])} / {name(players[1])}" + (f" / {name(p3)}" if p3 else "")
        toi_min = round(unit["toi_secs"] / 60, 1)
        print(
            f"  {label:6s}  {names:<45}  {toi_min}m  xGF%={xgf_pct * 100:.1f}%"
            if xgf_pct
            else f"  {label:6s}  {names:<45}  {toi_min}m  xGF%=—"
        )

    if dry_run:
        print(f"  (dry-run) {len(rows)} line combination rows would be written")
        return len(rows)

    # 8. Delete old rows for this season/team, then insert fresh
    client.table("line_combinations").delete().eq("season", season).eq("team", team).execute()
    for i in range(0, len(rows), 500):
        client.table("line_combinations").insert(rows[i : i + 500]).execute()
    print(f"  OK line_combinations: {len(rows)} rows written")
    return len(rows)


def run(season=NHL_SEASON, team=None, dry_run=False):
    client = get_client()
    teams = [team] if team else ALL_TEAMS
    print(f"\n=== Line Combinations Pipeline — Season {season} ({len(teams)} team(s)) ===")

    total_rows = 0
    teams_written = 0
    for t in teams:
        result = run_team(client, t, season, dry_run=dry_run)
        if result:
            total_rows += result
            teams_written += 1

    print(
        f"\nLine combinations pipeline complete — {total_rows} rows across "
        f"{teams_written}/{len(teams)} team(s)"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Infer 5v5 line combinations from shift co-occurrence"
    )
    parser.add_argument("season", nargs="?", type=int, default=NHL_SEASON)
    parser.add_argument("--team", default=None, help="Team abbreviation e.g. CAR (default: all 32)")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print, skip DB writes")
    args = parser.parse_args()
    run(season=args.season, team=args.team, dry_run=args.dry_run)
