"""
echl_stats.py -- ECHL rosters, skater/goalie/team stats and game log.

Thin wrapper over hockeytech_stats.py (shared with ahl_stats.py); the ECHL
config lives in hockeytech_leagues.py.

Usage:
    python echl_stats.py                  # current season (live-resolved)
    python echl_stats.py 73               # specific season_id (73 = 2025-26 Regular)
"""

import hockeytech_stats as _impl
from hockeytech_leagues import ECHL


def resolve_current_season() -> dict:
    return _impl.resolve_current_season(ECHL)


def resolve_season_type(season_id: str) -> str:
    return _impl.resolve_season_type(ECHL, season_id)


def _season_day_window(season_id: str):
    return _impl._season_day_window(ECHL, season_id)


def run(season_id: str | None = None) -> None:
    _impl.run(ECHL, season_id)


if __name__ == "__main__":
    _impl.main(ECHL)
