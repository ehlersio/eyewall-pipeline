"""
run.py — EyeWall Analytics pipeline orchestrator.

Nightly run order (important — modules depend on each other):
  1. nhl_stats    — rosters, player/team stats, game log
  2. shot_events  — league-wide shot coordinates from PBP (incremental)
  3. shift_data   — league-wide shift charts (incremental)
  4. zone_starts  — per-player zone start counts from PBP (incremental)
  5. rapm         — 3-year rolling ridge regression RAPM -> player_seasons.rapm
  6. moneypuck    — WAR (RAPM-derived) + percentiles -> player_seasons

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
"""
import sys
import time
from datetime import datetime

def run_all():
    start = time.time()
    print(f"\n{'='*55}")
    print(f"  EyeWall Analytics Pipeline -- {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*55}")

    import nhl_stats
    import shot_events
    import shift_data
    import zone_starts
    import rapm
    import moneypuck

    nhl_stats.run()
    shot_events.run()
    shift_data.run()
    zone_starts.run()
    rapm.run()
    moneypuck.run()

    # Validate RAPM after every nightly run — exits non-zero on failure
    # which triggers a GitHub Actions failure email
    import validate_rapm
    status = validate_rapm.run()

    elapsed = round(time.time() - start, 1)
    print(f"\n{'='*55}")
    print(f"  All pipelines complete in {elapsed}s")
    print(f"{'='*55}\n")

    if status == 'fail':
        sys.exit(1)

if __name__ == '__main__':
    arg    = sys.argv[1] if len(sys.argv) > 1 else 'all'
    season = int(sys.argv[2]) if len(sys.argv) > 2 else None

    if arg == 'nhl':
        import nhl_stats; nhl_stats.run()
    elif arg == 'shots':
        import shot_events
        shot_events.run(*([season] if season else []))
    elif arg == 'shifts':
        import shift_data
        shift_data.run(*([season] if season else []))
    elif arg == 'zones':
        import zone_starts
        zone_starts.run(*([season] if season else []))
    elif arg == 'rapm':
        import rapm; rapm.run()
    elif arg == 'moneypuck':
        import moneypuck; moneypuck.run()
    elif arg == 'validate':
        import validate_rapm
        eh_csv = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else None
        status = validate_rapm.run(eh_csv_path=eh_csv)
        if status == 'fail':
            sys.exit(1)  # triggers GitHub Actions failure email
    else:
        run_all()
