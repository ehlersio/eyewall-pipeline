"""
test_sh_detection.py — one-off validation script.

Finds the PWHL game on 2026-01-21, locates Adzija's goal(s) in it, and runs
detect_shorthanded_goals() against the game to confirm the known SH goal
gets flagged correctly.

Usage:
  python test_sh_detection.py
"""

from db import get_client
from pwhl_milestones import detect_shorthanded_goals, get_goal_rows

TARGET_DATES = ["2026-01-20", "2026-01-21"]
PLAYER_LAST_NAME = "Adzija"


def main():
    sb = get_client()

    # 1. Find the game(s) on either candidate date
    games = (
        sb.table("pwhl_game_log")
        .select("game_id, game_date, season_id, home_team_id, away_team_id, game_state")
        .in_("game_date", TARGET_DATES)
        .execute()
        .data
        or []
    )
    if not games:
        print(f"No games found for {TARGET_DATES} — check pwhl_game_log has these dates.")
        return
    print(f"Games on {TARGET_DATES}: {games}\n")

    # 2. Find Adzija's player_id
    players = (
        sb.table("pwhl_players")
        .select("player_id, first_name, last_name")
        .ilike("last_name", f"%{PLAYER_LAST_NAME}%")
        .execute()
        .data
        or []
    )
    if not players:
        print(f"No player found matching last name '{PLAYER_LAST_NAME}'.")
        return
    print(f"Matching players: {players}\n")
    player_ids = {p["player_id"] for p in players}

    # 3. For each game that day, check goals and run detection
    for game in games:
        gid = game["game_id"]
        goals = get_goal_rows(sb, gid)
        adzija_goals = [g for g in goals if g.get("shooter_id") in player_ids]

        print(f"--- Game {gid}: {len(goals)} goal(s) total ---")
        for goal in goals:
            marker = " <-- ADZIJA (player_id match)" if goal.get("shooter_id") in player_ids else ""
            print(
                f"  period={goal['period_id']} time_elapsed={goal['time_seconds']} "
                f"shooter_id={goal['shooter_id']} team_id={goal['team_id']}{marker}"
            )
        print()

        if not adzija_goals:
            print(
                f"  No goal in game {gid} has shooter_id={list(player_ids)} — "
                f"check the shooter_id values above against player_id 49.\n"
            )
            continue

        sh_flags = detect_shorthanded_goals(sb, game, goals)
        print("SH detection results for ALL goals in this game:")
        for goal in goals:
            key = (goal.get("period_id"), goal.get("time_seconds"), goal.get("shooter_id"))
            is_sh = sh_flags.get(key)
            marker = " <-- ADZIJA" if goal.get("shooter_id") in player_ids else ""
            print(
                f"  period={goal['period_id']} time_elapsed={goal['time_seconds']} "
                f"shooter_id={goal['shooter_id']} team_id={goal['team_id']} "
                f"is_shorthanded={is_sh}{marker}"
            )

        if adzija_goals:
            from pwhl_milestones import _penalty_window, get_penalties_for_game

            print(f"\n  Raw penalties for game {gid}:")
            penalties = get_penalties_for_game(sb, gid)
            for p in penalties:
                w = _penalty_window(p)
                print(f"    raw={p}  -> window={w}")
            if not penalties:
                print(
                    "    (none returned — check pwhl_pbp_events has penalty rows for this game_id)"
                )


if __name__ == "__main__":
    main()
