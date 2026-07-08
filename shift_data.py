"""
shift_data.py — Fetch NHL shift charts for ALL league games and store
                in shift_events table.

One row per player shift. Used by rapm.py to determine who was on
ice for each shot event when building the RAPM design matrix.
League-wide shifts are required for true RAPM.

Usage:
  python shift_data.py              # current season (NHL_SEASON)
  python run.py shifts              # via orchestrator, current season
  python run.py shifts 20242025     # backfill a prior season

Performance: ~1,300 games/season x ~750 shifts = ~1M rows per season.
One-time backfill of 3 seasons takes ~30-45 minutes.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from db import NHL_SEASON, get_client
from pipeline_common import FetchError

NHL_BASE = "https://api-web.nhle.com/v1"
STATS_BASE = "https://api.nhle.com/stats/rest/en"
HEADERS = {"User-Agent": "EyeWall-Analytics/1.0 (eyewallanalytics.com)"}

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

PERIOD_OFFSETS = {1: 0, 2: 1200, 3: 2400, 4: 3600, 5: 4800}


def nhl_get(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise FetchError(f"GET failed: {url} — {e}") from e


def mmss_to_secs(mmss):
    try:
        parts = mmss.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


def shift_to_abs_secs(time_str, period):
    offset = PERIOD_OFFSETS.get(period, (period - 1) * 1200)
    return offset + mmss_to_secs(time_str)


def get_all_completed_games(season):
    """Get all unique completed games across all 32 teams for a season."""
    seen = set()
    games = []
    for team in ALL_TEAMS:
        try:
            data = nhl_get(f"{NHL_BASE}/club-schedule-season/{team}/{season}")
        except FetchError as e:
            print(f"  ✗ {e}")
            continue
        for g in data.get("games", []):
            gid = g.get("id")
            if gid and gid not in seen and g.get("gameState") in ("OFF", "FINAL", "F"):
                seen.add(gid)
                games.append(g)
        time.sleep(0.1)
    return sorted(games, key=lambda g: g["id"])


def get_processed_games(client, season):
    """Get distinct game IDs already in shift_events via RPC.
    Avoids paginating through ~1M rows by using a DB-side distinct query.
    Requires the distinct_shift_game_ids function to be created in Supabase.
    """
    all_ids = set()
    offset = 0
    while True:
        result = client.rpc(
            "distinct_shift_game_ids",
            {
                "p_season": season,
                "p_offset": offset,
                "p_limit": 1000,
            },
        ).execute()
        rows = result.data
        if not rows:
            break
        all_ids.update(r["game_id"] for r in rows)
        if len(rows) < 1000:
            break
        offset += 1000
    return all_ids


def get_skipped_games(client, season):
    """Get game IDs previously marked as having no shift data."""
    all_ids = set()
    offset = 0
    while True:
        result = (
            client.table("skipped_games")
            .select("game_id")
            .eq("season", season)
            .eq("pipeline", "shifts")
            .range(offset, offset + 999)
            .execute()
        )
        rows = result.data
        if not rows:
            break
        all_ids.update(r["game_id"] for r in rows)
        offset += 1000
    return all_ids


def fetch_shift_chart(game_id):
    """Fetch raw shift chart rows from NHL API for a single game.

    Catches FetchError itself (unlike this module's other helpers) rather
    than letting it propagate to process_one() -- process_one()'s "empty
    JSON result -> fall back to HTML shift reports" logic deliberately
    treats "fetch broke" and "no JSON data for this game" the same way,
    both should trigger the HTML fallback attempt, not skip it.
    """
    try:
        data = nhl_get(f"{STATS_BASE}/shiftcharts", params={"cayenneExp": f"gameId={game_id}"})
    except FetchError:
        return []
    return data.get("data", [])


HTML_REPORTS_BASE = "https://www.nhl.com/scores/htmlreports"


def fetch_roster(game_id):
    """Fetch player roster from play-by-play API.
    Returns dict of (normalized_last, normalized_first) -> (player_id, team_id).
    Used to match HTML shift report names to player IDs.

    Lets FetchError propagate -- process_one()'s broad except already
    isolates one game's fetch failure from the rest of the run.
    """
    data = nhl_get(f"{NHL_BASE}/gamecenter/{game_id}/play-by-play")
    roster = {}
    team_map = {}  # team_id -> abbrev (populated from awayTeam/homeTeam)
    for t in ["awayTeam", "homeTeam"]:
        team = data.get(t, {})
        tid = team.get("id")
        abbrev = team.get("abbrev", "")
        if tid:
            team_map[tid] = abbrev
    for spot in data.get("rosterSpots", []):
        pid = spot.get("playerId")
        tid = spot.get("teamId")
        first = spot.get("firstName", {}).get("default", "").upper().strip()
        last = spot.get("lastName", {}).get("default", "").upper().strip()
        pos = spot.get("positionCode", "")
        if pid and last:
            roster[(last, first)] = (pid, team_map.get(tid, ""), pos)
            # Also index by last name only for fallback matching
            if last not in roster:
                roster[last] = (pid, team_map.get(tid, ""), pos)
    return roster, team_map


def parse_html_shifts(game_id, season, html, roster):
    """Parse NHL HTML shift report into shift_events rows.
    HTML structure: player header td contains 'NUMBER LASTNAME, FIRSTNAME',
    followed by shift rows with period and elapsed/game times.
    """
    rows = []
    # Split into player blocks by playerHeading
    # Each block starts with the player header and contains shift rows
    blocks = re.split(r'class="playerHeading \+ border"[^>]*>([^<]+)</td>', html)
    # blocks: [pre, player1_header, player1_content, player2_header, ...]
    i = 1
    while i < len(blocks) - 1:
        header = blocks[i].strip()  # e.g. "2 ZUB, ARTEM"
        content = blocks[i + 1]
        i += 2

        # Parse sweater number and name
        m = re.match(r"^(\d+)\s+(.+)$", header)
        if not m:
            continue
        name_part = m.group(2).strip()  # "ZUB, ARTEM" or "ZUB"

        # Normalize name for roster lookup
        if "," in name_part:
            parts = name_part.split(",", 1)
            last = parts[0].strip().upper()
            first = parts[1].strip().upper()
        else:
            last = name_part.strip().upper()
            first = ""

        # Look up player ID from roster
        player_info = roster.get((last, first)) or roster.get((last, "")) or roster.get(last)
        if not player_info:
            continue
        player_id, team_abbrev, pos_code = player_info

        # Skip goalies
        if pos_code == "G":
            continue

        # Parse shift rows — each row has: shift#, period, start elapsed, end elapsed, duration
        # Times are "M:SS / M:SS" (elapsed / game remaining) — we use elapsed
        shift_rows = re.findall(
            r'<tr class="[^"]*(?:odd|even)Color[^"]*">\s*'
            r"<td[^>]*>(\d+)</td>\s*"  # shift number
            r"<td[^>]*>(\d+)</td>\s*"  # period
            r"<td[^>]*>([\d:]+)\s*/[^<]*</td>\s*"  # start elapsed
            r"<td[^>]*>([\d:]+)\s*/[^<]*</td>\s*"  # end elapsed
            r"<td[^>]*>([\d:]+)</td>",  # duration
            content,
        )

        for _shift_num, period_str, start_str, end_str, _duration_str in shift_rows:
            period = int(period_str)
            start_secs = shift_to_abs_secs(start_str, period)
            end_secs = shift_to_abs_secs(end_str, period)

            if end_secs <= start_secs:
                continue

            rows.append(
                {
                    "game_id": game_id,
                    "season": season,
                    "player_id": player_id,
                    "team": team_abbrev,
                    "start_secs": start_secs,
                    "end_secs": end_secs,
                    "period": period,
                    "situation": None,
                }
            )

    return rows


def fetch_shift_chart_html(game_id, season):
    """Fetch shift data from NHL HTML shift reports (visitor + home).
    Fallback for games where the JSON API returns no data.
    Returns list of raw shift dicts in same format as process_shifts output.
    """
    # Derive season string and short game ID from game_id
    # game_id format: 2025020373 -> season 20252026, short 020373
    year = game_id // 1000000  # 2025
    season_str = f"{year}{year + 1}"  # 20252026
    short_id = str(game_id)[-6:]  # 020373

    # Fetch player roster for name->ID mapping
    roster, _ = fetch_roster(game_id)
    if not roster:
        return []

    all_rows = []
    for report_type in ["TV", "TH"]:  # visitor, home
        url = f"{HTML_REPORTS_BASE}/{season_str}/{report_type}{short_id}.HTM"
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                continue
            rows = parse_html_shifts(game_id, season, r.text, roster)
            all_rows.extend(rows)
            # add temporarily to shift_data.py fetch_shift_chart_html, after parse_html_shifts calls
            print(f"  Game {game_id}: {len(all_rows)} shifts from HTML")
        except Exception as e:
            print(f"  HTML fetch error {url}: {e}")
            continue

    return all_rows


def mark_skipped(client, game_id, season, reason="no_data"):
    """Mark a game as having no shift data so it won't be retried."""
    try:
        client.table("skipped_games").upsert(
            {
                "game_id": game_id,
                "season": season,
                "pipeline": "shifts",
                "reason": reason,
            },
            on_conflict="game_id,pipeline",
        ).execute()
    except Exception:
        pass  # non-critical


def process_shifts(game_id, season, raw_shifts):
    """Convert raw shift chart rows into shift_events rows for both teams."""
    rows = []
    for shift in raw_shifts:
        player_id = shift.get("playerId")
        team_abbrev = shift.get("teamAbbrev", "")
        start_str = shift.get("startTime", "0:00")
        end_str = shift.get("endTime", "0:00")
        period = shift.get("period", 1)
        detail_code = shift.get("detailCode", 0)

        if not player_id:
            continue
        if detail_code == 1:  # goalie — excluded from skater matrix
            continue
        if not start_str or not end_str or ":" not in start_str:
            continue

        start_secs = shift_to_abs_secs(start_str, period)
        end_secs = shift_to_abs_secs(end_str, period)

        if end_secs <= start_secs:
            continue

        rows.append(
            {
                "game_id": game_id,
                "season": season,
                "player_id": player_id,
                "team": team_abbrev,
                "start_secs": start_secs,
                "end_secs": end_secs,
                "period": period,
                "situation": None,
            }
        )

    return rows


def run(season=NHL_SEASON):
    client = get_client()
    print(f"\n=== Shift Data Pipeline (league-wide) — Season {season} ===")

    print("  Fetching all league game IDs...")
    games = get_all_completed_games(season)
    print(f"  Found {len(games):,} completed games")

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

    total_shifts = 0
    errors = 0
    completed = 0
    WORKERS = 5  # reduced from 10 — HTML fallback makes 3 requests/game

    def process_one(game):
        game_id = game["id"]
        try:
            # Try JSON API first (fast, available for early-season games)
            raw = fetch_shift_chart(game_id)
            if raw:
                rows = process_shifts(game_id, season, raw)
                if rows:
                    return game_id, rows, None

            # Fall back to HTML shift reports (available for all games)
            rows = fetch_shift_chart_html(game_id, season)
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
                print(f"  SKIP Game {game_id}: {error or 'no_data'}")
                mark_skipped(client, game_id, season, error or "no_data")
                errors += 1
            else:
                try:
                    client.table("shift_events").delete().eq("game_id", game_id).execute()
                    for j in range(0, len(rows), 500):
                        client.table("shift_events").insert(rows[j : j + 500]).execute()
                    total_shifts += len(rows)
                except Exception as e:
                    print(f"  Game {game_id}: DB error — {e}")
                    errors += 1

            if completed % 100 == 0 or completed == len(pending):
                print(f"  [{completed}/{len(pending)}] {total_shifts:,} shifts inserted so far")

    print("\nShift data pipeline complete")
    print(f"   Shifts inserted: {total_shifts:,}")
    if errors:
        print(f"   Games skipped/errored: {errors}")


if __name__ == "__main__":
    import sys

    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    run(season_arg)
