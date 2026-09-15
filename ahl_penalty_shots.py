"""
ahl_penalty_shots.py -- AHL penalty shots (make or miss), from
play-by-play (ahl_penalty_shots).

Thin wrapper over hockeytech_penalty_shots.py (shared with
echl_penalty_shots.py); the AHL config lives in hockeytech_leagues.py.

Run modes:
  python ahl_penalty_shots.py                  # ingest current season
  python ahl_penalty_shots.py 90               # specific season_id
  python ahl_penalty_shots.py --game 1028362   # single game_id (debug)
"""

import hockeytech_penalty_shots as _impl
from hockeytech_leagues import AHL


def run(season_id: str | None = None) -> None:
    _impl.run(AHL, season_id)


def run_single_game(game_id: int) -> None:
    _impl.run_single_game(AHL, game_id)


if __name__ == "__main__":
    _impl.main(AHL)
