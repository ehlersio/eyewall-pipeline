#!/usr/bin/env python3
"""
pwhl_news.py -- fetch PWHL news from RSS feeds, keep the PWHL items and POST
them to the Worker (/pwhl/news/ingest).

Thin wrapper over hockeytech_news.py (shared with ahl_news.py and
echl_news.py); the PWHL sources and keyword filter live in
hockeytech_leagues.py.

Usage:
    python pwhl_news.py
"""

import hockeytech_news as _impl
from hockeytech_leagues import PWHL


def main() -> None:
    _impl.main(PWHL)


if __name__ == "__main__":
    main()
