"""
goalie_starts.py -- Every goalie who dressed in every NHL game, and who
started -> `goalie_game_starts`.

Source: the NHL's own gamecenter/{id}/boxscore payload,
playerByGameStats.{homeTeam,awayTeam}.goalies[] -- each dressed goalie's
playerId, name, `starter` flag, TOI ("MM:SS") and decision. The `starter`
flag is the NHL's, present back to at least 2023-24 (checked 2026-09-13
on 2023-24, 2024-25 and 2025-26 games), so starts aren't inferred from
TOI -- a starter pulled in the first period is still the starter. If a
team's goalies ever come back with no starter flagged, the one with the
most TOI is marked started (counted in the run summary, never silent).
Usually two rows per team per game (starter + backup), occasionally three.

This is the history starting_goalie.py learns from, and the ground truth
its predictions are scored against.

Preseason is skipped (game_type 1): split-squad games and camp auditions
say nothing about who a team starts when it counts. Regular season (2) and
playoffs (3) are kept.

Incremental: only games in game_log (completed games -- nhl_stats.py only
writes OFF/FINAL games) with no goalie_game_starts rows yet are fetched.

Usage:
  python goalie_starts.py                  # current season, new games only
  python goalie_starts.py 20232024         # backfill a season
  python goalie_starts.py --game 2025020500 --dry-run
  python run.py goalie_starts [season]     # via orchestrator

Run order: after nhl_stats (needs fresh game_log).
"""

import argparse
import time

from db import NHL_SEASON, get_client, upsert
from nhl_stats import NHL_BASE, nhl_get
from pipeline_common import FetchError
from scratches import fetch_games, fetch_keyset

GAME_TYPES = (2, 3)  # regular season, playoffs
REQUEST_PAUSE = 0.1  # seconds between boxscore calls
WRITE_BATCH = 500


def fetch_boxscore(game_id: int) -> dict | None:
    """Raw gamecenter/{id}/boxscore payload, or None on fetch failure."""
    try:
        return nhl_get(f"{NHL_BASE}/gamecenter/{game_id}/boxscore")
    except FetchError:
        return None


def toi_secs(toi) -> int | None:
    """'62:05' -> 3725; None/malformed -> None."""
    try:
        minutes, seconds = str(toi).split(":")
        return int(minutes) * 60 + int(seconds)
    except (ValueError, AttributeError):
        return None


def parse_goalies(box: dict, game: dict, season: int) -> tuple[list, int]:
    """Boxscore + this game's game_log row -> (goalie_game_starts rows,
    number of teams whose starter had to be inferred from TOI)."""
    stats = (box or {}).get("playerByGameStats") or {}
    rows, inferred = [], 0
    for side, team_key, opp_key, is_home in (
        ("homeTeam", "home_team", "away_team", True),
        ("awayTeam", "away_team", "home_team", False),
    ):
        team_rows = []
        for g in (stats.get(side) or {}).get("goalies") or []:
            if g.get("playerId") is None:
                continue
            team_rows.append(
                {
                    "season": season,
                    "game_id": game["game_id"],
                    "game_date": game["game_date"],
                    "game_type": game["game_type"],
                    "team": game[team_key],
                    "opponent": game[opp_key],
                    "is_home": is_home,
                    "goalie_id": int(g["playerId"]),
                    "goalie_name": (g.get("name") or {}).get("default"),
                    "started": bool(g.get("starter")),
                    "toi_secs": toi_secs(g.get("toi")),
                    "decision": g.get("decision"),
                }
            )
        if team_rows and not any(r["started"] for r in team_rows):
            max(team_rows, key=lambda r: r["toi_secs"] or 0)["started"] = True
            inferred += 1
        rows += team_rows
    return rows, inferred


def fetch_done_game_ids(client, season):
    rows = fetch_keyset(client, "goalie_game_starts", "game_id", lambda q: q.eq("season", season))
    return {r["game_id"] for r in rows}


def run(season=None, game_id=None, dry_run=False):
    season = int(season or NHL_SEASON)
    client = get_client()
    print(f"\n=== Goalie starts -- season {season} ===")

    games = {
        gid: g
        for gid, g in fetch_games(client, season, game_id).items()
        if g.get("game_type") in GAME_TYPES
    }
    done = set() if game_id is not None else fetch_done_game_ids(client, season)
    todo = sorted(gid for gid in games if gid not in done)
    print(f"  {len(games)} completed regular-season/playoff games, {len(todo)} without goalie rows")

    pending, written, failed, inferred = [], 0, [], 0
    for i, gid in enumerate(todo, 1):
        box = fetch_boxscore(gid)
        if box is None:
            failed.append(gid)
            continue
        rows, n_inferred = parse_goalies(box, games[gid], season)
        inferred += n_inferred
        pending += rows
        if not dry_run and len(pending) >= WRITE_BATCH:
            upsert(client, "goalie_game_starts", pending, "game_id,goalie_id")
            written += len(pending)
            pending = []
        if i % 200 == 0:
            print(f"    {i}/{len(todo)} games")
        time.sleep(REQUEST_PAUSE)

    if dry_run:
        for r in pending[:8]:
            print(
                f"    {r['game_id']} {r['team']} {r['goalie_name']}: started={r['started']} toi={r['toi_secs']}"
            )
        print(f"  (dry-run) {len(pending)} rows would be upserted")
    elif pending:
        upsert(client, "goalie_game_starts", pending, "game_id,goalie_id")
        written += len(pending)
    print(
        f"  wrote {written} goalie rows; {len(failed)} boxscore fetch failures"
        f"{f' {failed[:5]}' if failed else ''}; {inferred} team-games with starter inferred from TOI"
    )
    return "ok" if not failed else "partial"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NHL goalie starts per game -> goalie_game_starts")
    parser.add_argument("season", nargs="?", type=int, default=None)
    parser.add_argument("--game", type=int, default=None, help="Single game id (debug)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(season=args.season, game_id=args.game, dry_run=args.dry_run)
