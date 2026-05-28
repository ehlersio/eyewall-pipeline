"""
run.py — EyeWall Analytics pipeline orchestrator.

Usage:
  python run.py              # run all pipelines
  python run.py nhl          # NHL stats only
  python run.py moneypuck    # MoneyPuck analytics only
  python run.py shots        # Shot events only
"""
import sys
import time
from datetime import datetime

def run_all():
    start = time.time()
    print(f"\n{'='*55}")
    print(f"  EyeWall Analytics Pipeline — {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*55}")

    import nhl_stats
    import moneypuck
    import shot_events

    nhl_stats.run()
    moneypuck.run()
    shot_events.run()

    elapsed = round(time.time() - start, 1)
    print(f"\n{'='*55}")
    print(f"  All pipelines complete in {elapsed}s")
    print(f"{'='*55}\n")

if __name__ == '__main__':
    arg = sys.argv[1] if len(sys.argv) > 1 else 'all'

    if arg == 'nhl':
        import nhl_stats; nhl_stats.run()
    elif arg == 'moneypuck':
        import moneypuck; moneypuck.run()
    elif arg == 'shots':
        import shot_events; shot_events.run()
    else:
        run_all()
