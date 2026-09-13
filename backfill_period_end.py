"""
backfill_period_end.py -- Correct game_log.period_end for past seasons from
the NHL's club schedules.

Found 2026-09-13: every 2023-24 and 2024-25 game_log row had period_end = 3
(regulation), including games that went to overtime or a shootout -- e.g.
game 2024020103 (EDM at CAR) ended in OT per the NHL API but was stored as
3. The schedule today carries the right value (periodDescriptor.number, and
gameOutcome.lastPeriodType), and nhl_stats.py reads it; those two seasons
were loaded once when the value came through as the default and, since
nhl_stats.py only rewrites the current season's game_log, never refreshed.
Consequence: elo_ratings.py never applied its overtime damping
(elo.OT_MOV_MULT) to any 2023-24/2024-25 game.

Dry run by default: compares game_log with the schedules and reports what
would change, plus how much the Elo ratings would move. --apply writes
(one UPDATE per corrected game_id, covering both team rows) and then
re-checks. Covers every game type present in game_log (regular season and
playoffs).

Usage:
  python backfill_period_end.py 20232024 20242025          # dry run
  python backfill_period_end.py 20232024 20242025 --apply
"""

import argparse
import time
from collections import Counter

import elo
import elo_ratings
from db import get_client
from nhl_stats import fetch_schedule, period_end_of

PAGE = 1000


def load_game_log(client, season: int) -> list:
    """Every game_log row for the season, paged in a fixed order (game_id,
    team) so OFFSET pages can't repeat or skip rows."""
    rows, offset = [], 0
    while True:
        page = (
            client.table("game_log")
            .select("game_id,team,game_type,period_end")
            .eq("season", season)
            .order("game_id")
            .order("team")
            .range(offset, offset + PAGE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    return rows


def exact_count(client, season: int) -> int:
    r = (
        client.table("game_log")
        .select("game_id", count="exact")
        .eq("season", season)
        .limit(1)
        .execute()
    )
    return r.count


def schedule_period_ends(teams, season: int) -> dict:
    """game_id -> period_end for every finished game in these teams' schedules."""
    out = {}
    for team in sorted(teams):
        for g in fetch_schedule(team, season):
            if g.get("gameState") in ("OFF", "FINAL"):
                out[g["id"]] = period_end_of(g)
        time.sleep(0.1)
    return out


def find_corrections(rows, truth) -> tuple:
    """(corrections {game_id: correct period_end}, game_ids missing from every schedule)."""
    stored = {}
    for r in rows:
        stored.setdefault(r["game_id"], set()).add(r["period_end"])
    corrections, missing = {}, []
    for gid, values in stored.items():
        if gid not in truth:
            missing.append(gid)
        elif values != {truth[gid]}:
            corrections[gid] = truth[gid]
    return corrections, missing


def elo_preview(corrections) -> list:
    """[(team, rating_now, rating_corrected, change)] replaying every season
    like elo_ratings.compute_ratings(), with and without the corrections."""

    def replay(patch):
        ratings = {}
        for si, season in enumerate(
            elo_ratings.seasons_in_order(elo_ratings.EARLIEST_SEASON, elo_ratings.NHL_SEASON)
        ):
            if si > 0:
                ratings = {t: elo.regress_to_mean(r) for t, r in ratings.items()}
            for g in elo_ratings.load_games(season):
                if g["home_score"] is None or g["away_score"] is None:
                    continue
                period_end = patch.get(g["game_id"], g.get("period_end")) or 3
                rh = ratings.setdefault(g["home_team"], elo.INITIAL_RATING)
                ra = ratings.setdefault(g["away_team"], elo.INITIAL_RATING)
                ratings[g["home_team"]], ratings[g["away_team"]] = elo.update_ratings(
                    rh,
                    ra,
                    g["home_score"] > g["away_score"],
                    abs(g["home_score"] - g["away_score"]),
                    period_end > 3,
                )
        return ratings

    now, fixed = replay({}), replay(corrections)
    rows = [(t, now[t], fixed.get(t, now[t]), fixed.get(t, now[t]) - now[t]) for t in now]
    return sorted(rows, key=lambda r: -abs(r[3]))


def run(seasons, apply=False):
    client = get_client()
    all_corrections = {}
    for season in seasons:
        rows = load_game_log(client, season)
        total = exact_count(client, season)
        if len(rows) != total:
            raise SystemExit(
                f"season {season}: read {len(rows)} rows but game_log has {total} -- aborting"
            )
        teams = {r["team"] for r in rows}
        truth = schedule_period_ends(teams, season)
        corrections, missing = find_corrections(rows, truth)
        stored = Counter(r["period_end"] for r in rows)
        n_games = len({r["game_id"] for r in rows})
        print(
            f"season {season}: {len(rows)} game_log rows / {n_games} games from {len(teams)} teams | "
            f"stored period_end {dict(stored)} | schedule says {dict(Counter(truth.get(g) for g in {r['game_id'] for r in rows}))}"
        )
        print(
            f"  {len(corrections)} games to correct ({dict(Counter(corrections.values()))}); "
            f"{len(missing)} game_ids not in any schedule"
        )
        all_corrections.update(corrections)

    print("\nElo preview (replaying every season with vs without the corrections):")
    preview = elo_preview(all_corrections)
    for team, now, fixed, change in preview[:8]:
        print(f"  {team}: {now:.1f} -> {fixed:.1f} ({change:+.1f})")
    print(
        f"  largest change {max(abs(p[3]) for p in preview):.1f} Elo points across {len(preview)} teams"
    )

    if not apply:
        print(
            f"\n(dry run) {len(all_corrections)} games would be updated -- re-run with --apply to write"
        )
        return len(all_corrections)

    for i, (gid, value) in enumerate(sorted(all_corrections.items()), 1):
        client.table("game_log").update({"period_end": value}).eq("game_id", gid).execute()
        if i % 100 == 0:
            print(f"  updated {i}/{len(all_corrections)}")
    print(f"  updated {len(all_corrections)} games")

    remaining = 0
    for season in seasons:
        rows = load_game_log(client, season)
        truth = schedule_period_ends({r["team"] for r in rows}, season)
        remaining += len(find_corrections(rows, truth)[0])
    print(f"  re-check: {remaining} games still mismatched")
    return len(all_corrections)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Correct game_log.period_end from NHL schedules")
    parser.add_argument("seasons", nargs="+", type=int, help="Seasons, e.g. 20232024 20242025")
    parser.add_argument(
        "--apply", action="store_true", help="Write the corrections (default: dry run)"
    )
    args = parser.parse_args()
    run(args.seasons, apply=args.apply)
