"""
inspect_penalty_shape.py — one-off diagnostic.

Fetches the raw gameCenterPlayByPlay feed for game 261 directly (same call
pwhl_pbp_events.py makes) and prints the FULL raw dict for every penalty
event, so we can see the actual field names HockeyTech uses — team_id and
penalty_minutes are coming back None for every row, so parse_pbp/_parse_penalty
is reading the wrong keys somewhere.

Usage:
  python inspect_penalty_shape.py
"""

import json

from pwhl_pbp_events import fetch_pbp

GAME_ID = 261


def main():
    events = fetch_pbp(GAME_ID)
    if not events:
        print(f"No PBP returned for game {GAME_ID}")
        return

    penalty_events = [e for e in events if isinstance(e, dict) and e.get("event") == "penalty"]
    print(f"{len(penalty_events)} penalty event(s) found in game {GAME_ID}\n")

    for i, ev in enumerate(penalty_events):
        print(f"--- Penalty event {i + 1} (full raw dict) ---")
        print(json.dumps(ev, indent=2))
        print()


if __name__ == "__main__":
    main()
