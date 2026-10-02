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

Every player a row names (shooter, goalie, assists, blocker) is also made sure of in
`players`: nhl_stats.py only adds rostered players and those with
regular-season or playoff stats, so a prospect who only played preseason
games had no name anywhere -- the shot map's season view said "Unknown"
for his shots (2026-10). Names come from each game's own rosterSpots.

Usage:
  python shot_events.py                       # current season
  python shot_events.py 20242025              # backfill a prior season
  python shot_events.py --players [season]    # just add the season's unnamed players
  python shot_events.py --reprocess [season]  # rewrite every game (fill a new column)
"""

import time
import traceback

import requests

from db import NHL_SEASON, get_client, upsert
from pipeline_common import (
    NHL_PLAYOFFS,
    NHL_PRESEASON,
    NHL_REGULAR_SEASON,
    FetchError,
    select_all,
)

NHL_BASE = "https://api-web.nhle.com/v1"
CAR_ABBR = "CAR"
HEADERS = {"User-Agent": "EyeWall-Analytics/1.0 (eyewallanalytics.com)"}

SHOT_TYPES = {"shot-on-goal", "missed-shot", "blocked-shot", "goal"}

# Every column of a row that holds an NHL player id -- each one is made
# sure of in `players` (see add_missing_players)
PLAYER_COLUMNS = ("player_id", "goalie_id", "assist1_id", "assist2_id", "blocker_id")

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
        raise FetchError(f"{url} -- {e}") from e


def get_all_completed_games(season):
    """Get all unique completed games across all 32 teams."""
    seen = set()
    games = []
    for team in ALL_TEAMS:
        try:
            data = nhl_get(f"{NHL_BASE}/club-schedule-season/{team}/{season}")
        except FetchError as e:
            print(f"  ERROR {e}")
            continue
        for g in data.get("games", []):
            gid = g.get("id")
            if gid and gid not in seen and g.get("gameState") in ("OFF", "FINAL", "F"):
                seen.add(gid)
                games.append(g)
        time.sleep(0.1)
    return sorted(games, key=lambda g: g["id"])


def get_already_processed(client, season):
    """Get game IDs already in shot_events (keyset-paginated -- see
    line_combinations.py::fetch_all's docstring for why OFFSET pagination
    is a timeout risk on this table as it grows every game of every
    season).

    Pages one game type at a time so each page is served by
    shot_events_season_type_id_idx (season, game type, id) -- see
    docs/game_type_column.sql. Filtering on season alone has no index in
    id order, so Postgres walks the primary key from the oldest row and
    discards every other season's rows on the way. A new season's rows are
    all at the end of the table, which is how the first page timed out
    (57014) on 2026-09-30 and left 2026-27's regular-season games out."""
    all_ids = set()
    for game_type in (NHL_PRESEASON, NHL_REGULAR_SEASON, NHL_PLAYOFFS):
        last_id = 0
        while True:
            rows = (
                client.table("shot_events")
                .select("id,game_id")
                .eq("season", season)
                .eq("game_type", game_type)
                .gt("id", last_id)
                .order("id")
                .limit(999)
                .execute()
                .data
            )
            if not rows:
                break
            all_ids.update(r["game_id"] for r in rows)
            last_id = rows[-1]["id"]
            if len(rows) < 999:
                break
    return all_ids


def roster_players(pbp) -> dict:
    """id -> {id, name, position} for everyone in a game's rosterSpots."""
    out = {}
    for s in pbp.get("rosterSpots") or []:
        pid = s.get("playerId")
        first = (s.get("firstName") or {}).get("default", "")
        last = (s.get("lastName") or {}).get("default", "")
        name = f"{first} {last}".strip()
        if pid and name:
            out[pid] = {"id": pid, "name": name, "position": s.get("positionCode")}
    return out


def known_player_ids(client) -> set:
    """Every player id already in `players` (paged -- it's past 1,000 rows)."""
    return {r["id"] for r in select_all(lambda: client.table("players").select("id"), order="id")}


def add_missing_players(client, ids, known: set, roster: dict) -> list:
    """Insert into `players` each of `ids` it doesn't have yet, named from
    `roster` (a game's rosterSpots) or, failing that, the player's NHL
    landing page. Only ever adds rows: a player already there keeps his
    row as nhl_stats.py wrote it (team, bio). Adds the new ids to `known`
    and returns the rows inserted."""
    rows = []
    for pid in sorted({i for i in ids if i} - known):
        row = roster.get(pid)
        if not row:
            try:
                data = nhl_get(f"{NHL_BASE}/player/{pid}/landing")
            except FetchError as e:
                print(f"  ERROR: {e}")
                continue
            name = (
                f"{(data.get('firstName') or {}).get('default', '')} "
                f"{(data.get('lastName') or {}).get('default', '')}"
            ).strip()
            if not name:
                continue
            row = {"id": pid, "name": name, "position": data.get("position")}
            time.sleep(0.1)
        rows.append(row)
    if rows:
        upsert(client, "players", rows, "id")
        known.update(r["id"] for r in rows)
    return rows


