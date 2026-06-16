"""
game_scoring.py — EyeWall Pipeline
Fetches NHL play-by-play and persists goal/assist data to the game_scoring table.

Usage:
    python game_scoring.py                        # current season, all completed games
    python game_scoring.py 20242025               # specific season
    python game_scoring.py 20242025 --backfill    # backfill all completed games for season
    python game_scoring.py --game 2025030414      # single game (useful for testing)
"""

import argparse
import os
import time

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

NHL_BASE = "https://api-web.nhle.com/v1"
NHL_SEASON = int(os.environ.get("NHL_SEASON", "20252026"))
REQUEST_DELAY = 0.5  # seconds between NHL API calls — stay well under rate limits

# ---------------------------------------------------------------------------
# NHL API helpers
# ---------------------------------------------------------------------------


def nhl_get(url: str) -> dict | None:
    try:
        r = requests.get(url, headers={"User-Agent": "EyeWall-Analytics/1.0"}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  NHL API error for {url}: {e}")
        return None


def get_season_schedule(season: int) -> list:
    """
    Fetches all completed games for a season across all teams.
    Uses the standings endpoint to get all team abbrevs, then fetches
    each team's schedule and deduplicates by game_id.
    """
    print(f"Fetching schedule for season {season}...")

    # Get all team abbrevs from standings
    standings = nhl_get(f"{NHL_BASE}/standings/now")
    if not standings:
        print("  Failed to fetch standings — cannot get team list")
        return []

    teams = [t["teamAbbrev"]["default"] for t in standings.get("standings", [])]
    print(f"  Found {len(teams)} teams")

    all_games = {}
    for abbrev in teams:
        url = f"{NHL_BASE}/club-schedule-season/{abbrev}/{season}"
        data = nhl_get(url)
        time.sleep(REQUEST_DELAY)
        if not data:
            continue
        for g in data.get("games", []):
            gid = g.get("id")
            if gid and is_completed(g):
                all_games[gid] = g

    print(f"  Total completed games across all teams: {len(all_games)}")
    return list(all_games.values())


def is_completed(game: dict) -> bool:
    return game.get("gameState") in ("OFF", "FINAL", "OFFICIAL")


# ---------------------------------------------------------------------------
# PBP parsing
# ---------------------------------------------------------------------------


def parse_goals_from_pbp(pbp: dict, game_id: int, season: int) -> list:
    """
    Extracts goal events from NHL PBP response.
    Returns list of dicts ready for Supabase upsert.
    """
    rows = []
    plays = pbp.get("plays", [])

    # Build team id → abbrev map from PBP
    home_team_id = pbp.get("homeTeam", {}).get("id")
    away_team_id = pbp.get("awayTeam", {}).get("id")
    home_abbrev = pbp.get("homeTeam", {}).get("abbrev", "HOME")
    away_abbrev = pbp.get("awayTeam", {}).get("abbrev", "AWAY")

    team_map = {}
    if home_team_id:
        team_map[home_team_id] = home_abbrev
    if away_team_id:
        team_map[away_team_id] = away_abbrev

    for play in plays:
        if play.get("typeDescKey") != "goal":
            continue

        details = play.get("details", {})
        period = play.get("periodDescriptor", {}).get("number")
        owner_id = details.get("eventOwnerTeamId")
        team = team_map.get(owner_id, str(owner_id))

        rows.append(
            {
                "game_id": game_id,
                "season": season,
                "period": period,
                "time_in_period": play.get("timeInPeriod"),
                "team": team,
                "scorer_id": details.get("scoringPlayerId"),
                "assist1_id": details.get("assist1PlayerId"),
                "assist2_id": details.get("assist2PlayerId"),
                "situation_code": play.get("situationCode"),
                "shot_type": details.get("shotType"),
                "home_score": details.get("homeScore"),
                "away_score": details.get("awayScore"),
            }
        )

    return rows


# ---------------------------------------------------------------------------
# Supabase write
# ---------------------------------------------------------------------------


def upsert_goals(rows: list) -> int:
    if not rows:
        return 0
    # Deduplicate within batch by conflict key
    seen = set()
    deduped = []
    for r in rows:
        key = (r["game_id"], r["period"], r["time_in_period"], r["team"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    try:
        supabase.table("game_scoring").upsert(
            deduped, on_conflict="game_id,period,time_in_period,team"
        ).execute()
        return len(deduped)
    except Exception as e:
        print(f"  Upsert error: {e}")
        return 0


def already_processed(game_id: int) -> bool:
    """Returns True if we already have scoring data for this game."""
    result = (
        supabase.table("game_scoring")
        .select("id", count="exact")
        .eq("game_id", game_id)
        .limit(1)
        .execute()
    )
    return (result.count or 0) > 0


# ---------------------------------------------------------------------------
# Single game processor
# ---------------------------------------------------------------------------


def process_game(game_id: int, season: int, force: bool = False) -> bool:
    """
    Fetches PBP for a single game and upserts goal/assist data.
    Returns True on success.
    """
    if not force and already_processed(game_id):
        print(f"  {game_id} — already processed, skipping")
        return True

    pbp = nhl_get(f"{NHL_BASE}/gamecenter/{game_id}/play-by-play")
    time.sleep(REQUEST_DELAY)

    if not pbp:
        print(f"  {game_id} — failed to fetch PBP")
        return False

    rows = parse_goals_from_pbp(pbp, game_id, season)
    if not rows:
        print(f"  {game_id} — no goals found in PBP (may be 0-0 or bad data)")
        return False

    count = upsert_goals(rows)
    print(f"  {game_id} — upserted {count} goals")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="EyeWall game scoring pipeline")
    parser.add_argument(
        "season", nargs="?", type=int, default=NHL_SEASON, help="Season to process (e.g. 20252026)"
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Process all completed games, not just unprocessed ones",
    )
    parser.add_argument("--game", type=int, default=None, help="Process a single game ID")
    parser.add_argument(
        "--force", action="store_true", help="Re-process even if already in database"
    )
    args = parser.parse_args()

    season = args.season

    # Single game mode
    if args.game:
        print(f"Processing single game {args.game} (season {season})")
        process_game(args.game, season, force=args.force)
        return

    # Full season mode
    print(f"Processing season {season} ({'backfill all' if args.backfill else 'unprocessed only'})")
    games = get_season_schedule(season)

    if not games:
        print("No completed games found — exiting")
        return

    total = len(games)
    processed = 0
    skipped = 0
    failed = 0

    for i, game in enumerate(games, 1):
        game_id = game.get("id")
        print(f"[{i}/{total}] Game {game_id}", end=" ")

        if already_processed(game_id) and not args.force:
            print("— skipped (already processed)")
            skipped += 1
            continue

        success = process_game(game_id, season, force=args.force)
        if success:
            processed += 1
        else:
            failed += 1

        # Small additional delay every 50 games to be extra safe
        if i % 50 == 0:
            print(f"  [Pause after {i} games...]")
            time.sleep(2)

    print(f"\nDone. Processed: {processed} | Skipped: {skipped} | Failed: {failed}")


if __name__ == "__main__":
    main()
