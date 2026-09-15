#!/usr/bin/env python3
"""
ahl_news.py -- fetch AHL news from RSS feeds and POST it to the Worker.

Thin wrapper over hockeytech_news.py (shared with echl_news.py); the AHL
sources live in hockeytech_leagues.py.

Usage:
    python ahl_news.py
"""

import hockeytech_news as _impl
from hockeytech_leagues import AHL


def main() -> None:
    _impl.main(AHL)


if __name__ == "__main__":
    main()
