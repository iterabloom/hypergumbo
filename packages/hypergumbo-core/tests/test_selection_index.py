# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the incremental block->test index behind coverage-directed selection.

THE SHAPE OF THE THING. A run tells us which tests executed which lines; those
lines resolve to AST blocks; the index stores ``block -> tests``. At commit time
we re-hash the changed files, diff against the stored digests, and select the
tests attached to blocks that moved.

WHAT THESE TESTS ARE REALLY FOR. Selecting too many tests is merely slow;
selecting too FEW is a missed regression, so every "is selected" assertion below
is paired with an "is NOT selected" one on a sibling. A selector that returns
every test would satisfy the first half of this file completely.

Three semantics are load-bearing and each has its own class:

  PRECISION      changing alpha must not select beta's test
  IMPORT-TIME    changing a module-level constant must select every test that
                 imports the file, because that code runs on import and belongs
                 to no test's own execution
  STALENESS      re-indexing REPLACES a test's edges; a test that no longer
                 touches a block must stop being selected by it

The index is built from a coverage database generated in a SUBPROCESS, for the
same reason the census tests do it: a second Coverage inside the pytest-cov
session swaps the trace function under the outer measurement.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hypergumbo_core.selection_index import (
    changed_blocks,
    normalize_test_id,
    open_index,
    select_tests,
    update_from_run,
)

_SAMPLE = '''\
CONST = 1


def alpha(x):
    return x + 1


def beta(y):
    return y * 2
'''

#: ``which`` picks the calls, so a run can cover one function or both.
_GENERATE = '''\
import sys
import coverage

db, root, which = sys.argv[1], sys.argv[2], sys.argv[3]
cov = coverage.Coverage(data_file=db, source=[root])
cov.start()
sys.path.insert(0, root)
import sample_mod
if "a" in which:
    cov.switch_context("test_a|run")
    sample_mod.alpha(1)
if "b" in which:
    cov.switch_context("test_b|run")
    sample_mod.beta(2)
cov.stop()
cov.save()
'''


def _run(tmp_path: Path, source: str, which: str = "ab") -> Path:
    (tmp_path / "sample_mod.py").write_text(source)
    db = tmp_path / f"cov-{which}-{abs(hash(source)) % 10**8}.coverage"
    subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_GENERATE),
         str(db), str(tmp_path), which],
        check=True, capture_output=True, text=True,
    )
    return db


@pytest.fixture
def indexed(tmp_path: Path):
    """An index built from one run over the pristine sample."""
    db = _run(tmp_path, _SAMPLE)
    conn = open_index(tmp_path / "index.sqlite")
    update_from_run(conn, db, ran_tests=["test_a", "test_b"])
    return conn, tmp_path / "sample_mod.py"


class TestPrecision:
    def test_editing_a_function_selects_the_test_that_ran_it(
        self, indexed,
    ) -> None:
        conn, mod = indexed
        mod.write_text(_SAMPLE.replace("return x + 1", "return x + 99"))
        assert "test_a" in select_tests(conn, {str(mod): _SAMPLE}).tests

    def test_editing_a_function_does_NOT_select_the_other_test(
        self, indexed,
    ) -> None:
        """Without this the selector returns the file's whole test set and the
        entire exercise is pointless."""
        conn, mod = indexed
        mod.write_text(_SAMPLE.replace("return x + 1", "return x + 99"))
        assert "test_b" not in select_tests(conn, {str(mod): _SAMPLE}).tests

    def test_an_unchanged_file_selects_nothing(self, indexed) -> None:
        conn, mod = indexed
        assert select_tests(conn, {str(mod): _SAMPLE}).tests == frozenset()

    def test_reformatting_selects_nothing(self, indexed) -> None:
        """The property that motivated block hashing over line numbers.

        Reformatting means WHITESPACE, not "prepend an import" — the first
        version of this test did the latter and rightly selected everything,
        because adding a module-level statement IS a module-level change.
        """
        conn, mod = indexed
        mod.write_text(_SAMPLE.replace("return x + 1", "return x+1  # tidy"))
        assert select_tests(conn, {str(mod): _SAMPLE}).tests == frozenset()

    def test_a_module_level_insertion_DOES_select(self, indexed) -> None:
        """The partner. Adding an import changes import-time behaviour and must
        select, or "reformatting is free" would be hiding a real hole."""
        conn, mod = indexed
        mod.write_text("import os\n" + _SAMPLE)
        assert select_tests(conn, {str(mod): _SAMPLE}).tests == {"test_a", "test_b"}


