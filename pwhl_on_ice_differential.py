"""
pwhl_on_ice_differential.py — first consumer of pwhl_goal_on_ice (Session 42)

Computes each player's on-ice goals-for/goals-against split (not just a net
+/- number -- pwhl_player_seasons/pwhl_skater_game_box already have that,
straight from HockeyTech's own leaderboard) for a season, from
pwhl_goal_on_ice. Uses the same empirically-validated convention as that
table's own docstring: exclude power-play goals only (short-handed,
empty-net, and penalty-shot goals all count) -- confirmed Session 42 to
reproduce HockeyTech's plusMinus field exactly, 10,669/10,669 player-games
across the full historical backfill.

This is a report/validation step, not a persisted table yet -- deliberately
holding off on a third new table until the shape below gets a look. Run
directly to print a leaderboard; compute_on_ice_differential() is the
reusable piece if this graduates into a real pipeline module.

Usage:
  python pwhl_on_ice_differential.py          # current season (PWHL_SEASON)
  python pwhl_on_ice_differential.py 8        # specific season_id
"""

import os
import sys
from collections import defaultdict

from dotenv import load_dotenv
from supabase import create_client

from season_lookup import get_pwhl_season

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
# Live-resolved via Worker; falls back to PWHL_SEASON env var (or "8") — see
# season_lookup.get_pwhl_season(). Not os.environ.get("PWHL_SEASON", "8"):
# that only applies its default when the key is absent, not when it's set
# to an empty string (the Session 30 bug).
PWHL_SEASON = str(get_pwhl_season()["season_id"])


def _fetch_all(sb, table: str, cols: str, **filters):
    """OFFSET pagination accepted as-is (Session 47 audit #10 pass):
    ad hoc/report-only (no scheduled caller), PWHL-scale season data --
    revisit if this graduates into a real, scheduled pipeline module."""
    out = []
    offset = 0
    step = 1000
    while True:
        q = sb.table(table).select(cols)
        for k, v in filters.items():
            q = q.eq(k, v)
        r = q.range(offset, offset + step - 1).execute()
        if not r.data:
            break
        out.extend(r.data)
        offset += len(r.data)
    return out


def compute_on_ice_differential(sb, season_id: int) -> list[dict]:
    """One row per player: goals_for, goals_against (excl PP goals only),
    goal_diff, gp (distinct games with at least one on-ice appearance)."""
    rows = _fetch_all(
        sb,
        "pwhl_goal_on_ice",
        "game_id,player_id,team_id,on_ice_for,is_power_play",
        season_id=season_id,
    )

    gf = defaultdict(int)
    ga = defaultdict(int)
    games = defaultdict(set)
    team_of = {}
    for r in rows:
        if r["is_power_play"]:
            continue
        pid = r["player_id"]
        team_of[pid] = r["team_id"] if r["on_ice_for"] else team_of.get(pid, r["team_id"])
        games[pid].add(r["game_id"])
        if r["on_ice_for"]:
            gf[pid] += 1
        else:
            ga[pid] += 1

    player_ids = list(set(gf) | set(ga))
    names = {}
    if player_ids:
        for i in range(0, len(player_ids), 500):
            batch = player_ids[i : i + 500]
            res = (
                sb.table("pwhl_players")
                .select("player_id,first_name,last_name")
                .in_("player_id", batch)
                .execute()
            )
            for p in res.data or []:
                names[p["player_id"]] = (
                    f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
                )

    out = []
    for pid in player_ids:
        out.append(
            {
                "player_id": pid,
                "name": names.get(pid, f"player {pid}"),
                "team_id": team_of.get(pid),
                "goals_for": gf.get(pid, 0),
                "goals_against": ga.get(pid, 0),
                "goal_diff": gf.get(pid, 0) - ga.get(pid, 0),
                "gp": len(games[pid]),
            }
        )
    return out


def main(season_id: str | None = None) -> None:
    season_id = int(season_id or PWHL_SEASON)
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    diff = compute_on_ice_differential(sb, season_id)
    diff.sort(key=lambda r: r["goal_diff"], reverse=True)

    print(f"=== On-ice goal differential -- season {season_id} ({len(diff)} players) ===")
    print(f"{'Player':<25}{'GP':>4}{'GF':>5}{'GA':>5}{'Diff':>6}")
    print("-- Top 10 --")
    for r in diff[:10]:
        print(
            f"{r['name']:<25}{r['gp']:>4}{r['goals_for']:>5}{r['goals_against']:>5}{r['goal_diff']:>+6}"
        )
    print("-- Bottom 10 --")
    for r in diff[-10:]:
        print(
            f"{r['name']:<25}{r['gp']:>4}{r['goals_for']:>5}{r['goals_against']:>5}{r['goal_diff']:>+6}"
        )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
