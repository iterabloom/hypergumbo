# SPDX-License-Identifier: AGPL-3.0-or-later
"""The index records WHICH statements of a block each test ran (format 4).

The ``test -> block`` pairs say a test reached a function. They cannot say
whether a given line of that function is run by any test other than the ones
a change affects, which is the question a budgeted test run has to answer
before it may leave tests out and still claim the tree's coverage. A fold has
the answer in hand (coverage's per-test lines) and used to throw it away after
reducing it to block names.

Each pair now carries a bitmap: bit *i* set means the test ran the block's
statement number *i* (``block_hash``: numbered by the tree the digest hashes,
so comments, blank lines and code above the block do not move the numbers).

WHAT IS PINNED, each with a control that fails when the rule is broken:

* a fold records what each test ran, phases of one test merged, nested blocks
  separate, and REPLACES a test's earlier record;
* three states that must not be confused (absent is not empty): NO RECORD
  (a pair written before format 4, or by a caller with no statement data),
  RECORDED AND EMPTY (the pair exists because the file was imported; the test
  ran none of the block's statements itself), and recorded with statements;
* a record is about the block AS IT WAS. When a fold sees a block whose digest
  changed, the records of the tests that did not run in that fold are dropped
  (the pairs stay); a block edited since the last fold is reported as changed
  and no record is read for it; an edit the digest ignores changes no answer;
* a format-3 file is opened where it stands and every pair in it has no
  record; the queries stay index lookups.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# ``tests_of_line`` is reached as ``si.tests_of_line``: imported by name,
# pytest would collect it as a test.
from hypergumbo_core import selection_index as si
from hypergumbo_core.selection_index import (
    FORMAT,
    open_index,
    record_counts,
    statement_records,
    store_edges,
    touching_tests,
    update_from_run,
)

SAMPLE = '''\
import os

LIMIT = 3


def alpha(x):
    total = x + 1
    if total > LIMIT:
        total = 0
    else:
        total += 1
    return total


def outer(flag):
    before = 1

    def inner(y):
        return y + before

    if flag:
        return inner(2)
    return before


class Holder:
    size = 2

    def get(self):
        return self.size


def long(n):
    a = n
    b = a
    c = b
    d = c
    e = d
    f = e
    g = f
    h = g
    i = h
    j = i
    return j
'''

#: alpha's statements, by ordinal.
A_ASSIGN, A_IF, A_THEN, A_ELSE, A_RETURN = range(5)

_DRIVER = '''\
import sys
import coverage

db, root = sys.argv[1], sys.argv[2]
steps = [s.split("=>", 1) for s in sys.argv[3:]]
cov = coverage.Coverage(data_file=db, source=[root])
cov.start()
sys.path.insert(0, root)
import sample_mod
for context, expression in steps:
    cov.switch_context(context)
    eval(expression, {"m": sample_mod})
cov.stop()
cov.save()
'''


def _run(root: Path, *steps: str, source: str | None = None) -> Path:
    """Run ``context=>expression`` steps under coverage; returns the data file."""
    module = root / "sample_mod.py"
    if source is not None or not module.exists():
        module.write_text(SAMPLE if source is None else source)
    db = root / f"cov-{len(list(root.glob('cov-*')))}.coverage"
    subprocess.run(
        [sys.executable, "-c", _DRIVER, str(db), str(root), *steps],
        check=True, capture_output=True, text=True)
    return db


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def conn(root: Path) -> sqlite3.Connection:
    return open_index(root / "index.sqlite")


def _module(root: Path) -> str:
    return str(root / "sample_mod.py")


def _records(conn: sqlite3.Connection, root: Path, block: str) -> dict[str, object]:
    return {test: (None if got is None else set(got))
            for test, got in statement_records(conn, _module(root), block).items()}


def _line_of(root: Path, text: str) -> int:
    lines = (root / "sample_mod.py").read_text().splitlines()
    (found,) = [n for n, line in enumerate(lines, 1) if line.strip() == text]
    return found


# --- what a fold records -----------------------------------------------------


class TestAFoldRecordsWhatEachTestRan:
    def test_two_tests_taking_two_branches(self, conn, root: Path) -> None:
        update_from_run(conn, _run(
            root, "test_low|run=>m.alpha(1)", "test_high|run=>m.alpha(5)"))
        assert _records(conn, root, "alpha") == {
            "test_low": {A_ASSIGN, A_IF, A_ELSE, A_RETURN},
            "test_high": {A_ASSIGN, A_IF, A_THEN, A_RETURN},
        }

    def test_the_phases_of_one_test_are_one_record(self, conn, root: Path) -> None:
        update_from_run(conn, _run(
            root, "test_t|setup=>m.alpha(1)", "test_t|run=>m.alpha(5)"))
        assert _records(conn, root, "alpha") == {
            "test_t": {A_ASSIGN, A_IF, A_THEN, A_ELSE, A_RETURN}}

    def test_a_nested_block_has_its_own_record(self, conn, root: Path) -> None:
        update_from_run(conn, _run(
            root, "test_in|run=>m.outer(True)", "test_out|run=>m.outer(False)"))
        assert _records(conn, root, "outer") == {
            "test_in": {0, 1, 2}, "test_out": {0, 1, 3}}
        assert _records(conn, root, "outer.inner") == {"test_in": {0}}

    def test_a_statement_past_the_first_byte(self, conn, root: Path) -> None:
        update_from_run(conn, _run(root, "test_long|run=>m.long(1)"))
        assert _records(conn, root, "long") == {"test_long": set(range(11))}

    def test_a_later_run_replaces_the_record(self, conn, root: Path) -> None:
        update_from_run(conn, _run(root, "test_t|run=>m.alpha(1)"))
        update_from_run(conn, _run(root, "test_t|run=>m.alpha(5)"))
        assert _records(conn, root, "alpha") == {
            "test_t": {A_ASSIGN, A_IF, A_THEN, A_RETURN}}

    def test_a_run_of_another_test_leaves_a_record_alone(self, conn, root: Path) -> None:
        """The control for the staleness rule below: an UNCHANGED block's
        records are not dropped by a fold their test was not part of."""
        update_from_run(conn, _run(root, "test_low|run=>m.alpha(1)"))
        update_from_run(conn, _run(root, "test_high|run=>m.alpha(5)"))
        assert _records(conn, root, "alpha")["test_low"] == {
            A_ASSIGN, A_IF, A_ELSE, A_RETURN}


# --- absent is not empty --------------------------------------------------------


class TestThreeStatesOfARecord:
    def test_an_import_time_block_is_recorded_and_empty(self, conn, root: Path) -> None:
        """The pair exists by implication (the test ran something in the
        file, so the file was imported). Its statements ran at import, under
        no test: the test itself ran none, and the record says exactly that."""
        update_from_run(conn, _run(root, "test_get|run=>m.Holder().get()"))
        assert _records(conn, root, "<module>") == {"test_get": set()}
        assert _records(conn, root, "Holder") == {"test_get": set()}
        assert _records(conn, root, "Holder.get") == {"test_get": {0}}

    def test_a_pair_stored_without_statement_data_has_no_record(
        self, conn, root: Path,
    ) -> None:
        store_edges(conn, {"test_old": [(_module(root), "alpha")]})
        conn.commit()
        assert _records(conn, root, "alpha") == {"test_old": None}
        assert touching_tests(conn, [_module(root)]) == {"test_old"}

    def test_the_counts_tell_the_three_apart(self, conn, root: Path) -> None:
        update_from_run(conn, _run(root, "test_get|run=>m.Holder().get()"))
        store_edges(conn, {"test_old": [(_module(root), "alpha")]})
        conn.commit()
        counts = record_counts(conn)
        assert (counts.pairs, counts.recorded, counts.with_statements) == (4, 3, 1)
        assert counts.bitmap_bytes == 1

    def test_an_empty_index_counts_nothing(self, conn) -> None:
        counts = record_counts(conn)
        assert (counts.pairs, counts.recorded, counts.with_statements,
                counts.bitmap_bytes) == (0, 0, 0, 0)


# --- asking about a line ---------------------------------------------------------


class TestWhichTestsRanALine:
    @pytest.fixture
    def folded(self, conn, root: Path):
        update_from_run(conn, _run(
            root, "test_low|run=>m.alpha(1)", "test_high|run=>m.alpha(5)"))
        return conn

    def test_a_line_one_test_ran_and_the_other_reached_past(self, folded, root) -> None:
        got = si.tests_of_line(folded, _module(root), _line_of(root, "total = 0"))
        assert (got.status, got.block, got.statement) == ("recorded", "alpha", A_THEN)
        assert got.ran == {"test_high"}
        assert got.reached_without == {"test_low"}
        assert got.unrecorded == frozenset()

    def test_a_line_both_ran(self, folded, root) -> None:
        got = si.tests_of_line(folded, _module(root), _line_of(root, "return total"))
        assert got.ran == {"test_low", "test_high"} and not got.reached_without

    def test_an_edit_the_digest_ignores_changes_no_answer(self, folded, root) -> None:
        """A comment inside the function and code above it: the line moved
        by four, the digest did not, and nothing was folded in between."""
        before = _line_of(root, "total = 0")
        edited = SAMPLE.replace(
            "LIMIT = 3\n", "LIMIT = 3\nEXTRA = 1\nMORE = 2\n").replace(
            "    total = x + 1\n", "    total = x + 1\n    # why: see the ADR\n\n")
        (root / "sample_mod.py").write_text(edited)
        after = _line_of(root, "total = 0")
        assert after == before + 4
        got = si.tests_of_line(folded, _module(root), after)
        assert (got.status, got.statement) == ("recorded", A_THEN)
        assert got.ran == {"test_high"} and got.reached_without == {"test_low"}

    def test_a_block_edited_since_the_fold_is_not_read(self, folded, root) -> None:
        (root / "sample_mod.py").write_text(
            SAMPLE.replace("    total = x + 1\n", "    total = x + 1\n    extra = 2\n"))
        got = si.tests_of_line(folded, _module(root), _line_of(root, "total = 0"))
        assert (got.status, got.block) == ("block_changed", "alpha")
        assert not (got.ran or got.reached_without or got.unrecorded)

    def test_a_block_the_index_has_never_seen(self, folded, root) -> None:
        (root / "sample_mod.py").write_text(SAMPLE + "\n\ndef fresh():\n    return 1\n")
        got = si.tests_of_line(folded, _module(root), _line_of(root, "return 1"))
        assert (got.status, got.block) == ("block_new", "fresh")

    def test_lines_that_are_no_statement(self, folded, root) -> None:
        for text in ("def alpha(x):", ""):
            lines = (root / "sample_mod.py").read_text().splitlines()
            line = next(n for n, t in enumerate(lines, 1) if t.strip() == text)
            assert si.tests_of_line(folded, _module(root), line).status == "no_statement"
        assert si.tests_of_line(folded, _module(root), 10_000).status == "no_statement"

    def test_an_import_time_line_names_the_tests_that_import_the_file(
        self, folded, root,
    ) -> None:
        got = si.tests_of_line(folded, _module(root), _line_of(root, "LIMIT = 3"))
        assert (got.status, got.block) == ("import_time", "<module>")
        assert got.ran == {"test_low", "test_high"}

    def test_a_file_the_index_does_not_know(self, folded, root) -> None:
        other = root / "other.py"
        other.write_text("def f():\n    return 1\n")
        assert si.tests_of_line(folded, str(other), 2).status == "unknown_file"

    def test_a_file_that_is_gone_or_does_not_parse(self, folded, root) -> None:
        assert si.tests_of_line(folded, str(root / "nope.py"), 1).status == "unreadable"
        (root / "sample_mod.py").write_text("def broken(:\n")
        assert si.tests_of_line(folded, _module(root), 1).status == "unreadable"

    def test_the_source_can_be_handed_in(self, folded, root) -> None:
        """A caller that already holds the text (or wants another version of
        the file judged) is not made to write it to disk."""
        got = si.tests_of_line(folded, _module(root), 1, source="def alpha(x):\n    return x\n")
        assert got.status == "no_statement"
        got = si.tests_of_line(folded, _module(root), 2, source="def alpha(x):\n    return x\n")
        assert got.status == "block_changed"

    def test_a_pair_with_no_record_is_reported_apart(self, folded, root) -> None:
        store_edges(folded, {"test_old": [(_module(root), "alpha")]})
        folded.commit()
        got = si.tests_of_line(folded, _module(root), _line_of(root, "total = 0"))
        assert got.unrecorded == {"test_old"}
        assert got.ran == {"test_high"} and got.reached_without == {"test_low"}


class TestTheAnswerAsTextAndData:
    @pytest.fixture
    def folded(self, conn, root: Path):
        update_from_run(conn, _run(
            root, "test_low|run=>m.alpha(1)", "test_high|run=>m.alpha(5)"))
        store_edges(conn, {"test_old": [(_module(root), "alpha")]})
        conn.commit()
        return conn

    def test_a_recorded_line(self, folded, root) -> None:
        line = _line_of(root, "total = 0")
        got = si.tests_of_line(folded, _module(root), line)
        assert got.describe().splitlines() == [
            f"{_module(root)}:{line}  statement 2 of alpha",
            "ran it: 1 test(s)",
            "  test_high",
            "reached the block without running it: 1 test(s)",
            "  test_low",
            "reached the block, no statement record: 1 test(s)",
            "  test_old",
        ]
        assert got.as_dict() == {
            "path": _module(root), "line": line, "status": "recorded",
            "block": "alpha", "statement": 2, "ran": ["test_high"],
            "reached_without": ["test_low"], "unrecorded": ["test_old"]}

    def test_an_import_time_line_lists_the_importers(self, folded, root) -> None:
        text = si.tests_of_line(folded, _module(root), _line_of(root, "LIMIT = 3")).describe()
        assert "runs when the file is imported" in text.splitlines()[0]
        # test_old is paired with alpha only: nothing says it imports the file.
        assert text.splitlines()[1:] == ["  test_high", "  test_low"]

    @pytest.mark.parametrize("status, phrase", [
        ("block_changed", "has changed since the index last saw it"),
        ("block_new", "never seen this block"),
        ("no_statement", "not a statement"),
        ("unknown_file", "nothing about this file"),
        ("unreadable", "missing or does not parse"),
    ])
    def test_every_other_status_says_what_it_means(self, status: str, phrase: str) -> None:
        text = si.LineTests("/p.py", 3, status, "f", 0).describe()
        assert text.startswith("/p.py:3  statement 0 of f: ") and phrase in text
        assert si.LineTests("/p.py", 3, status).describe().startswith("/p.py:3: ")

    def test_the_counts_as_text(self, folded) -> None:
        assert record_counts(folded).describe().splitlines() == [
            # Each folded test: <module> and Holder (import time, empty) and
            # alpha; test_old: alpha, with no record.
            "test -> block pairs:            7",
            "  with a statement record:      6 (85%)",
            "    naming at least one:        2",
            "    empty (import-time blocks): 4",
            "  with no record:               1",
            "bitmap bytes:                   2",
        ]

    def test_the_counts_of_an_empty_index_as_text(self, conn) -> None:
        assert record_counts(conn).describe().splitlines()[1] == \
            "  with a statement record:      0"

    def test_the_command_line_prints_both(self, folded, root) -> None:
        """``scripts/coverage-select`` only shuffles arguments; this is the
        one check that the two subcommands reach the functions above."""
        folded.close()
        script = Path(__file__).resolve().parents[3] / "scripts" / "coverage-select"
        line = _line_of(root, "total = 0")

        def call(*args: str) -> str:
            done = subprocess.run(
                [sys.executable, str(script), "--index", str(root / "index.sqlite"), *args],
                check=True, capture_output=True, text=True)
            assert done.stderr == "", done.stderr
            return done.stdout

        assert call("line-tests", f"{_module(root)}:{line}").splitlines()[:3] == [
            f"{_module(root)}:{line}  statement 2 of alpha", "ran it: 1 test(s)", "  test_high"]
        assert json.loads(call("line-tests", "--json", f"{_module(root)}:{line}"))["ran"] == [
            "test_high"]
        assert call("line-records").splitlines()[0] == "test -> block pairs:            7"


# --- a record is about the block as it was -----------------------------------------


class TestAChangedBlockDropsTheRecordsItNoLongerFits:
    EDITED = SAMPLE.replace("    total = x + 1\n", "    extra = 2\n    total = x + extra\n")

    def test_the_test_that_did_not_run_again_loses_its_record_not_its_pair(
        self, conn, root: Path,
    ) -> None:
        update_from_run(conn, _run(
            root, "test_low|run=>m.alpha(1)", "test_high|run=>m.alpha(5)"))
        update_from_run(conn, _run(root, "test_low|run=>m.alpha(1)", source=self.EDITED))
        records = _records(conn, root, "alpha")
        # extra, total, if, (then), else, return: the numbering moved by one.
        assert records["test_low"] == {0, 1, 2, 4, 5}
        assert records["test_high"] is None
        assert touching_tests(conn, [_module(root)]) == {"test_low", "test_high"}
        got = si.tests_of_line(conn, _module(root), _line_of(root, "total = 0"))
        assert got.status == "recorded"
        assert got.unrecorded == {"test_high"} and got.reached_without == {"test_low"}

    def test_other_blocks_of_the_file_keep_their_records(self, conn, root: Path) -> None:
        update_from_run(conn, _run(
            root, "test_low|run=>m.alpha(1)", "test_out|run=>m.outer(False)"))
        update_from_run(conn, _run(root, "test_low|run=>m.alpha(1)", source=self.EDITED))
        assert _records(conn, root, "outer") == {"test_out": {0, 1, 3}}

    def test_a_digest_given_for_the_first_time_drops_nothing(self, conn, root: Path) -> None:
        """A block first stored by a caller with no digest has an empty one;
        learning its digest is not a change to its code."""
        store_edges(conn, {"test_a": [(_module(root), "alpha")]},
                    statements={"test_a": {(_module(root), "alpha"): b"\x03"}})
        store_edges(conn, {}, digests={_module(root): {"alpha": "d1"}})
        assert _records(conn, root, "alpha") == {"test_a": {0, 1}}
        store_edges(conn, {}, digests={_module(root): {"alpha": "d2"}})
        assert _records(conn, root, "alpha") == {"test_a": None}


# --- the file format -----------------------------------------------------------------


_FORMAT_3_PAIRS = """
CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE);
CREATE TABLE block (id INTEGER PRIMARY KEY, file INTEGER NOT NULL, name TEXT NOT NULL,
                    digest TEXT NOT NULL, UNIQUE (file, name));
