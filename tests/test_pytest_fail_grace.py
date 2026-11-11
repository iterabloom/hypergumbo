# SPDX-License-Identifier: AGPL-3.0-or-later
"""``--fail-grace``: a red run stops soon after its first failure.

A failing per-PR pytest step ran its whole selection to the end. Measured on
recent PRs, a selection of 200 to 760 files takes 11 to 28 minutes, and the
verdict was already decided at the first failure. ``--fail-grace=N`` lets the
run continue N seconds after the first failure, so failures that arrive
together are all reported, and then stops it (WI-fipuf, the owner's rule).

Each test here runs pytest on a throwaway project in a subprocess, with and
without pytest-xdist, because the two stop a session by different switches.
"""

# covers: scripts/pytest_plugins/pytest_fail_grace.py
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "scripts" / "pytest_plugins"

#: One early failure, then four seconds of slow passing tests.
_PROJECT = {
    "test_a_first.py": "def test_bad():\n    assert False\n",
    "test_b_slow.py": (
        "import time\nimport pytest\n\n"
        "@pytest.mark.parametrize('i', range(40))\n"
        "def test_slow(i):\n    time.sleep(0.1)\n"
    ),
}


def _run(tmp_path: Path, *args: str, files: dict | None = None) -> subprocess.CompletedProcess:
    for name, text in (files or _PROJECT).items():
        (tmp_path / name).write_text(text)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTEST_", "COV_CORE_", "COVERAGE_"))}
    env["PYTHONPATH"] = str(PLUGIN_DIR)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "pytest_fail_grace",
         "-o", "addopts=", "-p", "no:cacheprovider", "-q", "--rootdir", str(tmp_path),
         *args, str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path, env=env)


def _passed(done: subprocess.CompletedProcess) -> int:
    import re

    match = re.search(r"(\d+) passed", done.stdout)
    return int(match.group(1)) if match else 0


@pytest.mark.parametrize("workers", ["0", "2"])
def test_the_run_stops_soon_after_the_first_failure(tmp_path: Path, workers: str) -> None:
    done = _run(tmp_path, "--fail-grace=0.5", "-n", workers)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "1 failed" in done.stdout
    assert "fail-grace: stopped 0.5s after the first failure" in done.stdout
    assert _passed(done) < 40, "every slow test still ran"


@pytest.mark.parametrize("workers", ["0", "2"])
def test_without_the_option_the_run_goes_to_the_end(tmp_path: Path, workers: str) -> None:
    """The control: the same project, and nothing stops it."""
    done = _run(tmp_path, "-n", workers)
    assert done.returncode == 1
    assert _passed(done) == 40


def test_a_green_run_is_untouched(tmp_path: Path) -> None:
    done = _run(tmp_path, "--fail-grace=0.1", "-n", "2",
                files={"test_b_slow.py": _PROJECT["test_b_slow.py"]})
    assert done.returncode == 0, done.stdout + done.stderr
    assert _passed(done) == 40
    assert "fail-grace" not in done.stdout


def test_failures_inside_the_grace_period_are_all_reported(tmp_path: Path) -> None:
    """Why there is a grace period at all, and not ``-x``."""
    files = {
        "test_a_first.py": "def test_bad():\n    assert False\n",
        "test_b_second.py": "import time\n\ndef test_also_bad():\n    time.sleep(0.3)\n    assert False\n",
        "test_c_slow.py": _PROJECT["test_b_slow.py"],
    }
    done = _run(tmp_path, "--fail-grace=1.5", "-n", "0", files=files)
    assert done.returncode == 1
    assert "2 failed" in done.stdout
    assert _passed(done) < 40


def test_an_expected_failure_does_not_start_the_clock(tmp_path: Path) -> None:
    files = {
        "test_a_first.py": (
            "import pytest\n\n@pytest.mark.xfail\ndef test_known():\n    assert False\n"
        ),
        "test_b_slow.py": _PROJECT["test_b_slow.py"],
    }
    done = _run(tmp_path, "--fail-grace=0.1", "-n", "0", files=files)
    assert done.returncode == 0, done.stdout + done.stderr
    assert _passed(done) == 40


