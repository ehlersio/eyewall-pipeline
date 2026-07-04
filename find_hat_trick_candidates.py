"""
find_hat_trick_candidates.py — one-off diagnostic.

Scans pwhl_shot_events for every game where a single shooter scored 3+
goals this season, and prints the full goal sequence for that game in
CORRECTED chronological order (period_id ASC, time_seconds ASC — elapsed,
per the 2026-07-04 time-convention fix). Use this to pick a real game and
cross-check the printed order/timing against the official PWHL recap,
confirming natural hat-trick detection is now sequencing correctly.

Usage:
  python find_hat_trick_candidates.py            # current season
  python find_hat_trick_candidates.py 8           # specific season_id
"""

import sys
from collections import defaultdict

from db import get_client
from pwhl_stats import TEAM_ID_MAP


def get_all_goals(sb, season_id: int) -> list[dict]:
    r = (
        sb.table("pwhl_shot_events")
        .select("game_id, team_id, shooter_id, period_id, time_seconds")
        .eq("season_id", season_id)
        .eq("event_type", "goal")
        .execute()
    )
    return r.data or []


def main():
    sb = get_client()
    season_id = int(sys.argv[1]) if len(sys.argv) > 1 else 8

    goals = get_all_goals(sb, season_id)
    if not goals:
        print(f"No goals found for season_id={season_id}")
        return

    by_game_shooter = defaultdict(list)
    for g in goals:
        by_game_shooter[(g["game_id"], g["shooter_id"])].append(g)

    candidates = {k: v for k, v in by_game_shooter.items() if len(v) >= 3}
    if not candidates:
        print("No shooter with 3+ goals in a single game found this season.")
        return

    print(f"{len(candidates)} hat-trick candidate(s) found:\n")

    for (game_id, shooter_id), shooter_goals in candidates.items():
        # Full game goal sequence, corrected chronological order
        game_goals = [g for g in goals if g["game_id"] == game_id]
        game_goals.sort(key=lambda r: (r["period_id"] or 0, r["time_seconds"] or 0))

        print(f"--- Game {game_id} — shooter_id={shooter_id} ({len(shooter_goals)} goals) ---")
        for g in game_goals:
            marker = " <-- HAT TRICK SCORER" if g["shooter_id"] == shooter_id else ""
            team = TEAM_ID_MAP.get(str(g["team_id"]), g["team_id"])
            mm, ss = divmod(g["time_seconds"] or 0, 60)
            print(
                f"  P{g['period_id']} {mm}:{ss:02d} elapsed — shooter_id={g['shooter_id']} "
                f"team={team}{marker}"
            )
        print()


if __name__ == "__main__":
    main()