class TestImportTimeCode:
    """Module-level code runs on import, under no test's context — so nothing
    would attach to it, and a constant change would select ZERO tests."""

    def test_changing_a_module_constant_selects_every_test_in_the_file(
        self, indexed,
    ) -> None:
        conn, mod = indexed
        mod.write_text(_SAMPLE.replace("CONST = 1", "CONST = 2"))
        assert select_tests(conn, {str(mod): _SAMPLE}).tests == {"test_a", "test_b"}


class TestNewCode:
    """A block with no history attaches to no test. That is CORRECT and safe
    only because this selector is unioned with the others, never subtracted."""

    def test_a_new_function_is_reported_rather_than_silently_empty(
        self, indexed,
    ) -> None:
        conn, mod = indexed
        mod.write_text(_SAMPLE + "\n\ndef gamma(z):\n    return z\n")
        sel = select_tests(conn, {str(mod): _SAMPLE})
        assert "gamma" in {name for _p, name in sel.new_blocks}
        assert sel.tests == frozenset(), (
            "new code has no coverage history; it must contribute no tests "
            "rather than appear to be covered"
        )

    def test_a_file_never_indexed_is_reported_as_unknown(
        self, indexed, tmp_path,
    ) -> None:
        conn, _mod = indexed
        other = tmp_path / "never_seen.py"
        other.write_text("X = 1\n")
        sel = select_tests(conn, {str(other): 'X = 1\n'})
        assert str(other) in sel.unknown_paths
        assert sel.tests == frozenset()


class TestStaleness:
    """Re-indexing must REPLACE a test's edges, not union with them."""

    def test_a_test_that_stops_touching_a_block_stops_being_selected(
        self, indexed, tmp_path,
    ) -> None:
        conn, mod = indexed
        # test_a now exercises beta instead of alpha.
        rewired = _SAMPLE.replace("def alpha(x):\n    return x + 1",
                                  "def alpha(x):\n    return beta(x)")
        db2 = _run(tmp_path, rewired, which="a")
        mod.write_text(rewired)
        update_from_run(conn, db2, ran_tests=["test_a"])

        mod.write_text(rewired.replace("return y * 2", "return y * 3"))
        assert "test_a" in select_tests(conn, {str(mod): rewired}).tests, (
            "test_a now runs beta and must be selected when beta changes"
        )

    def test_a_test_absent_from_a_run_keeps_its_edges(
        self, indexed, tmp_path,
    ) -> None:
        """The control. Replacing edges for tests that DID run must not wipe
        tests that simply were not part of this run — that would make every
        targeted run erase the rest of the map."""
        conn, mod = indexed
        db2 = _run(tmp_path, _SAMPLE, which="a")
        update_from_run(conn, db2, ran_tests=["test_a"])
        mod.write_text(_SAMPLE.replace("return y * 2", "return y * 3"))
        assert "test_b" in select_tests(conn, {str(mod): _SAMPLE}).tests


class TestUnmeasuredTests:
    """A test that ran and produced no coverage rows cannot be spoken about."""

    def test_a_ran_test_with_no_rows_is_recorded_as_unmeasured(
        self, tmp_path,
    ) -> None:
        db = _run(tmp_path, _SAMPLE, which="a")
        conn = open_index(tmp_path / "i.sqlite")
        update_from_run(conn, db, ran_tests=["test_a", "test_subprocess"],
                        full_run=True)
        rows = {r[0] for r in conn.execute("SELECT test_id FROM unmeasured_test")}
        assert rows == {"test_subprocess"}

    def test_an_unmeasured_test_is_never_coverage_selected(
        self, tmp_path,
    ) -> None:
        db = _run(tmp_path, _SAMPLE, which="a")
        conn = open_index(tmp_path / "i.sqlite")
        update_from_run(conn, db, ran_tests=["test_a", "test_subprocess"],
                        full_run=True)
        mod = tmp_path / "sample_mod.py"
        mod.write_text(_SAMPLE.replace("return x + 1", "return x + 5"))
        sel = select_tests(conn, {str(mod): _SAMPLE})
        assert "test_subprocess" not in sel.tests
        assert "test_subprocess" in sel.unmeasured


