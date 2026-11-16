# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: .github/workflows/release.yml
"""The release workflow's gates before publishing can fail.

``build-and-publish`` runs only when ``security-audit`` and ``io-manifest``
succeed, so a step in those two jobs is a gate exactly as far as its exit
code reaches the job. Two steps there have been wired so that it could not:

* ``pipx run safety check || true`` on a runner with no ``pipx``: the command
  always errored and ``|| true`` hid it;
* ``pip install trufflehog`` followed by
  ``trufflehog filesystem . --no-update --only-verified || true``: PyPI's
  ``trufflehog`` is a different, older program that rejects those arguments,
  so the step printed a usage error and passed. The 10.0.0 release run shows
  it.

Both looked like a passing security check in the job list. These tests read
the workflow file and pin the three properties that make the secret scan a
gate: no command in a gate job is followed by ``|| true``, the scanner is the
one hypergumbo installs by version and checksum, and it is invoked by a path
that exists whether or not ``~/.local/bin`` is on ``PATH``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"

#: The jobs ``build-and-publish`` waits for.
GATE_JOBS = ("security-audit", "io-manifest")


def _jobs() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = loaded["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _command_lines(job: dict[str, Any]) -> list[tuple[str, str]]:
    """(step name, line) for every non-comment line of every ``run:`` script."""
    lines: list[tuple[str, str]] = []
    for step in job["steps"]:
        script = step.get("run")
        if not script:
            continue
        for raw in script.splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                lines.append((step.get("name", "<unnamed>"), line))
    return lines


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [s for s in job["steps"] if s.get("name") == name]
    assert len(matches) == 1, f"expected one step named {name!r}, found {len(matches)}"
    step = matches[0]
    assert isinstance(step, dict)
    return step


def test_publishing_waits_for_both_gate_jobs() -> None:
    needs = _jobs()["build-and-publish"]["needs"]
    assert sorted(needs) == sorted(GATE_JOBS)


@pytest.mark.parametrize("job_name", GATE_JOBS)
def test_no_gate_command_has_its_exit_code_swallowed(job_name: str) -> None:
    swallowed = [
        (step, line) for step, line in _command_lines(_jobs()[job_name])
        if "|| true" in line or "||true" in line or line.endswith("|| :")
    ]
    assert not swallowed, (
        f"{job_name}: a swallowed exit code makes the step pass whatever "
        f"the command did: {swallowed}"
    )


@pytest.mark.parametrize("job_name", GATE_JOBS)
def test_no_gate_step_continues_on_error(job_name: str) -> None:
    lenient = [
        step.get("name", "<unnamed>") for step in _jobs()[job_name]["steps"]
        if step.get("continue-on-error")
    ]
    assert not lenient, f"{job_name}: continue-on-error steps cannot gate: {lenient}"


def test_secret_scan_installs_the_pinned_gitleaks() -> None:
    script = _step(_jobs()["security-audit"], "Secret scanning")["run"]
    lines = [line for _, line in _command_lines({"steps": [{"run": script}]})]
    # Installed by hypergumbo's own command (pinned version, checksum checked),
    # as a module: this job does not install the package that provides the
    # ``hypergumbo`` console script.
    assert "python -m hypergumbo_core install-gitleaks --quiet" in lines
    assert not any("pip install" in line for line in lines), lines


def test_secret_scan_runs_gitleaks_by_its_installed_path() -> None:
    script = _step(_jobs()["security-audit"], "Secret scanning")["run"]
    lines = [line for _, line in _command_lines({"steps": [{"run": script}]})]
    scans = [line for line in lines if " detect " in f" {line} "]
    assert scans == ['"$HOME/.local/bin/gitleaks" detect --source . --no-git --no-banner'], lines


def test_secret_scan_step_is_not_conditional() -> None:
    step = _step(_jobs()["security-audit"], "Secret scanning")
    assert "if" not in step
