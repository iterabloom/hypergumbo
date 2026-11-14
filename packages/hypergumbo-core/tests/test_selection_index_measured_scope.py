# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a run did not measure, it cannot change; what it did not measure
whole, it cannot vouch for.

Two defects, one cause. The index took "this run recorded no line of file F
for test T" to mean "T does not run F", whatever the run had measured.

1. EDGES WERE LOST. Folding a run deleted EVERY edge of each test in it and
   stored what the run saw. A run that measures one package then deletes the
   test's edges into every other package; a run in another checkout deletes
   its edges into this one (test ids are repo-relative, so checkouts share
   them). In a day and a half this repository's index lost 26,212 of 412,614
   test-to-file pairs that way, and narrowing would have dropped 66 test files
   that execute the changed file.

2. SILENCE WAS READ AS EVIDENCE. A test narrowing may drop is one the index
   has rows for and no row on a changed file. But a test last run with one
   package measured has rows, and no row on a file of another package, for a
   reason that says nothing about that file.

The rules these tests pin:

- a fold replaces a test's edges into the files that run MEASURED (coverage's
  own file list), and no other edge;
- each test's latest run is recorded per checkout, with the files it measured
  and whether the caller declared it measured every package;
- a test file is INFORMED about some source files when every one of its tests
  that ran in the file's latest run left rows there, that run was declared
  complete, and it measured each of those files. Narrowing drops only
  informed files.

