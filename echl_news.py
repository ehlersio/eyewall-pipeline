#!/usr/bin/env python3
"""
echl_news.py -- fetch ECHL news from RSS feeds and POST it to the Worker.

Thin wrapper over hockeytech_news.py (shared with ahl_news.py); the ECHL
sources live in hockeytech_leagues.py.

Usage:
    python echl_news.py
"""

import hockeytech_news as _impl
from hockeytech_leagues import ECHL


def main() -> None:
    _impl.main(ECHL)


if __name__ == "__main__":
    main()
