"""
diagnose_narrative_impact.py -- One-off diagnostic (not part of the pipeline)
for PP_GOALS_FULL_FIX.md's narrative-regeneration scoping question: of the
existing game_summaries entries for 2025-26, how many were actually built
from a wrong pp_goals/pp_opps value (not just "could theoretically be
affected")?

Reconstructs the OLD, buggy situationCode-based pp_goals/pp_opps per
team-game (replicating nhl_stats.py's pre-fix logic exactly, from fresh PBP
-- this file no longer has that logic in it, only the corrected right-rail
path), and diffs it against the now-corrected game_log values already
written by backfill_pp_stats.py. Prints the exact set of (game_id, team)
rows whose narrative-time PP numbers differ from the truth.
"""

import time

from db import get_client
from nhl_stats import NHL_BASE, nhl_get
from pipeline_common import FetchError


def old_buggy_pp_stats(game_id: int) -> dict | None:
    """Exact replica of the pre-fix nhl_stats.py logic, for diagnostic diffing only."""
    try:
        pbp = nhl_get(f"{NHL_BASE}/gamecenter/{game_id}/play-by-play")
    except FetchError:
        return None

    plays = pbp.get("plays", [])
    home_team_id = pbp.get("homeTeam", {}).get("id")
    away_team_id = pbp.get("awayTeam", {}).get("id")

    home_pp_goals = home_pp_opps = away_pp_goals = away_pp_opps = 0

    for p in plays:
        key = p.get("typeDescKey", "")
        details = p.get("details", {})
        sc = p.get("situationCode", "")

        if key == "penalty" and details.get("duration", 0) == 2:
            penalized_id = details.get("eventOwnerTeamId")
            if penalized_id == away_team_id:
                home_pp_opps += 1
            elif penalized_id == home_team_id:
                away_pp_opps += 1

        if key == "goal" and len(sc) == 4:
            scorer_id = details.get("eventOwnerTeamId")
            away_sk = int(sc[1])
            home_sk = int(sc[3])  # the bug: should be sc[2]
            home_on_pp = home_sk > away_sk
            away_on_pp = away_sk > home_sk

            if scorer_id == home_team_id and home_on_pp:
                home_pp_goals += 1
            elif scorer_id == away_team_id and away_on_pp:
                away_pp_goals += 1

    return {"home": (home_pp_goals, home_pp_opps), "away": (away_pp_goals, away_pp_opps)}


def run(season: int = 20252026):
    client = get_client()

    summary_rows = []
    offset = 0
    while True:
        page = (
            client.table("game_summaries")
            .select("game_id,team")
            .eq("season", season)
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not page:
            break
        summary_rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000

    game_ids = sorted({r["game_id"] for r in summary_rows})
    print(f"Checking {len(game_ids)} games with existing narratives...")

    game_log_rows = []
    offset = 0
    while True:
        page = (
            client.table("game_log")
            .select("game_id,team,home_team,pp_goals,pp_opps")
            .eq("season", season)
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not page:
            break
        game_log_rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    corrected = {(r["game_id"], r["team"]): (r["pp_goals"], r["pp_opps"]) for r in game_log_rows}
    home_team_of = {r["game_id"]: r["home_team"] for r in game_log_rows}

    wrong = []
    errors = []
    for i, gid in enumerate(game_ids):
        old = old_buggy_pp_stats(gid)
        if old is None:
            errors.append(gid)
            time.sleep(0.3)
            continue

        home_abbr = home_team_of.get(gid)
        for r in summary_rows:
            if r["game_id"] != gid:
                continue
            t = r["team"]
            is_home = t == home_abbr
            old_val = old["home"] if is_home else old["away"]
            new_val = corrected.get((gid, t))
            if new_val is not None and tuple(old_val) != tuple(new_val):
                wrong.append((gid, t, old_val, new_val))

        if i % 50 == 0:
            print(f"  ...{i}/{len(game_ids)} checked")
        time.sleep(0.15)

    print(f"\nDone. {len(wrong)} of {len(summary_rows)} existing narrative rows had wrong PP data.")
    print(f"{len(errors)} games couldn't be re-fetched for comparison: {errors}")

    affected_game_ids = sorted({w[0] for w in wrong})
    print(f"{len(affected_game_ids)} unique games have at least one wrong team-row.")

    import json

    with open("narrative_impact_result.json", "w") as f:
        json.dump(
            {
                "wrong_rows": [
                    {"game_id": g, "team": t, "old": list(o), "new": list(n)}
                    for g, t, o, n in wrong
                ],
                "affected_game_ids": affected_game_ids,
                "errors": errors,
            },
            f,
            indent=2,
        )
    print("Full results written to narrative_impact_result.json")

    return wrong, errors


if __name__ == "__main__":
    run()
