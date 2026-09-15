"""
echl_penalty_shots.py -- ECHL penalty shots (make or miss), from
play-by-play (echl_penalty_shots).

Thin wrapper over hockeytech_penalty_shots.py (shared with
ahl_penalty_shots.py); the ECHL config lives in hockeytech_leagues.py.

Run modes:
  python echl_penalty_shots.py                 # ingest current season
  python echl_penalty_shots.py 73              # specific season_id
  python echl_penalty_shots.py --game 24320    # single game_id (debug)
"""

import hockeytech_penalty_shots as _impl
from hockeytech_leagues import ECHL


def run(season_id: str | None = None) -> None:
    _impl.run(ECHL, season_id)


def run_single_game(game_id: int) -> None:
    _impl.run_single_game(ECHL, game_id)


if __name__ == "__main__":
    _impl.main(ECHL)
