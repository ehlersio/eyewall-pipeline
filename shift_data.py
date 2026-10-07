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

Skaters only (2026-10). Goalie shifts are left out on both paths by the
game's roster (play-by-play rosterSpots positionCode == "G"). The JSON path
used to test the shiftcharts row's detailCode == 1, which never matches a
goalie (detailCode is 0 on every shift row, the feed has no position), so
every goalie shift was stored and reached RAPM's design matrix, line
combinations and special teams. Rows ingested before the fix still carry
goalie shifts until the season is re-ingested (delete its shift_events rows,
then run this again).

Skips (2026-10). A game goes into skipped_games -- never retried -- only when
the feeds answered and had nothing: no shiftcharts rows and no HTML shift
report rows. Any error leaves the game pending for the next run instead: a
fetch that failed (timeout, connection error, 429/5xx after nhl_get's
retries, or an HTML report answering 429/5xx) or anything else that raised.
Before, any error marked the game skipped for good with the error text as
its reason, so one bad night lost those games' shifts for the season.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from db import NHL_SEASON, get_client
from pipeline_common import NHL_RETRY_STATUSES, FetchError, nhl_get

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
            print(f"  ERROR: {e}")
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
    """Get game IDs previously marked as having no shift data.

    Left on OFFSET pagination (Session 47 audit #10 pass, accept-and-
    monitor): skipped_games only holds games with no data at all, a small
    fraction of a season -- no timeout history, revisit if that changes.
    """
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
    """Fetch raw shift chart rows from NHL API for a single game. Raises
    FetchError when the fetch broke; shifts_for_game() still tries the HTML
    reports then, but won't call the game empty on their word alone.
    """
    data = nhl_get(f"{STATS_BASE}/shiftcharts", params={"cayenneExp": f"gameId={game_id}"})
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


def goalie_ids(roster) -> set[int]:
    """player_ids fetch_roster() lists as goalies (positionCode "G")."""
    return {pid for pid, _team, pos in roster.values() if pos == "G"}


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

    A report that isn't there (404 and the like) just adds no rows. One that
    couldn't be fetched (timeout, connection error, 429/5xx) raises
    FetchError, even if the other report came back: half a game's shifts
    would be stored as the whole game and never fetched again.
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
    failed = []
    for report_type in ["TV", "TH"]:  # visitor, home
        url = f"{HTML_REPORTS_BASE}/{season_str}/{report_type}{short_id}.HTM"
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            failed.append(f"{url}: {e}")
            continue
        if r.status_code in NHL_RETRY_STATUSES:
            failed.append(f"{url}: HTTP {r.status_code}")
            continue
        if r.status_code != 200:
            continue
        try:
            rows = parse_html_shifts(game_id, season, r.text, roster)
        except Exception as e:
            print(f"  HTML parse error {url}: {e}")
            continue
        all_rows.extend(rows)
        print(f"  Game {game_id}: {len(all_rows)} shifts from HTML")

    if failed:
        raise FetchError(f"HTML shift reports: {'; '.join(failed)}")
    return all_rows


def mark_skipped(client, game_id, season, reason="no_data"):
    """Mark a game as having no shift data so it won't be retried. Only for
    a game the feeds answered with nothing -- see the module docstring."""
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


def process_shifts(game_id, season, raw_shifts, goalies=frozenset()):
    """Convert raw shift chart rows into shift_events rows for both teams,
    skaters only: `goalies` is the game's goalie player_ids (goalie_ids())
    -- the shiftcharts rows themselves carry no position."""
    rows = []
    for shift in raw_shifts:
        player_id = shift.get("playerId")
        team_abbrev = shift.get("teamAbbrev", "")
        start_str = shift.get("startTime", "0:00")
        end_str = shift.get("endTime", "0:00")
        period = shift.get("period", 1)

        if not player_id:
            continue
        if player_id in goalies:  # excluded from the skater matrix
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


def shifts_for_game(game_id, season):
    """One game's skater shift_events rows: the JSON shiftcharts feed first
    (fast, early-season games), else the HTML shift reports (every game).
    Either way the game's roster says who the goalies are.

    [] means both feeds answered and had nothing for this game. Raises
    FetchError when a fetch failed and the game may have shifts we couldn't
    get: the roster, an HTML report, or the JSON feed when the HTML reports
    came back empty."""
    try:
        raw = fetch_shift_chart(game_id)
        json_error = None
    except FetchError as e:
        raw, json_error = [], e
    if raw:
        roster, _ = fetch_roster(game_id)
        rows = process_shifts(game_id, season, raw, goalie_ids(roster))
        if rows:
            return rows
    rows = fetch_shift_chart_html(game_id, season)
    if not rows and json_error is not None:
        raise json_error
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
    no_data = 0
    completed = 0
    WORKERS = 5  # reduced from 10 — HTML fallback makes 3 requests/game

    def process_one(game):
        """(game_id, rows, error). Only an error-free empty result is
        no_data; any error leaves the game for the next run."""
        game_id = game["id"]
        try:
            return game_id, shifts_for_game(game_id, season), None
        except Exception as e:
            return game_id, [], f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(process_one, g): g for g in pending}
        for future in as_completed(futures):
            game_id, rows, error = future.result()
            completed += 1

            if error:
                print(f"  RETRY NEXT RUN Game {game_id}: {error}")
                errors += 1
            elif not rows:
                print(f"  SKIP Game {game_id}: no_data")
                mark_skipped(client, game_id, season, "no_data")
                no_data += 1
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
    if no_data:
        print(f"   Games skipped (no shift data): {no_data}")
    if errors:
        print(f"   Games failed (left for the next run): {errors}")


if __name__ == "__main__":
    import sys

    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    run(season_arg)
