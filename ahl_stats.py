"""
ahl_stats.py -- AHL rosters, skater/goalie/team stats and game log.

Thin wrapper over hockeytech_stats.py (shared with echl_stats.py); the AHL
config lives in hockeytech_leagues.py.

Usage:
    python ahl_stats.py                  # current season (live-resolved)
    python ahl_stats.py 90               # specific season_id (90 = 2025-26 Regular)
"""

import hockeytech_stats as _impl
from hockeytech_leagues import AHL


def resolve_current_season() -> dict:
    return _impl.resolve_current_season(AHL)


def resolve_season_type(season_id: str) -> str:
    return _impl.resolve_season_type(AHL, season_id)


def _season_day_window(season_id: str):
    return _impl._season_day_window(AHL, season_id)


def run(season_id: str | None = None) -> None:
    _impl.run(AHL, season_id)


if __name__ == "__main__":
    _impl.main(AHL)
