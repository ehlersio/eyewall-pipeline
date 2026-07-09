"""
test_moneypuck_stage_isolation.py — regression coverage for Session 46's
fix to moneypuck.py's silent try/except swallowing (Item 2, following the
audit's #7 total-abort fix in run.py).

Before the fix: run_game_xg(), run_team_xgf_rollup(), and run_goalie_qs()
each caught any Exception internally and returned after a bare `print`,
with no way for a caller (run.py) to detect that a sub-stage had failed --
the pipeline always reported green. Same shape for the inline RAPM-values
load inside run() itself.

Fixed by moving the try/except out of the individual functions (letting
them raise naturally) and isolating each call via _run_substage(), which
logs loudly and records the failure in a list that run() now returns --
distinct from run.py's STAGE_FAILED sentinel (moneypuck.run() itself
didn't raise, it completed with a partial failure), but visible to
run.py's run_all() all the same, which folds this list into its own
failed_stages report.
"""

import moneypuck


class TestRunSubstageIsolation:
    def test_success_records_no_failure(self):
        failures = []
        moneypuck._run_substage(failures, "some_stage", lambda: "ok")
        assert failures == []

    def test_exception_is_caught_and_recorded(self):
        failures = []

        def boom():
            raise ValueError("simulated MoneyPuck CSV schema change")

        moneypuck._run_substage(failures, "game_xg", boom)

        assert failures == ["moneypuck.game_xg (ValueError)"]

    def test_exception_does_not_propagate(self):
        """The whole point: one sub-stage crashing must not stop the others
        from being attempted by run()."""
        failures = []

        def boom():
            raise KeyError("unexpected MoneyPuck row shape")

        try:
            moneypuck._run_substage(failures, "goalie_qs", boom)
        except Exception as e:
            raise AssertionError(f"_run_substage() let an exception propagate: {e}") from None

    def test_multiple_independent_calls_all_get_attempted(self):
        """Mirrors how run() calls game_xg / team_xgf_rollup / goalie_qs
        sequentially -- one failing must not prevent the others from
        running, and every failure must be recorded, not just the first."""
        failures = []
        attempted = []

        def make_stage(label, should_fail):
            def stage():
                attempted.append(label)
                if should_fail:
                    raise RuntimeError(f"simulated {label} failure")

            return stage

        moneypuck._run_substage(failures, "game_xg", make_stage("game_xg", True))
        moneypuck._run_substage(failures, "team_xgf_rollup", make_stage("team_xgf_rollup", False))
        moneypuck._run_substage(failures, "goalie_qs", make_stage("goalie_qs", True))

        assert attempted == ["game_xg", "team_xgf_rollup", "goalie_qs"]
        assert failures == [
            "moneypuck.game_xg (RuntimeError)",
            "moneypuck.goalie_qs (RuntimeError)",
        ]
