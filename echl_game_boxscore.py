"""
echl_game_boxscore.py -- ECHL per-game, per-player box scores
(echl_skater_game_box / echl_goalie_game_box).

Thin wrapper over hockeytech_game_boxscore.py (shared with
ahl_game_boxscore.py); the ECHL config lives in hockeytech_leagues.py.

Run modes:
  python echl_game_boxscore.py                 # ingest current season
  python echl_game_boxscore.py 73              # specific season_id
  python echl_game_boxscore.py --game 24296    # single game_id (debug)
"""

import hockeytech_game_boxscore as _impl
from hockeytech_leagues import ECHL


def run(season_id: str | None = None) -> None:
    _impl.run(ECHL, season_id)


def run_single_game(game_id: int) -> None:
    _impl.run_single_game(ECHL, game_id)


if __name__ == "__main__":
    _impl.main(ECHL)
