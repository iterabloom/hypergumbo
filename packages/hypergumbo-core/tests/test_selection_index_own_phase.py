# SPDX-License-Identifier: AGPL-3.0-or-later
"""A test is measured by what ITS OWN CALL ran, not by what a fixture ran.

The index holds a test's file back from narrowing when coverage cannot speak
for the test: it ran and left no row, because its work is in a subprocess or
in code outside the measured packages. Two guards rest on "left no row": the
``unmeasured_test`` list a full run writes, and ``rows = 0`` in a test's run
record.

A fixture that runs for every test defeats both. The root ``conftest.py`` got
one (an autouse teardown that calls
``hypergumbo_core.declared_test_inputs.install_declaration``), every test
then left a row, and the unmeasured list of this repository fell from 6,916
tests to 227 at the next full run. Narrowing, asked about a one-statement
change to the function ``coverage-select graph-walk`` runs, dropped seven of
the ten test files that drive ``coverage-select`` and ``smart-test`` through
a subprocess.

pytest-cov records a test's setup, call and teardown as separate contexts
(``<id>|setup``, ``<id>|run``, ``<id>|teardown``). The rule these tests pin:
a test counts as having produced rows only if a row is in its call phase (or
in a context with no phase at all, which is not pytest-cov's). Rows from the
other two phases are still stored as edges: the code did run during the test,
and a change to it selects the test.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hypergumbo_core.selection_index import (
    droppable_test_files,
    informed_test_files,
    open_index,
    touching_tests,
    update_from_run,
)

COVERAGE_SELECT = Path(__file__).resolve().parents[3] / "scripts" / "coverage-select"

_GENERATE = '''\
import importlib
import json
import sys
import coverage

db, spec = sys.argv[1], json.loads(sys.argv[2])
cov = coverage.Coverage(data_file=db, source=spec["measure"])
cov.start()
sys.path[:0] = spec["measure"]
for context, calls in spec["calls"]:
    cov.switch_context(context)
    for call in calls:
        module, function = call.split(".")
        getattr(importlib.import_module(module), function)()
cov.stop()
cov.save()
'''

REAL = "tests/test_real.py::test_in_process"
SUB = "tests/test_sub.py::test_through_a_subprocess"
SETUP = "tests/test_setup.py::test_asserts_on_what_a_fixture_built"
PLAIN = "tests/test_plain.py::test_from_another_runner"

#: What a suite with an autouse teardown fixture leaves: ``tidy`` after every
#: test; ``work`` in the call of one test and in the setup of another.
SUITE = [
    (f"{REAL}|run", ["mod.work"]), (f"{REAL}|teardown", ["mod.tidy"]),
    (f"{SUB}|teardown", ["mod.tidy"]),
    (f"{SETUP}|setup", ["mod.work"]), (f"{SETUP}|teardown", ["mod.tidy"]),
]
RAN = [REAL, SUB, SETUP]


class Repo:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "repo"
        self.source = self.root / "packages" / "p" / "src" / "p" / "mod.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text(
            "def work():\n    return 1\n\n\ndef tidy():\n    return None\n")
        self.conn = open_index(tmp_path / "index.sqlite")
        self._runs = 0

    def fold(self, calls: list[tuple[str, list[str]]], ran: list[str] | None, *,
             full: bool = False) -> None:
        self._runs += 1
        db = self.root / f"run-{self._runs}.coverage"
        spec = {"measure": [str(self.source.parent)], "calls": calls}
        subprocess.run([sys.executable, "-c", textwrap.dedent(_GENERATE), str(db), json.dumps(spec)],
                       check=True, capture_output=True, text=True)
        update_from_run(self.conn, db, ran_tests=ran, repo_root=self.root,
                        scope_complete=True, full_run=full)

    def unmeasured(self) -> set[str]:
        return {row[0] for row in self.conn.execute("SELECT test_id FROM unmeasured_test")}


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def test_a_full_run_calls_a_test_with_rows_from_fixtures_only_unmeasured(repo: Repo) -> None:
    repo.fold(SUITE, RAN, full=True)
    assert repo.unmeasured() == {SUB, SETUP}


def test_the_rows_a_fixture_left_are_still_edges(repo: Repo) -> None:
    """``tidy`` did run during every test: a change to it reaches them all."""
    repo.fold(SUITE, RAN, full=True)
    assert touching_tests(repo.conn, [str(repo.source)]) == {REAL, SUB, SETUP}


def test_narrowing_may_drop_only_the_file_whose_test_ran_code_itself(repo: Repo) -> None:
    """The guard that does not wait for a full run: the run record. A run
    says nothing about a test whose own call left no row, and its file is
    held back."""
    repo.fold(SUITE, RAN)
    assert repo.unmeasured() == set(), "only a full run writes the list"
    assert informed_test_files(repo.conn, repo.root, [str(repo.source)]) == {"tests/test_real.py"}
    assert droppable_test_files(repo.conn, repo.root, [str(repo.source)]) == {"tests/test_real.py"}


def test_only_a_row_of_its_own_call_takes_a_test_off_the_list(repo: Repo) -> None:
    repo.fold(SUITE, RAN, full=True)
    # Run again, alone: the fixture leaves its row and the test stays listed.
    repo.fold([(f"{SUB}|teardown", ["mod.tidy"])], [SUB])
    assert SUB in repo.unmeasured()
    # Its call runs measured code: it has spoken.
    repo.fold([(f"{SUB}|run", ["mod.work"]), (f"{SUB}|teardown", ["mod.tidy"])], [SUB])
    assert repo.unmeasured() == {SETUP}


def test_a_later_run_with_fixture_rows_alone_replaces_the_tests_record(repo: Repo) -> None:
    """The latest run decides, also when it has no list of the tests that
    ran: a test known to the run only through a fixture's row is still IN the
    run, and its earlier record must not go on vouching for its file."""
    repo.fold([(f"{SUB}|run", ["mod.work"])], [SUB])
    assert informed_test_files(repo.conn, repo.root, [str(repo.source)]) == {"tests/test_sub.py"}
    repo.fold([(f"{SUB}|teardown", ["mod.tidy"])], None)
    assert informed_test_files(repo.conn, repo.root, [str(repo.source)]) == frozenset()


def test_a_context_with_no_phase_is_the_tests_own(repo: Repo) -> None:
    """Coverage contexts that are not pytest-cov's carry no phase; such a row
    is the test's own, as it always was."""
    repo.fold([(PLAIN, ["mod.work"]), (f"{SUB}|teardown", ["mod.tidy"])], [PLAIN, SUB], full=True)
    assert repo.unmeasured() == {SUB}
    assert droppable_test_files(repo.conn, repo.root, [str(repo.source)]) == {"tests/test_plain.py"}


