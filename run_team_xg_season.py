def run_team_xg_season(client, season: int):
    """Aggregate game_xg rows into team_seasons.xgf_pct.

    Reads all 5v5 game_xg rows for the season, computes each team's
    season-long xGF% (total xGF / (total xGF + total xGA)), then
    upserts into team_seasons on the (team, season, game_type=2) conflict key.

    Runs after run_game_xg() so game_xg is already up to date.
    """
    print("\n--- Team season xGF% aggregation ---")
    try:
        # Pull all 5v5 game_xg rows for this season in one query.
        # game_xg has one row per team per game — typically ~2,624 rows
        # for a full 82-game season across 32 teams (32 * 82 = 2,624).
        # Well within Supabase's 1000-row default, so we paginate to be safe.
        rows = []
        offset = 0
        while True:
            batch = (
                client.table("game_xg")
                .select("team,xgf,xga")
                .eq("season", season)
                .eq("situation", "5on5")
                .range(offset, offset + 999)
                .execute()
                .data
            )
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < 1000:
                break
            offset += 1000

        print(f"  Loaded {len(rows)} 5v5 game_xg rows")

        if not rows:
            print("  No game_xg rows found — skipping")
            return

        # Aggregate per team
        totals = {}  # team -> { xgf: float, xga: float, gp: int }
        for r in rows:
            team = r["team"]
            if team not in totals:
                totals[team] = {"xgf": 0.0, "xga": 0.0, "gp": 0}
            totals[team]["xgf"] += r["xgf"] or 0.0
            totals[team]["xga"] += r["xga"] or 0.0
            totals[team]["gp"] += 1

        # Build upserts — only xgf_pct; don't touch other team_seasons columns
        upserts = []
        for team, t in totals.items():
            total_xg = t["xgf"] + t["xga"]
            xgf_pct = round(t["xgf"] / total_xg, 4) if total_xg > 0 else None
            upserts.append(
                {
                    "team": team,
                    "season": season,
                    "game_type": 2,  # game_xg is regular season only
                    "xgf_pct": xgf_pct,
                }
            )

        print(f"  Upserting xgf_pct for {len(upserts)} teams...")
        # on_conflict must match the unique constraint on team_seasons.
        # Existing columns (wins, losses, etc.) are left untouched — only
        # xgf_pct is written because that's all we're sending.
        client.table("team_seasons").upsert(upserts, on_conflict="team,season,game_type").execute()

        # Log a few for sanity
        sample = sorted(upserts, key=lambda x: x["xgf_pct"] or 0, reverse=True)[:5]
        for s in sample:
            print(f"    {s['team']}: xGF% = {s['xgf_pct']}")
        print(f"  ✓ team_seasons.xgf_pct updated for {len(upserts)} teams")

    except Exception as e:
        print(f"  WARNING: run_team_xg_season failed: {e}")
