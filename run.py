"""
run.py — EyeWall Analytics pipeline orchestrator.

Nightly run order (important — modules depend on each other):
  1. nhl_stats    — rosters, player/team stats, game log
  2. shot_events  — league-wide shot coordinates from PBP (incremental)
  3. shift_data   — league-wide shift charts (incremental)
  4. zone_starts  — per-player zone start counts from PBP (incremental)
  5. rapm         — 3-year rolling ridge regression RAPM -> player_seasons.rapm
  6. moneypuck    — WAR (RAPM-derived) + percentiles -> player_seasons
  7. game_scoring — PBP goals/assists parser -> game_scoring table
  8. ai_summaries — post-game summaries (all teams)
  9. ai_scouting  — missing scouting blurbs (all teams)

AI predictions run separately via ai_pipeline.yml morning cron (10AM ET).

Usage:
  python run.py                  # run all pipelines (nightly order)
  python run.py nhl              # NHL stats only
  python run.py shots            # Shot events only (incremental)
  python run.py shifts           # Shift charts only (incremental)
  python run.py shifts 20242025  # Shift charts for a specific season (backfill)
  python run.py zones            # Zone starts only (incremental)
  python run.py rapm             # RAPM regression only
  python run.py moneypuck        # MoneyPuck WAR + percentiles only
  python run.py validate         # Internal RAPM sanity checks
  python run.py validate eh.csv  # RAPM vs Evolving Hockey CSV comparison
  python run.py ai               # AI pipeline only (summaries + scouting)
"""

import subprocess
import sys
import time
from datetime import datetime


def run_subprocess(label, cmd):
    """Run a script via subprocess. Raises on non-zero exit."""
    print(f"\n  >> {label}")
    result = subprocess.run([sys.executable, *cmd])
    if result.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed with exit code {result.returncode}")


def run_ai_pipeline():
    """AI pipeline — game_scoring, summaries, scouting. Runs after moneypuck."""
    run_subprocess("game_scoring   — PBP goals/assists parser", ["game_scoring.py"])
    run_subprocess("ai_summaries   — post-game summaries", ["ai_summaries.py"])
    run_subprocess("ai_scouting    — missing scouting blurbs", ["ai_scouting.py", "--missing"])


def run_all():
    start = time.time()
    print(f"\n{'=' * 55}")
    print(f"  EyeWall Analytics Pipeline -- {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'=' * 55}")

    import line_combinations
    import moneypuck
    import nhl_stats
    import power_rankings
    import rapm
    import shift_data
    import shot_events
    import special_teams
    import zone_starts

    nhl_stats.run()
    shot_events.run()
    shift_data.run()
    zone_starts.run()
    rapm_status = rapm.run()
    moneypuck.run()
    line_combinations.run()  # must run after shift_data + shot_events
    special_teams.run()  # must run after shift_data
    power_rankings.run()  # must run after moneypuck (needs fresh WAR + xGF%)

    # AI pipeline — runs after player_seasons is fresh
    run_ai_pipeline()

    # Validate RAPM after every nightly run — exits non-zero on failure
    # which triggers a GitHub Actions failure email
    import validate_rapm

    validation_status = validate_rapm.run()

    elapsed = round(time.time() - start, 1)
    print(f"\n{'=' * 55}")
    print(f"  All pipelines complete in {elapsed}s")
    print(f"{'=' * 55}\n")

    # Allowlist, not a blocklist: rapm.run() returning anything other than
    # "ok", or validate_rapm.run() returning anything other than "pass"/"warn"
    # (including an unexpected None), fails the job loudly instead of being
    # silently treated as success — see Session 45 for the incident this
    # closed (rapm.run()'s abort paths returned nothing and player_seasons.rapm
    # keeps stale prior-night values on abort, so validation could pass
    # against stale data even when tonight's regression never ran).
    if rapm_status != "ok" or validation_status not in ("pass", "warn"):
        sys.exit(1)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    season = int(sys.argv[2]) if len(sys.argv) > 2 else None

    if arg == "nhl":
        import nhl_stats

        nhl_stats.run()
    elif arg == "shots":
        import shot_events

        shot_events.run(*([season] if season else []))
    elif arg == "shifts":
        import shift_data

        shift_data.run(*([season] if season else []))
    elif arg == "zones":
        import zone_starts

        zone_starts.run(*([season] if season else []))
    elif arg == "rapm":
        import rapm

        rapm.run()
    elif arg == "moneypuck":
        import moneypuck

        moneypuck.run()
    elif arg == "lines":
        import line_combinations

        line_combinations.run(*([season] if season else []))
    elif arg == "special":
        import special_teams

        special_teams.run(season=season)
    elif arg == "rankings":
        import power_rankings

        power_rankings.run(season=season)
    elif arg == "validate":
        import validate_rapm

        eh_csv = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
        status = validate_rapm.run(eh_csv_path=eh_csv)
        if status not in ("pass", "warn"):
            sys.exit(1)
    elif arg == "ai":
        run_ai_pipeline()
    else:
        run_all()
