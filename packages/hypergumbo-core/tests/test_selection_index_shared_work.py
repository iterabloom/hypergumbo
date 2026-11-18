# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work done once and shared is paired with every test that used it.

A module, class or session fixture runs once per worker, inside the setup of
whichever test needs it first. pytest-cov records its lines under that one
test (``<id>|setup``), so every other test that uses the fixture had no row
for the code the fixture ran: a change to that code selected one dependent and
left the rest out.

The recorder plugin (``scripts/pytest_plugins/pytest_source_reads.py``) now
runs such work under a coverage context of its own, ``|shared|fixture|<key>``,
and writes which tests used it. These tests pin what the fold does with the
two: the rows of a shared context go to every test of the run recorded using
it, they never make a test "measured", a shared context is never read as a
test id, and rows nobody is recorded using are treated as code run under no
test (the selector then does not narrow on it).
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hypergumbo_core.prose_only import RECORDER_VERSION
from hypergumbo_core.selection_index import (
    SHARED_CONTEXT,
    blocks_run_at_import,
    is_shared_context,
    open_index,
    shared_context,
    statement_records,
    touching_tests,
    update_from_run,
)

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

FIRST = "tests/test_x.py::test_first"
SECOND = "tests/test_x.py::test_second"
OTHER = "tests/test_y.py::test_uses_nothing_shared"
FIXTURE = shared_context("fixture", "tests/test_x.py::shared")

#: What the plugin leaves for a module fixture two tests use: ``built`` ran
#: once, under the fixture's own context; each test's call ran ``own``.
SUITE = [
    (FIXTURE, ["mod.built"]),
    (f"{FIRST}|run", ["mod.own"]),
    (f"{SECOND}|run", ["mod.own"]),
    (f"{OTHER}|run", ["mod.own"]),
]
USERS = {FIRST: {FIXTURE}, SECOND: {FIXTURE}}


class Repo:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "repo"
        self.source = self.root / "packages" / "p" / "src" / "p" / "mod.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text(
            "def built():\n    value = [1]\n    return value\n\n\n"
            "def own():\n    return 1\n")
        self.conn = open_index(tmp_path / "index.sqlite")
        self._runs = 0

    def fold(self, calls: list[tuple[str, list[str]]], users: dict[str, set[str]] | None,
             *, ran: list[str] | None = None, full: bool = False,
             unlabelled: list[str] | None = None):
        self._runs += 1
        db = self.root / f"run-{self._runs}.coverage"
        spec = {"measure": [str(self.source.parent)], "calls": calls}
        subprocess.run([sys.executable, "-c", textwrap.dedent(_GENERATE), str(db), json.dumps(spec)],
                       check=True, capture_output=True, text=True)
        return update_from_run(self.conn, db, ran_tests=ran, repo_root=self.root,
                               scope_complete=True, full_run=full, shared_users=users,
                               unlabelled_caches=unlabelled or ())

    def tests_of(self, block: str) -> dict[str, frozenset[int] | None]:
        return statement_records(self.conn, str(self.source), block)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def test_the_name_of_a_shared_context_cannot_be_a_test_id() -> None:
    """A pytest node id starts with a path; a shared context starts with a
    separator pytest-cov only ever puts BEFORE the phase."""
    assert FIXTURE == "|shared|fixture|tests/test_x.py::shared"
    assert FIXTURE.startswith(SHARED_CONTEXT)
    assert is_shared_context(FIXTURE)
    assert not is_shared_context(f"{FIRST}|setup")
    assert not is_shared_context("")


def test_every_test_that_used_the_fixture_is_paired_with_what_it_ran(repo: Repo) -> None:
    repo.fold(SUITE, USERS)
    assert set(repo.tests_of("built")) == {FIRST, SECOND}
    assert touching_tests(repo.conn, [str(repo.source)]) == {FIRST, SECOND, OTHER}


def test_the_pairing_is_statement_by_statement(repo: Repo) -> None:
    repo.fold(SUITE, USERS)
    assert repo.tests_of("built") == {FIRST: frozenset({0, 1}), SECOND: frozenset({0, 1})}


def test_a_test_that_did_not_use_it_is_not_paired(repo: Repo) -> None:
    repo.fold(SUITE, USERS)
    assert OTHER not in repo.tests_of("built")
    assert OTHER in repo.tests_of("own")


def test_inherited_statements_join_the_ones_the_test_ran_itself(repo: Repo) -> None:
    """``built`` has two statements. The fixture ran both; a record made of the
    test's own rows alone would be the same, so the test runs a DIFFERENT
    block itself and both records must be there."""
    repo.fold(SUITE, USERS)
    assert repo.tests_of("own")[FIRST] == frozenset({0})
    assert repo.tests_of("built")[FIRST] == frozenset({0, 1})


def test_a_shared_context_is_never_stored_as_a_test(repo: Repo) -> None:
    repo.fold(SUITE, USERS, ran=[FIRST, SECOND, OTHER], full=True)
    stored = {row[0] for row in repo.conn.execute("SELECT test_id FROM test")}
    assert stored == {FIRST, SECOND, OTHER}
    assert not {row[0] for row in repo.conn.execute("SELECT test_id FROM unmeasured_test")}


