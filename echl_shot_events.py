"""
echl_shot_events.py -- ECHL shot attempts and goals with coordinates, from
play-by-play (echl_shot_events).

Thin wrapper over hockeytech_shot_events.py (shared with
ahl_shot_events.py); the ECHL config lives in hockeytech_leagues.py.

Run modes:
  python echl_shot_events.py                 # ingest current season
  python echl_shot_events.py 73              # specific season_id
  python echl_shot_events.py --game 24296    # single game_id (debug)
"""

import hockeytech_shot_events as _impl
from hockeytech_leagues import ECHL


def run(season_id: str | None = None) -> None:
    _impl.run(ECHL, season_id)


def run_single_game(game_id: int) -> None:
    _impl.run_single_game(ECHL, game_id)


if __name__ == "__main__":
    _impl.main(ECHL)