def test_a_full_fold_says_how_many_tests_coverage_cannot_speak_for(
        repo: Repo, tmp_path: Path) -> None:
    """The command smart-test folds with. The list fell from 6,916 tests to
    227 in one run and nothing was printed: a full fold now states the count
    and what it was."""
    (repo.root / "tests").mkdir()
    cases = []
    for test in RAN:
        file, name = test.split("::")
        (repo.root / file).write_text(f"def {name}():\n    pass\n")
        cases.append(f'<testcase classname="{file[:-3].replace("/", ".")}" name="{name}"/>')
    junit = tmp_path / "junit.xml"
    junit.write_text(f"<testsuites><testsuite>{''.join(cases)}</testsuite></testsuites>")
    db = repo.root / "run.coverage"
    spec = {"measure": [str(repo.source.parent)], "calls": SUITE}
    subprocess.run([sys.executable, "-c", textwrap.dedent(_GENERATE), str(db), json.dumps(spec)],
                   check=True, capture_output=True, text=True)
    repo.conn.execute("INSERT INTO unmeasured_test (test_id) VALUES ('tests/test_gone.py::test_x')")
    repo.conn.commit()
    repo.conn.close()

    def fold(*flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(COVERAGE_SELECT), "--repo-root", str(repo.root),
             "--index", str(tmp_path / "index.sqlite"), "--junit", str(junit),
             "update", "--coverage-db", str(db), "--sha", "0" * 40, *flags],
            capture_output=True, text=True, timeout=120)

    partial = fold()
    assert partial.returncode == 0, partial.stderr
    assert "cannot speak for" not in partial.stderr, "only a full run rewrites the list"
    full = fold("--full")
    assert full.returncode == 0, full.stderr
    assert ("full run: coverage cannot speak for 3 test(s) (their own call ran no measured "
            "code); 1 before this run") in full.stderr