def test_inherited_rows_do_not_make_a_test_measured(repo: Repo) -> None:
    """A test whose own call ran no measured code is one coverage cannot speak
    for, whatever its fixtures ran: the rule of the own-phase tests."""
    silent = "tests/test_x.py::test_asserts_on_the_fixture_alone"
    calls = [(FIXTURE, ["mod.built"]), (f"{FIRST}|run", ["mod.own"])]
    repo.fold(calls, {FIRST: {FIXTURE}, silent: {FIXTURE}}, ran=[FIRST, silent], full=True)
    assert set(repo.tests_of("built")) == {FIRST, silent}
    assert {row[0] for row in repo.conn.execute("SELECT test_id FROM unmeasured_test")} == {silent}


def test_rows_nobody_is_recorded_using_count_as_run_under_no_test(repo: Repo) -> None:
    """The recorder of a killed worker never wrote who used the fixture. The
    rows are not dropped and not given to a guess: the function is marked as
    one that runs outside every test, and the selector does not narrow on it.
    Like a module body, it is paired with every test that runs anything in
    the file, with a record that says the test ran none of its statements."""
    repo.fold(SUITE, {FIRST: set()})
    assert repo.tests_of("built") == {
        FIRST: frozenset(), SECOND: frozenset(), OTHER: frozenset()}
    assert blocks_run_at_import(repo.conn, [str(repo.source)]) == {(str(repo.source), "built")}


def test_with_no_record_of_users_at_all_the_same(repo: Repo) -> None:
    repo.fold(SUITE, None)
    assert blocks_run_at_import(repo.conn, [str(repo.source)]) == {(str(repo.source), "built")}


def test_rows_with_a_recorded_user_are_not_marked_as_run_under_no_test(repo: Repo) -> None:
    repo.fold(SUITE, USERS)
    assert blocks_run_at_import(repo.conn, [str(repo.source)]) == frozenset()


def test_the_fold_says_what_it_did_with_shared_work(repo: Repo) -> None:
    lost = shared_context("fixture", "tests/test_z.py::built_by_a_killed_worker")
    done = repo.fold([*SUITE, (lost, ["mod.own"])], USERS)
    assert done.contexts == 2
    assert done.inheriting_tests == 2
    assert done.unused == (lost,)


def test_a_users_test_id_is_spelled_as_the_index_spells_it(repo: Repo) -> None:
    """A run confined to one package names a test relative to the package;
    the index stores it relative to the repository."""
    test_file = repo.root / "packages" / "p" / "tests" / "test_x.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_first():\n    pass\n")
    stored = "packages/p/tests/test_x.py::test_first"
    repo.fold([(FIXTURE, ["mod.built"]), (f"{FIRST}|run", ["mod.own"])], {FIRST: {FIXTURE}})
    assert set(repo.tests_of("built")) == {stored}


# ── cached functions ────────────────────────────────────────────────────────

CACHE = shared_context("cache", "packages/p/src/p/mod.py::built")


def test_a_cache_context_is_given_to_its_callers_like_a_fixtures(repo: Repo) -> None:
    done = repo.fold([(CACHE, ["mod.built"]), (FIXTURE, ["mod.own"]),
                      (f"{FIRST}|run", ["mod.own"]), (f"{SECOND}|run", ["mod.own"])],
                     {FIRST: {CACHE, FIXTURE}, SECOND: {CACHE}})
    assert set(repo.tests_of("built")) == {FIRST, SECOND}
    assert done.by_kind == {"cache": 1, "fixture": 1}
    assert done.contexts == 2 and done.unused == () and done.unlabelled == ()


def test_a_cache_the_recorder_could_not_wrap_is_marked_as_run_under_no_test(repo: Repo) -> None:
    """Its body ran under FIRST, the first caller. Nothing says who else was
    handed the value, so the function is not narrowed on. The key names the
    file relative to the repository."""
    calls = [(f"{FIRST}|run", ["mod.built", "mod.own"]), (f"{SECOND}|run", ["mod.own"])]
    done = repo.fold(calls, {}, unlabelled=["packages/p/src/p/mod.py::built"])
    assert blocks_run_at_import(repo.conn, [str(repo.source)]) == {(str(repo.source), "built")}
    assert done.unlabelled == ("packages/p/src/p/mod.py::built",)
    # Without the list: an ordinary function, narrowed on as before.
    again = Repo(repo.root.parent / "again")
    again.fold(calls, {})
    assert blocks_run_at_import(again.conn, [str(again.source)]) == frozenset()


def test_an_unwrapped_cache_the_index_has_no_block_for_marks_nothing(repo: Repo) -> None:
    """A nested function, or a file the run did not measure."""
    done = repo.fold(SUITE, USERS, unlabelled=["packages/p/src/p/mod.py::outer.<locals>.inner",
                                               "packages/p/src/p/absent.py::f"])
    assert blocks_run_at_import(repo.conn, [str(repo.source)]) == frozenset()
    assert len(done.unlabelled) == 2


