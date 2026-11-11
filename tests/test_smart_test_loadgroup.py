# SPDX-License-Identifier: AGPL-3.0-or-later
"""A module whose fixture is expensive runs on ONE xdist worker.

xdist's default ``load`` mode hands tests out one at a time, so one module's
tests land on several workers and each of those builds the module fixture
again. ``test_route_concept_parity.py``'s fixture (a full pipeline run per
case, 57-84 s) was built in all six workers when the file ran alone, and in
two during a full suite.

The cure has two halves, and both are pinned here:

1. a module with such a fixture marks itself
   ``pytestmark = pytest.mark.xdist_group("<its own file stem>")``;
2. every parallel run passes ``--dist=loadgroup``, which sends a marked module
   to one worker and schedules every unmarked test exactly as ``load`` does.
   smart-test adds it in ``run_pytest``; the CI steps spell it out.

Measured on the three pipeline modules plus 18 other core test files, -n 6,
twice each: 99 s and 99 s with ``load``, 65 s and 67 s with ``loadgroup``,
1827 passed both ways.

The ``build_once`` fixture is not a substitute. It also builds once, but a
second worker that needs the result waits on its lock instead of running
other tests, so it saves contention and little wall time.
"""

# covers: scripts/smart-test
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SMART_TEST = REPO_ROOT / "scripts" / "smart-test"
WOODPECKER = REPO_ROOT / ".woodpecker"
CORE_TESTS = REPO_ROOT / "packages" / "hypergumbo-core" / "tests"

#: The modules whose fixture is one or more full pipeline runs and whose other
#: tests are cheap, so putting them all on one worker costs nothing. A module
#: whose OTHER tests are slow too does not belong here: grouping would run
#: them one after another (test_meta_write_discipline has two independent
#: 10-second tree scans beside its shared fixture, and is left ungrouped).
GROUPED = (
    "test_doc_family_id_parity",
    "test_import_dst_ids_are_canonical",
    "test_route_concept_parity",
    "test_stable_id_floor_gate",
)


def _function(name: str) -> str:
    lines = SMART_TEST.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{name}() {{"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


def _dist(*args: str) -> str:
    script = "\n".join([
        "set -euo pipefail",
        _function("xdist_workers_of"),
        _function("dist_args_for_run"),
        'dist_args_for_run "$@"',
    ])
    done = subprocess.run(["bash", "-c", script, "bash", *args],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.mark.parametrize("args", [
    ("-n", "auto"), ("-n", "6"), ("-n6",), ("--numprocesses=4",),
    ("--numprocesses", "2"), ("-n", "1"), ("--tb=short", "-n", "3", "-q"),
])
def test_a_run_with_workers_groups(args: tuple[str, ...]) -> None:
    assert _dist(*args) == "--dist=loadgroup"


@pytest.mark.parametrize("args", [(), ("-q",), ("-n", "0"), ("-n0",)])
def test_a_run_without_workers_is_left_alone(args: tuple[str, ...]) -> None:
    """No -n may mean no xdist installed, where --dist is an unknown option."""
    assert _dist(*args) == ""


def test_the_last_worker_count_wins() -> None:
    assert _dist("-n", "auto", "-n", "0") == ""
    assert _dist("-n", "0", "-n", "4") == "--dist=loadgroup"


def test_run_pytest_puts_it_before_the_callers_arguments() -> None:
    """Before them, so a --dist the caller passes comes later and wins."""
    body = _function("run_pytest")
    assert 'dist_arg=$(dist_args_for_run "${extra_args[@]}")' in body
    assert 'extra_args=("$dist_arg" "${extra_args[@]}")' in body
    runs = [line for line in body.splitlines()
            if " pytest $test_targets" in line and not line.lstrip().startswith("#")]
    assert len(runs) == 2, runs
    assert all('"${extra_args[@]}"' in line for line in runs), runs


def _parallel_ci_pytest_lines() -> list[tuple[str, str]]:
    found = []
    for path in sorted(WOODPECKER.glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0]
            # The command itself, not an echo that mentions it.
            if re.match(r"\s*pytest\s", code) and re.search(r"\s-n\s", code):
                found.append((path.name, line.strip()))
    return found


def test_every_parallel_ci_pytest_run_groups() -> None:
    lines = _parallel_ci_pytest_lines()
    # Reach: the PR step, the full suite and its tests/ step, and nightly.
    assert {name for name, _ in lines} >= {"woodpecker.yml", "full-suite.yml", "nightly.yml"}, lines
    assert len(lines) >= 4, lines
    missing = [entry for entry in lines if "--dist=loadgroup" not in entry[1]]
    assert missing == [], missing


def _group_of(path: Path) -> str | None:
    """The name a module's ``pytestmark = pytest.mark.xdist_group(...)`` gives."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "pytestmark" for t in node.targets)):
            continue
        call = node.value
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "xdist_group" and call.args
                and isinstance(call.args[0], ast.Constant)):
            return str(call.args[0].value)
    return None


@pytest.mark.parametrize("stem", GROUPED)
def test_the_expensive_modules_are_grouped(stem: str) -> None:
    assert _group_of(CORE_TESTS / f"{stem}.py") == stem


def test_a_group_is_named_after_its_own_module() -> None:
    """Two modules sharing a group name would share ONE worker, and their
    expensive fixtures would be built one after the other."""
    grouped = {}
    for path in sorted(REPO_ROOT.glob("packages/*/tests/test_*.py")) + sorted(
            (REPO_ROOT / "tests").glob("test_*.py")):
        group = _group_of(path)
        if group is not None:
            grouped[path.stem] = group
    assert set(GROUPED) <= set(grouped), grouped
    assert {stem: group for stem, group in grouped.items() if group != stem} == {}


_PROBE = '''
import os, pytest
pytestmark = pytest.mark.xdist_group("probe")

@pytest.fixture(scope="module")
def built():
    with open(os.environ["BUILD_LOG"], "a") as log:
        log.write(os.environ.get("PYTEST_XDIST_WORKER", "main") + "\\n")

@pytest.mark.parametrize("i", range(12))
def test_uses_the_fixture(i, built):
    pass
'''

_OTHER = '''
import os, pytest

@pytest.mark.parametrize("i", range(24))
def test_unmarked(i):
    with open(os.environ["RUN_LOG"], "a") as log:
        log.write(os.environ.get("PYTEST_XDIST_WORKER", "main") + "\\n")
'''


def _run_probe(tmp_path: Path, dist: str) -> tuple[list[str], set[str]]:
    (tmp_path / "test_probe.py").write_text(_PROBE)
    (tmp_path / "test_other.py").write_text(_OTHER)
    build_log, run_log = tmp_path / "builds.log", tmp_path / "runs.log"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-o", "addopts=", "-n", "3", f"--dist={dist}", str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path,
        env={**os.environ, "BUILD_LOG": str(build_log),
             "RUN_LOG": str(run_log)},
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return (build_log.read_text().split(),
            set(run_log.read_text().split()))


def test_loadgroup_builds_a_marked_module_once_and_still_spreads_the_rest(
    tmp_path: Path,
) -> None:
    """What the flag is FOR, run against the installed xdist."""
    builds, unmarked_workers = _run_probe(tmp_path, "loadgroup")
    assert len(builds) == 1, builds
    assert len(unmarked_workers) > 1, unmarked_workers


def test_control_load_builds_it_in_more_than_one_worker(tmp_path: Path) -> None:
    """Without the flag the mark does nothing; otherwise the test above
    could pass for a reason that has nothing to do with it."""
    builds, _ = _run_probe(tmp_path, "load")
    assert len(builds) > 1, builds
