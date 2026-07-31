# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-rabum: static guard that the mypy ratchet actually GATES merges.

The flip from a reporting rung to a blocking one is four separate edits in
`ci.yml`, and three of them are individually silent. Removing
`continue-on-error` makes the job red but changes nothing on its own, because
the merge verdict is `ci-complete`, and a job absent from `ci-complete.needs`
cannot fail it. That is the exact shape the previous rung had — a job that
looked like a gate and gated nothing — and it is why `WI-basat` accumulated
seven errors over ten days with CI green the whole time.

So this pins all four, together:

1. the job carries no `continue-on-error`,
2. `mypy` is in `ci-complete.needs`,
3. `ci-complete`'s failure check actually reads `needs.mypy.result`,
4. the ratchet runs `--mode=strict`.

Plus the instrument pin: the baseline's counts are only comparable against the
mypy that produced them, so a bump must be a deliberate edit that carries its
own `--update-baseline`. `~=2.1` admits 2.2, which could recategorise errors and
move the numbers with no code change; `~=2.1.0` does not.

Named for the mapper: `scripts/check-mypy-ratchet` resolves to
`tests/test_check_mypy_ratchet*`, so editing the script selects this file.
NOTE a real gap this cannot close — `ci.yml` is not a mapped source, so editing
*it* selects nothing (the INV-kinin class). A silent revert of the flip is
caught by the twice-daily full suite, not per-PR.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _jobs() -> dict:
    return yaml.safe_load(CI_YML.read_text())["jobs"]


def test_mypy_job_is_not_continue_on_error() -> None:
    """A reporting rung sets this; a gate must not."""
    assert "continue-on-error" not in _jobs()["mypy"], (
        "the mypy job carries continue-on-error — it reports and does not gate"
    )


def test_mypy_is_a_dependency_of_the_merge_verdict() -> None:
    """Absent from ci-complete.needs, a red mypy job blocks nothing."""
    assert "mypy" in _jobs()["ci-complete"]["needs"], (
        "mypy is not in ci-complete.needs, so its failure cannot fail the run"
    )


def test_ci_complete_actually_reads_the_mypy_result() -> None:
    """Being in `needs` is necessary and not sufficient.

    `ci-complete` runs `if: always()` and decides by explicitly testing each
    result, so a job can sit in `needs` and still be ignored by the verdict.
    """
    steps = _jobs()["ci-complete"]["steps"]
    body = "\n".join(s.get("run", "") for s in steps)
    assert "needs.mypy.result" in body, (
        "ci-complete does not read needs.mypy.result — mypy is in `needs` but "
        "its failure is not part of the verdict"
    )
    assert 'needs.mypy.result }}" == "failure"' in body, (
        "ci-complete reads needs.mypy.result but does not fail on it"
    )


def test_ratchet_runs_in_strict_mode() -> None:
    """`--mode=warning` exits 0 on a regression by design."""
    body = "\n".join(s.get("run", "") for s in _jobs()["mypy"]["steps"])
    assert "--mode=strict" in body, "the mypy job does not run the ratchet strictly"
    assert "--mode=warning" not in body


def test_mypy_is_pinned_to_a_patch_series() -> None:
    """The instrument is part of the measurement (see the module docstring)."""
    body = "\n".join(s.get("run", "") for s in _jobs()["mypy"]["steps"])
    assert '"mypy~=2.1.0"' in body, (
        "mypy is not pinned to a patch series; a minor bump could recategorise "
        "errors and move the baseline's counts with no code change"
    )


def test_baseline_records_the_instrument_that_measured_it() -> None:
    """A shrink-only baseline whose mypy can change under it is not a ratchet."""
    import json

    baseline = json.loads((REPO_ROOT / ".ci" / "mypy-strict-baseline.json").read_text())
    assert baseline.get("mypy_version"), (
        "the committed baseline records no mypy_version, so the ratchet cannot "
        "tell a code delta from a tooling delta"
    )
