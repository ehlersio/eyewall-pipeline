"""
special_teams.py — Infer PP and PK unit compositions from shift + shot data.

For each team in the season:
  1. Looks up the team's own game_ids from game_log, then its non-5v5
     shot_events for those games (NOT shot_events.car_game, which only
     ever flags games *Carolina* played in — see shot_events.py's
     docstring; same trap line_combinations.py already routed around)
  2. Joins game_log to resolve home/away → correctly interpret situation codes
  3. Joins shift_events to find which players were on ice during each PP shot
  4. Clusters player combinations by co-occurrence frequency
  5. Top cluster = unit 1, second cluster = unit 2
  6. Writes to special_teams_units (skips rows where source = 'manual')

Run order: after shift_data.py (needs fresh shift_events).

Usage:
    python special_teams.py                    # all 32 teams, current season
    python special_teams.py --season 20252026  # specific season
    python special_teams.py --team CAR         # one team only
    python special_teams.py --dry-run          # print results, skip DB writes
"""

import argparse
import os
from collections import Counter
from datetime import UTC, datetime
from itertools import combinations

import httpx
from dotenv import load_dotenv

# ClientOptions must come from the package root, not supabase.lib.client_options
# — see db.py's import comment for why (AttributeError: 'storage', still true as
# of 2.31.0). Don't "clean up" this import back to the submodule path.
from supabase import ClientOptions, create_client

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
NHL_SEASON = int(os.environ.get("NHL_SEASON", "20252026"))

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

# Situation code format: {away_skaters}{away_goalie}{home_skaters}{home_goalie}
# PP for home team: away has 4 skaters, home has 5 → code starts with '14' and ends in '51'
# PP for away team: away has 5 skaters, home has 4 → code starts with '15' and ends in '41'
# We also include 6v4, 6v5, etc. edge cases
HOME_PP_CODES = {"1451", "1461", "1351", "1361"}  # home team on PP
AWAY_PP_CODES = {"1541", "1641", "1531", "1631"}  # away team on PP

MIN_PP_SHOTS = 10  # minimum PP shots to attempt unit inference for a team
MIN_UNIT_SHOTS = 5  # minimum shots a combination must appear in to be a unit
MIN_OVERLAP = 3  # minimum players overlapping to count a combo
PP_UNIT_SIZE = 5  # forwards + D on PP
PK_UNIT_SIZE = 4  # forwards + D on PK

supabase = create_client(
    SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(httpx_client=httpx.Client(timeout=60))
)


# ── Data fetchers ─────────────────────────────────────────────────────────────


def fetch_game_home_away(season: int) -> dict[int, tuple[str, str]]:
    """Returns {game_id: (home_team, away_team)} for all games in the season."""
    rows = (
        supabase.table("game_log")
        .select("game_id,home_team,away_team")
        .eq("season", season)
        .not_.is_("home_team", "null")
        .limit(2000)
        .execute()
        .data
    )
    seen = {}
    for r in rows or []:
        gid = r["game_id"]
        if gid not in seen and r.get("home_team") and r.get("away_team"):
            seen[gid] = (r["home_team"], r["away_team"])
    return seen


def fetch_game_ids_for_team(team: str, season: int) -> set[int]:
    """Returns the game_ids `team` played in this season, from `game_log`
    (one row per team per game) -- not `shot_events.car_game`, which only
    ever flags games *Carolina* played in (see shot_events.py's docstring).
    Same fix as line_combinations.py's fetch_all(..., "game_log", ...) call;
    special_teams.py's per-team shot fetch was the trap that module's own
    comment warned still existed here."""
    rows = (
        supabase.table("game_log")
        .select("game_id")
        .eq("season", season)
        .eq("team", team)
        .limit(200)  # a full season incl. playoffs is well under this
        .execute()
        .data
    )
    return {r["game_id"] for r in (rows or [])}


