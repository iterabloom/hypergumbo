# SPDX-License-Identifier: AGPL-3.0-or-later
"""A shared fixture's code is paired with EVERY test that uses the fixture.

A module, class or session fixture runs once per worker, inside the setup of
whichever test needs it first, and its post-yield half inside the teardown of
whichever test is last. pytest-cov labels a line with the test that was
running, so the code a shared fixture ran belonged to one test of the many
that depend on it, and a change to that code selected that one test.

The recorder plugin runs each such fixture, both halves, under a coverage
context of the fixture's own, and records which tests used it and which shared
fixtures were built inside it. pytest runs here on a throwaway project in a
subprocess, without pytest-xdist and with it; the tests read the coverage
data and the record that run left, and then fold both into a selection index
the way ``coverage-select update`` does.
"""

# covers: scripts/pytest_plugins/pytest_source_reads.py
from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.prose_only import shared_work_used
from hypergumbo_core.selection_index import (
    blocks_run_at_import,
    is_shared_context,
    open_index,
    shared_context,
    statement_records,
    update_from_run,
)

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "scripts" / "pytest_plugins"

_PROJECT = {
    "work.py": (
        "def built():\n    value = [1]\n    return value\n\n\n"
        "def torn_down():\n    return None\n\n\n"
        "def deep_built():\n    return 7\n\n\n"
        "def late_built():\n    return 3\n\n\n"
        "def after_late():\n    return 1\n\n\n"
        "def per_test():\n    return 0\n\n\n"
        "def own():\n    return 1\n"),
    "conftest.py": (
        "import pytest\nimport work\n\n\n"
        "@pytest.fixture(scope='session')\ndef deep():\n    return work.deep_built()\n\n\n"
        "@pytest.fixture(scope='session')\ndef late():\n    return work.late_built()\n\n\n"
        "@pytest.fixture\ndef each():\n    return work.per_test()\n"),
    "test_a.py": (
        "import pytest\nimport work\n\n\n"
        "@pytest.fixture(scope='module')\ndef shared(deep):\n"
        "    value = work.built()\n    yield value\n    work.torn_down()\n\n\n"
        "@pytest.fixture(scope='module')\ndef lazy(request):\n"
        "    return request.getfixturevalue('late') + work.after_late()\n\n\n"
        "def test_first(shared, each):\n    assert work.own() == 1\n\n\n"
        "def test_second(shared):\n    assert shared == [1]\n\n\n"
        "def test_third(lazy):\n    assert lazy == 4\n\n\n"
        "def test_fourth(lazy):\n    assert lazy == 4\n"),
    "test_b.py": (
        "import work\n\n\n"
        "def test_other_module(deep):\n    assert deep == 7\n\n\n"
        "def test_uses_nothing_shared():\n    assert work.own() == 1\n"),
}

SHARED = shared_context("fixture", "test_a.py::shared")
LAZY = shared_context("fixture", "test_a.py::lazy")
DEEP = shared_context("fixture", "::deep")
LATE = shared_context("fixture", "::late")


@dataclass
class Run:
    project: Path
    coverage_db: Path
    reads: Path
    #: coverage context -> names of the functions of work.py a line of which ran under it
    ran: dict[str, set[str]]
    record: dict[str, Any]


def _functions_by_line(source: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef):
            for inner in node.body:
                for line in range(inner.lineno, (inner.end_lineno or inner.lineno) + 1):
                    out[line] = node.name
    return out


def _numbits(numbits: bytes) -> list[int]:
    return [index * 8 + bit for index, byte in enumerate(numbits) for bit in range(8)
            if byte & (1 << bit)]


def _run(tmp_path: Path, workers: str, *, test_contexts: bool = True,
         extra: tuple[str, ...] = ()) -> Run:
    project = tmp_path / "project"
    project.mkdir()
    for name, text in _PROJECT.items():
        (project / name).write_text(text)
    reads = tmp_path / "reads"
    db = tmp_path / "coverage.sqlite"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTEST_", "COV_CORE_", "COVERAGE_", "GIT_"))}
    env["PYTHONPATH"] = str(PLUGIN_DIR)
    env["COVERAGE_FILE"] = str(db)
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "pytest_source_reads",
         "-o", "addopts=", "-p", "no:cacheprovider", "-q", "--rootdir", str(project),
         f"--source-reads-dir={reads}", "--cov=work", "--cov-report=",
         *(["--cov-context=test"] if test_contexts else []),
         *extra, "-n", workers, str(project)],
        capture_output=True, text=True, cwd=project, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    by_line = _functions_by_line(_PROJECT["work.py"])
    ran: dict[str, set[str]] = {}
    if not db.exists():
        db.touch()  # coverage was off: an empty file reads as "no rows"
    conn = sqlite3.connect(db)
    has_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'line_bits'").fetchone() is not None
    for context, numbits in conn.execute(
            "SELECT c.context, lb.numbits FROM line_bits lb "
            "JOIN context c ON c.id = lb.context_id JOIN file f ON f.id = lb.file_id "
            "WHERE f.path LIKE '%work.py'") if has_rows else ():
        names = {by_line[n] for n in _numbits(numbits) if n in by_line}
        if names:
            ran.setdefault(context, set()).update(names)
    conn.close()
    record: dict[str, Any] = {"tests": {}, "nested": {}}
    for part in sorted(reads.glob("*.json")):
        data = json.loads(part.read_text())
        record["tests"].update(data["tests"])
        for key, inner in data["nested"].items():
            record["nested"].setdefault(key, set()).update(inner)
    return Run(project, db, reads, ran, record)


