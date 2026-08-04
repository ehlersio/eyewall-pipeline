"""
backfill_uta_2025_team_stats.py -- One-time backfill of UTA's 2025-26
team_seasons summary fields (goals_for, goals_against, goals_for_pg,
goals_ag_pg, pp_pct, pk_pct, shots_for_pg, shots_ag_pg, faceoff_win_pct).

Root cause: NHL's team/summary endpoint assigns Utah a different teamId
per season -- 59 ("Utah Hockey Club") for 2024-25, 68 ("Utah Mammoth") for
2025-26 after the franchise rebrand. nhl_stats.py's TEAM_ID_TO_ABBR only
had 59, so 2025-26 UTA rows from that endpoint were silently dropped
(joined via an empty abbr, never written). Fixed going forward in
nhl_stats.py (both ids now mapped); this script backfills the rows that
were already written null before that fix.

Only touches the 9 summary-endpoint-derived columns -- doesn't re-upsert
standings-derived fields (wins/losses/points/etc.) or corsi/xgf, which
were already correct (they come from different endpoints/rollups).

Run: python backfill_uta_2025_team_stats.py
"""

from db import get_client, upsert
from nhl_stats import fetch_team_stats

SEASON = 20252026
UTA_TEAM_IDS = {59, 68}


def run() -> int:
    client = get_client()
    updated = 0
    for game_type in (2, 3):
        teams = fetch_team_stats(SEASON, game_type)
        uta = next((t for t in teams if t.get("teamId") in UTA_TEAM_IDS), None)
        if uta is None:
            print(f"  game_type={game_type}: still no UTA row from team/summary -- not backfilling")
            continue
        row = {
            "team": "UTA",
            "season": SEASON,
            "game_type": game_type,
            "goals_for": uta.get("goalsFor"),
            "goals_against": uta.get("goalsAgainst"),
            "goals_for_pg": uta.get("goalsForPerGame"),
            "goals_ag_pg": uta.get("goalsAgainstPerGame"),
            "pp_pct": uta.get("powerPlayPct"),
            "pk_pct": uta.get("penaltyKillPct"),
            "shots_for_pg": uta.get("shotsForPerGame"),
            "shots_ag_pg": uta.get("shotsAgainstPerGame"),
            "faceoff_win_pct": uta.get("faceoffWinPct"),
        }
        upsert(client, "team_seasons", [row], "team,season,game_type")
        print(
            f"  game_type={game_type}: backfilled from teamId={uta.get('teamId')} ({uta.get('teamFullName')})"
        )
        updated += 1
    return updated


if __name__ == "__main__":
    print(f"=== UTA {SEASON} team_seasons summary backfill ===")
    n = run()
    print(f"=== Done: {n} game_type row(s) backfilled ===")
