"""
find_nhl_hat_trick_candidates.py — one-off diagnostic.

Scans game_scoring for every game where a single scorer had 3+ goals this
season, printing the full goal sequence per game in chronological order
(period ASC, time_in_period ASC) so you can pick a clean natural hat trick
to spot-check against a real recap.

Usage:
  python find_nhl_hat_trick_candidates.py            # current season
  python find_nhl_hat_trick_candidates.py 20252026    # specific season
"""

import sys
from collections import defaultdict

from db import NHL_SEASON, get_client


def _time_to_seconds(t: str | None) -> int:
    if not t or ":" not in t:
        return 10**6
    try:
        m, s = t.split(":")
        return int(m) * 60 + int(s)
    except ValueError:
        return 10**6


def main():
    sb = get_client()
    season = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON

    r = (
        sb.table("game_scoring")
        .select("game_id, team, scorer_id, period, time_in_period")
        .eq("season", season)
        .neq("period", 5)  # exclude shootout, matches milestones.py convention
        .execute()
    )
    goals = r.data or []
    if not goals:
        print(f"No goals found for season={season}")
        return

    by_game_scorer = defaultdict(list)
    for g in goals:
        by_game_scorer[(g["game_id"], g["scorer_id"])].append(g)

    candidates = {k: v for k, v in by_game_scorer.items() if len(v) >= 3}
    if not candidates:
        print("No scorer with 3+ goals in a single game found this season.")
        return

    print(f"{len(candidates)} hat-trick candidate(s) found:\n")

    for (game_id, scorer_id), scorer_goals in candidates.items():
        game_goals = [g for g in goals if g["game_id"] == game_id]
        game_goals.sort(key=lambda r: (r["period"], _time_to_seconds(r.get("time_in_period"))))

        print(f"--- Game {game_id} — scorer_id={scorer_id} ({len(scorer_goals)} goals) ---")
        for g in game_goals:
            marker = " <-- HAT TRICK SCORER" if g["scorer_id"] == scorer_id else ""
            print(
                f"  P{g['period']} {g.get('time_in_period')} — scorer_id={g['scorer_id']} "
                f"team={g['team']}{marker}"
            )
        print()


if __name__ == "__main__":
    main()
