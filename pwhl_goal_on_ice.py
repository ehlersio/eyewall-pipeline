"""
pwhl_goal_on_ice.py — PWHL goal-level on-ice rosters (pwhl_goal_on_ice).

Thin wrapper over hockeytech_goal_on_ice.py (2026-10), which serves AHL and
ECHL too; output unchanged, pinned by test_goal_on_ice_characterization.py.
One row per (game_goal_id, player_id) from gameSummary's
periods[].goals[].plus_players[]/minus_players[]: plus_players are the
scoring team's skaters on the ice, minus_players the conceding team's.

Convention validated against gameSummary's skaters[].stats.plusMinus
(Session 42, 416/416 player-games; later the full backfill, 10,669/10,669):
summing on_ice_for (+1) / not (-1) over every goal EXCEPT power-play goals
reproduces HockeyTech's plusMinus. Short-handed, empty-net and penalty-shot
goals all count. Each row carries the four flags so consumers can filter
without a join.

Goal-scoped, not shift data: it doesn't change the WAR/RAPM blocker and
shouldn't stand in for shifts in line combinations or on-ice rates.

In pwhl-nightly.yml since 2026-10 (it was manual-only from Session 42).

Run modes:
  python pwhl_goal_on_ice.py                  # ingest current season
  python pwhl_goal_on_ice.py 5                 # specific season_id
  python pwhl_goal_on_ice.py --game 277        # single game_id (debug)
"""

import argparse
import logging

from supabase import create_client

import hockeytech_goal_on_ice as _impl
from hockeytech_goal_on_ice import extract_goal_on_ice  # noqa: F401  (re-exported)
from hockeytech_leagues import PWHL
from pwhl_stats import PWHL_SEASON, SUPABASE_SERVICE_KEY, SUPABASE_URL, _resolve_season_type

log = logging.getLogger(__name__)

PIPELINE = _impl.pipeline_name(PWHL)


def get_skipped_games(sb) -> set:
    return _impl.get_skipped_games(PWHL, sb)


def get_processed_games(sb, season_id: str) -> set:
    return _impl.get_processed_games(PWHL, sb, season_id)


def mark_skipped(sb, game_id: int, reason: str) -> None:
    _impl.mark_skipped(PWHL, sb, game_id, reason)


def ingest_game(sb, gid: int, home_id: int, away_id: int, season_id: str, season_type: str) -> int:
    return _impl.ingest_game(PWHL, sb, gid, home_id, away_id, season_id, season_type)


def run(season_id: str | None = None) -> None:
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping run"
        )
        return
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    _impl.run_season(PWHL, sb, season_id, season_type)


def run_single_game(game_id: int) -> None:
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    row = _impl.game_row(PWHL, sb, game_id)
    if row is None:
        log.error(f"game_id {game_id} not found in pwhl_game_log")
        return

    season_id = str(row["season_id"])
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        raise ValueError(
            f"Unknown season_id {season_id} for game {game_id} — not found in HockeyTech bootstrap data"
        )

    log.info(f"=== PWHL Goal On-Ice -- single game {game_id} (season {season_id}) ===")
    ingest_game(
        sb, game_id, row["home_team_id"] or 0, row["away_team_id"] or 0, season_id, season_type
    )
    log.info("=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PWHL goal-level on-ice roster pipeline")
    parser.add_argument("season", nargs="?", default=None, help="Season ID (e.g. 5, 8, 9)")
    parser.add_argument("--game", type=int, default=None, help="Single game_id (debug)")
    args = parser.parse_args()

    if args.game is not None:
        run_single_game(args.game)
    else:
        run(args.season)
