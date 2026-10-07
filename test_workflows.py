"""Pins the workflow conventions from README "Pipeline scheduling" (2026-10).

Text checks (PyYAML isn't a dependency): every job has a timeout, every
workflow but ci.yml reports failures to the Worker's /ops/notify under its
own file name, every writer workflow has a concurrency group, the six
Worker-dispatched workflows skip a scheduled run that already succeeded today
(and never skip a dispatch), and live-score-refresh.yml is manual only.
"""

import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).parent / ".github" / "workflows"
ALL = sorted(p.name for p in WORKFLOWS.glob("*.yml"))
DISPATCHED = {
    "nightly.yml": "run-pipeline",
    "pwhl-nightly.yml": "run-pwhl-pipeline",
    "ahl-nightly.yml": "run-ahl-pipeline",
    "echl-nightly.yml": "run-echl-pipeline",
    "moneypuck-ingest.yml": "ingest",
    "ai_pipeline.yml": "ai-morning",
}
GUARD_IF = "steps.guard.outputs.skip != 'true'"


def text(name):
    return (WORKFLOWS / name).read_text()


def jobs(name):
    """{job_id: job_text} for a workflow."""
    t = text(name)
    body = t[t.index("\njobs:\n") + len("\njobs:\n") :]
    parts = re.split(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", body)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def steps(job_text):
    """[(name, step_text)] for a job, in order."""
    chunks = re.split(r"(?m)^      - name: ", job_text)[1:]
    return [(c.split("\n", 1)[0].strip(), c) for c in chunks]


@pytest.mark.parametrize("name", ALL)
def test_every_job_has_a_timeout(name):
    for job, body in jobs(name).items():
        assert re.search(r"(?m)^    timeout-minutes: \d+\b", body), f"{name}:{job}"


@pytest.mark.parametrize("name", [n for n in ALL if n != "ci.yml"])
def test_failures_notify_the_worker(name):
    for job, body in jobs(name).items():
        notify = dict(steps(body)).get("Notify on failure")
        assert notify, f"{name}:{job} has no Notify on failure step"
        assert "if: failure()" in notify
        assert '/ops/notify?secret=$POLL_SECRET"' in notify
        assert "POLL_SECRET: ${{ secrets.EYEWALL_POLL_SECRET }}" in notify
        assert f'\\"source\\":\\"{name}\\"' in notify
        assert '\\"status\\":\\"failure\\"' in notify
        assert "echo" not in notify


@pytest.mark.parametrize("name", [n for n in ALL if n != "ci.yml"])
def test_successes_report_ok_to_the_worker(name):
    """A clean run reports status ok under the same source, so the Worker's
    health:ops record (and /admin/health) clears instead of showing the last
    failure until the next one. The Worker never pushes for ok."""
    for job, body in jobs(name).items():
        step_map = dict(steps(body))
        ok = step_map.get("Report success")
        assert ok, f"{name}:{job} has no Report success step"
        assert "if: success()" in ok
        assert '/ops/notify?secret=$POLL_SECRET"' in ok
        assert f'\\"source\\":\\"{name}\\"' in ok
        assert '\\"status\\":\\"ok\\"' in ok
        assert "failed" not in ok.split("run:")[1]


@pytest.mark.parametrize("name", [n for n in ALL if n != "ci.yml"])
def test_writer_workflows_do_not_overlap(name):
    t = text(name)
    assert re.search(r"(?m)^concurrency:\n  group: \S+\n  cancel-in-progress: false$", t), name


@pytest.mark.parametrize("name,job", sorted(DISPATCHED.items()))
def test_dispatched_workflows_skip_a_scheduled_rerun(name, job):
    t = text(name)
    assert "workflow_dispatch:" in t
    assert re.search(r"(?m)^  schedule:$", t), "keeps its cron as the fallback"
    assert "actions: read" in t
    st = steps(jobs(name)[job])
    first_name, guard = st[0]
    assert first_name == "Skip if already ran today"
    assert "id: guard" in guard
    # A dispatch (the Worker's or a manual one) must never skip.
    assert "if: github.event_name == 'schedule'" in guard
    assert "GH_TOKEN: ${{ github.token }}" in guard
    assert f"--workflow {name} --status success" in guard
    for step_name, body in st[1:]:
        if step_name in ("Notify on failure", "Report success"):
            continue
        assert GUARD_IF in body, f"{name}: step {step_name!r} ignores the guard"


def test_ai_pipeline_bare_dispatch_runs_the_morning_job_only():
    t = text("ai_pipeline.yml")
    assert re.search(r'default: "morning"', t)
    j = jobs("ai_pipeline.yml")
    assert "github.event_name == 'workflow_dispatch' && (inputs.job == 'night'" in j["ai-night"]
    assert "github.event_name == 'schedule'" not in j["ai-night"]
    assert "inputs.job == 'morning'" in j["ai-morning"]


def test_live_score_refresh_is_manual_only():
    t = text("live-score-refresh.yml")
    on = t[t.index("\non:\n") : t.index("\njobs:\n")]
    assert "workflow_dispatch:" in on
    assert "schedule:" not in on and "cron:" not in on


def test_pwhl_nightly_takes_a_season_id():
    t = text("pwhl-nightly.yml")
    assert re.search(r"(?m)^      season_id:$", t)
    for script in (
        "pwhl_shot_events.py",
        "pwhl_pbp_events.py",
        "pwhl_game_boxscore.py",
        "pwhl_goal_on_ice.py",
        "pwhl_penalty_shots.py",
    ):
        assert f'python {script} "$SEASON_ID"' in t


def test_sbnation_has_no_push_trigger():
    on = text("sbnation-ingest.yml").split("\njobs:\n")[0]
    assert "push:" not in on


@pytest.mark.parametrize("league", ["ahl", "echl"])
def test_hockeytech_nightlies_ingest_goal_on_ice(league):
    t = text(f"{league}-nightly.yml")
    assert f'python hockeytech_goal_on_ice.py {league} "${{{{ inputs.season_id }}}}"' in t
