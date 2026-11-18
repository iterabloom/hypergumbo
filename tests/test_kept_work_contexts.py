# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work kept by hand is paired with EVERY test that uses it.

A function that keeps what it computed in a dict or a module global runs the
work under the first test that calls it; every later caller is handed the
result and had no row for the code that made it. Such a function marks itself
with ``hypergumbo_core.kept_work``: ``used`` on every call, ``filling`` around
the work. The recorder treats the two exactly as a cached function's call and
body (``tests/test_shared_cache_contexts.py``). pytest runs here on a
throwaway project in a subprocess under the real recorder, with the marks'
module imported after the plugin and before it; the tests read the coverage
data and the record, and fold both into a selection index.
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

from hypergumbo_core import kept_work
from hypergumbo_core.prose_only import shared_work_used, unwrapped_caches
from hypergumbo_core.selection_index import (
    blocks_run_at_import,
    is_shared_context,
    open_index,
    shared_context,
    statement_records,
    update_from_run,
)

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "scripts" / "pytest_plugins"
#: Where THIS process found ``hypergumbo_core``: the toy project imports
#: ``kept_work`` from it. CI's agent-infra step does not install the package
#: (the parent imports it off a path entry the child would not inherit).
CORE_SRC = Path(kept_work.__file__).resolve().parents[1]

_WORK = '''\
import pathlib
import sys

from hypergumbo_core import kept_work

_TABLES = {}
_WARNED = set()


def beneath():
    return 5


def table(name):
    kept_work.used(table, name)
    if name not in _TABLES:
        with kept_work.filling(table, name):
            _TABLES[name] = {"n": beneath(), "name": name}
    return _TABLES[name]


def size_of(name):
    """Opens a source file of the project as data, once per name."""
    kept_work.used(size_of, name)
    if name not in _TABLES:
        with kept_work.filling(size_of, name):
            _TABLES[name] = len(pathlib.Path(__file__).with_name(name + ".py").read_text())
    return _TABLES[name]


def warn_once(what):
    kept_work.used(warn_once)
    if what not in _WARNED:
        with kept_work.filling(warn_once):
            _WARNED.add(what)
            print("warning:", what, file=sys.stderr)


def refuses():
    kept_work.used(refuses)
    with kept_work.filling(refuses):
        raise ValueError("refused")


def at_collection():
    kept_work.used(at_collection)
    if "collected" not in _TABLES:
        with kept_work.filling(at_collection):
            _TABLES["collected"] = 11
    return _TABLES["collected"]


def own():
    return 1
'''

_PROJECT = {
    "work.py": _WORK,
    "data_a.py": "A = 1\n",
    "data_b.py": "B = 22\n",
    "test_a.py": (
        "import pytest\nimport work\n\nAT_IMPORT = work.at_collection()\n\n\n"
        "def test_fills():\n    assert work.table('a')['n'] == 5\n\n\n"
        "def test_hits():\n    assert work.table('a')['name'] == 'a'\n\n\n"
        "def test_uses_nothing_kept():\n    assert work.own() == 1\n\n\n"
        "def test_kept_then_own():\n    work.table('c')\n    assert work.own() == 1\n\n\n"
        "def test_warns():\n    work.warn_once('x')\n\n\n"
        "def test_is_not_warned_again():\n    work.warn_once('x')\n\n\n"
        "def test_an_exception_in_a_fill_passes_through():\n"
        "    with pytest.raises(ValueError, match='refused'):\n        work.refuses()\n\n\n"
        "def test_reads_what_collection_filled():\n    assert work.at_collection() == 11\n"),
    "test_events.py": (
        "import work\n\n\n"
        "def test_fills_a():\n    assert work.size_of('data_a') == 6\n\n\n"
        "def test_is_handed_a():\n    assert work.size_of('data_a') == 6\n\n\n"
        "def test_fills_b():\n    assert work.size_of('data_b') == 7\n"),
    # A module-scoped fixture hands out a closure that keeps work: the
    # first test to ask runs it, the next is handed it (INV-kazud).
    "test_fixture.py": (
        "import pytest\nimport work\nfrom hypergumbo_core import kept_work\n\n\n"
        "@pytest.fixture(scope='module')\ndef survey():\n    cache = {}\n\n"
        "    def run(name):\n        kept_work.used(run, name)\n"
        "        if name not in cache:\n            with kept_work.filling(run, name):\n"
        "                cache[name] = work.beneath() + len(name)\n"
        "        return cache[name]\n    return run\n\n\n"
        "def test_surveys_first(survey):\n    assert survey('x') == 6\n\n\n"
        "def test_is_handed_the_survey(survey):\n    assert survey('x') == 6\n"),
}

