# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kalub Step 3 + 1c-parity: static guards for full-suite coverage teeth and
the ci.yml scoped-gate no-data distinction.

**Full-suite teeth (Step 3).** `full-suite.yml` runs each package's *whole* test
suite with `--cov`, but historically only *scraped* the total for the coverage
badge/last-green marker — the job passed regardless of coverage. So the blocking
path (per-PR ci.yml + full-suite) enforced 100% only on *changed* files; a
cross-cutting regression on an *unchanged* file rested entirely on nightly. These
guards pin that every per-package job now FAILS when its whole-codebase coverage
is not exactly 100% — reusing the same `[ "$COV" = "100" ]` comparison the
last-green-marker write already uses (full-suite.yml), so the teeth cannot
regress a genuinely-100% package. Pre-verified: no current coverage debt
(WI-kalub option-b investigation; all six packages measured at 100% in isolation).

**ci.yml no-data parity (Step 1c twin).** The per-PR scoped coverage gate must
distinguish coverage.py's "No data to report" (the affected slice didn't exercise
a changed file — not a regression) from a real <100% failure, matching
smart-test's gate.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FULL_SUITE = REPO_ROOT / ".github" / "workflows" / "full-suite.yml"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# The per-package coverage-scrape line every test job emits.
_COVERAGE_OUT = 'echo "coverage=${COV:-0}" >> "$GITHUB_OUTPUT"'
# The teeth: fail the job unless the package is at exactly 100%.
_TEETH = re.compile(r'\[ "\$\{COV:-0\}" = "100" \]')


def test_full_suite_present() -> None:
    assert FULL_SUITE.is_file(), f"full-suite.yml not found at {FULL_SUITE}"


def test_every_per_package_job_has_coverage_teeth() -> None:
    """Every per-package `coverage=${COV:-0}` scrape must be paired with a teeth
    assert that fails the job when coverage != 100%."""
    text = FULL_SUITE.read_text()
    n_packages = text.count(_COVERAGE_OUT)
    assert n_packages >= 6, (
        f"expected >=6 per-package coverage scrapes in full-suite.yml, found "
        f"{n_packages}; the teeth guard needs updating if the job layout changed"
    )
    n_teeth = len(_TEETH.findall(text))
    assert n_teeth >= n_packages, (
        f"full-suite.yml has {n_packages} per-package coverage scrapes but only "
        f"{n_teeth} coverage-teeth asserts. Every per-package job must FAIL when "
        f'its whole-codebase coverage is not 100% (`[ "${{COV:-0}}" = "100" ]`), '
        "so a cross-cutting regression on an unchanged file reds full-suite + "
        "stop-the-line instead of resting only on nightly (WI-kalub Step 3)."
    )


def test_teeth_follow_each_scrape() -> None:
    """Structural: each `coverage=${COV:-0}` scrape is paired with a teeth assert
    in the same block (within a short window — an explanatory comment may sit
    between them), so no per-package job is left untoothed."""
    lines = FULL_SUITE.read_text().splitlines()
    window = 10
    for i, ln in enumerate(lines):
        if _COVERAGE_OUT in ln:
            following = "\n".join(lines[i + 1:i + 1 + window])
            assert _TEETH.search(following), (
                f"the coverage scrape at full-suite.yml:{i + 1} has no coverage-"
                f"teeth assert within {window} lines after it (WI-kalub Step 3). "
                f"Lines after it:\n{following}"
            )


def test_ci_scoped_gate_distinguishes_no_data() -> None:
    """1c parity: ci.yml's per-PR scoped coverage gate must special-case
    coverage.py's 'No data to report' (exit 1 — slice didn't exercise the file)
    rather than treating it as a real <100% failure."""
    text = CI.read_text()
    assert "No data to report" in text, (
        "ci.yml's scoped coverage gate does not distinguish coverage.py's 'No data "
        "to report' (the affected slice didn't exercise a changed file, e.g. "
        "subprocess-only) from a real <100% failure — it can false-red. Mirror "
        "smart-test's gate (WI-kalub Step 1c parity)."
    )
