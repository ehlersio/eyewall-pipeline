"""
get_candidate_game_info.py — one-off diagnostic.

Prints game_date/teams/scorer name for the two natural hat-trick candidates
(212, 323) found by find_hat_trick_candidates.py, so we can search for the
actual recap and verify the goal sequence.
"""

from db import get_client
from pwhl_stats import TEAM_ID_MAP

GAME_IDS = [212, 323]
SHOOTER_IDS = [9, 61]


def main():
    sb = get_client()

    games = (
        sb.table("pwhl_game_log")
        .select("game_id, game_date, home_team_id, away_team_id")
        .in_("game_id", GAME_IDS)
        .execute()
        .data
        or []
    )
    for g in games:
        home = TEAM_ID_MAP.get(str(g["home_team_id"]), g["home_team_id"])
        away = TEAM_ID_MAP.get(str(g["away_team_id"]), g["away_team_id"])
        print(f"Game {g['game_id']}: {g['game_date']} — {away} @ {home}")

    players = (
        sb.table("pwhl_players")
        .select("player_id, first_name, last_name")
        .in_("player_id", SHOOTER_IDS)
        .execute()
        .data
        or []
    )
    print()
    for p in players:
        print(f"player_id={p['player_id']}: {p['first_name']} {p['last_name']}")


if __name__ == "__main__":
    main()