COVERAGE_SELECT = Path(__file__).resolve().parents[3] / "scripts" / "coverage-select"


def _record(reads: Path, users: dict[str, list[str]]) -> None:
    reads.mkdir(parents=True)
    bucket = {"reads": [], "opaque": [], "docs": [], "lines": []}
    (reads / "main-1.json").write_text(json.dumps({
        "version": RECORDER_VERSION, "observed": True, "expected": {}, "sources": {},
        "failed": [],
        "tests": {test: {**bucket, "fixtures": keys} for test, keys in users.items()},
        "fixtures": {}, "nested": {}, "modules": {}, "unattributed": bucket}))


def test_the_command_reads_who_used_what_from_the_recorders_files(
        repo: Repo, tmp_path: Path) -> None:
    """``coverage-select update`` is what smart-test runs after a run. It must
    read the record BEFORE it stores and removes it, and say what it did."""
    db = repo.root / "run.coverage"
    spec = {"measure": [str(repo.source.parent)], "calls": SUITE}
    subprocess.run([sys.executable, "-c", textwrap.dedent(_GENERATE), str(db), json.dumps(spec)],
                   check=True, capture_output=True, text=True)
    reads = tmp_path / "reads"
    _record(reads, {FIRST: ["tests/test_x.py::shared"], SECOND: ["tests/test_x.py::shared"],
                    OTHER: []})
    done = subprocess.run(
        [sys.executable, str(COVERAGE_SELECT), "--repo-root", str(repo.root),
         "--index", str(tmp_path / "cli.sqlite"), "--verbose", "update",
         "--coverage-db", str(db), "--sha", "0" * 40, "--source-reads", str(reads)],
        capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert ("shared work: 1 fixture context(s) with measured code; 2 test(s) given their rows; "
            "0 context(s) no test is recorded using") in done.stderr
    conn = open_index(tmp_path / "cli.sqlite")
    assert set(statement_records(conn, str(repo.source), "built")) == {FIRST, SECOND}
    assert not list(reads.glob("*.json")), "the record is stored and removed as before"


def test_the_command_counts_contexts_by_kind_and_names_the_caches_it_could_not_label(
        repo: Repo, tmp_path: Path) -> None:
    """Printed without ``--verbose`` or ``--full``, because a function is
    being held back from narrowing."""
    db = repo.root / "run.coverage"
    calls = [(CACHE, ["mod.built"]), (FIXTURE, ["mod.own"]), (f"{FIRST}|run", ["mod.own"])]
    spec = {"measure": [str(repo.source.parent)], "calls": calls}
    subprocess.run([sys.executable, "-c", textwrap.dedent(_GENERATE), str(db), json.dumps(spec)],
                   check=True, capture_output=True, text=True)
    reads = tmp_path / "reads"
    reads.mkdir(parents=True)
    bucket = {"reads": [], "opaque": [], "docs": [], "lines": []}
    (reads / "main-1.json").write_text(json.dumps({
        "version": RECORDER_VERSION, "observed": True, "expected": {}, "sources": {},
        "failed": [], "fixtures": {}, "caches": {}, "nested": {}, "modules": {},
        "unattributed": bucket,
        "unwrapped_caches": ["packages/p/src/p/mod.py::own"],
        "tests": {FIRST: {**bucket, "fixtures": ["tests/test_x.py::shared"],
                          "caches": ["packages/p/src/p/mod.py::built"]}}}))
    done = subprocess.run(
        [sys.executable, str(COVERAGE_SELECT), "--repo-root", str(repo.root),
         "--index", str(tmp_path / "cli.sqlite"), "update",
         "--coverage-db", str(db), "--sha", "0" * 40, "--source-reads", str(reads)],
        capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert ("shared work: 1 fixture and 1 cache context(s) with measured code; "
            "1 test(s) given their rows; 0 context(s) no test is recorded using; "
            "1 cached function(s) made before the recorder loaded, held back from narrowing "
            "(packages/p/src/p/mod.py::own)") in done.stderr
    conn = open_index(tmp_path / "cli.sqlite")
    assert set(statement_records(conn, str(repo.source), "built")) == {FIRST}
    assert blocks_run_at_import(conn, [str(repo.source)]) == {(str(repo.source), "own")}


def test_the_command_says_so_when_nobody_is_recorded_using_a_context(
        repo: Repo, tmp_path: Path) -> None:
    """Without ``--verbose`` and without ``--full``: the line is printed
    because code is being held back from narrowing."""
    db = repo.root / "run.coverage"
    spec = {"measure": [str(repo.source.parent)], "calls": SUITE}
    subprocess.run([sys.executable, "-c", textwrap.dedent(_GENERATE), str(db), json.dumps(spec)],
                   check=True, capture_output=True, text=True)
    done = subprocess.run(
        [sys.executable, str(COVERAGE_SELECT), "--repo-root", str(repo.root),
         "--index", str(tmp_path / "cli.sqlite"), "update",
         "--coverage-db", str(db), "--sha", "0" * 40],
        capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert (f"1 context(s) no test is recorded using ({FIXTURE})") in done.stderr