def process_game(game, season, roster_out=None):
    game_id = game["id"]
    home_abbr = game.get("homeTeam", {}).get("abbrev", "")
    away_abbr = game.get("awayTeam", {}).get("abbrev", "")
    is_car_game = home_abbr == CAR_ABBR or away_abbr == CAR_ABBR
    is_playoff = game.get("gameType") == 3

    pbp = nhl_get(f"{NHL_BASE}/gamecenter/{game_id}/play-by-play")
    if not pbp.get("plays"):
        return []
    # The caller's chance to name anyone these rows mention (see run())
    if roster_out is not None:
        roster_out.update(roster_players(pbp))

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
                # The NHL's own id for this play. Unique within a game, not
                # across games -- (game_id, event_id) is the identifying
                # pair. It's what lets a row here be pointed back at the
                # play it came from: the game center `landing` feed names
                # the same id for a goal, and a goal's tracking replay is
                # addressed by (gameId, eventId). See
                # docs/shot_events_event_id.sql.
                "event_id": play.get("eventId"),
                "player_id": shooter_id,
                "goalie_id": goalie_id,
                # Who assisted (goals) and who blocked (blocked shots) --
                # see docs/shot_events_assists_blocker.sql
                "assist1_id": d.get("assist1PlayerId"),
                "assist2_id": d.get("assist2PlayerId"),
                "blocker_id": d.get("blockingPlayerId"),
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


def run(season=NHL_SEASON, reprocess=False):
    """Ingest `season`'s completed games not yet in shot_events. With
    `reprocess`, rewrite every completed game instead -- for filling a new
    column on rows already ingested (each game's rows are replaced whole)."""
    client = get_client()
    print(f"\n=== Shot Events Pipeline (league-wide) -- Season {season} ===")

    print("  Fetching all league game IDs...")
    games = get_all_completed_games(season)
    print(f"  Found {len(games):,} completed games across all 32 teams")

    if reprocess:
        pending = games
        print(f"  Re-processing all {len(pending):,}")
    else:
        already_done = get_already_processed(client, season)
        pending = [g for g in games if g["id"] not in already_done]
        print(f"  {len(already_done):,} already processed, {len(pending):,} pending")

    if not pending:
        print("  All games already processed")
        return

    known = None  # player ids in `players`, read once the first game has shots
    added_players = 0
    total_shots = 0
    errors = 0  # process_game() returned no data (game has no PBP plays yet, e.g. postponed)
    fetch_failed = 0  # nhl_get() raised FetchError -- the fetch itself broke, not a parser bug
    crashed = 0  # process_game() raised something else -- a real parsing/schema exception

    for i, game in enumerate(pending):
        try:
            roster = {}
            shots = process_game(game, season, roster_out=roster)
        except FetchError as e:
            # Fetch failed (network/HTTP/JSON) after nhl_get()'s own handling -- kept
            # separate from `crashed` so "Games that crashed the parser" isn't inflated
            # by fetch failures that have nothing to do with parsing logic.
            print(f"  !! FETCH FAILED on game {game.get('id')}: {e}")
            fetch_failed += 1
            continue
        except Exception as e:
            # One malformed game must not abort the whole season's run -- log loudly
            # (full traceback + game_id) and move on to the next game.
            print(f"  !! CRASHED on game {game.get('id')}: {type(e).__name__}: {e}")
            traceback.print_exc()
            crashed += 1
            continue

        if shots:
            client.table("shot_events").delete().eq("game_id", game["id"]).execute()
            for j in range(0, len(shots), 500):
                client.table("shot_events").insert(shots[j : j + 500]).execute()
            total_shots += len(shots)
            if known is None:
                known = known_player_ids(client)
            ids = [s[k] for s in shots for k in PLAYER_COLUMNS]
            added_players += len(add_missing_players(client, ids, known, roster))
        else:
            errors += 1

        if (i + 1) % 100 == 0 or (i + 1) == len(pending):
            print(f"  [{i + 1}/{len(pending)}] {total_shots:,} shots inserted so far")

        time.sleep(0.3)

    print("\nShot events pipeline complete")
    print(f"   Shots inserted: {total_shots:,}")
    if added_players:
        print(f"   Players added to `players`: {added_players}")
    if errors:
        print(f"   Games with no data: {errors}")
    if fetch_failed:
        print(f"   Games where the fetch failed: {fetch_failed}")
    if crashed:
        print(f"   Games that crashed the parser: {crashed}")


def run_missing_players(season=NHL_SEASON):
    """Add every player `season`'s shot_events rows name but `players`
    lacks -- the backfill for rows written before run() did this itself.
    Names come from each player's NHL landing page."""
    client = get_client()
    print(f"\n=== Shot events: missing players -- Season {season} ===")
    # Keyset-paged, like get_already_processed(): an OFFSET walk over this
    # table times out (it did, 2026-10).
    ids, last_id, n = set(), 0, 0
    while True:
        rows = (
            client.table("shot_events")
            .select("id," + ",".join(PLAYER_COLUMNS))
            .eq("season", season)
            .gt("id", last_id)
            .order("id")
            .limit(999)
            .execute()
            .data
        )
        if not rows:
            break
        n += len(rows)
        ids.update(r[k] for r in rows for k in PLAYER_COLUMNS)
        last_id = rows[-1]["id"]
        if len(rows) < 999:
            break
    added = add_missing_players(client, ids, known_player_ids(client), {})
    for r in added:
        print(f"  + {r['id']} {r['name']} ({r['position']})")
    print(f"  {len(added)} added, from {n:,} rows")


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if args and args[0] == "--players":
        run_missing_players(*([int(args[1])] if len(args) > 1 else []))
    elif args and args[0] == "--reprocess":
        run(season=int(args[1]) if len(args) > 1 else NHL_SEASON, reprocess=True)
    else:
        season_arg = int(args[0]) if args else NHL_SEASON
        run(season=season_arg)
