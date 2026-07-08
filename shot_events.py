"""
shot_events.py — Aggregate shot coordinates from NHL PBP for ALL league games.
                 Writes to shot_events table in Supabase.

League-wide coverage is required for RAPM — CAR-only shots (~25k)
don't give the ridge regression enough variance to separate individual
contributions from linemate quality. League-wide gives ~800k events.

Key changes from CAR-only version:
  - Fetches all 32 teams' schedules to get every game
  - team column = real abbreviation (e.g. 'BOS', 'TBL') not 'CAR'/'OPP'
  - car_game = True for games involving CAR (used by frontend heat maps)
  - Frontend filters: heat maps use car_game=True, team='CAR'/'OPP'-equivalent
    by checking if team == 'CAR' or (car_game and team != 'CAR')

Frontend compatibility:
  - Skater heat maps:  car_game=True AND team='CAR'
  - Goalie heat maps:  car_game=True AND team!='CAR' AND goalie_id=<id>
  - RAPM:             situation_code='1551' (all teams, no car_game filter)

Usage:
  python shot_events.py              # current season
  python shot_events.py 20242025     # backfill a prior season
"""

import time
import traceback

import requests

from db import NHL_SEASON, get_client

NHL_BASE = "https://api-web.nhle.com/v1"
CAR_ABBR = "CAR"
HEADERS = {"User-Agent": "EyeWall-Analytics/1.0 (eyewallanalytics.com)"}

SHOT_TYPES = {"shot-on-goal", "missed-shot", "blocked-shot", "goal"}

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


def nhl_get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  ERROR {url} -- {e}")
        return None


def get_all_completed_games(season):
    """Get all unique completed games across all 32 teams."""
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


def get_already_processed(client, season):
    """Get game IDs already in shot_events (paginated)."""
    all_ids = set()
    offset = 0
    while True:
        rows = (
            client.table("shot_events")
            .select("game_id")
            .eq("season", season)
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not rows:
            break
        all_ids.update(r["game_id"] for r in rows)
        offset += 1000
    return all_ids


def process_game(game, season):
    game_id = game["id"]
    home_abbr = game.get("homeTeam", {}).get("abbrev", "")
    away_abbr = game.get("awayTeam", {}).get("abbrev", "")
    is_car_game = home_abbr == CAR_ABBR or away_abbr == CAR_ABBR
    is_playoff = game.get("gameType") == 3

    pbp = nhl_get(f"{NHL_BASE}/gamecenter/{game_id}/play-by-play")
    if not pbp or not pbp.get("plays"):
        return []

    # Build team ID -> abbrev map from PBP roster
    home_id = pbp.get("homeTeam", {}).get("id")
    away_id = pbp.get("awayTeam", {}).get("id")
    home_abbr_pbp = pbp.get("homeTeam", {}).get("abbrev", home_abbr)
    away_abbr_pbp = pbp.get("awayTeam", {}).get("abbrev", away_abbr)

    def team_abbr(team_id):
        if team_id == home_id:
            return home_abbr_pbp
        if team_id == away_id:
            return away_abbr_pbp
        return ""

    shots = []
    for play in pbp["plays"]:
        if play.get("typeDescKey") not in SHOT_TYPES:
            continue
        d = play.get("details", {})
        if d.get("xCoord") is None:
            continue

        owner_team_id = d.get("eventOwnerTeamId")
        shooter_id = d.get("scoringPlayerId") or d.get("shootingPlayerId")
        goalie_id = d.get("goalieInNetId")
        situation_code = play.get("situationCode")

        if not shooter_id:
            continue

        shooter_team = team_abbr(owner_team_id)

        shots.append(
            {
                "player_id": shooter_id,
                "goalie_id": goalie_id,
                "season": season,
                "game_id": game_id,
                "team": shooter_team,  # real abbrev e.g. 'BOS'
                "car_game": is_car_game,  # True if CAR played in this game
                "period": play.get("periodDescriptor", {}).get("number"),
                "time_in_period": play.get("timeInPeriod"),
                "x": d["xCoord"],
                "y": d.get("yCoord"),
                "shot_type": d.get("shotType"),
                "event_type": play["typeDescKey"],
                "is_playoff": is_playoff,
                "situation_code": situation_code,
            }
        )

    return shots


def run(season=NHL_SEASON):
    client = get_client()
    print(f"\n=== Shot Events Pipeline (league-wide) -- Season {season} ===")

    print("  Fetching all league game IDs...")
    games = get_all_completed_games(season)
    print(f"  Found {len(games):,} completed games across all 32 teams")

    already_done = get_already_processed(client, season)
    pending = [g for g in games if g["id"] not in already_done]
    print(f"  {len(already_done):,} already processed, {len(pending):,} pending")

    if not pending:
        print("  All games already processed")
        return

    total_shots = 0
    errors = (
        0  # process_game() returned no data (nhl_get already swallowed the fetch failure/absence)
    )
    crashed = (
        0  # process_game() itself raised -- a real parsing/schema exception, not a fetch failure
    )

    for i, game in enumerate(pending):
        try:
            shots = process_game(game, season)
        except Exception as e:
            # One malformed game must not abort the whole season's run -- log loudly
            # (full traceback + game_id) and move on to the next game. Kept as its own
            # `crashed` bucket rather than folded into `errors` because this is a
            # different failure shape than "no data": nhl_get() already swallows
            # network/JSON failures to None before this point (see Item 3 in
            # SESSION_46_SCOPE.md -- once that lands with a distinct FetchError,
            # this except block should catch that separately from other exceptions
            # too, same reasoning as here: "fetch failed" and "parsing broke" are
            # different signals worth keeping apart in the logs).
            print(f"  !! CRASHED on game {game.get('id')}: {type(e).__name__}: {e}")
            traceback.print_exc()
            crashed += 1
            continue

        if shots:
            client.table("shot_events").delete().eq("game_id", game["id"]).execute()
            for j in range(0, len(shots), 500):
                client.table("shot_events").insert(shots[j : j + 500]).execute()
            total_shots += len(shots)
        else:
            errors += 1

        if (i + 1) % 100 == 0 or (i + 1) == len(pending):
            print(f"  [{i + 1}/{len(pending)}] {total_shots:,} shots inserted so far")

        time.sleep(0.3)

    print("\nShot events pipeline complete")
    print(f"   Shots inserted: {total_shots:,}")
    if errors:
        print(f"   Games with no data: {errors}")
    if crashed:
        print(f"   Games that crashed the parser: {crashed}")


if __name__ == "__main__":
    import sys

    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    run(season=season_arg)