TABLE = shared_context("cache", "work.py::table")
WARN = shared_context("cache", "work.py::warn_once")


@dataclass
class Run:
    project: Path
    coverage_db: Path
    reads: Path
    #: coverage context -> names of the functions of work.py a line of which ran under it
    ran: dict[str, set[str]]
    tests: dict[str, Any]


def _functions_by_line(source: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef):
            for inner in node.body:
                for line in range(inner.lineno, (inner.end_lineno or inner.lineno) + 1):
                    out[line] = node.name
    return out


def _numbits(numbits: bytes) -> list[int]:
    return [index * 8 + bit for index, byte in enumerate(numbits) for bit in range(8)
            if byte & (1 << bit)]


def _run(tmp_path: Path, workers: str, *, marks_first: bool = False,
         record: bool = True) -> Run:
    project = tmp_path / "project"
    project.mkdir()
    for name, text in _PROJECT.items():
        (project / name).write_text(text)
    # ``-p early`` loads before the recorder plugin: the marks' module is
    # then imported first, and the plugin has to hand itself over.
    (project / "early.py").write_text(
        "import hypergumbo_core.kept_work\n" if marks_first else "")
    reads = tmp_path / "reads"
    db = tmp_path / "coverage.sqlite"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTEST_", "COV_CORE_", "COVERAGE_", "GIT_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(PLUGIN_DIR), str(project), str(CORE_SRC)])
    env["COVERAGE_FILE"] = str(db)
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "early", "-p", "pytest_source_reads",
         "-o", "addopts=", "-p", "no:cacheprovider", "-q", "--rootdir", str(project),
         *([f"--source-reads-dir={reads}"] if record else []),
         "--cov=work", "--cov-report=", "--cov-context=test",
         "-n", workers, str(project)],
        capture_output=True, text=True, cwd=project, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    by_line = _functions_by_line(_WORK)
    ran: dict[str, set[str]] = {}
    conn = sqlite3.connect(db)
    for context, numbits in conn.execute(
            "SELECT c.context, lb.numbits FROM line_bits lb "
            "JOIN context c ON c.id = lb.context_id JOIN file f ON f.id = lb.file_id "
            "WHERE f.path LIKE '%work.py'"):
        names = {by_line[n] for n in _numbits(numbits) if n in by_line}
        if names:
            ran.setdefault(context, set()).update(names)
    conn.close()
    tests: dict[str, Any] = {}
    for part in sorted(reads.glob("*.json")):
        tests.update(json.loads(part.read_text())["tests"])
    return Run(project, db, reads, ran, tests)


@pytest.fixture(scope="module", params=[("0", True), ("2", False)],
                ids=["marks-imported-first", "xdist-plugin-first"])
