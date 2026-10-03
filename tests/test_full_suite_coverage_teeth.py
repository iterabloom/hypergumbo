# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kalub Step 3 + 1c-parity: static guards for full-suite coverage teeth and
the per-PR scoped-gate no-data distinction -- on the LIVE pipelines.

**These guards used to read the dormant files** (INV-hokin). The header said
``covers: .woodpecker/full-suite.yml`` while every assertion read
``.github/workflows/full-suite.yml`` and ``.github/workflows/ci.yml``. GitHub
Actions is disabled under Woodpecker CI (the full-suite schedule was commented
out on 2026-07-23), so the tests pinned files that run nowhere and would have
stayed green had the live pipelines lost their teeth. They now read
``.woodpecker/``.

**Full-suite teeth (Step 3).** The GHA original scraped each package's coverage
for a badge and only later gained a ``[ "${COV:-0}" = "100" ]`` assert per
package job. The Woodpecker port has no scrape: its teeth are
``--cov-fail-under=100`` on ONE pytest over every package's tests and sources,
which fails the step under the runner's ``set -e``. The guard pins, for every
Woodpecker step that runs the whole package suite (``packages/*/tests/``):

* the pytest carries ``--cov-fail-under=100`` and no other threshold;
* its exit status is not swallowed (no ``|| true`` / ``|| :`` / ``|| exit 0``
  after it, no ``failure: ignore`` on the step);
* the step is unconditional.

Which sources it measures is pinned by ``test_ci_package_lists.py``.

**What the live teeth do NOT enforce, stated so nobody reads more into them.**
The union run is not the GHA per-package run: a line covered only by ANOTHER
package's tests counts towards 100% here, so "tests live in the same package as
the code they cover" (AGENTS.md) is enforced by no live CI, only by the local
``check-package-coverage`` (INV-hokin premise audit item 2; partly WI-kahar).

**Per-PR no-data parity (Step 1c twin).** The per-PR scoped coverage gate must
distinguish coverage.py's "No data to report" (the affected slice didn't
exercise a changed file -- not a regression) from a real <100% failure,
matching smart-test's gate. The live per-PR pipeline is
``.woodpecker/woodpecker.yml``.
"""

# covers: .woodpecker/*.yml
from __future__ import annotations

import re
from pathlib import Path

import pytest

# A HARD import (see test_ci_self_claims_gate_scope.py): importorskip could only
# ever make these guards skip silently.
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WOODPECKER = REPO_ROOT / ".woodpecker"
FULL_SUITE = WOODPECKER / "full-suite.yml"
PER_PR = WOODPECKER / "woodpecker.yml"

#: A ``pytest`` invocation with its backslash continuations, whose targets
#: include the whole package suite.
_WHOLE_SUITE_PYTEST = re.compile(
    r"^\s*pytest\b[^\n]*(?:\\\n[^\n]*)*packages/\*/tests/[^\n]*", re.MULTILINE,
)
_THRESHOLD = re.compile(r"--cov-fail-under[= ](\S+)")
#: An or-list after the command that turns its failure into success.
_SWALLOWED = re.compile(r"\|\|\s*(?:true|:|exit\s+0)\b")


def _whole_suite_steps() -> dict[str, tuple[dict, str]]:
    """``file:step`` -> (step, matched pytest command) for every Woodpecker
    step whose commands run ``packages/*/tests/``."""
    found: dict[str, tuple[dict, str]] = {}
    for pipeline in sorted(WOODPECKER.glob("*.yml")):
        for step in yaml.safe_load(pipeline.read_text()).get("steps") or []:
            for command in step.get("commands") or []:
                m = _WHOLE_SUITE_PYTEST.search(command)
                if m:
                    found[f"{pipeline.name}:{step['name']}"] = (step, m.group(0))
    return found


def test_the_full_suite_whole_suite_step_is_found() -> None:
    """REACH: without this every per-step assertion below is vacuously true."""
    assert "full-suite.yml:test-all-packages" in _whole_suite_steps()


@pytest.mark.parametrize("key", sorted(_whole_suite_steps()))
def test_whole_suite_pytest_fails_under_100(key: str) -> None:
    _, command = _whole_suite_steps()[key]
    thresholds = _THRESHOLD.findall(command)
    assert thresholds == ["100"], (
        f"{key}: the whole-suite pytest must carry exactly "
        f"--cov-fail-under=100, found {thresholds or 'none'}. Without it the "
        "step passes at any coverage and a cross-cutting regression on an "
        "unchanged file is caught by nothing scheduled (WI-kalub Step 3)."
    )


@pytest.mark.parametrize("key", sorted(_whole_suite_steps()))
def test_whole_suite_failure_is_not_swallowed(key: str) -> None:
    step, command = _whole_suite_steps()[key]
    assert not _SWALLOWED.search(command), (
        f"{key}: the whole-suite pytest's exit status is or-ed away: {command!r}"
    )
    assert step.get("failure") != "ignore", f"{key} is `failure: ignore`"
    assert not step.get("when"), f"{key} grew a when-clause"


def test_per_pr_scoped_gate_distinguishes_no_data() -> None:
    """1c parity: the live per-PR scoped coverage gate must special-case
    coverage.py's 'No data to report' (slice didn't exercise the file) rather
    than treating it as a real <100% failure."""
    commands = [
        c for step in yaml.safe_load(PER_PR.read_text())["steps"]
        for c in step.get("commands") or []
        if "coverage report" in c and "--fail-under=100" in c
    ]
    assert commands, f"no scoped coverage gate found in {PER_PR.name} (reach)"
    assert any("No data to report" in c for c in commands), (
        f"{PER_PR.name}'s scoped coverage gate does not distinguish coverage.py's "
        "'No data to report' (the affected slice didn't exercise a changed "
        "file, e.g. subprocess-only) from a real <100% failure -- it can "
        "false-red. Mirror smart-test's gate (WI-kalub Step 1c parity)."
    )
