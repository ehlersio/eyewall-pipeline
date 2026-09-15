"""
ahl_shot_events.py -- AHL shot attempts and goals with coordinates, from
play-by-play (ahl_shot_events).

Thin wrapper over hockeytech_shot_events.py (shared with
echl_shot_events.py); the AHL config lives in hockeytech_leagues.py.

Run modes:
  python ahl_shot_events.py                  # ingest current season
  python ahl_shot_events.py 90               # specific season_id
  python ahl_shot_events.py --game 1028992   # single game_id (debug)
"""

import hockeytech_shot_events as _impl
from hockeytech_leagues import AHL


def run(season_id: str | None = None) -> None:
    _impl.run(AHL, season_id)


def run_single_game(game_id: int) -> None:
    _impl.run_single_game(AHL, game_id)


if __name__ == "__main__":
    _impl.main(AHL)