CREATE TABLE test (id INTEGER PRIMARY KEY, test_id TEXT NOT NULL UNIQUE);
CREATE TABLE test_block (test INTEGER NOT NULL, block INTEGER NOT NULL,
                         PRIMARY KEY (test, block)) WITHOUT ROWID;
CREATE INDEX test_block_by_block ON test_block (block, test);
INSERT INTO file VALUES (1, '/repo/src/m.py');
INSERT INTO block VALUES (1, 1, 'alpha', 'd1');
INSERT INTO test VALUES (1, 'test_a'), (2, 'test_b');
INSERT INTO test_block VALUES (1, 1), (2, 1);
"""


class TestTheFormat:
    def _format_3(self, path: Path, stamp: int = 3) -> Path:
        conn = sqlite3.connect(str(path))
        conn.executescript(_FORMAT_3_PAIRS)
        conn.execute(f"PRAGMA user_version = {stamp}")
        conn.commit()
        conn.close()
        return path

    def test_this_is_format_4(self) -> None:
        assert FORMAT == 4

    @pytest.mark.parametrize("stamp", [2, 3])
    def test_an_earlier_file_is_opened_where_it_stands(self, tmp_path: Path, stamp: int) -> None:
        path = self._format_3(tmp_path / "old.sqlite", stamp)
        conn = open_index(path)
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 4
        assert statement_records(conn, "/repo/src/m.py", "alpha") == {
            "test_a": None, "test_b": None}
        assert touching_tests(conn, ["/repo/src/m.py"]) == {"test_a", "test_b"}
        assert not list(tmp_path.glob("old.sqlite.*")), "no copy is made"
        conn.close()

    def test_it_can_be_written_to_and_reopened(self, tmp_path: Path) -> None:
        path = self._format_3(tmp_path / "old.sqlite")
        conn = open_index(path)
        store_edges(conn, {"test_a": [("/repo/src/m.py", "alpha")]},
                    statements={"test_a": {("/repo/src/m.py", "alpha"): b"\x05"}})
        conn.commit()
        conn.close()
        conn = open_index(path)
        assert statement_records(conn, "/repo/src/m.py", "alpha") == {
            "test_a": frozenset({0, 2}), "test_b": None}

    def test_a_fresh_file_has_the_column(self, tmp_path: Path) -> None:
        conn = open_index(tmp_path / "new.sqlite")
        columns = [row[1] for row in conn.execute("PRAGMA table_info(test_block)")]
        assert columns == ["test", "block", "statements"]


class TestTheRecordQueriesAreIndexLookups:
    def _plan(self, conn: sqlite3.Connection, sql: str, args: tuple[object, ...]) -> str:
        return " | ".join(r[3] for r in conn.execute(f"EXPLAIN QUERY PLAN {sql}", args))

    def test_the_records_of_one_block(self, conn) -> None:
        plan = self._plan(conn, si._RECORDS_OF_BLOCK, ("/p.py", "f"))
        assert "SCAN" not in plan, plan
        assert "test_block_by_block" in plan or "sqlite_autoindex_test_block" in plan, plan

    def test_dropping_the_records_of_one_block(self, conn) -> None:
        plan = self._plan(conn, si._DROP_RECORDS_OF_BLOCK, (1,))
        assert "SCAN" not in plan, plan

    def test_the_control_counting_scans_the_pair_table(self, conn) -> None:
        """So that 'no SCAN' above is an assertion that can fail."""
        assert "SCAN" in self._plan(conn, si._RECORD_COUNTS, ())


# --- the bitmap ------------------------------------------------------------------------


def _bits(ordinals: set[int]) -> int:
    """The integer production hands ``_int_bitmap``: bit *i* for ordinal *i*."""
    return sum(1 << ordinal for ordinal in ordinals)


class TestTheBitmap:
    @pytest.mark.parametrize("ordinals", [
        set(), {0}, {7}, {8}, {0, 7, 8, 15, 16}, set(range(40))])
    def test_it_round_trips(self, ordinals: set[int]) -> None:
        assert set(si._ordinals(si._int_bitmap(_bits(ordinals)))) == ordinals

    def test_it_is_as_long_as_its_highest_statement_needs(self) -> None:
        assert si._int_bitmap(_bits(set())) == b""
        assert si._int_bitmap(_bits({0})) == b"\x01"
        assert si._int_bitmap(_bits({7})) == b"\x80"
        assert si._int_bitmap(_bits({8})) == b"\x00\x01"

    def test_none_is_not_an_empty_bitmap(self) -> None:
        assert si._ordinals(None) is None
        assert si._ordinals(b"") == frozenset()

