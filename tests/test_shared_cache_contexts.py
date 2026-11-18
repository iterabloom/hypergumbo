# SPDX-License-Identifier: AGPL-3.0-or-later
"""A cached function's code is paired with EVERY test that calls the function.

A function under ``functools.lru_cache`` or ``functools.cache`` runs its body
once per argument per process. pytest-cov labels a line with the test that
was running, so the body, and everything it calls, belonged to the test that
happened to call first. Every later caller was handed the stored value and
had no row for that code: a change to it selected one dependent of many.

The recorder plugin replaces ``functools.lru_cache`` before any module of the
repository is imported. The body then runs under a coverage context of the
function's own and every call, hit or miss, is recorded as a use by the
running test. pytest runs here on a throwaway project in a subprocess,
without pytest-xdist and with it; the tests read the coverage data and the
record that run left, and fold both into a selection index the way
``coverage-select update`` does. The project's own tests assert that a cached
function still behaves as functools made it.
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

_WORK = '''\
import functools
import threading


def beneath():
    return 5


@functools.lru_cache(maxsize=None)
def table(name):
    """Doc of table."""
    return {"n": beneath(), "name": name}


@functools.lru_cache
def doubled(x):
    return x * 2


@functools.cache
def plain():
    return doubled(1) + 1


@functools.lru_cache(maxsize=4, typed=True)
def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)


@functools.lru_cache(maxsize=None)
def at_collection():
    return 11


@functools.lru_cache(maxsize=None)
def threaded():
    return threading.get_ident()


@functools.lru_cache(maxsize=None)
def refuses(x):
    raise ValueError(x)


class Holder:
    @functools.lru_cache(maxsize=None)
    def twice(self, x):
        return x * 2


def own():
    return 1
'''

_PROJECT = {
    "work.py": _WORK,
    # Imported by ``-p early`` BEFORE the recorder plugin: its caches are
    # functools' own, and the recorder can only say that they are there.
    "early.py": (
        "import functools\n\n\n"
        "@functools.lru_cache(maxsize=None)\ndef before():\n    return 1\n\n\n"
        "class K:\n    @functools.lru_cache(maxsize=None)\n"
        "    def m(self):\n        return 2\n\n"
        "    @staticmethod\n    @functools.lru_cache(maxsize=None)\n"
        "    def s():\n        return 3\n"),
    "conftest.py": (
        "import pytest\nimport work\n\n\n"
        "@pytest.fixture(scope='module')\ndef built():\n    return work.plain()\n"),
    "test_a.py": (
        "import work\n\nAT_IMPORT = work.at_collection()\n\n\n"
        "def test_fills():\n    assert work.table('a')['n'] == 5\n\n\n"
        "def test_hits():\n    assert work.table('a')['name'] == 'a'\n\n\n"
        "def test_other_entry():\n    assert work.table('b')['n'] == 5\n\n\n"
        "def test_uses_nothing_cached():\n    assert work.own() == 1\n\n\n"
        "def test_cached_then_own():\n    work.table('c')\n    assert work.own() == 1\n\n\n"
        "def test_through_a_fixture(built):\n    assert built == 3\n\n\n"
        "def test_through_the_same_fixture(built):\n    assert built == 3\n\n\n"
        "def test_reads_what_collection_filled():\n    assert work.at_collection() == 11\n"),
    # What functools.lru_cache promises, asserted INSIDE the run: the
    # subprocess passes only if a wrapped function still keeps it.
    "test_api.py": (
        "import functools\nimport threading\n\nimport early\nimport pytest\nimport work\n\n\n"
        "def test_cache_info_and_cache_clear():\n"
        "    work.table.cache_clear()\n"
        "    assert work.table.cache_info().currsize == 0\n"
        "    work.table('z'); work.table('z')\n"
        "    info = work.table.cache_info()\n"
        "    assert (info.hits, info.misses, info.currsize, info.maxsize) == (1, 1, 1, None)\n"
        "    work.table.cache_clear()\n"
        "    assert work.table.cache_info().currsize == 0\n\n\n"
        "def test_wrapped_is_the_function_itself():\n"
        "    before = work.table.cache_info()\n"
        "    assert work.table.__wrapped__('q') == {'n': 5, 'name': 'q'}\n"
        "    assert work.table.cache_info() == before\n"
        "    assert (work.table.__name__, work.table.__doc__) == ('table', 'Doc of table.')\n"
        "    assert work.table.__qualname__ == 'table'\n\n\n"
        "def test_cache_parameters():\n"
        "    assert work.table.cache_parameters() == {'maxsize': None, 'typed': False}\n"
        "    assert work.doubled.cache_parameters() == {'maxsize': 128, 'typed': False}\n"
        "    assert work.fib.cache_parameters() == {'maxsize': 4, 'typed': True}\n"
        "    assert work.plain.cache_parameters() == {'maxsize': None, 'typed': False}\n\n\n"
        "def test_a_cached_function_that_calls_itself():\n"
        "    assert work.fib(12) == 144\n\n\n"
        "def test_a_method_binds_its_instance():\n"
        "    assert work.Holder().twice(4) == 8\n\n\n"
        "def test_an_unhashable_argument_is_still_a_type_error():\n"
        "    with pytest.raises(TypeError):\n        work.table([])\n\n\n"
        "def test_an_exception_passes_through_and_nothing_is_stored():\n"
        "    for _ in range(2):\n"
        "        with pytest.raises(ValueError):\n            work.refuses(1)\n"
        "    assert work.refuses.cache_info().currsize == 0\n\n\n"
        "def test_a_builtin_is_cached_by_functools_itself():\n"
        "    cached = functools.lru_cache(maxsize=None)(len)\n"
        "    assert cached('ab') == 2 and type(cached).__name__ == '_lru_cache_wrapper'\n\n\n"
        "def test_a_call_on_another_thread():\n"
        "    seen = []\n"
        "    thread = threading.Thread(target=lambda: seen.append(work.threaded()))\n"
        "    thread.start(); thread.join()\n"
        "    assert seen == [thread.ident] and work.threaded() == thread.ident\n\n\n"
        "def test_a_cache_made_before_the_recorder_still_works():\n"
        "    assert (early.before(), early.K().m(), early.K.s()) == (1, 2, 3)\n"),
}

TABLE = shared_context("cache", "work.py::table")
PLAIN = shared_context("cache", "work.py::plain")
DOUBLED = shared_context("cache", "work.py::doubled")
BUILT = "fixture|::built"


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
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef):
            for inner in node.body:
                for line in range(inner.lineno, (inner.end_lineno or inner.lineno) + 1):
                    out[line] = node.name
    return out


def _numbits(numbits: bytes) -> list[int]:
    return [index * 8 + bit for index, byte in enumerate(numbits) for bit in range(8)
            if byte & (1 << bit)]


def _run(tmp_path: Path, workers: str, *, test_contexts: bool = True,
         record: bool = True) -> Run:
    project = tmp_path / "project"
    project.mkdir()
    for name, text in _PROJECT.items():
        (project / name).write_text(text)
    reads = tmp_path / "reads"
    db = tmp_path / "coverage.sqlite"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTEST_", "COV_CORE_", "COVERAGE_", "GIT_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(PLUGIN_DIR), str(project)])
    env["COVERAGE_FILE"] = str(db)
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "early", "-p", "pytest_source_reads",
         "-o", "addopts=", "-p", "no:cacheprovider", "-q", "--rootdir", str(project),
         *([f"--source-reads-dir={reads}"] if record else []),
         "--cov=work", "--cov=early", "--cov-report=",
         *(["--cov-context=test"] if test_contexts else []),
         "-n", workers, str(project)],
        capture_output=True, text=True, cwd=project, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    by_line = _functions_by_line(_PROJECT["work.py"])
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
    found: dict[str, Any] = {"tests": {}, "nested": {}, "unwrapped": set()}
    for part in sorted(reads.glob("*.json")):
        data = json.loads(part.read_text())
        found["tests"].update(data["tests"])
        found["unwrapped"].update(data["unwrapped_caches"])
        for key, inner in data["nested"].items():
            found["nested"].setdefault(key, set()).update(inner)
    return Run(project, db, reads, ran, found)


@pytest.fixture(scope="module", params=["0", "2"])
def run(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Run:
    """One run of the project, without xdist and with it. That it passed is
    itself the claim of ``test_api.py``: a wrapped cache is still a cache."""
    return _run(tmp_path_factory.mktemp("cached"), request.param)


def _under_a_test(run: Run, function: str) -> set[str]:
    return {context for context, names in run.ran.items()
            if function in names and context and not is_shared_context(context)}


# ── what runs under which context ───────────────────────────────────────────


def test_the_body_and_what_it_calls_run_under_the_functions_own_context(run: Run) -> None:
    assert run.ran[TABLE] == {"table", "beneath"}
    # One test calls the undecorated function (``__wrapped__``): see below.
    direct = {"test_api.py::test_wrapped_is_the_function_itself|run"}
    assert _under_a_test(run, "table") == direct == _under_a_test(run, "beneath")


def test_a_cached_function_called_by_another_runs_under_its_own(run: Run) -> None:
    """``plain`` calls ``doubled``; both are cached."""
    assert run.ran[DOUBLED] == {"doubled"}
    assert run.ran[PLAIN] == {"plain"}, "the outer context is back after the inner body"


def test_after_a_cached_body_the_tests_own_context_is_back(run: Run) -> None:
    """The test fills an entry (a miss) and THEN calls ``own``."""
    assert run.ran["test_a.py::test_cached_then_own|run"] == {"own"}


def test_called_through_wrapped_the_body_is_the_callers(run: Run) -> None:
    """``f.__wrapped__(...)`` is the function without its cache: no use of
    the cache, no shared work."""
    own = run.ran["test_api.py::test_wrapped_is_the_function_itself|run"]
    assert {"table", "beneath"} <= own


def test_a_body_that_runs_outside_every_test_is_not_labelled(run: Run) -> None:
    """``at_collection`` is filled while a test module is imported. Its lines
    are under no test, which the index already treats as run at import."""
    assert "at_collection" in run.ran[""]
    assert not [c for c in run.ran if "at_collection" in c]


def test_a_body_on_another_thread_is_not_labelled(run: Run) -> None:
    """Coverage has one current context for the process; switching it from a
    second thread would relabel the lines of the first."""
    assert "threaded" in run.ran["test_api.py::test_a_call_on_another_thread|run"]
    assert not [c for c in run.ran if is_shared_context(c) and "threaded" in c]


def test_a_method_and_a_recursive_function_each_have_one_context(run: Run) -> None:
    assert run.ran[shared_context("cache", "work.py::Holder.twice")] == {"twice"}
    assert run.ran[shared_context("cache", "work.py::fib")] == {"fib"}
    assert _under_a_test(run, "fib") == set()


# ── what the record says ────────────────────────────────────────────────────


def _caches(run: Run, test: str) -> set[str]:
    return set(run.record["tests"][test]["caches"])


def test_every_call_is_recorded_hit_or_miss(run: Run) -> None:
    for test in ("test_fills", "test_hits", "test_other_entry"):
        assert _caches(run, f"test_a.py::{test}") == {"work.py::table"}, test
    assert _caches(run, "test_a.py::test_uses_nothing_cached") == set()


def test_a_hit_on_what_collection_filled_is_recorded_too(run: Run) -> None:
    assert _caches(run, "test_a.py::test_reads_what_collection_filled") == {
        "work.py::at_collection"}


def test_a_call_on_another_thread_is_still_a_use(run: Run) -> None:
    assert _caches(run, "test_api.py::test_a_call_on_another_thread") == {"work.py::threaded"}


def test_nesting_is_recorded_through_fixtures_and_through_caches(run: Run) -> None:
    nested = run.record["nested"]
    assert "cache|work.py::plain" in nested[BUILT]
    assert nested["cache|work.py::plain"] == {"cache|work.py::doubled"}
    assert nested["cache|work.py::fib"] == {"cache|work.py::fib"}


def test_caches_made_before_the_recorder_are_listed(run: Run) -> None:
    """In a module, in a class, and behind ``staticmethod``."""
    assert run.record["unwrapped"] == {"early.py::before", "early.py::K.m", "early.py::K.s"}
    assert unwrapped_caches(run.reads) == run.record["unwrapped"]


# ── folded ──────────────────────────────────────────────────────────────────


def test_folded_every_caller_is_paired_with_the_body_and_what_it_calls(
        run: Run, tmp_path: Path) -> None:
    """The close condition of the item: one test fills the cache, another
    hits it, and BOTH are paired with the cached function and with the
    function only its body calls, statement by statement."""
    conn = open_index(tmp_path / "index.sqlite")
    done = update_from_run(conn, run.coverage_db, repo_root=run.project,
                           shared_users=shared_work_used(run.reads),
                           unlabelled_caches=unwrapped_caches(run.reads))
    work = str(run.project / "work.py")
    callers = {f"test_a.py::{name}" for name in ("test_fills", "test_hits", "test_other_entry")}
    api = "test_api.py::test_cache_info_and_cache_clear"
    for function in ("table", "beneath"):
        records = statement_records(conn, work, function)
        assert callers | {api} <= set(records), function
        assert records["test_a.py::test_hits"], function
        assert records["test_a.py::test_hits"] == records["test_a.py::test_fills"], function
        assert "test_a.py::test_uses_nothing_cached" not in records
    # Through a fixture, then a cached function, then another cached function.
    through = {"test_a.py::test_through_a_fixture", "test_a.py::test_through_the_same_fixture"}
    assert through <= set(statement_records(conn, work, "doubled"))
    assert done.unused == ()
    # ``built`` is a fixture of a conftest, which is not measured here: its
    # context has no row of its own, and its users reach the caches through it.
    # table, doubled, plain, fib, Holder.twice, and refuses (its body raises).
    assert done.by_kind == {"cache": 6}
    # Not narrowed on: the function collection filled (under no test), and
    # the caches the recorder could not wrap.
    held = blocks_run_at_import(conn, [work, str(run.project / "early.py")])
    assert (work, "at_collection") in held
    assert {(str(run.project / "early.py"), name) for name in ("before", "K.m", "K.s")} <= held
    assert (work, "table") not in held and (work, "beneath") not in held
    assert done.unlabelled == ("early.py::K.m", "early.py::K.s", "early.py::before")


# ── with the recorder or the labelling off ──────────────────────────────────


def test_without_the_recorder_a_cache_is_a_cache_and_nothing_is_labelled(
        tmp_path_factory: pytest.TempPathFactory) -> None:
    """The plugin is loaded and its option is not given. The project's own
    tests pass, and no line is under a context of a cache's own."""
    off = _run(tmp_path_factory.mktemp("off"), "0", record=False)
    assert not [c for c in off.ran if is_shared_context(c)]
    assert "table" in off.ran["test_a.py::test_fills|run"]


def test_without_pytest_covs_test_contexts_nothing_is_labelled_and_uses_are_kept(
        tmp_path_factory: pytest.TempPathFactory) -> None:
    plain = _run(tmp_path_factory.mktemp("plain"), "0", test_contexts=False)
    assert set(plain.ran) == {""}
    assert _caches(plain, "test_a.py::test_hits") == {"work.py::table"}