class TestChangedBlocks:
    def test_it_names_the_block_not_just_the_file(self, indexed) -> None:
        """Selection is per BLOCK; a file-level answer would be the thing this
        module exists to improve on."""
        conn, mod = indexed
        mod.write_text(_SAMPLE.replace("return x + 1", "return x + 7"))
        changed, _new = changed_blocks(conn, {str(mod): _SAMPLE})
        assert {name for _p, name in changed} == {"alpha"}

    def test_a_deleted_file_selects_the_tests_that_used_it(
        self, indexed,
    ) -> None:
        """A deletion must not read as "nothing changed".

        The tests that exercised the removed code are exactly the ones worth
        running, and reporting it as merely UNKNOWN would conflate "never
        indexed" (nothing is known) with "was indexed, now gone" (a lot is
        known, and it all changed).
        """
        conn, mod = indexed
        mod.unlink()
        sel = select_tests(conn, {str(mod): _SAMPLE})
        assert str(mod) in sel.missing_paths
        assert str(mod) not in sel.unknown_paths
        assert sel.tests == {"test_a", "test_b"}


class TestPersistence:
    def test_the_index_survives_reopening(self, indexed, tmp_path) -> None:
        conn, mod = indexed
        conn.close()
        reopened = open_index(tmp_path / "index.sqlite")
        mod.write_text(_SAMPLE.replace("return x + 1", "return x + 3"))
        assert "test_a" in select_tests(reopened, {str(mod): _SAMPLE}).tests

    def test_opening_a_fresh_path_creates_the_schema(self, tmp_path) -> None:
        conn = open_index(tmp_path / "brand-new.sqlite")
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"block", "test_block", "unmeasured_test", "meta"} <= names

    def test_a_full_run_marker_is_recorded(self, tmp_path) -> None:
        """Selection is only sound as of the last FULL run; the age of that
        marker is what the dry-run reports as index_age."""
        db = _run(tmp_path, _SAMPLE)
        conn = open_index(tmp_path / "i.sqlite")
        update_from_run(conn, db, ran_tests=["test_a", "test_b"],
                        git_sha="deadbeef", full_run=True)
        got = dict(conn.execute("SELECT key, value FROM meta"))
        assert got["last_full_run_sha"] == "deadbeef"


def test_sqlite_module_is_used_directly(indexed) -> None:
    """Guard against the index quietly becoming an in-memory dict."""
    conn, _mod = indexed
    assert isinstance(conn, sqlite3.Connection)


class TestVanishedBlocks:
    """Deleting or renaming a function is detected by its KEY disappearing.

    This is the ONLY signal for a rename or a move, because children are
    removed from their parent's digest entirely (see block_hash) — so the
    enclosing module block does not move when a function is deleted. Without
    the vanished-key rule a deletion would select nothing at all.
    """

    WITHOUT_ALPHA = _SAMPLE.replace("def alpha(x):\n    return x + 1\n\n\n", "")

    def test_deleting_a_function_selects_the_tests_that_ran_it(
        self, indexed,
    ) -> None:
        conn, mod = indexed
        mod.write_text(self.WITHOUT_ALPHA)
        assert "test_a" in select_tests(conn, {str(mod): _SAMPLE}).tests

    def test_the_module_block_does_NOT_move_and_that_is_why_this_rule_exists(
        self, indexed,
    ) -> None:
        """The control that proves the vanished-key rule is doing the work.

        If the module digest also changed, this test would pass for the wrong
        reason and the rule could be deleted without the suite noticing.
        """
        conn, mod = indexed
        mod.write_text(self.WITHOUT_ALPHA)
        changed, _new = changed_blocks(conn, {str(mod): _SAMPLE})
        assert {name for _p, name in changed} == {"alpha"}
        assert "test_b" not in select_tests(conn, {str(mod): _SAMPLE}).tests