def run(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Run:
    workers, marks_first = request.param
    return _run(tmp_path_factory.mktemp("kept"), workers, marks_first=marks_first)


def _under_a_test(run: Run, function: str) -> set[str]:
    return {context for context, names in run.ran.items()
            if function in names and context and not is_shared_context(context)}


def _caches(run: Run, test: str) -> set[str]:
    return set(run.tests[test]["caches"])


def test_the_fill_and_what_it_calls_run_under_the_functions_own_context(run: Run) -> None:
    """The whole function is ``table``'s lines; the fill and ``beneath``
    run under the work's context, the hit test's own row has the lookup."""
    assert run.ran[TABLE] == {"table", "beneath"}
    assert _under_a_test(run, "beneath") == set()
    assert "table" in run.ran["test_a.py::test_hits|run"]


def test_after_a_fill_the_tests_own_context_is_back(run: Run) -> None:
    assert run.ran["test_a.py::test_kept_then_own|run"] >= {"own"}


def test_a_closure_a_module_fixture_hands_out_is_work_kept_too(run: Run) -> None:
    assert run.ran[shared_context("cache", "test_fixture.py::survey.<locals>.run")] == {"beneath"}
    for test in ("test_surveys_first", "test_is_handed_the_survey"):
        assert _caches(run, f"test_fixture.py::{test}") == {
            "test_fixture.py::survey.<locals>.run"}, test


def test_a_flag_set_once_is_work_kept_too(run: Run) -> None:
    assert run.ran[WARN] == {"warn_once"}
    for test in ("test_warns", "test_is_not_warned_again"):
        assert _caches(run, f"test_a.py::{test}") == {"work.py::warn_once"}, test


def test_a_fill_outside_every_test_is_not_labelled(run: Run) -> None:
    assert "at_collection" in run.ran[""]
    assert not [c for c in run.ran if is_shared_context(c) and "at_collection" in c]
    assert _caches(run, "test_a.py::test_reads_what_collection_filled") == {
        "work.py::at_collection"}


def test_every_call_is_a_use_hit_or_miss(run: Run) -> None:
    for test in ("test_fills", "test_hits"):
        assert _caches(run, f"test_a.py::{test}") == {"work.py::table"}, test
    assert _caches(run, "test_a.py::test_uses_nothing_kept") == set()
    assert _caches(run, "test_a.py::test_an_exception_in_a_fill_passes_through") == {
        "work.py::refuses"}


def test_what_a_fill_read_goes_to_the_callers_of_that_entry(run: Run) -> None:
    reads = {name: run.tests[f"test_events.py::{name}"]["reads"]
             for name in ("test_fills_a", "test_is_handed_a", "test_fills_b")}
    assert reads == {"test_fills_a": ["data_a.py"], "test_is_handed_a": ["data_a.py"],
                     "test_fills_b": ["data_b.py"]}


def test_folded_every_user_is_paired_with_the_work(run: Run, tmp_path: Path) -> None:
    """The close condition: one test fills, another is handed the result,
    and BOTH are paired with the fill and with the function only it calls."""
    conn = open_index(tmp_path / "index.sqlite")
    done = update_from_run(conn, run.coverage_db, repo_root=run.project,
                           shared_users=shared_work_used(run.reads),
                           unlabelled_caches=unwrapped_caches(run.reads))
    work = str(run.project / "work.py")
    for function in ("table", "beneath"):
        records = statement_records(conn, work, function)
        assert {"test_a.py::test_fills", "test_a.py::test_hits"} <= set(records), function
        assert "test_a.py::test_uses_nothing_kept" not in records
    assert {"test_fixture.py::test_surveys_first",
            "test_fixture.py::test_is_handed_the_survey"} <= set(
                statement_records(conn, work, "beneath"))
    warned = statement_records(conn, work, "warn_once")
    assert warned["test_a.py::test_is_not_warned_again"] == warned["test_a.py::test_warns"]
    assert done.unused == ()
    assert (work, "at_collection") in blocks_run_at_import(conn, [work])


def test_without_the_recorder_the_marks_do_nothing(
        tmp_path_factory: pytest.TempPathFactory) -> None:
    """The plugin is loaded and its option is not given: the project's own
    tests pass and no line is under a context of the work's own."""
    off = _run(tmp_path_factory.mktemp("off"), "0", marks_first=True, record=False)
    assert not [c for c in off.ran if is_shared_context(c)]
    assert {"table", "beneath"} <= off.ran["test_a.py::test_fills|run"]
