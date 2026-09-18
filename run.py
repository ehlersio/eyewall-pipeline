"""
run.py — EyeWall Analytics pipeline orchestrator.

Nightly run order (important — modules depend on each other):
  1. nhl_stats    — rosters, player/team stats, game log
  2. elo_ratings  — full Elo recompute from game_log (needs nhl_stats' fresh game_log; see
      docs/elo_prediction_model_results.md -- backs nhl.js's /prediction/analyze)
  3. playoff_race — magic/tragic numbers + clinched/eliminated (needs nhl_stats' fresh standings)
  4. shot_events  — league-wide shot coordinates from PBP (incremental)
  5. shift_data   — league-wide shift charts (incremental)
  6. zone_starts  — per-player zone start counts from PBP (incremental)
  7. rapm         — 3-year rolling ridge regression RAPM -> player_seasons.rapm
  8. moneypuck    — WAR (RAPM-derived) + percentiles -> player_seasons
  9. game_scoring — PBP goals/assists parser -> game_scoring table
  10. ai_summaries — post-game summaries (all teams)
  11. ai_scouting  — missing scouting blurbs (all teams)
  12. ai_results_vs_process — missing results-vs-process blurbs (NHL only, all teams)
  13. ai_line_chemistry — missing line-chemistry blurbs (needs fresh line_combinations -- see
      run_all()'s stage("line_combinations", ...) call, not numbered above since this docstring
      predates that stage)

  Also not numbered above (same reason): injuries -- ESPN injury feed ->
  player_injuries, runs right after nhl_stats (matches against its fresh
  players table). Then scratches -- NHL right-rail scratches ->
  game_scratches, right after injuries (needs nhl_stats' fresh game_log
  and injuries' same-day player_injury_history snapshot to classify
  healthy vs injured). Nothing else depends on either. Then transactions
  -- ESPN's NHL transactions feed -> nhl_transactions; fully independent,
  placed here only to keep the ESPN-sourced stages together. Then
  draft_history -- NHL records API draft picks (with each pick's chain of
  owners) -> draft_pick_history; also fully independent. Then trade_trees
  -- parses the stored trade entries into trades / trade_assets, resolves
  picks against draft_pick_history and links each asset to its next trade
  (needs both stages just before it). playoff_odds runs
  right after playoff_race -- Monte Carlo playoff odds from tonight's
  standings, game_log results and Elo ratings -> playoff_odds /
  playoff_odds_game_impacts. injury_impact runs right after moneypuck --
  man-games and WAR lost to injury per team (needs game_log, today's
  injury snapshot, shift_data's shift_events and moneypuck's fresh WAR)
  -> injury_games_lost / team_injury_impact. projected_lines runs right
  after line_combinations -- next-game projected lines per team (needs
  shift_data, game_log, the injury snapshot and player_seasons) ->
  projected_lines.

AI predictions run separately via ai_pipeline.yml morning cron (10AM ET).

Usage:
  python run.py                  # run all pipelines (nightly order)
  python run.py nhl              # NHL stats only
  python run.py injuries         # ESPN injuries only
  python run.py scratches        # Game scratches only (incremental)
  python run.py scratches 20252026  # Game scratches backfill for a season
  python run.py transactions     # ESPN NHL transactions, current calendar year
  python run.py transactions 2025   # Transactions backfill for a calendar year
  python run.py draft_history    # Draft pick history, last 5 drafts
  python run.py draft_history 1963  # Draft pick history since a draft year (backfill)
  python run.py trade_trees      # Structured trades + where each asset went next
  python run.py playoffs         # Magic/tragic numbers only (needs fresh nhl_stats data)
  python run.py playoff_odds     # Simulated playoff odds only (needs fresh nhl_stats + elo_ratings)
  python run.py injury_impact    # Man-games + WAR lost to injury, last 7 days of games
  python run.py injury_impact 20262027 --full  # Recompute every game of a season
  python run.py goalie_starts    # Boxscore goalie starters, new games only
  python run.py goalie_starts 20232024  # Goalie starters backfill for a season
  python run.py starting_goalie  # Start probabilities for every team's next game
  python run.py win_probs        # Pre-game Elo win probabilities, games today/tomorrow
  python run.py prediction_scorecard             # Public scorecard, live rows
  python run.py prediction_scorecard --backtest  # ...plus the three backtest rows
  python run.py shots            # Shot events only (incremental)
  python run.py shifts           # Shift charts only (incremental)
  python run.py shifts 20242025  # Shift charts for a specific season (backfill)
  python run.py zones            # Zone starts only (incremental)
  python run.py rapm             # RAPM regression only
  python run.py elo              # Team Elo rating recompute only
  python run.py moneypuck        # MoneyPuck WAR + percentiles only
  python run.py projected_lines  # Next-game projected lines, every team
  python run.py validate         # Internal RAPM sanity checks
  python run.py validate eh.csv  # RAPM vs Evolving Hockey CSV comparison
  python run.py ai               # AI pipeline only (summaries + scouting + narratives)
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
    """AI pipeline — game_scoring, summaries, scouting, results-vs-process,
    line-chemistry, daily trivia. Runs after moneypuck (results-vs-process
    needs its fresh on_ice_gf_pct/results_vs_process_diff columns) and after
    line_combinations (line-chemistry needs fresh line_combinations rows to
    narrate).

    Each sub-stage is isolated: none of the six depend on each other's
    output (confirmed — neither ai_summaries.py, ai_scouting.py,
    ai_results_vs_process.py, ai_line_chemistry.py, nor trivia_questions.py
    reference game_scoring or each other), so one crashing must not prevent
    the others from running.
    """
    failures = []
    for label, cmd in (
        ("game_scoring   — PBP goals/assists parser", ["game_scoring.py"]),
        ("ai_summaries   — post-game summaries", ["ai_summaries.py"]),
        ("ai_scouting    — missing scouting blurbs", ["ai_scouting.py", "--missing"]),
        (
            "ai_results_vs_process — missing results-vs-process blurbs",
            ["ai_results_vs_process.py", "--missing"],
        ),
        (
            "ai_line_chemistry — missing line-chemistry blurbs",
            ["ai_line_chemistry.py", "--missing"],
        ),
        (
            "trivia_questions — daily NHL trivia (easy + medium)",
            ["trivia_questions.py", "--sport", "nhl"],
        ),
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

    import draft_history
    import elo_ratings
    import goalie_starts
    import injuries
    import injury_impact
    import line_combinations
    import moneypuck
    import nhl_stats
    import playoff_odds
    import playoff_race
    import power_rankings
    import prediction_scorecard
    import projected_lines
    import rapm
    import scratches
    import shift_data
    import shot_events
    import special_teams
    import starting_goalie
    import trade_trees
    import transactions
    import win_probs
    import zone_starts

    failed_stages = []

    def stage(label, fn, *args, **kwargs):
        result = run_stage(label, fn, *args, **kwargs)
        if result is STAGE_FAILED:
            failed_stages.append(f"{label} ({STAGE_FAILED.exc_type})")
        return result

    stage("nhl_stats", nhl_stats.run)
    stage("injuries", injuries.run)  # matches against nhl_stats' fresh players table
    stage("scratches", scratches.run)  # needs fresh game_log + today's injury snapshot
    stage("goalie_starts", goalie_starts.run)  # needs fresh game_log (boxscore starters)
    stage("starting_goalie", starting_goalie.run)  # needs rosters, injury snapshot, goalie_starts
    stage("transactions", transactions.run)  # independent; ESPN NHL transactions feed
    stage("draft_history", draft_history.run)  # independent; NHL records API, last 5 drafts
    stage("trade_trees", trade_trees.run)  # needs fresh nhl_transactions + draft_pick_history
    stage("elo_ratings", elo_ratings.run)  # needs nhl_stats' fresh game_log
    stage("win_probs", win_probs.run)  # needs tonight's elo_ratings; logs today/tomorrow pre-game
    stage("playoff_race", playoff_race.run)  # needs nhl_stats' fresh standings
    stage("playoff_odds", playoff_odds.run)  # needs fresh standings + game_log + elo_ratings
    # needs game_log results, goalie_starts, win_probs, playoff_odds (live rows only)
    stage("prediction_scorecard", prediction_scorecard.run)
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

    stage("injury_impact", injury_impact.run)  # needs game_log, injury snapshot, shifts, fresh WAR

    stage("line_combinations", line_combinations.run)  # must run after shift_data + shot_events
    stage(
        "projected_lines", projected_lines.run
    )  # needs shift_data, game_log, injuries, player_seasons
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
    # "ok", or validate_rapm.run() returning anything other than
    # "pass"/"warn"/"off_season" (including an unexpected None or
    # STAGE_FAILED), fails the job loudly instead of being silently treated
    # as success — see Session 45 for the incident this closed (rapm.run()'s
    # abort paths returned nothing and player_seasons.rapm keeps stale
    # prior-night values on abort, so validation could pass against stale
    # data even when tonight's regression never ran). "off_season" is a
    # narrow, separate carve-out (added after the off-season nightly run
    # started failing on this check) for when game_log genuinely has zero
    # completed games this season -- nothing stale to hide, nothing to
    # validate, not the Session 45 failure mode. Any other stage failing
    # (per-stage isolation, Session 46) also fails the job, but critically
    # doesn't prevent the remaining stages from having run first.
    if (
        failed_stages
        or rapm_status != "ok"
        or validation_status not in ("pass", "warn", "off_season")
    ):
        sys.exit(1)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    # Only a numeric second argument is a season -- `injury_impact --full` and
    # `validate eh.csv` pass something else there, and int() would crash on it.
    season = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None

    if arg == "nhl":
        import nhl_stats

        nhl_stats.run()
    elif arg == "injuries":
        import injuries

        injuries.run()
    elif arg == "scratches":
        import scratches

        scratches.run(season)
    elif arg == "transactions":
        import transactions

        # Calendar year(s), not an NHL season -- ESPN's feed is keyed by year.
        transactions.run(years=[season] if season else None)
    elif arg == "draft_history":
        import draft_history

        # A first draft year (e.g. 1963 for a full backfill), not an NHL season.
        draft_history.run(since_year=season)
    elif arg == "trade_trees":
        import trade_trees

        trade_trees.run()
    elif arg == "playoff_odds":
        import playoff_odds

        playoff_odds.run(season=season)
    elif arg == "injury_impact":
        import injury_impact

        injury_impact.run(season=season, full="--full" in sys.argv)
    elif arg == "goalie_starts":
        import goalie_starts

        goalie_starts.run(season=season)
    elif arg == "starting_goalie":
        import starting_goalie

        starting_goalie.run(season=season)
    elif arg == "win_probs":
        import win_probs

        win_probs.run(season=season)
    elif arg == "prediction_scorecard":
        import prediction_scorecard

        prediction_scorecard.run(season=season, backtest="--backtest" in sys.argv)
    elif arg == "playoffs":
        import playoff_race

        playoff_race.run()
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
    elif arg == "elo":
        import elo_ratings

        elo_ratings.run()
    elif arg == "moneypuck":
        import moneypuck

        if moneypuck.run():
            sys.exit(1)
    elif arg == "lines":
        import line_combinations

        line_combinations.run(*([season] if season else []))
    elif arg == "projected_lines":
        import projected_lines

        projected_lines.run(*([season] if season else []))
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
        if status not in ("pass", "warn", "off_season"):
            sys.exit(1)
    elif arg == "ai":
        if run_ai_pipeline():
            sys.exit(1)
    else:
        run_all()