class TestSourceUnreadableAtIndexTime:
    def test_a_file_gone_before_indexing_records_no_blocks(
        self, tmp_path,
    ) -> None:
        """The run happened, then the file vanished before the index was
        updated. Nothing can be attributed, and it must not raise — the
        updater runs unattended after every test run.
        """
        db = _run(tmp_path, _SAMPLE)
        (tmp_path / "sample_mod.py").unlink()
        conn = open_index(tmp_path / "i.sqlite")
        # full_run because the unmeasured assertion below is a claim that these
        # tests produced nothing, which only holds when coverage had complete
        # scope — see TestUnmeasuredRequiresCompleteScope.
        update_from_run(conn, db, ran_tests=["test_a", "test_b"], full_run=True)
        assert conn.execute("SELECT count(*) FROM block").fetchone()[0] == 0
        assert {r[0] for r in conn.execute(
            "SELECT test_id FROM unmeasured_test")} == {"test_a", "test_b"}


class TestNodeIdNormalisation:
    """Two spellings of one test would fragment the index and under-select.

    pytest node ids are relative to ROOTDIR, and rootdir depends on the
    invocation: a run confined to one package makes that package the rootdir
    (``packages/hypergumbo-core/pyproject.toml`` carries
    ``[tool.pytest.ini_options]``), while a full run spans packages and uses the
    repo. Both happen in normal use:

        full run     -> packages/hypergumbo-core/tests/test_x.py::test_y
        targeted run -> tests/test_x.py::test_y

    smart-test now pins ``--rootdir`` so this does not arise at the source, but
    the index must survive being fed by a run that did not go through it.
    Same class of defect as the ``|setup`` suffix that once matched 0 of 20,437.
    """

    def test_a_package_relative_id_is_rewritten_to_repo_relative(
        self, tmp_path,
    ) -> None:
        pkg = tmp_path / "packages" / "demo" / "tests"
        pkg.mkdir(parents=True)
        (pkg / "test_x.py").write_text("")
        assert normalize_test_id("tests/test_x.py::test_y", tmp_path) == \
            "packages/demo/tests/test_x.py::test_y"

    def test_an_already_repo_relative_id_is_left_alone(self, tmp_path) -> None:
        """The control: normalisation must be idempotent, or a full run's ids
        would get rewritten into something that no longer exists."""
        pkg = tmp_path / "packages" / "demo" / "tests"
        pkg.mkdir(parents=True)
        (pkg / "test_x.py").write_text("")
        node = "packages/demo/tests/test_x.py::test_y"
        assert normalize_test_id(node, tmp_path) == node

    def test_an_ambiguous_id_is_left_alone_rather_than_guessed(
        self, tmp_path,
    ) -> None:
        """If two packages could both claim it, resolving would be a coin flip
        and a wrong guess silently attaches tests to the wrong file."""
        for name in ("a", "b"):
            d = tmp_path / "packages" / name / "tests"
            d.mkdir(parents=True)
            (d / "test_dup.py").write_text("")
        node = "tests/test_dup.py::test_y"
        assert normalize_test_id(node, tmp_path) == node

    def test_an_id_with_no_file_part_passes_through(self, tmp_path) -> None:
        assert normalize_test_id("bare_test_name", tmp_path) == "bare_test_name"

    def test_the_index_stores_the_normalised_id(self, tmp_path) -> None:
        """End to end: the same test seen under both spellings must be ONE row."""
        db = _run(tmp_path, _SAMPLE, which="a")
        conn = open_index(tmp_path / "i.sqlite")
        pkg = tmp_path / "packages" / "demo" / "tests"
        pkg.mkdir(parents=True)
        (pkg / "test_a.py").write_text("")
        update_from_run(conn, db, repo_root=tmp_path)
        stored = {r[0] for r in conn.execute("SELECT DISTINCT test_id FROM test_block")}
        assert stored == {"test_a"}, stored


