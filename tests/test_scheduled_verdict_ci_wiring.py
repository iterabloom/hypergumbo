# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two scheduled workflows record each step's result and publish the verdict.

Woodpecker gives a step no other step's result. So every step of
``full-suite.yml`` and ``nightly.yml`` ends with
``publish-scheduled-verdict mark <its own name>`` (reached only when the
step's other commands passed, under ``set -e``), and a last step,
``publish-verdict``, runs after all of them on success and failure and
publishes what the markers say. Each way that wiring can silently lie is
pinned here: a missing mark, a mark under a sibling's name (that sibling then
reads ``passed`` when it failed), a step left out of ``--steps``, a publish
step that a failure skips or that swallows its own failure, and a coverage
report written to one path and read from another. The last tests run the
real script over the commands as written.
"""
# covers: .woodpecker/full-suite.yml, .woodpecker/nightly.yml, .woodpecker/woodpecker.yml, scripts/publish-scheduled-verdict
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

# A HARD import: pyyaml is installed wherever tests/ runs; importorskip could
# only turn these pins into a silent skip (see test_ci_self_claims_gate_scope.py).
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WOODPECKER = REPO_ROOT / ".woodpecker"
SCRIPT = "scripts/publish-scheduled-verdict"
PUBLISH = "publish-verdict"
SHA = "0123456789abcdef0123456789abcdef01234567"

#: workflow file -> (workflow name, leg argument, the whole-suite pytest step)
WORKFLOWS = {
    "full-suite.yml": ("full-suite", None, "test-all-packages"),
    "nightly.yml": ("nightly", "${PYTHON_VERSION}", "test-matrix"),
}


def _steps(name: str) -> list[dict]:
    return yaml.safe_load((WOODPECKER / name).read_text(encoding="utf-8"))["steps"]


def _others(name: str) -> list[dict]:
    return [s for s in _steps(name) if s["name"] != PUBLISH]


def _publish(name: str) -> dict:
    [step] = [s for s in _steps(name) if s["name"] == PUBLISH]
    return step


def _publish_argv(name: str) -> list[str]:
    [command] = _publish(name)["commands"]
    return shlex.split(command)


def _option(argv: list[str], flag: str) -> list[str]:
    """The values after ``flag`` up to the next ``--option``."""
    start = argv.index(flag) + 1
    end = next((i for i in range(start, len(argv)) if argv[i].startswith("--")), len(argv))
    return argv[start:end]


@pytest.fixture(params=sorted(WORKFLOWS))
def workflow(request) -> str:
    return request.param


def test_publish_verdict_is_the_last_step(workflow: str) -> None:
    assert _steps(workflow)[-1]["name"] == PUBLISH


def test_every_step_ends_by_marking_itself_passed(workflow: str) -> None:
    for step in _others(workflow):
        assert step["commands"][-1] == f"{SCRIPT} mark {step['name']}", (
            f"{workflow}:{step['name']} must end with its own mark, as a separate command")


def test_no_step_marks_anything_before_its_last_command(workflow: str) -> None:
    for step in _others(workflow):
        early = [c for c in step["commands"][:-1] if f"{SCRIPT} mark" in c]
        assert not early, f"{workflow}:{step['name']} marks before its work is done: {early}"


def test_publish_waits_for_every_other_step(workflow: str) -> None:
    deps = _publish(workflow).get("depends_on") or []
    assert sorted(deps) == sorted(s["name"] for s in _others(workflow))


def test_publish_runs_on_success_and_on_failure(workflow: str) -> None:
    assert _publish(workflow).get("when") == [{"status": ["success", "failure"]}]


def test_publish_carries_the_deploy_key_secret(workflow: str) -> None:
    env = _publish(workflow).get("environment") or {}
    assert env.get("BADGES_DEPLOY_KEY") == {"from_secret": "badges_deploy_key"}


def test_publish_cannot_swallow_its_own_failure(workflow: str) -> None:
    step = _publish(workflow)
    assert step.get("failure") != "ignore"
    for command in step["commands"]:
        assert "|| true" not in command and "||" not in command and "; true" not in command


def test_publish_names_every_other_step_in_file_order(workflow: str) -> None:
    argv = _publish_argv(workflow)
    assert argv[:2] == [SCRIPT, "publish"]
    assert _option(argv, "--steps") == [s["name"] for s in _others(workflow)]


def test_publish_names_its_workflow_and_leg(workflow: str) -> None:
    name, leg, _ = WORKFLOWS[workflow]
    argv = _publish_argv(workflow)
    assert _option(argv, "--workflow") == [name]
    if leg is None:
        assert "--leg" not in argv
    else:
        assert _option(argv, "--leg") == [leg]
    assert "--remote" not in argv and "--dry-run" not in argv


def _cov_json_paths(command: str) -> list[str]:
    return [tok.split(":", 1)[1] for tok in shlex.split(command.replace("\\\n", " "))
            if tok.startswith("--cov-report=json:")]


def test_the_whole_suite_pytest_writes_the_report_the_publish_reads(workflow: str) -> None:
    _, _, pytest_step = WORKFLOWS[workflow]
    [step] = [s for s in _steps(workflow) if s["name"] == pytest_step]
    [whole_suite] = [c for c in step["commands"] if "--cov-fail-under=100" in c]
    written = _cov_json_paths(whole_suite)
    assert len(written) == 1, whole_suite
    assert _option(_publish_argv(workflow), "--coverage-json") == written
    # the default marker directory, so the working tree stays clean
    assert written[0].startswith(".git/scheduled-verdict/")


def test_no_other_step_writes_a_json_coverage_report(workflow: str) -> None:
    _, _, pytest_step = WORKFLOWS[workflow]
    for step in _others(workflow):
        if step["name"] != pytest_step:
            assert not any(_cov_json_paths(c) for c in step["commands"]), step["name"]


# ── run the commands as written ─────────────────────────────────────────────

def _expand(command: str) -> list[str]:
    argv = shlex.split(command.replace("${PYTHON_VERSION}", "3.12"))
    assert argv[0] == SCRIPT
    return [str(REPO_ROOT / SCRIPT), *argv[1:]]


def _run_workflow(tmp_path: Path, workflow: str, *, fail: str | None) -> dict:
    """Every step's mark (except ``fail``'s), then the publish as a dry run."""
    repo = tmp_path / "ws"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    env = {**os.environ, "CI_COMMIT_SHA": SHA}
    for step in _others(workflow):
        if step["name"] != fail:
            subprocess.run(_expand(step["commands"][-1]), cwd=repo, env=env, check=True)
    [command] = _publish(workflow)["commands"]
    proc = subprocess.run([*_expand(command), "--dry-run"], cwd=repo, env=env,
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["verdict"]


def test_the_commands_as_written_publish_passed_when_every_step_passed(
        tmp_path: Path, workflow: str) -> None:
    verdict = _run_workflow(tmp_path, workflow, fail=None)
    assert verdict["status"] == "passed"
    assert [s["name"] for s in verdict["steps"]] == [s["name"] for s in _others(workflow)]


def test_the_commands_as_written_name_the_step_that_did_not_pass(
        tmp_path: Path, workflow: str) -> None:
    _, _, pytest_step = WORKFLOWS[workflow]
    verdict = _run_workflow(tmp_path, workflow, fail=pytest_step)
    assert verdict["status"] == "failed"
    assert [s["name"] for s in verdict["steps"] if s["result"] == "not_passed"] == [pytest_step]


def test_a_change_to_the_publisher_probes_its_transport_on_the_runner() -> None:
    """The push happens only in the scheduled workflows, on a runner that
    cannot reach port 22. A pull request that changes the publisher must make
    the connection from that runner (no key: the secret is not a pull
    request's), or a broken transport is found at the next scheduled run."""
    (step,) = [s for s in _steps("woodpecker.yml") if s["name"] == "verdict-transport-probe"]
    assert step["commands"] == [f"{SCRIPT} probe"]
    assert step["when"] == [{"path": {"include": [SCRIPT]}}]
    assert step["depends_on"] == [], "it waits for nothing and nothing may skip it"
    assert "environment" not in step and "failure" not in step
