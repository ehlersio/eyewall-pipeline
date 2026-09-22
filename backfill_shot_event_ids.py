"""
backfill_shot_event_ids.py -- fills shot_events.event_id for games ingested
before that column existed (see docs/shot_events_event_id.sql, run that
first).

shot_events.py skips any game already in the table, so the new column would
otherwise stay null forever on every game already there. This re-fetches a
game's play-by-play and rewrites its rows through shot_events.process_game()
-- the same code the nightly run uses, so the rows come out identical apart
from the new id.

Scope: the NHL publishes player-and-puck tracking replays from 2023-24 on
(checked 2026-09), and the id's whole purpose here is addressing those, so
that's the default range. Pass --season to do one.

Safe to stop and re-run: it only picks up games that still have a row with
no event_id, so a second run resumes where the first left off. Rewriting a
game deletes and re-inserts its rows, which changes their row ids --
nothing stores those (they're used only for keyset pagination within a
single query), confirmed across this repo and eyewall-poller.

Usage:
  python backfill_shot_event_ids.py --dry-run        # count the work, touch nothing
  python backfill_shot_event_ids.py                  # 2023-24 onward
  python backfill_shot_event_ids.py --season 20252026
  python backfill_shot_event_ids.py --limit 25       # a first slice, to watch it
"""

import argparse
import sys
import time
import traceback

import shot_events
from db import get_client
from pipeline_common import FetchError, select_all

# Seasons with tracking replays -- earlier ones gain nothing from the id.
SEASONS = (20232024, 20242025, 20252026, 20262027)
SLEEP_BETWEEN_GAMES = 0.2  # be polite to the NHL API


def games_missing_event_id(client, season):
    """Game ids in `season` with at least one row that has no event_id,
    oldest first. One row per game is enough to know it needs rewriting --
    process_game() rewrites the whole game either way."""
    rows = select_all(
        lambda: (
            client.table("shot_events")
            .select("game_id")
            .eq("season", season)
            .is_("event_id", "null")
        ),
        order="game_id",
    )
    return sorted({r["game_id"] for r in rows})


def rewrite_game(client, game, season):
    """Re-process one game. Returns how many rows were written, or None if
    the play-by-play had nothing usable (the rows are then left alone).

    `game` must be the schedule entry, not just an id: process_game() reads
    homeTeam/awayTeam off it for the car_game flag and gameType for
    is_playoff, so a stub would quietly rewrite every row as a
    non-Carolina regular-season game."""
    game_id = game["id"]
    shots = shot_events.process_game(game, season)
    if not shots:
        return None
    client.table("shot_events").delete().eq("game_id", game_id).execute()
    for i in range(0, len(shots), 500):
        client.table("shot_events").insert(shots[i : i + 500]).execute()
    return len(shots)


def run(seasons, dry_run=False, limit=None):
    client = get_client()
    total_games = total_rows = failed = empty = 0

    for season in seasons:
        missing = set(games_missing_event_id(client, season))
        print(f"\n--- {season}: {len(missing):,} game(s) with rows missing event_id ---")
        if dry_run or not missing:
            continue

        # The schedule entries themselves, not bare ids -- see rewrite_game().
        schedule = shot_events.get_all_completed_games(season)
        pending = sorted((g for g in schedule if g.get("id") in missing), key=lambda g: g["id"])
        if len(pending) != len(missing):
            print(
                f"  note: {len(missing) - len(pending)} game(s) are no longer in the schedule; skipped"
            )
        if limit is not None:
            pending = pending[: max(0, limit - total_games)]

        for i, game in enumerate(pending, 1):
            game_id = game["id"]
            try:
                written = rewrite_game(client, game, season)
            except FetchError as e:
                print(f"  !! fetch failed on {game_id}: {e}")
                failed += 1
                continue
            except Exception as e:  # one bad game must not stop the run
                print(f"  !! crashed on {game_id}: {type(e).__name__}: {e}")
                traceback.print_exc()
                failed += 1
                continue

            if written is None:
                print(f"  -- {game_id}: no usable play-by-play, rows left as they were")
                empty += 1
            else:
                total_rows += written
            total_games += 1
            if i % 25 == 0 or i == len(pending):
                print(f"  [{i}/{len(pending)}] {total_rows:,} rows rewritten")
            time.sleep(SLEEP_BETWEEN_GAMES)
            if limit is not None and total_games >= limit:
                break

    print(f"\n=== {total_games:,} games rewritten, {total_rows:,} rows ===")
    if empty:
        print(f"    {empty} game(s) had no usable play-by-play")
    if failed:
        print(f"    {failed} game(s) failed -- re-run to pick them up")
    return 1 if failed else 0


def verify(seasons):
    """What's left with no event_id, per season."""
    client = get_client()
    for season in seasons:
        rows = select_all(
            lambda s=season: (
                client.table("shot_events")
                .select("game_id")
                .eq("season", s)
                .is_("event_id", "null")
            ),
            order="game_id",
        )
        games = {r["game_id"] for r in rows}
        print(f"  {season}: {len(rows):,} rows with no event_id across {len(games):,} game(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill shot_events.event_id")
    parser.add_argument("--season", type=int, default=None, help="one season, e.g. 20252026")
    parser.add_argument("--dry-run", action="store_true", help="count the work, change nothing")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many games")
    parser.add_argument(
        "--verify", action="store_true", help="report what's still missing and exit"
    )
    args = parser.parse_args()

    chosen = (args.season,) if args.season else SEASONS
    if args.verify:
        verify(chosen)
        sys.exit(0)
    sys.exit(run(chosen, dry_run=args.dry_run, limit=args.limit))
