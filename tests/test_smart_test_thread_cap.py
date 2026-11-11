# SPDX-License-Identifier: AGPL-3.0-or-later
"""smart-test gives each parallel test worker ONE OpenMP thread.

WI-zuzoz. When the embedding model is in the local cache, a pipeline run
inside a test performs torch inference, and torch sizes its OpenMP pool to
every core. Each pytest-xdist worker starts its own pool, so six workers put
up to 36 compute threads on six cores. Measured, same commit and machine:
the full suite took 45:49 as it was and 35:16 with ``OMP_NUM_THREADS=1``;
``test_route_concept_parity.py`` alone took 401 s and 61 s.

The cap is for PARALLEL runs only. One uncontended process is slower with it
(17 pipeline runs: 26 s with the pool, 44 s capped), and a value the caller
set is the caller's decision.
"""

# covers: scripts/smart-test
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SMART_TEST = REPO_ROOT / "scripts" / "smart-test"


def _function(name: str) -> str:
    lines = SMART_TEST.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{name}() {{"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


def _cap(*args: str, preset: str | None = None) -> str:
    script = "\n".join([
        "set -euo pipefail",
        "unset OMP_NUM_THREADS" if preset is None else f"export OMP_NUM_THREADS={preset}",
        _function("omp_threads_for_run"),
        'omp_threads_for_run "$@"',
    ])
    done = subprocess.run(["bash", "-c", script, "bash", *args],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.mark.parametrize("args", [
    ("-n", "auto"), ("-n", "6"), ("-n6",), ("-nauto",),
    ("--numprocesses=4",), ("--numprocesses", "2"),
    ("--tb=short", "-n", "3", "-q"),
])
def test_a_parallel_run_is_capped(args: tuple[str, ...]) -> None:
    assert _cap(*args) == "1"


@pytest.mark.parametrize("args", [
    (), ("-q",), ("-n", "0"), ("-n0",), ("-n", "1"), ("--numprocesses=1",),
])
def test_a_single_process_run_keeps_its_threads(args: tuple[str, ...]) -> None:
    """Capped, one uncontended process is slower, not faster."""
    assert _cap(*args) == ""


def test_the_last_worker_count_wins() -> None:
    """As it does for pytest: smart-test adds its own -n before the caller's."""
    assert _cap("-n", "auto", "-n", "0") == ""
    assert _cap("-n", "0", "-n", "4") == "1"


def test_a_value_the_caller_set_is_left_alone() -> None:
    assert _cap("-n", "auto", preset="4") == ""


def test_both_pytest_invocations_apply_it() -> None:
    body = _function("run_pytest")
    runs = [line for line in body.splitlines()
            if " pytest $test_targets" in line and not line.lstrip().startswith("#")]
    assert len(runs) == 2, runs
    assert all("${omp_cap:+OMP_NUM_THREADS=$omp_cap}" in line for line in runs), runs
    assert 'omp_cap=$(omp_threads_for_run "${extra_args[@]}")' in body