Coverage databases are generated in a subprocess, as in test_selection_index.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hypergumbo_core.selection_index import (
    FORMAT,
    droppable_test_files,
    informed_test_files,
    open_index,
    store_edges,
    touching_test_files,
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
sys.path[:0] = spec["path"]
for context, calls in spec["calls"].items():
    cov.switch_context(context + "|run")
    for call in calls:
        module, function = call.split(".")
        getattr(importlib.import_module(module), function)()
cov.stop()
cov.save()
'''

ONE = "tests/test_x.py::test_one"
TWO = "tests/test_x.py::test_two"
OTHER = "tests/test_y.py::test_other"
FILE_X, FILE_Y = "tests/test_x.py", "tests/test_y.py"


class Checkout:
    """A repository laid out like this one, with two packages:
    ``packages/pkg_a/src/mod_a.py`` and ``packages/pkg_b/src/mod_b.py``, and
    a way to run named tests against them with a chosen part of the source
    measured."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for package, module, function in (("pkg_a", "mod_a", "fa"), ("pkg_b", "mod_b", "fb")):
            self.src(package).mkdir(parents=True)
            (self.src(package) / f"{module}.py").write_text(
                f"def {function}():\n    return 1\n\n\ndef {function}_spare():\n    return 2\n")
        self.a = str(self.src("pkg_a") / "mod_a.py")
        self.b = str(self.src("pkg_b") / "mod_b.py")
        self._runs = 0

    def src(self, package: str) -> Path:
        return self.root / "packages" / package / "src"

    def run(self, calls: dict[str, list[str]], measure: tuple[str, ...] = ("pkg_a", "pkg_b")) -> Path:
        self._runs += 1
        db = self.root / f"run-{self._runs}.coverage"
        spec = {"measure": [str(self.src(m)) for m in measure],
                "path": [str(self.src("pkg_a")), str(self.src("pkg_b"))],
                "calls": calls}
        subprocess.run([sys.executable, "-c", textwrap.dedent(_GENERATE), str(db), json.dumps(spec)],
                       check=True, capture_output=True, text=True)
        return db

    def fold(self, conn: sqlite3.Connection, calls: dict[str, list[str]], *,
             measure: tuple[str, ...] = ("pkg_a", "pkg_b"), complete: bool = True,
             ran: list[str] | None = None) -> None:
        update_from_run(conn, self.run(calls, measure),
                        ran_tests=list(calls) if ran is None else ran,
                        repo_root=self.root, scope_complete=complete)


@pytest.fixture
def repo(tmp_path: Path) -> Checkout:
    return Checkout(tmp_path / "main")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_index(tmp_path / "index.sqlite")


BOTH = {ONE: ["mod_a.fa", "mod_b.fb"]}


class TestAFoldReplacesOnlyWhatItMeasured:
    def test_a_run_that_measured_one_package_keeps_the_edges_into_the_other(
            self, repo: Checkout, conn: sqlite3.Connection) -> None:
        """The defect. The test runs both files in both runs; the second run
        measured ``pkg_a`` only."""
        repo.fold(conn, BOTH)
        assert touching_tests(conn, [repo.a]) == touching_tests(conn, [repo.b]) == {ONE}
        repo.fold(conn, BOTH, measure=("pkg_a",), complete=False)
        assert touching_tests(conn, [repo.b]) == {ONE}
        assert touching_tests(conn, [repo.a]) == {ONE}

    def test_a_measured_file_the_test_no_longer_runs_loses_its_edge(
            self, repo: Checkout, conn: sqlite3.Connection) -> None:
        """The control for the rule above: staleness is still removed, by a
        run that measured the file. Another test runs ``mod_b``, so the file
        is in the run's measured list while this test has no row on it."""
        repo.fold(conn, BOTH)
        repo.fold(conn, {ONE: ["mod_a.fa"], OTHER: ["mod_b.fb"]})
        assert touching_tests(conn, [repo.b]) == {OTHER}
        assert touching_tests(conn, [repo.a]) == {ONE}

    def test_a_run_in_another_checkout_keeps_this_checkouts_edges(
            self, repo: Checkout, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test ids are repo-relative, so two checkouts name one test the same
        way while its edges name each checkout's own files."""
        repo.fold(conn, BOTH)
        worktree = Checkout(tmp_path / "worktree")
        worktree.fold(conn, BOTH)
        assert touching_tests(conn, [repo.a]) == touching_tests(conn, [repo.b]) == {ONE}
        assert touching_tests(conn, [worktree.a]) == {ONE}

    def test_store_edges_alone_touches_only_the_files_it_is_given(
            self, conn: sqlite3.Connection) -> None:
        """With no measured list, the files named in the call are the scope:
        a caller cannot erase an edge into a file it does not mention."""
        store_edges(conn, {"t": [("/r/a.py", "f"), ("/r/b.py", "g")]})
        store_edges(conn, {"t": [("/r/a.py", "h")]})
        assert set(conn.execute(
            "SELECT f.path, b.name FROM test_block tb JOIN block b ON b.id = tb.block "
            "JOIN file f ON f.id = b.file")) == {("/r/a.py", "h"), ("/r/b.py", "g")}
        # Named as measured and not executed: the edge goes.
        store_edges(conn, {"t": [("/r/a.py", "h")]}, measured=["/r/a.py", "/r/b.py"])
        assert touching_tests(conn, ["/r/b.py"]) == frozenset()


class TestInformed:
    """Which test files the index may be silent about with meaning."""

    def informed(self, conn: sqlite3.Connection, repo: Checkout, *paths: str) -> frozenset[str]:
        return informed_test_files(conn, repo.root, paths or [repo.a, repo.b])

    def test_a_complete_run_that_measured_the_files_informs(
            self, repo: Checkout, conn: sqlite3.Connection) -> None:
        repo.fold(conn, {ONE: ["mod_a.fa"], OTHER: ["mod_a.fa", "mod_b.fb"]})
        assert self.informed(conn, repo) == {FILE_X, FILE_Y}
        # Reach: the two files are told apart by what they ran.
        assert touching_test_files(conn, [repo.b], repo.root) == {FILE_Y}

    def test_a_run_that_was_not_declared_complete_does_not(
            self, repo: Checkout, conn: sqlite3.Connection) -> None:
        """The second defect: the test's latest run measured ``pkg_a`` alone,
        so its having no row on ``mod_b`` says nothing."""
        repo.fold(conn, {ONE: ["mod_a.fa"]})
        assert self.informed(conn, repo) == {FILE_X}
        repo.fold(conn, {ONE: ["mod_a.fa"]}, measure=("pkg_a",), complete=False)
        assert self.informed(conn, repo) == frozenset()
        assert self.informed(conn, repo, repo.a) == frozenset()

    def test_a_complete_run_informs_only_about_files_it_measured(
            self, repo: Checkout, conn: sqlite3.Connection) -> None:
        """Declared complete, yet ``mod_b`` is not in the run's own file list
        (an omitted file, or one nothing in the run imported). The index
        knows ``mod_b`` from an earlier run, so only the run's list can say
        this one did not measure it."""
        repo.fold(conn, {ONE: ["mod_a.fa"], OTHER: ["mod_b.fb"]})
        assert self.informed(conn, repo, repo.b) == {FILE_X, FILE_Y}
        repo.fold(conn, {ONE: ["mod_a.fa"]}, measure=("pkg_a",), complete=True)
        assert self.informed(conn, repo, repo.a) == {FILE_X, FILE_Y}
        assert self.informed(conn, repo, repo.b) == {FILE_Y}
        assert self.informed(conn, repo) == {FILE_Y}

    def test_the_latest_run_decides(self, repo: Checkout, conn: sqlite3.Connection) -> None:
        repo.fold(conn, {ONE: ["mod_a.fa"]}, measure=("pkg_a",), complete=False)
        assert self.informed(conn, repo) == frozenset()
        repo.fold(conn, {ONE: ["mod_a.fa"]})
        assert self.informed(conn, repo) == {FILE_X}

    def test_an_index_with_edges_and_no_run_record_informs_nothing(
            self, repo: Checkout, conn: sqlite3.Connection) -> None:
        """Every edge stored before runs were recorded: nothing is known
        about what those runs measured, so nothing may be dropped on them."""
        store_edges(conn, {ONE: [(repo.a, "fa")]})
        assert touching_tests(conn, [repo.a]) == {ONE}
        assert self.informed(conn, repo, repo.a) == frozenset()

    def test_a_test_that_ran_and_left_no_row_keeps_its_file_uninformed(
            self, repo: Checkout, conn: sqlite3.Connection) -> None:
        """A skipped test, or one that works through a subprocess: its file
        holds a test the run says nothing about."""
        repo.fold(conn, {ONE: ["mod_a.fa"], OTHER: ["mod_a.fa"]}, ran=[ONE, TWO, OTHER])
        assert self.informed(conn, repo) == {FILE_Y}
        # ... until that test leaves rows too.
        repo.fold(conn, {ONE: ["mod_a.fa"], TWO: ["mod_b.fb"]})
        assert self.informed(conn, repo) == {FILE_X, FILE_Y}

    def test_a_test_no_longer_in_its_file_does_not_hold_the_file_back(
            self, repo: Checkout, conn: sqlite3.Connection) -> None:
        """``test_two`` was last seen in a partial run and then removed from
        the file: the file's next run does not mention it."""
        repo.fold(conn, {ONE: ["mod_a.fa"], TWO: ["mod_a.fa"]}, measure=("pkg_a",), complete=False)
        repo.fold(conn, {ONE: ["mod_a.fa"]})
        assert self.informed(conn, repo) == {FILE_X}

    def test_no_junit_list_means_the_run_cannot_be_complete(
            self, repo: Checkout, conn: sqlite3.Connection) -> None:
        """Without the list of tests that ran, a test that left no row is
        invisible, so the run cannot vouch for a whole file."""
        update_from_run(conn, repo.run({ONE: ["mod_a.fa"]}), repo_root=repo.root,
                        scope_complete=True)
        assert self.informed(conn, repo) == frozenset()

    def test_each_checkout_has_its_own_latest_run(
            self, repo: Checkout, conn: sqlite3.Connection, tmp_path: Path) -> None:
        repo.fold(conn, {ONE: ["mod_a.fa"]})
        worktree = Checkout(tmp_path / "worktree")
        assert informed_test_files(conn, worktree.root, [worktree.a]) == frozenset()
        worktree.fold(conn, {ONE: ["mod_a.fa"]}, measure=("pkg_a",), complete=False)
        assert self.informed(conn, repo) == {FILE_X}
        assert informed_test_files(conn, worktree.root, [worktree.a]) == frozenset()

    def test_no_paths_and_unknown_paths(self, repo: Checkout, conn: sqlite3.Connection) -> None:
        repo.fold(conn, {ONE: ["mod_a.fa"]})
        assert informed_test_files(conn, repo.root, []) == {FILE_X}
        assert self.informed(conn, repo, str(repo.root / "never_seen.py")) == frozenset()

    def test_a_full_run_is_complete(self, repo: Checkout, conn: sqlite3.Connection) -> None:
        update_from_run(conn, repo.run({ONE: ["mod_a.fa"]}), ran_tests=[ONE],
                        repo_root=repo.root, full_run=True)
        assert self.informed(conn, repo) == {FILE_X}


class TestDroppable:
    """What narrowing may drop: informed files with no unmeasured test."""

    def layout(self, tmp_path: Path) -> tuple[Path, str]:
        """A checkout shaped like this repository, so the path rules apply."""
        root = tmp_path / "repo"
        source = root / "packages" / "p" / "src" / "p" / "mod.py"
        source.parent.mkdir(parents=True)
        source.write_text("def f():\n    return 1\n")
        return root, str(source)

    def fold(self, conn: sqlite3.Connection, root: Path, source: str, *,
             complete: bool, tmp_path: Path, n: int) -> None:
        db = tmp_path / f"run-{n}.coverage"
        spec = {"measure": [str(Path(source).parent)], "path": [str(Path(source).parent)],
                "calls": {ONE: ["mod.f"]}}
        subprocess.run([sys.executable, "-c", textwrap.dedent(_GENERATE), str(db), json.dumps(spec)],
                       check=True, capture_output=True, text=True)
        update_from_run(conn, db, ran_tests=[ONE], repo_root=root, scope_complete=complete)

    def test_a_changed_source_must_have_been_measured_by_a_complete_run(
            self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        root, source = self.layout(tmp_path)
        self.fold(conn, root, source, complete=False, tmp_path=tmp_path, n=1)
        assert droppable_test_files(conn, root, [source]) == frozenset()
        self.fold(conn, root, source, complete=True, tmp_path=tmp_path, n=2)
        assert droppable_test_files(conn, root, [source]) == {FILE_X}

    def test_a_changed_test_or_script_asks_nothing_of_the_run(
            self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Neither is something coverage measures, so neither can be in a
        run's file list, and neither may hold narrowing back by being absent
        from it. A source file the run did not measure does."""
        root, source = self.layout(tmp_path)
        self.fold(conn, root, source, complete=True, tmp_path=tmp_path, n=1)
        unmeasurable = [str(root / "packages" / "p" / "tests" / "test_new.py"),
                        str(root / "scripts" / "tool"), str(root / "docs" / "a.md")]
        assert droppable_test_files(conn, root, unmeasurable) == {FILE_X}
        assert droppable_test_files(conn, root, [*unmeasurable, source]) == {FILE_X}
        other = str(root / "packages" / "p" / "src" / "p" / "never_measured.py")
        assert droppable_test_files(conn, root, [*unmeasurable, other]) == frozenset()

    def test_an_unmeasured_test_still_holds_its_file_back(
            self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        root, source = self.layout(tmp_path)
        self.fold(conn, root, source, complete=True, tmp_path=tmp_path, n=1)
        conn.execute("INSERT INTO unmeasured_test (test_id) VALUES (?)", (TWO,))
        assert droppable_test_files(conn, root, [source]) == frozenset()


class TestRowsOfACheckoutThatIsGone:
    def test_they_are_removed_at_the_next_fold(
            self, repo: Checkout, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Edges are no longer deleted by a later run elsewhere, so a deleted
        worktree's rows would stay for good."""
        worktree = Checkout(tmp_path / "worktree")
        worktree.fold(conn, BOTH)
        repo.fold(conn, BOTH)
        assert touching_tests(conn, [worktree.a]) == {ONE}
        shutil.rmtree(worktree.root)
        repo.fold(conn, BOTH)
        assert touching_tests(conn, [worktree.a]) == frozenset()
        paths = {row[0] for row in conn.execute("SELECT path FROM file")}
        assert paths == {repo.a, repo.b}
        assert [row[0] for row in conn.execute("SELECT DISTINCT checkout FROM run")] == [str(repo.root)]
        # Nothing left pointing at a row that is gone.
        for orphans in (
            "SELECT count(*) FROM test_block WHERE block NOT IN (SELECT id FROM block)",
            "SELECT count(*) FROM block WHERE file NOT IN (SELECT id FROM file)",
            "SELECT count(*) FROM run_file WHERE file NOT IN (SELECT id FROM file)",
            "SELECT count(*) FROM run_file WHERE run NOT IN (SELECT id FROM run)",
            "SELECT count(*) FROM test_run WHERE run NOT IN (SELECT id FROM run)",
        ):
            assert conn.execute(orphans).fetchone()[0] == 0, orphans

    def test_a_worktree_nested_in_this_checkout_goes_too(
            self, repo: Checkout, conn: sqlite3.Connection) -> None:
        """Its files are under this checkout's root and are not this
        checkout's source files."""
        nested = Checkout(repo.root / ".claude" / "worktrees" / "wt")
        nested.fold(conn, BOTH)
        repo.fold(conn, BOTH)
        assert touching_tests(conn, [nested.a]) == {ONE}
        shutil.rmtree(nested.root)
        repo.fold(conn, BOTH)
        assert {row[0] for row in conn.execute("SELECT path FROM file")} == {repo.a, repo.b}
        assert [row[0] for row in conn.execute("SELECT DISTINCT checkout FROM run")] == [str(repo.root)]

    def test_a_deleted_file_of_this_checkout_keeps_its_rows(
            self, repo: Checkout, conn: sqlite3.Connection) -> None:
        """A file the change deletes is still how the tests that ran it are
        found (``Selection.missing_paths``)."""
        repo.fold(conn, BOTH)
        Path(repo.b).unlink()
        repo.fold(conn, {ONE: ["mod_a.fa"]}, measure=("pkg_a",), complete=False)
        assert touching_tests(conn, [repo.b]) == {ONE}

    def test_runs_no_test_points_at_are_removed(
            self, repo: Checkout, conn: sqlite3.Connection) -> None:
        for _ in range(3):
            repo.fold(conn, BOTH)
        assert conn.execute("SELECT count(*) FROM run").fetchone()[0] == 1
        assert conn.execute("SELECT count(DISTINCT run) FROM run_file").fetchone()[0] == 1


def test_a_format_2_index_gains_the_run_tables_and_informs_nothing(tmp_path: Path) -> None:
    """Format 3 adds tables and changes none, so a format-2 file is opened in
    place. Its edges stay; no test in it has a recorded run."""
    path = tmp_path / "index.sqlite"
    conn = open_index(path)
    store_edges(conn, {ONE: [("/r/a.py", "f")]})
    for table in ("test_run", "run_file", "run"):
        conn.execute(f"DROP TABLE {table}")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    conn = open_index(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == FORMAT == 3
    assert touching_tests(conn, ["/r/a.py"]) == {ONE}
    assert informed_test_files(conn, Path("/r"), ["/r/a.py"]) == frozenset()
