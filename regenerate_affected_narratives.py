"""
regenerate_affected_narratives.py -- One-off driver (not part of the nightly
pipeline) to regenerate game_summaries entries confirmed wrong by
diagnose_narrative_impact.py (see PP_GOALS_FULL_FIX.md's narrative
regeneration deliverable). Reads narrative_impact_result.json's
affected_game_ids and regenerates both teams' narratives for each -- the
existing ai_summaries.process_game() has no way to regenerate a single
team's narrative in isolation, and leaving one team's narrative
stale/mismatched next to the other's freshly-regenerated one for the same
game would be inconsistent.

Run: python regenerate_affected_narratives.py [--limit N]
"""

import json
import sys
import time

from ai_summaries import REQUEST_DELAY, process_game, supabase
from db import NHL_SEASON


def run(limit: int | None = None, skip: int = 0):
    with open("narrative_impact_result.json") as f:
        data = json.load(f)
    affected_game_ids = data["affected_game_ids"]
    if skip:
        affected_game_ids = affected_game_ids[skip:]
    if limit:
        affected_game_ids = affected_game_ids[:limit]

    print(f"Regenerating narratives for {len(affected_game_ids)} games...")

    generated = 0
    failed = 0
    for i, game_id in enumerate(affected_game_ids, 1):
        row = (
            supabase.table("game_log")
            .select("home_team,away_team,season")
            .eq("game_id", game_id)
            .limit(1)
            .execute()
            .data
        )
        if not row:
            print(f"[{i}/{len(affected_game_ids)}] {game_id} — not found in game_log, skipping")
            continue

        season = row[0].get("season") or NHL_SEASON
        home_team = row[0]["home_team"]
        away_team = row[0]["away_team"]
        print(f"[{i}/{len(affected_game_ids)}] Game {game_id} ({away_team} @ {home_team})")

        try:
            home_ok, away_ok = process_game(game_id, season, home_team, away_team, force=True)
        except Exception as e:
            print(f"  ERROR on game {game_id}: {e}")
            failed += 2
            time.sleep(REQUEST_DELAY)
            continue
        generated += (1 if home_ok else 0) + (1 if away_ok else 0)
        failed += (0 if home_ok else 1) + (0 if away_ok else 1)

        time.sleep(REQUEST_DELAY)

    print(f"\nDone. Generated: {generated} | Failed: {failed}")


if __name__ == "__main__":
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    skip = 0
    if "--skip" in sys.argv:
        skip = int(sys.argv[sys.argv.index("--skip") + 1])
    run(limit=limit, skip=skip)
