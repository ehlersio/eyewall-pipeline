"""
ahl_game_boxscore.py -- AHL per-game, per-player box scores
(ahl_skater_game_box / ahl_goalie_game_box).

Thin wrapper over hockeytech_game_boxscore.py (shared with
echl_game_boxscore.py); the AHL config lives in hockeytech_leagues.py.

Run modes:
  python ahl_game_boxscore.py                  # ingest current season
  python ahl_game_boxscore.py 90               # specific season_id
  python ahl_game_boxscore.py --game 1028992   # single game_id (debug)
"""

import hockeytech_game_boxscore as _impl
from hockeytech_leagues import AHL


def run(season_id: str | None = None) -> None:
    _impl.run(AHL, season_id)


def run_single_game(game_id: int) -> None:
    _impl.run_single_game(AHL, game_id)


if __name__ == "__main__":
    _impl.main(AHL)
