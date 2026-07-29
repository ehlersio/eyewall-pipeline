"""
backfill_game_xg.py -- One-time historical backfill of game_xg for a season
that predates this pipeline's MoneyPuck game-xG integration.

moneypuck.py's run_game_xg()/run_team_xgf_rollup() only ever run against the
live-resolved *current* NHL_SEASON (see moneypuck.run()'s default arg) --
nightly cron never revisits a season once it rolls over. 2023-24 was current
before this integration existed at all, so it never got backfilled and the
frontend's xGF% per-game chart (TeamComparisonPopup.jsx) has nothing to show
for that season. MoneyPuck's all-teams game-by-game CSV is not season-scoped
by URL (unlike the skaters/goalies CSVs) -- it already contains 2023-24 rows,
this just needs to actually be asked for that season once.

Run: python backfill_game_xg.py 20232024
"""

import sys

from db import get_client
from moneypuck import run_game_xg, run_team_xgf_rollup


def run(season: int) -> None:
    client = get_client()
    print(f"=== game_xg backfill — season {season} ===")
    run_game_xg(client, season)
    run_team_xgf_rollup(client, season)
    print(f"=== Done: season {season} ===")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python backfill_game_xg.py <season>")
        sys.exit(1)
    run(int(sys.argv[1]))