@pytest.fixture(scope="module", params=["0", "2"])
def run(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Run:
    """One run of the project, without xdist and with it."""
    return _run(tmp_path_factory.mktemp("shared"), request.param)


def _under_a_test(run: Run, function: str) -> set[str]:
    return {context for context, names in run.ran.items()
            if function in names and not is_shared_context(context)}


def test_the_setup_half_runs_under_the_fixtures_own_context(run: Run) -> None:
    assert "built" in run.ran[SHARED]
    assert _under_a_test(run, "built") == set()


def test_the_post_yield_half_does_too(run: Run) -> None:
    """It runs in the teardown of whichever test is last on the worker."""
    assert "torn_down" in run.ran[SHARED]
    assert _under_a_test(run, "torn_down") == set()


def test_a_session_fixture_in_a_conftest_has_its_own_context(run: Run) -> None:
    assert run.ran[DEEP] == {"deep_built"}
    assert _under_a_test(run, "deep_built") == set()


def test_a_fixture_built_inside_another_runs_under_its_own_not_the_outer_ones(run: Run) -> None:
    assert run.ran[LATE] == {"late_built"}


def test_after_the_inner_one_the_outer_fixtures_context_is_back(run: Run) -> None:
    """``lazy`` fetches ``late`` by name and THEN calls ``after_late``."""
    assert run.ran[LAZY] == {"after_late"}
    assert _under_a_test(run, "after_late") == set()


def test_after_a_shared_fixture_the_tests_own_context_is_back(run: Run) -> None:
    """``each`` is function scope and is set up AFTER ``shared`` in the same
    test's setup; ``own`` runs in the test's call."""
    assert run.ran["test_a.py::test_first|setup"] == {"per_test"}
    assert run.ran["test_a.py::test_first|run"] == {"own"}


def test_a_function_scope_fixture_gets_no_context_of_its_own(run: Run) -> None:
    assert [c for c in run.ran if is_shared_context(c)].count(
        shared_context("fixture", "::each")) == 0
    assert sorted(c for c in run.ran if is_shared_context(c)) == sorted(
        {SHARED, LAZY, DEEP, LATE})


#: The project's own shared fixtures. Installed pytest plugins bring session
#: fixtures of theirs, and every test is recorded using those too.
OURS = {"test_a.py::shared", "test_a.py::lazy", "::deep", "::late"}


def test_without_pytest_covs_test_contexts_nothing_is_labelled(
        tmp_path_factory: pytest.TempPathFactory) -> None:
    """Switching back "to the test's own context" would CREATE test contexts
    in a run that asked for none. The record of who used what is still kept."""
    plain = _run(tmp_path_factory.mktemp("plain"), "0", test_contexts=False)
    assert set(plain.ran) == {""}
    assert set(plain.record["tests"]["test_a.py::test_second"]["fixtures"]) & OURS == {
        "test_a.py::shared", "::deep"}


def test_with_coverage_switched_off_the_run_passes_and_the_record_is_kept(
        tmp_path_factory: pytest.TempPathFactory) -> None:
    """``smart-test --no-cov`` still passes ``--cov-context=test``. pytest-cov
    then has no coverage object to label anything with."""
    off = _run(tmp_path_factory.mktemp("off"), "0", extra=("--no-cov",))
    assert off.ran == {}
    assert off.record["nested"] == {"fixture|test_a.py::lazy": {"fixture|::late"}}


def test_the_record_names_the_shared_fixtures_each_test_used(run: Run) -> None:
    tests = run.record["tests"]
    assert set(tests["test_a.py::test_second"]["fixtures"]) & OURS == {
        "test_a.py::shared", "::deep"}
    assert set(tests["test_b.py::test_uses_nothing_shared"]["fixtures"]) & OURS == set()


def test_the_record_names_what_was_built_inside_a_shared_fixture(run: Run) -> None:
    assert run.record["nested"] == {"fixture|test_a.py::lazy": {"fixture|::late"}}


def test_folded_every_user_is_paired_with_the_code_the_fixture_ran(
        run: Run, tmp_path: Path) -> None:
    """The close condition of the item: two tests share a module fixture and
    BOTH are paired with the function only the fixture calls, statement by
    statement; and a test that reaches a session fixture only through a
    fixture that was already built is paired with its code too."""
    conn = open_index(tmp_path / "index.sqlite")
    done = update_from_run(conn, run.coverage_db, repo_root=run.project,
                           shared_users=shared_work_used(run.reads))
    work = str(run.project / "work.py")
    both = {"test_a.py::test_first": frozenset({0, 1}), "test_a.py::test_second": frozenset({0, 1})}
    assert statement_records(conn, work, "built") == both
    assert set(statement_records(conn, work, "torn_down")) == set(both)
    assert set(statement_records(conn, work, "deep_built")) == {*both, "test_b.py::test_other_module"}
    assert set(statement_records(conn, work, "late_built")) == {
        "test_a.py::test_third", "test_a.py::test_fourth"}
    assert done.unused == ()
    assert blocks_run_at_import(conn, [work]) == frozenset()
