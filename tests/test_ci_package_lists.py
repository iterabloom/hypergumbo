# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scheduled CI and the lint scanners must reach every package (WI-bavak).

Background
----------
``hypergumbo-lang-scip-python`` was added on 2026-09-19. The release-path
package lists were fixed and pinned in ``test_release_package_lists.py``; the
lists this module pins were not:

* the Woodpecker ``full-suite`` and ``nightly`` pipelines run
  ``pytest packages/*/tests/`` but never installed it, and never measured its
  coverage. Its tests still passed there, but only by accident:
  ``test_adjudication_packet.py`` loads ``scripts/measure-taint-precision.py``
  at collection time, and that script puts every ``packages/*/src`` on
  ``sys.path``. So the package ran uninstalled (no entry point, so no
  analyzer registration), unmeasured, and one unrelated edit away from five
  collection errors;
* four ``scripts/check-*`` linters put a hand-kept list of package ``src``
  trees on ``sys.path``, and ``check-docstring-drift`` scanned a hand-kept
  tuple of them. The last one did skip scip-python's source. The other three
  scan by glob or find analyzers through installed entry points, so their
  lists were only an import fallback; they now glob too, so there is no list
  to forget.

How the test works
------------------
The package set comes from the filesystem, never from a list under test. A
pipeline step that runs the whole package suite must install every package
and measure the same sources as ``scripts/lib/cov-paths.sh`` (which
``test_release_package_lists.py`` already ties to the filesystem). The linters
are run in a subprocess (so their ``sys.path`` edits cannot leak into this
session, the INV-vazuh trap) and their resulting ``sys.path`` is read back.

The ``.github/workflows`` copies are not checked: GitHub Actions is disabled
under Woodpecker CI (``scripts/ci-debug``), and those files already lag in
other ways.
"""
# covers: .woodpecker/*.yml, scripts/lib/cov-paths.sh, scripts/check-id-construction, scripts/check-pass-id-agreement, scripts/check-multi-value-field-axis-declaration, scripts/check-recorded-producer-input

from __future__ import annotations

import json
import re
from itertools import pairwise
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES = REPO_ROOT / "packages"

_STEP = re.compile(r"^  - name: (\S+)$", re.MULTILINE)
_EDITABLE = re.compile(r'-e "?packages/([A-Za-z0-9_.-]+?)(?:\[[^\]]*\])?"?(?=\s|$)')
_COV = re.compile(r"--cov=(packages/[A-Za-z0-9_.-]+/src)")
#: A ``pytest`` command, with its backslash continuations, whose targets include
#: the whole package suite. ``ruff check packages/*/tests/`` is not one.
_WHOLE_SUITE_PYTEST = re.compile(
    r"^\s*pytest\b[^\n]*(?:\\\n[^\n]*)*packages/\*/tests/", re.MULTILINE,
)


def _packages_with_tests() -> set[str]:
    return {
        p.parent.name for p in PACKAGES.glob("*/tests")
        if (p.parent / "pyproject.toml").is_file()
    }


def _canonical_cov() -> set[str]:
    return set(_COV.findall((REPO_ROOT / "scripts" / "lib" / "cov-paths.sh").read_text()))


def _whole_suite_steps() -> dict[str, str]:
    """``file:step`` -> step text, for every Woodpecker step that runs
    ``packages/*/tests/``."""
    steps = {}
    for pipeline in sorted((REPO_ROOT / ".woodpecker").glob("*.yml")):
        text = pipeline.read_text()
        starts = [m.start() for m in _STEP.finditer(text)] + [len(text)]
        for begin, end in pairwise(starts):
            body = text[begin:end]
            if _WHOLE_SUITE_PYTEST.search(body):
                steps[f"{pipeline.name}:{_STEP.match(body).group(1)}"] = body
    return steps


def test_the_whole_suite_steps_are_found() -> None:
    """Reach: without this, every assertion below is vacuously true."""
    steps = _whole_suite_steps()
    assert "full-suite.yml:test-all-packages" in steps
    assert "nightly.yml:test-matrix" in steps


@pytest.mark.parametrize("step", sorted(_whole_suite_steps()))
def test_a_whole_suite_step_installs_every_package(step: str) -> None:
    installed = set(_EDITABLE.findall(_whole_suite_steps()[step]))
    missing = _packages_with_tests() - installed
    assert not missing, f"{step} runs packages/*/tests/ but installs none of {sorted(missing)}"


@pytest.mark.parametrize("step", sorted(_whole_suite_steps()))
def test_a_whole_suite_step_measures_the_canonical_sources(step: str) -> None:
    measured = set(_COV.findall(_whole_suite_steps()[step]))
    assert measured, f"{step} measures no coverage"
    assert measured == _canonical_cov(), (
        f"{step}: extra {sorted(measured - _canonical_cov())}, "
        f"missing {sorted(_canonical_cov() - measured)}"
    )


@pytest.mark.parametrize("script", [
    "check-id-construction",
    "check-pass-id-agreement",
    "check-multi-value-field-axis-declaration",
    "check-recorded-producer-input",
])
def test_a_linter_puts_every_package_src_on_its_path(script: str) -> None:
    probe = (
        "import json, runpy, sys\n"
        f"runpy.run_path({str(REPO_ROOT / 'scripts' / script)!r}, run_name='probe')\n"
        "print(json.dumps(sys.path))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True,
        cwd=REPO_ROOT,
    )
    on_path = set(json.loads(out.stdout.strip().splitlines()[-1]))
    missing = {str(p) for p in PACKAGES.glob("*/src")} - on_path
    assert not missing, f"{script} leaves {sorted(missing)} off sys.path"
