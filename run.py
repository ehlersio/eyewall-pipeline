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
import traceback
from datetime import datetime


class _StageFailed:
    """Sentinel distinct from any real stage return value (None included) --
    lets run_all() tell "this stage raised" apart from "this stage returned
    a falsy/None result on purpose." A plain object() can't carry state (no
    __dict__), so this is a trivial class instead -- run_stage() stamps
    .exc_type on the single shared instance right before returning it, so
    callers can build an exception-type breakdown (FetchError vs a genuine
    bug) without re-deriving it from the log. Safe because run_stage() is
    synchronous and .exc_type is always read immediately after the call
    that set it, before the next stage runs."""

    def __init__(self):
        self.exc_type = None


STAGE_FAILED = _StageFailed()


def run_subprocess(label, cmd):
    """Run a script via subprocess. Raises on non-zero exit.

    Caller (run_ai_pipeline, via run_stage) prints the stage label already.
    """
    result = subprocess.run([sys.executable, *cmd])
    if result.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed with exit code {result.returncode}")


def run_stage(label, fn, *args, **kwargs):
    """Run one pipeline stage in isolation.

    A single stage's exception (a genuine bug, a schema change, an
    unhandled edge case -- as opposed to a fetch failure, which the
    individual modules' HTTP helpers now raise as FetchError rather than
    swallowing to None/[]) must not abort every other stage in the nightly
    run, including ones that have nothing to do with the failure. Logs
    loudly (full traceback + stage label) and returns STAGE_FAILED so the
    caller can track/report it, instead of letting it propagate.

    Deliberately still catches the general Exception base rather than
    special-casing FetchError -- "skip it, keep going" is the same action
    either way at this level, and type(e).__name__ (stamped on
    STAGE_FAILED.exc_type below) already gives callers the FetchError-vs-
    genuine-bug distinction for the summary line without a separate branch.
    """
    print(f"\n  >> {label}")
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        print(f"\n  !! {label} FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()
        STAGE_FAILED.exc_type = type(e).__name__
        return STAGE_FAILED


def run_ai_pipeline():
    """AI pipeline — game_scoring, summaries, scouting. Runs after moneypuck.

    Each sub-stage is isolated: game_scoring, ai_summaries, and ai_scouting
    don't depend on each other's output (confirmed — neither ai_summaries.py
    nor ai_scouting.py reference game_scoring), so one crashing must not
    prevent the other two from running.
    """
    failures = []
    for label, cmd in (
        ("game_scoring   — PBP goals/assists parser", ["game_scoring.py"]),
        ("ai_summaries   — post-game summaries", ["ai_summaries.py"]),
        ("ai_scouting    — missing scouting blurbs", ["ai_scouting.py", "--missing"]),
    ):
        if run_stage(label, run_subprocess, label, cmd) is STAGE_FAILED:
            # Runs as a subprocess -- the exception seen here is always
            # RuntimeError (raised by run_subprocess() on non-zero exit),
            # not the actual exception type from inside the subprocess.
            # Still worth annotating for consistency with the other stages.
            failures.append(f"{label} ({STAGE_FAILED.exc_type})")
    return failures


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

    failed_stages = []

    def stage(label, fn, *args, **kwargs):
        result = run_stage(label, fn, *args, **kwargs)
        if result is STAGE_FAILED:
            failed_stages.append(f"{label} ({STAGE_FAILED.exc_type})")
        return result

    stage("nhl_stats", nhl_stats.run)
    stage("shot_events", shot_events.run)
    stage("shift_data", shift_data.run)
    stage("zone_starts", zone_starts.run)
    rapm_status = stage("rapm", rapm.run)

    # moneypuck.run() returns a list of its own internal sub-stage failures
    # (e.g. RAPM values load, game_xg, goalie_qs) rather than raising for
    # those specific pieces -- see moneypuck.py's run() docstring. Fold that
    # list into failed_stages too, so a partial moneypuck failure is just as
    # visible in the summary as a stage that raised outright.
    moneypuck_result = stage("moneypuck", moneypuck.run)
    if moneypuck_result and moneypuck_result is not STAGE_FAILED:
        failed_stages.extend(moneypuck_result)

    stage("line_combinations", line_combinations.run)  # must run after shift_data + shot_events
    stage("special_teams", special_teams.run)  # must run after shift_data
    stage("power_rankings", power_rankings.run)  # must run after moneypuck (needs fresh WAR + xGF%)

    # AI pipeline — runs after player_seasons is fresh
    failed_stages.extend(run_ai_pipeline())

    # Validate RAPM after every nightly run — exits non-zero on failure
    # which triggers a GitHub Actions failure email
    import validate_rapm

    validation_status = stage("validate_rapm", validate_rapm.run)

    elapsed = round(time.time() - start, 1)
    print(f"\n{'=' * 55}")
    print(f"  All pipelines complete in {elapsed}s")
    if failed_stages:
        print(f"  {len(failed_stages)} stage(s) failed: {', '.join(failed_stages)}")
    else:
        print("  All stages completed without error")
    print(f"{'=' * 55}\n")

    # Allowlist, not a blocklist: rapm.run() returning anything other than
    # "ok", or validate_rapm.run() returning anything other than "pass"/"warn"
    # (including an unexpected None or STAGE_FAILED), fails the job loudly
    # instead of being silently treated as success — see Session 45 for the
    # incident this closed (rapm.run()'s abort paths returned nothing and
    # player_seasons.rapm keeps stale prior-night values on abort, so
    # validation could pass against stale data even when tonight's
    # regression never ran). Any other stage failing (per-stage isolation,
    # Session 46) also fails the job, but critically doesn't prevent the
    # remaining stages from having run first.
    if failed_stages or rapm_status != "ok" or validation_status not in ("pass", "warn"):
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

        if moneypuck.run():
            sys.exit(1)
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
        if run_ai_pipeline():
            sys.exit(1)
    else:
        run_all()
