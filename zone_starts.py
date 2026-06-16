"""
zone_starts.py — Compute per-player zone start counts from NHL PBP.

For each game, identifies which zone each player's shift started in
by finding the nearest faceoff within 10 seconds of shift start.
Stores aggregated oz/dz/nz start counts per player per game.

Used by rapm.py to apply zone-start adjustment — players like Slavin
who start predominantly in the defensive zone get an upward RAPM
adjustment because they face harder shot volume contexts.

Usage:
  python zone_starts.py              # current season
  python zone_starts.py 20242025     # backfill a prior season
"""

import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from db import NHL_SEASON, get_client

NHL_BASE = "https://api-web.nhle.com/v1"
STATS_BASE = "https://api.nhle.com/stats/rest/en"
HEADERS = {"User-Agent": "EyeWall-Analytics/1.0 (eyewallanalytics.com)"}

HTML_REPORTS_BASE = "https://www.nhl.com/scores/htmlreports"

# Window in seconds to associate a faceoff with a shift start
FACEOFF_WINDOW_SECS = 10

PERIOD_OFFSETS = {1: 0, 2: 1200, 3: 2400, 4: 3600, 5: 4800}

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


def nhl_get(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  GET failed: {url} -- {e}")
        return None


def mmss_to_secs(mmss):
    try:
        parts = mmss.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


def abs_secs(period, time_str):
    offset = PERIOD_OFFSETS.get(period, (period - 1) * 1200)
    return offset + mmss_to_secs(time_str or "0:00")


def get_all_completed_games(season):
    seen = set()
    games = []
    for team in ALL_TEAMS:
        data = nhl_get(f"{NHL_BASE}/club-schedule-season/{team}/{season}")
        if not data:
            continue
        for g in data.get("games", []):
            gid = g.get("id")
            if gid and gid not in seen and g.get("gameState") in ("OFF", "FINAL", "F"):
                seen.add(gid)
                games.append(g)
        time.sleep(0.1)
    return sorted(games, key=lambda g: g["id"])


def get_processed_games(client, season):
    all_ids = set()
    offset = 0
    while True:
        result = (
            client.table("zone_starts")
            .select("game_id")
            .eq("season", season)
            .range(offset, offset + 999)
            .execute()
        )
        rows = result.data
        if not rows:
            break
        all_ids.update(r["game_id"] for r in rows)
        offset += 1000
    return all_ids


def get_skipped_games(client, season):
    """Get game IDs previously marked as having no zone start data."""
    all_ids = set()
    offset = 0
    while True:
        result = (
            client.table("skipped_games")
            .select("game_id")
            .eq("season", season)
            .eq("pipeline", "zone_starts")
            .range(offset, offset + 999)
            .execute()
        )
        rows = result.data
        if not rows:
            break
        all_ids.update(r["game_id"] for r in rows)
        offset += 1000
    return all_ids


def mark_skipped(client, game_id, season, reason="no_data"):
    """Mark a game as having no zone start data so it won't be retried."""
    try:
        client.table("skipped_games").upsert(
            {
                "game_id": game_id,
                "season": season,
                "pipeline": "zone_starts",
                "reason": reason,
            },
            on_conflict="game_id,pipeline",
        ).execute()
    except Exception:
        pass  # non-critical


def get_shift_chart(game_id):
    data = nhl_get(f"{STATS_BASE}/shiftcharts", params={"cayenneExp": f"gameId={game_id}"})
    if not data:
        return []
    return data.get("data", [])


# ── HTML shift report fallback ─────────────────────────────────────────────
# Mirrors the approach in shift_data.py. Used when the JSON shift chart API
# returns no data (affects some regular season and preseason games).
#
# Returns shifts in the same dict format that process_game expects:
#   { 'playerId': int, 'teamAbbrev': str, 'startTime': 'M:SS',
#     'period': int, 'detailCode': 0 }


def fetch_roster_for_html(game_id):
    """Fetch player roster from play-by-play API.
    Returns dict mapping (LAST, FIRST) and LAST -> (player_id, team_abbrev, pos_code).
    """
    data = nhl_get(f"{NHL_BASE}/gamecenter/{game_id}/play-by-play")
    if not data:
        return {}, ""

    team_map = {}
    for t in ["awayTeam", "homeTeam"]:
        team = data.get(t, {})
        tid = team.get("id")
        abbrev = team.get("abbrev", "")
        if tid:
            team_map[tid] = abbrev

    home_team = data.get("homeTeam", {}).get("abbrev", "")

    roster = {}
    for spot in data.get("rosterSpots", []):
        pid = spot.get("playerId")
        tid = spot.get("teamId")
        first = spot.get("firstName", {}).get("default", "").upper().strip()
        last = spot.get("lastName", {}).get("default", "").upper().strip()
        pos = spot.get("positionCode", "")
        if pid and last:
            roster[(last, first)] = (pid, team_map.get(tid, ""), pos)
            if last not in roster:
                roster[last] = (pid, team_map.get(tid, ""), pos)

    return roster, home_team


def parse_html_shifts_for_zone(game_id, season, html, roster):
    """Parse NHL HTML shift report into the dict format process_game expects.
    Goalies are excluded (detailCode=1 equivalent — skipped via pos_code check).
    Returns list of dicts with keys: playerId, teamAbbrev, startTime, period, detailCode.
    """
    shifts = []
    blocks = re.split(r'class="playerHeading \+ border"[^>]*>([^<]+)</td>', html)
    i = 1
    while i < len(blocks) - 1:
        header = blocks[i].strip()
        content = blocks[i + 1]
        i += 2

        m = re.match(r"^(\d+)\s+(.+)$", header)
        if not m:
            continue
        name_part = m.group(2).strip()

        if "," in name_part:
            parts = name_part.split(",", 1)
            last = parts[0].strip().upper()
            first = parts[1].strip().upper()
        else:
            last = name_part.strip().upper()
            first = ""

        player_info = roster.get((last, first)) or roster.get((last, "")) or roster.get(last)
        if not player_info:
            continue

        player_id, team_abbrev, pos_code = player_info

        if pos_code == "G":
            continue

        shift_rows = re.findall(
            r'<tr class="[^"]*(?:odd|even)Color[^"]*">\s*'
            r"<td[^>]*>(\d+)</td>\s*"
            r"<td[^>]*>(\d+)</td>\s*"
            r"<td[^>]*>([\d:]+)\s*/[^<]*</td>\s*"
            r"<td[^>]*>([\d:]+)\s*/[^<]*</td>\s*"
            r"<td[^>]*>([\d:]+)</td>",
            content,
        )

        for _shift_num, period_str, start_str, _end_str, _duration_str in shift_rows:
            shifts.append(
                {
                    "playerId": player_id,
                    "teamAbbrev": team_abbrev,
                    "startTime": start_str,
                    "period": int(period_str),
                    "detailCode": 0,
                }
            )

    return shifts


def get_shift_chart_html_fallback(game_id, season):
    """Fetch shifts from NHL HTML shift reports when JSON API returns nothing.
    Returns shifts in the same format as get_shift_chart() so process_game
    can use them without modification.
    """
    year = game_id // 1000000  # e.g. 2024
    season_str = f"{year}{year + 1}"  # e.g. 20242025
    short_id = str(game_id)[-6:]  # e.g. 020034

    roster, _ = fetch_roster_for_html(game_id)
    if not roster:
        return []

    all_shifts = []
    for report_type in ["TV", "TH"]:  # visitor, home
        url = f"{HTML_REPORTS_BASE}/{season_str}/{report_type}{short_id}.HTM"
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                continue
            shifts = parse_html_shifts_for_zone(game_id, season, r.text, roster)
            all_shifts.extend(shifts)
        except Exception as e:
            print(f"  HTML fallback error {url}: {e}")
            continue

    return all_shifts


# ── End HTML fallback ──────────────────────────────────────────────────────


def process_game(game_id, season, home_team):
    """
    For each player shift, find the nearest preceding faceoff
    within FACEOFF_WINDOW_SECS and record its zone.

    IMPORTANT: zoneCode in NHL API is from HOME team perspective.
    Away team players get the flipped zone (O<->D).

    Tries the JSON shift chart API first; falls back to HTML shift
    reports if the JSON API returns no data.
    """
    pbp = nhl_get(f"{NHL_BASE}/gamecenter/{game_id}/play-by-play")
    if not pbp or not pbp.get("plays"):
        return []

    # Always get home team from PBP -- most reliable source regardless of what was passed in
    home_team = pbp.get("homeTeam", {}).get("abbrev", "") or home_team

    # Faceoff timeline from HOME perspective
    faceoffs = []
    for play in pbp["plays"]:
        if play.get("typeDescKey") != "faceoff":
            continue
        d = play.get("details", {})
        zone = d.get("zoneCode")  # 'O', 'D', 'N' from HOME perspective
        if not zone:
            continue
        period = play.get("periodDescriptor", {}).get("number", 1)
        t = play.get("timeInPeriod", "0:00")
        secs = abs_secs(period, t)
        faceoffs.append((secs, zone))

    if not faceoffs:
        return []

    # Try JSON shift chart first; fall back to HTML reports
    raw_shifts = get_shift_chart(game_id)
    if not raw_shifts:
        raw_shifts = get_shift_chart_html_fallback(game_id, season)
    if not raw_shifts:
        return []

    player_starts = defaultdict(lambda: {"team": "", "oz": 0, "dz": 0, "nz": 0})

    for shift in raw_shifts:
        player_id = shift.get("playerId")
        team_abbrev = shift.get("teamAbbrev", "")
        start_str = shift.get("startTime", "0:00")
        period = shift.get("period", 1)
        detail_code = shift.get("detailCode", 0)

        if not player_id or detail_code == 1:
            continue
        if not start_str or ":" not in start_str:
            continue

        shift_start = abs_secs(period, start_str)
        is_home = team_abbrev == home_team

        best_zone = None
        best_delta = FACEOFF_WINDOW_SECS + 1

        for fo_secs, fo_zone in faceoffs:
            delta = shift_start - fo_secs
            if 0 <= delta <= FACEOFF_WINDOW_SECS and delta < best_delta:
                best_delta = delta
                best_zone = fo_zone

        if not best_zone:
            continue

        # Flip zone for away team -- API reports from home perspective
        if not is_home and best_zone != "N":
            best_zone = "O" if best_zone == "D" else "D"

        ps = player_starts[player_id]
        ps["team"] = team_abbrev
        if best_zone == "O":
            ps["oz"] += 1
        elif best_zone == "D":
            ps["dz"] += 1
        else:
            ps["nz"] += 1

    rows = []
    for player_id, counts in player_starts.items():
        if counts["oz"] + counts["dz"] + counts["nz"] == 0:
            continue
        rows.append(
            {
                "game_id": game_id,
                "season": season,
                "player_id": player_id,
                "team": counts["team"],
                "oz_starts": counts["oz"],
                "dz_starts": counts["dz"],
                "nz_starts": counts["nz"],
            }
        )

    return rows


def run(season=NHL_SEASON):
    client = get_client()
    print(f"\n=== Zone Starts Pipeline (league-wide) -- Season {season} ===")

    print("  Fetching all league game IDs...")
    games = get_all_completed_games(season)
    print(f"  Found {len(games):,} completed games")

    # Load home team mapping from game_log — more reliable than schedule API field
    print("  Loading home team map from game_log...")
    home_team_map = {}
    offset = 0
    while True:
        rows = (
            client.table("game_log")
            .select("game_id,home_team")
            .eq("season", season)
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not rows:
            break
        for r in rows:
            home_team_map[r["game_id"]] = r["home_team"]
        offset += 1000
    print(f"  Loaded {len(home_team_map):,} game home teams")

    already_done = get_processed_games(client, season)
    skipped = get_skipped_games(client, season)
    excluded = already_done | skipped
    pending = [g for g in games if g["id"] not in excluded]
    print(
        f"  {len(already_done):,} already processed, {len(skipped):,} skipped, {len(pending):,} pending"
    )

    if not pending:
        print("  All games already processed")
        return

    total_rows = 0
    errors = 0
    completed = 0
    WORKERS = 8  # zone_starts fetches PBP + shifts per game, slightly heavier

    def process_one(game):
        game_id = game["id"]
        home_team = home_team_map.get(game_id) or game.get("homeTeam", {}).get("abbrev", "")
        try:
            rows = process_game(game_id, season, home_team)
            if not rows:
                return game_id, [], "no_data"
            return game_id, rows, None
        except Exception as e:
            return game_id, [], str(e)

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(process_one, g): g for g in pending}
        for future in as_completed(futures):
            game_id, rows, error = future.result()
            completed += 1

            if error or not rows:
                mark_skipped(client, game_id, season, error or "no_data")
                errors += 1
            else:
                try:
                    client.table("zone_starts").delete().eq("game_id", game_id).execute()
                    for j in range(0, len(rows), 500):
                        client.table("zone_starts").insert(rows[j : j + 500]).execute()
                    total_rows += len(rows)
                except Exception as e:
                    print(f"  Game {game_id}: DB error — {e}")
                    errors += 1

            if completed % 100 == 0 or completed == len(pending):
                print(f"  [{completed}/{len(pending)}] {total_rows:,} player-game rows inserted")

    print("\nZone starts pipeline complete")
    print(f"   Player-game rows inserted: {total_rows:,}")
    if errors:
        print(f"   Games with no data: {errors}")


if __name__ == "__main__":
    import sys

    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    run(season_arg)
