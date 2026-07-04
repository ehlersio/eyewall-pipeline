"""
inspect_goal_shape.py — one-off diagnostic.

Fetches the raw gameCenterPlayByPlay feed for game 261 (same known-good
game from today's SH-goal work) and prints the FULL raw dict for every
GOAL event (event_type='shot' with details.isGoal=true), to check whether
HockeyTech includes assist data that pwhl_shot_events.py's parser is
currently silently dropping — same pattern as the againstTeam.id field we
found sitting unused on penalty events earlier today.

Usage:
  python inspect_goal_shape.py
"""

import json

from pwhl_shot_events import fetch_pbp

GAME_ID = 261


def main():
    events = fetch_pbp(GAME_ID)
    if not events:
        print(f"No PBP returned for game {GAME_ID}")
        return

    goal_events = [
        e
        for e in events
        if isinstance(e, dict)
        and e.get("event") == "shot"
        and (e.get("details") or {}).get("isGoal")
    ]
    print(f"{len(goal_events)} goal event(s) found in game {GAME_ID}\n")

    for i, ev in enumerate(goal_events):
        print(f"--- Goal event {i + 1} (full raw dict) ---")
        print(json.dumps(ev, indent=2))
        print()


if __name__ == "__main__":
    main()