def fetch_situational_shots_for_team(team: str, season: int, game_ids: set[int]) -> list[dict]:
    """All non-5v5 shot_events rows for `team`'s own games this season,
    scoped via `game_ids` (from fetch_game_ids_for_team) rather than
    `shot_events.car_game`. Shared by both the PP and PK derivations below
    -- they need the same raw rows, just interpreted from opposite
    perspectives (team on the man advantage vs. team a skater down)."""
    if not game_ids:
        return []
    rows = []
    offset = 0
    game_id_list = list(game_ids)
    while True:
        batch = (
            supabase.table("shot_events")
            .select("game_id,period,time_in_period,situation_code,team")
            .eq("season", season)
            .in_("game_id", game_id_list)
            .not_.is_("situation_code", "null")
            .neq("situation_code", "1551")
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def filter_pp_shots(team: str, situational_rows: list[dict], game_home_away: dict) -> list[dict]:
    """Filters situational shot rows down to PP shots where `team` is the
    team on the power play. Each returned row has: game_id, period,
    time_in_period."""
    pp_shots = []
    for r in situational_rows:
        gid = r["game_id"]
        code = r.get("situation_code", "")
        ha = game_home_away.get(gid)
        if not ha:
            continue
        home, away = ha

        is_team_pp = False
        if (code in HOME_PP_CODES and home == team) or (code in AWAY_PP_CODES and away == team):
            is_team_pp = True

        if is_team_pp:
            pp_shots.append(
                {
                    "game_id": gid,
                    "period": r["period"],
                    "time_in_period": r["time_in_period"],
                }
            )

    return pp_shots


def filter_pk_shots(team: str, situational_rows: list[dict], game_home_away: dict) -> list[dict]:
    """Filters situational shot rows down to PK shots where `team` is a
    skater down (the opponent is on the power play) -- the mirror image of
    filter_pp_shots."""
    pk_shots = []
    for r in situational_rows:
        gid = r["game_id"]
        code = r.get("situation_code", "")
        ha = game_home_away.get(gid)
        if not ha:
            continue
        home, away = ha

        is_team_pk = False
        if (code in HOME_PP_CODES and away == team) or (code in AWAY_PP_CODES and home == team):
            is_team_pk = True

        if is_team_pk:
            pk_shots.append(
                {
                    "game_id": gid,
                    "period": r["period"],
                    "time_in_period": r["time_in_period"],
                }
            )

    return pk_shots


def fetch_shifts_for_team(team: str, season: int) -> list[dict]:
    """Returns all shift_events for the team in the season.

    Keyset (not OFFSET) pagination -- team-scoped so each call is bounded
    today, but this is the same shift_events table that hit a Postgres
    57014 statement timeout via OFFSET pagination at this exact scope
    (single team, single season) on 2026-07-04, fixed the same way in
    line_combinations.py::fetch_all (see its docstring for the incident).
    """
    rows = []
    last_id = 0
    while True:
        batch = (
            supabase.table("shift_events")
            .select("id,game_id,player_id,period,start_secs,end_secs")
            .eq("season", season)
            .eq("team", team)
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


def fetch_existing_manual_units(team: str, season: int) -> set[tuple]:
    """Returns set of (unit_type, unit_number) that are manually set — never overwrite."""
    rows = (
        supabase.table("special_teams_units")
        .select("unit_type,unit_number")
        .eq("team", team)
        .eq("season", season)
        .eq("source", "manual")
        .execute()
        .data
    )
    return {(r["unit_type"], r["unit_number"]) for r in (rows or [])}


# ── Inference logic ───────────────────────────────────────────────────────────


def build_shift_index(shifts: list[dict]) -> dict:
    """
    Build a nested index: {game_id: {period: [(player_id, start, end)]}}
    for fast on-ice player lookup.
    """
    idx = {}
    for s in shifts:
        gid = s["game_id"]
        per = s["period"]
        if gid not in idx:
            idx[gid] = {}
        if per not in idx[gid]:
            idx[gid][per] = []
        idx[gid][per].append((s["player_id"], s["start_secs"], s["end_secs"]))
    return idx


def time_to_secs(t) -> int:
    """Convert 'MM:SS' string or integer seconds to integer seconds."""
    if t is None:
        return 0
    if isinstance(t, int):
        return t
    try:
        parts = str(t).split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(t)
    except (ValueError, AttributeError):
        return 0


def players_on_ice_at(shift_idx: dict, game_id: int, period: int, time_secs: int) -> list[int]:
    """Returns player IDs on ice at a specific game_id + period + time."""
    shifts = shift_idx.get(game_id, {}).get(period, [])
    return [
        pid
        for pid, start, end in shifts
        if start is not None and end is not None and start <= time_secs <= end
    ]


def infer_units(
    pp_shots: list[dict], shift_idx: dict, unit_size: int
) -> tuple[list[int] | None, list[int] | None]:
    """
    Given PP shot events and a shift index, infer the two most common unit
    player combinations.

    Returns (unit1_player_ids, unit2_player_ids) — either may be None if
    insufficient data.
    """
    if not pp_shots:
        return None, None

    # For each PP shot, get the players on ice and count combos
    combo_counter: Counter = Counter()
    player_counter: Counter = Counter()

    for shot in pp_shots:
        players = players_on_ice_at(
            shift_idx, shot["game_id"], shot["period"], time_to_secs(shot["time_in_period"])
        )
        if len(players) < MIN_OVERLAP:
            continue
        player_counter.update(players)
        # Count all pairs as a co-occurrence signal
        for pair in combinations(sorted(players), 2):
            combo_counter[pair] += 1

    if not player_counter:
        return None, None

    # Unit 1: the unit_size most frequently occurring players overall
    top_players = [pid for pid, _ in player_counter.most_common(unit_size * 2)]

    # Find the tightest cluster: players that appear together most
    # Strategy: seed unit1 with the single most common player, then add
    # the player with the highest co-occurrence with the current unit
    def build_unit(seed_players: list[int], exclude: set[int], size: int) -> list[int] | None:
        remaining = [p for p in seed_players if p not in exclude]
        if not remaining:
            return None
        unit = [remaining[0]]
        remaining = remaining[1:]
        while len(unit) < size and remaining:
            # Pick the player with highest average co-occurrence with current unit
            best_pid = max(
                remaining,
                key=lambda p: sum(combo_counter.get(tuple(sorted([p, u])), 0) for u in unit),
            )
            unit.append(best_pid)
            remaining.remove(best_pid)
        return unit if len(unit) >= MIN_OVERLAP else None

    unit1 = build_unit(top_players, set(), unit_size)
    if unit1 is None:
        return None, None

    # Unit 2: same process but excluding unit1 players
    unit2 = build_unit(top_players, set(unit1), unit_size)

    return unit1, unit2


# ── DB write ──────────────────────────────────────────────────────────────────


def upsert_unit(
    team: str, season: int, unit_type: str, unit_number: int, player_ids: list[int]
) -> None:
    supabase.table("special_teams_units").upsert(
        {
            "team": team,
            "season": season,
            "unit_type": unit_type,
            "unit_number": unit_number,
            "player_ids": player_ids,
            "source": "inferred",
            "updated_at": datetime.now(UTC).isoformat(),
        },
        on_conflict="team,season,unit_type,unit_number",
    ).execute()


# ── Core runner ───────────────────────────────────────────────────────────────


def run_team(team: str, season: int, game_home_away: dict, dry_run: bool = False) -> None:
    print(f"  {team}:", end=" ", flush=True)

    manual_units = fetch_existing_manual_units(team, season)
    shifts = fetch_shifts_for_team(team, season)
    if not shifts:
        print("no shifts — skip")
        return

    shift_idx = build_shift_index(shifts)

    game_ids = fetch_game_ids_for_team(team, season)
    if not game_ids:
        print("no games in game_log — skip")
        return
    situational_rows = fetch_situational_shots_for_team(team, season, game_ids)

    # ── PP ────────────────────────────────────────────────────
    pp_shots = filter_pp_shots(team, situational_rows, game_home_away)
    if len(pp_shots) < MIN_PP_SHOTS:
        print(f"insufficient PP shots ({len(pp_shots)}) — skip")
        return

    pp1_ids, pp2_ids = infer_units(pp_shots, shift_idx, PP_UNIT_SIZE)

    # ── PK ────────────────────────────────────────────────────
    # PK = opponent is on PP. Same situational rows as PP, flipped perspective.
    pk_shots = filter_pk_shots(team, situational_rows, game_home_away)
    pk1_ids, pk2_ids = infer_units(pk_shots, shift_idx, PK_UNIT_SIZE)

    # ── Report / write ────────────────────────────────────────
    results = [
        ("PP", 1, pp1_ids),
        ("PP", 2, pp2_ids),
        ("PK", 1, pk1_ids),
        ("PK", 2, pk2_ids),
    ]

    written = 0
    for unit_type, unit_num, player_ids in results:
        key = (unit_type, unit_num)
        if player_ids is None:
            continue
        if key in manual_units:
            print(f"\n    {unit_type}{unit_num}: skipped (manual)", end="")
            continue
        if dry_run:
            print(f"\n    {unit_type}{unit_num}: {player_ids}", end="")
        else:
            upsert_unit(team, season, unit_type, unit_num, player_ids)
        written += 1

    print(f" {written} units {'would be ' if dry_run else ''}written")


def run(season: int = None, team: str = None, dry_run: bool = False) -> None:
    season = season or NHL_SEASON
    print(f"\n--- Special teams unit inference ({season}) ---")

    print("  Loading game home/away map...", end=" ", flush=True)
    game_home_away = fetch_game_home_away(season)
    print(f"{len(game_home_away)} games")

    teams = [team] if team else ALL_TEAMS
    for t in teams:
        run_team(t, season, game_home_away, dry_run=dry_run)

    print("  Done")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Infer PP/PK unit compositions")
    parser.add_argument("--season", type=int, default=None)
    parser.add_argument("--team", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(season=args.season, team=args.team, dry_run=args.dry_run)
