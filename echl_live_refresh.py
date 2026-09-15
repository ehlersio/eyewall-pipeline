#!/usr/bin/env python3
"""
echl_live_refresh.py -- frequent refresh of echl_game_log's live-volatile
fields (game_state, game_status_code, home_score, away_score) for games
around today. Run from live-score-refresh.yml.

Thin wrapper over hockeytech_live_refresh.py (shared with
ahl_live_refresh.py), which explains why this exists.

Usage:
    python echl_live_refresh.py
"""

import hockeytech_live_refresh as _impl
from hockeytech_leagues import ECHL


def main() -> None:
    _impl.main(ECHL)


if __name__ == "__main__":
    main()
