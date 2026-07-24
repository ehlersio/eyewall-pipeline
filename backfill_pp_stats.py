"""
backfill_pp_stats.py -- One-time backfill of game_log's
pp_goals/pp_opps/pk_goals_against/pk_opps (+ team_scored_first where still
null) using the corrected enrich_game_log() (NHL's official right-rail box
score, see nhl_stats.fetch_pp_stats). See PP_GOALS_FULL_FIX.md.

- 2025-26: force_all=True -- the old situationCode reconstruction had
  already written wrong-but-non-null values for most rows, so the
  incremental (null-only) gate wouldn't touch them.
- 2023-24, 2024-25: 100% null already, force_all is a no-op there but kept
  for consistency/clarity of intent.

Run: python backfill_pp_stats.py 20252026
     python backfill_pp_stats.py 20232024
     python backfill_pp_stats.py 20242025
"""

import sys

from db import get_client
from nhl_stats import enrich_game_log


def run(season: int) -> int:
    client = get_client()
    print(f"=== PP/PK backfill — season {season} ===")
    updated = enrich_game_log(client, season, force_all=True)
    print(f"=== Done: {updated} rows updated for season {season} ===")
    return updated


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python backfill_pp_stats.py <season>")
        sys.exit(1)
    run(int(sys.argv[1]))