class TestUnmeasuredRequiresCompleteScope:
    """"Ran and produced nothing" is only meaningful if coverage was watching
    everything.

    OBSERVED: smart-test's targeted path scopes ``--cov`` to the CHANGED SOURCE
    FILES ONLY, so on an ordinary run most tests touch nothing in scope. Taken
    at face value that marked 1,507 of 1,662 tests unmeasured in a single run —
    and :func:`select_tests` EXCLUDES unmeasured tests, so most of the suite
    would have become permanently unselectable with every unit test green.
    """

    def test_a_partial_run_does_NOT_declare_tests_unmeasured(
        self, tmp_path,
    ) -> None:
        db = _run(tmp_path, _SAMPLE, which="a")
        conn = open_index(tmp_path / "i.sqlite")
        update_from_run(conn, db, ran_tests=["test_a", "test_b", "test_c"])
        assert conn.execute(
            "SELECT count(*) FROM unmeasured_test").fetchone()[0] == 0

    def test_a_full_run_does_declare_them(self, tmp_path) -> None:
        """The partner: with complete scope the claim IS meaningful, and losing
        it would mean the 3,614-test unmeasured population is never recorded."""
        db = _run(tmp_path, _SAMPLE, which="a")
        conn = open_index(tmp_path / "i.sqlite")
        update_from_run(conn, db, ran_tests=["test_a", "test_c"], full_run=True)
        assert {r[0] for r in conn.execute(
            "SELECT test_id FROM unmeasured_test")} == {"test_c"}

    def test_edges_clear_an_unmeasured_flag_at_ANY_scope(
        self, tmp_path,
    ) -> None:
        """Producing edges is positive evidence regardless of scope, so a test
        wrongly flagged by an earlier run must be able to recover without
        waiting for the next full run."""
        db = _run(tmp_path, _SAMPLE, which="a")
        conn = open_index(tmp_path / "i.sqlite")
        update_from_run(conn, db, ran_tests=["test_a", "test_b"], full_run=True)
        assert {r[0] for r in conn.execute(
            "SELECT test_id FROM unmeasured_test")} == {"test_b"}
        db2 = _run(tmp_path, _SAMPLE, which="b")
        update_from_run(conn, db2, ran_tests=["test_b"])
        assert conn.execute(
            "SELECT count(*) FROM unmeasured_test").fetchone()[0] == 0


class TestChangeIsMeasuredAgainstGitNotAgainstTheIndex:
    """THE BASELINE BUG, observed end-to-end on 2026-08-14.

    ``changed_blocks`` originally diffed current source against the digests
    STORED IN THE INDEX. But the index is rewritten after every run, so the
    baseline moved every time: run 1 stored the working tree, run 2 compared the
    working tree against itself and selected NOTHING, while smart-test — which
    diffs against a GIT baseline (last-green-sha / merge-base) — selected 87
    files. Measured: `coverage would select 0 files, actually selected 87,
    trustworthy: True`. It answered "what changed since I last looked" when the
    question is "what changed in this commit".

    So the caller supplies the BASE SOURCE per path and the index supplies only
    ``block -> tests``. Change detection is git's job; attribution is the
    index's.
    """

    def test_a_change_against_the_base_is_found_even_after_reindexing(
        self, indexed,
    ) -> None:
        conn, mod = indexed
        edited = _SAMPLE.replace("return x + 1", "return x + 42")
        mod.write_text(edited)
        # Re-index the EDITED tree, exactly as a prior run would have.
        sel = select_tests(conn, {str(mod): _SAMPLE})
        assert "test_a" in sel.tests, (
            "the diff is against the supplied base, so re-indexing must not "
            "erase the change"
        )

    def test_no_change_against_the_base_selects_nothing(self, indexed) -> None:
        """The control: identical base and working tree is genuinely empty."""
        conn, mod = indexed
        assert select_tests(conn, {str(mod): _SAMPLE}).tests == frozenset()

    def test_a_file_absent_from_the_base_is_new_not_changed(
        self, indexed, tmp_path,
    ) -> None:
        """``None`` base means the commit ADDED this file: every block is new,
        so it has no history and contributes no tests."""
        conn, _mod = indexed
        added = tmp_path / "added_mod.py"
        added.write_text("def fresh():\n    return 1\n")
        sel = select_tests(conn, {str(added): None})
        assert sel.tests == frozenset()
        assert "fresh" in {name for _p, name in sel.new_blocks}

    def test_a_function_deleted_relative_to_the_base_selects_its_tests(
        self, indexed,
    ) -> None:
        conn, mod = indexed
        mod.write_text(_SAMPLE.replace(
            "def alpha(x):\n    return x + 1\n\n\n", ""))
        assert "test_a" in select_tests(conn, {str(mod): _SAMPLE}).tests
