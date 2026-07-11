"""
test_run_stage_isolation.py — regression coverage for Session 46's fix to
the pipeline's total-abort failure mode (cross-repo audit finding #7).

Before the fix: run_all() called every stage sequentially with no
try/except around any of them, so one stage's exception (a real bug, a
schema change, an unhandled edge case) killed the entire nightly run,
including stages that had nothing to do with the failure. Same shape
inside run_ai_pipeline()'s three sequential subprocess calls.

Fixed by run_stage(), which isolates one stage's exception, logs it, and
returns the STAGE_FAILED sentinel instead of propagating -- distinct from
any real stage return value (None included), so a stage that legitimately
returns None on success isn't confused with one that crashed.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import run


class TestRunStage:
    def test_success_returns_the_function_result(self):
        assert run.run_stage("ok stage", lambda: "some result") == "some result"

    def test_success_with_none_return_is_not_confused_with_failure(self):
        """A stage that legitimately returns None on success must not look
        like STAGE_FAILED to the caller."""
        result = run.run_stage("returns none", lambda: None)
        assert result is None
        assert result is not run.STAGE_FAILED

    def test_exception_is_caught_and_returns_stage_failed(self):
        def boom():
            raise ValueError("simulated schema change")

        assert run.run_stage("crashing stage", boom) is run.STAGE_FAILED

    def test_exception_does_not_propagate(self):
        """The whole point: run_stage() must never let the caller's
        exception reach run_all(), or we're back to total-abort."""

        def boom():
            raise KeyError("unexpected API shape")

        try:
            run.run_stage("crashing stage", boom)
        except Exception as e:
            raise AssertionError(f"run_stage() let an exception propagate: {e}") from None


class TestRunAiPipelineIsolation:
    def test_one_failing_substage_does_not_block_the_others(self, monkeypatch):
        """game_scoring, ai_summaries, ai_scouting, and ai_results_vs_process
        don't depend on each other's output -- if game_scoring crashes, the
        other three must still run, and the failure must be reported rather
        than silently dropped."""
        attempted = []

        def fake_run_subprocess(label, cmd):
            attempted.append(label)
            if "game_scoring" in label:
                raise RuntimeError("simulated game_scoring.py failure")

        monkeypatch.setattr(run, "run_subprocess", fake_run_subprocess)

        failures = run.run_ai_pipeline()

        assert len(attempted) == 4, "all four sub-stages must be attempted"
        assert len(failures) == 1
        assert "game_scoring" in failures[0]

    def test_all_substages_succeeding_reports_no_failures(self, monkeypatch):
        monkeypatch.setattr(run, "run_subprocess", lambda label, cmd: None)

        assert run.run_ai_pipeline() == []
