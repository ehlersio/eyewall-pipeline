#!/usr/bin/env python3
"""
pwhl_live_refresh.py -- frequent refresh of pwhl_game_log's live-volatile
fields (game_state, game_status_code, home_score, away_score, ot, shootout)
for games around today. Run from live-score-refresh.yml.

Thin wrapper over hockeytech_live_refresh.py (shared with
ahl_live_refresh.py and echl_live_refresh.py), which explains why this
exists and why it reads the scorebar view; the PWHL config lives in
hockeytech_leagues.py.

Usage:
    python pwhl_live_refresh.py
"""

import hockeytech_live_refresh as _impl
from hockeytech_leagues import PWHL


def main() -> None:
    _impl.main(PWHL)


if __name__ == "__main__":
    main()