def test_the_stop_names_how_to_see_every_failure(tmp_path: Path) -> None:
    done = _run(tmp_path, "--fail-grace=0.5", "-n", "0")
    assert "--fail-grace=0" in done.stdout


def test_zero_turns_it_off(tmp_path: Path) -> None:
    done = _run(tmp_path, "--fail-grace=0", "-n", "0")
    assert done.returncode == 1
    assert _passed(done) == 40


# ── smart-test's side: the order it runs in, and which runs use the rule ─────

SMART_TEST = REPO_ROOT / "scripts" / "smart-test"


def _run_order(affected: str, changed_tests: str = "", coverage: str = "",
               citations: str = "") -> list[str]:
    """Drive the shipped RUN_ORDER block, extracted from smart-test."""
    text = SMART_TEST.read_text(encoding="utf-8")
    start = text.index("RUN_ORDER=$( {")
    end = text.index("} | awk 'NF && !seen[$0]++')", start) + len("} | awk 'NF && !seen[$0]++')")
    script = "\n".join([
        "set -euo pipefail",
        f"AFFECTED_TESTS=${affected!r}",  # $'...': bash reads the \\n
        f"CHANGED_TEST_FILES=${changed_tests!r}",  # $'...': bash reads the \\n
        f"COVERAGE_UNION_ADDS=${coverage!r}",  # $'...': bash reads the \\n
        f"CITATION_GATE_TESTS=${citations!r}",  # $'...': bash reads the \\n
        text[start:end],
        'echo "$RUN_ORDER"',
    ])
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


def test_the_likely_failures_run_first() -> None:
    order = _run_order("t/a.py\nt/b.py\nt/c.py\nt/d.py", changed_tests="t/d.py",
                       coverage="t/c.py", citations="t/b.py")
    assert order == ["t/d.py", "t/c.py", "t/b.py", "t/a.py"]


def test_ordering_neither_adds_nor_drops_a_test() -> None:
    """A priority entry outside the run set stays out; a duplicate runs once."""
    order = _run_order("t/a.py\nt/b.py\nt/c.py", changed_tests="t/c.py\nt/zzz.py",
                       coverage="t/c.py\nt/not_selected.py")
    assert order == ["t/c.py", "t/a.py", "t/b.py"]


def test_with_nothing_to_prioritise_the_order_is_unchanged() -> None:
    assert _run_order("t/a.py\nt/b.py") == ["t/a.py", "t/b.py"]


def test_only_targeted_runs_use_the_rule() -> None:
    """A full run exists to see every failure, and a cut-short one must not be
    folded into the selection index as a complete picture."""
    text = SMART_TEST.read_text(encoding="utf-8")
    uses = [line.strip() for line in text.splitlines() if "FAIL_GRACE_ARGS[@]" in line]
    assert len(uses) == 2, uses
    assert all(line.startswith('run_pytest "$RUN_ORDER"') for line in uses), uses
    assert not [line for line in text.splitlines()
                if "FULL_SUITE_PYTEST_TARGETS" in line and "FAIL_GRACE" in line]


def test_the_per_pr_pytest_step_uses_the_rule() -> None:
    """The step that motivated it. Only the per-PR gate: the cron suites exist
    to list every failure."""
    step = (REPO_ROOT / ".woodpecker" / "woodpecker.yml").read_text(encoding="utf-8")
    runs = [line for line in step.splitlines() if "--junit-xml" in line and "pytest" in line]
    assert len(runs) == 1, runs
    assert "-p pytest_fail_grace --fail-grace=15" in runs[0]
    assert 'PYTHONPATH="scripts/pytest_plugins' in step
    for cron in ("full-suite.yml", "nightly.yml"):
        text = (REPO_ROOT / ".woodpecker" / cron).read_text(encoding="utf-8")
        assert "fail-grace" not in text, cron
