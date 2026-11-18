# SPDX-License-Identifier: AGPL-3.0-or-later
"""Incremental ``block -> tests`` index for coverage-directed test selection.

WHAT IT IS. After every run, fold that run's per-test coverage contexts into a
persistent map from AST block (see :mod:`hypergumbo_core.block_hash`) to the
tests observed executing it. At commit time, re-hash the changed files, diff
each one against its base-commit source, which the caller supplies
(``git show <base>:<path>``), and select the tests attached to blocks that
moved.

WHY INCREMENTAL RATHER THAN A CENSUS SNAPSHOT. A snapshot cannot see code that
did not exist when it was taken, so under red-green-refactor every newly written
function would map to no test — the developer's own new test included. Folding
each run in as it happens closes that: the moment you run the new test, the
index learns the new blocks. This is the same reason pytest-testmon maintains
its database per-run instead of from a profiling pass.

THE FIVE SEMANTICS THAT ARE EASY TO GET WRONG, each with tests of its own:

1. **Replace, do not union, per test, inside what the run measured.**
   Re-indexing a test REPLACES its edges. Union would keep an edge to a block
   the test no longer touches, and stale edges only ever accumulate. But
   replacement applies ONLY to tests present in this run — otherwise a
   targeted run of one file would erase the map for everything else — and ONLY
   to edges into files this run MEASURED, read from the coverage database's
   own file list. A run that measured one package knows nothing about a test's
   edges into another, and a run in one checkout nothing about another
   checkout's files (test ids are repo-relative, so checkouts share them while
   paths are absolute). Replacing everything lost this repository's index
   26,212 of 412,614 test-to-file pairs in a day and a half.

2. **Import-time blocks belong to every test that touches the file.** Module
   bodies, class attributes and decorators execute once at import, under the
   EMPTY context, so no test owns them, and they are a substantial share of
   all statements. Left unattached, editing a module-level constant selects
   zero tests. The rule that fixes it is an implication rather than a
   guess: if a test executes any line in file F then F was imported, so F's
   import-time blocks run whenever that test runs.

   A FUNCTION CAN RUN AT IMPORT TOO, and then it is import-time code whatever
   kind of block it is. ``TABLE = build()`` in a module body runs ``build``
   under no test's context; a test that reads ``TABLE`` never runs ``build``
   itself and has no row for it. 113 of this repository's 6,515 functions
   have a statement that runs while some module is imported, and narrowing,
   asked about one statement added to ``io_boundary.load_catalog``, dropped
   the test files of two analyzers whose modules call it at import. So the
   fold keeps which function blocks had a statement in coverage's empty
   context (``at_import_block``; :func:`blocks_run_at_import`), and a change
   to one of them counts as import-time code changed. The mark is added by
   any run that sees it and removed only by a full run that does not (a
   partial run loads fewer modules, so not seeing it there says nothing).
   Code run at collection (a call inside ``parametrize`` arguments) is marked
   by the same evidence.

3. **A test that ran and produced no rows is UNMEASURED, not unneeded.** A
   sizeable part of this suite is in that state — the tracker package is
   outside smart-test's COV_PATHS, and subprocess/`scripts/` tests leave no
   in-process trace. "Produced rows" means IN ITS OWN CALL: pytest-cov
   records a test's setup, call and teardown as separate contexts, and a
   fixture that runs for every test (the root conftest's autouse teardown
   calls ``declared_test_inputs.install_declaration``) leaves a row for
   every test. Counting that row once took this repository's unmeasured
   list from 6,916 tests to 227 in one full run, and narrowing then dropped
   test files that drive ``coverage-select`` through a subprocess for a
   change to the code it runs. The rows of the other two phases are still
   stored as edges (the code did run during the test); they never make the
   test one coverage has spoken for. Costs 244 tests whose in-process work
   is all in a setup fixture: they are held back like the rest. Coverage
   can never
   select them, so they are recorded explicitly (on full runs only: a targeted
   run's ``--cov`` scope makes silence meaningless) and reported by
   :class:`Selection`, which is what stops ``--narrow`` (below) from silently
   dropping them.

4. **A test's rows are evidence about a file only if its latest run measured
   that file, as part of a run that measured everything.** "No row on F" means
   "does not run F" when F was measured. It means nothing when F was not, and
   a partial run cannot vouch even for the files it did measure: a new path
   from measured code into unmeasured code leaves no row anywhere, so the next
   change to that unmeasured code would find the test silent. Each fold
   therefore records a RUN (its checkout, the files it measured, whether the
   caller declared that it measured every package) and makes it the latest run
   of every test file in it; a test that ran and left no row is recorded as
   such. :func:`informed_test_files` answers "whose silence about these files
   is evidence", and narrowing drops nothing else
   (:func:`droppable_test_files`). An index written before runs were recorded
   has no latest run for any test, so nothing in it is droppable until its
   tests run again: the state the lost edges left is distrusted by
   construction, not repaired by a guess.

5. **A pair also records WHICH statements of the block the test ran, and
   that record is about the block as it was.** A pair says a test reached a
   function. It cannot say whether one line of the function is run by any
   test other than those a change affects, which a run that leaves tests out
   must know before it claims the tree's coverage. So each pair carries a
   bitmap, bit *i* for the block's statement number *i*
   (:mod:`~hypergumbo_core.block_hash` numbers statements by the tree the
   digest hashes, so an edit the digest ignores moves no number). Three
   states, never merged: NO RECORD (``NULL``: the pair predates format 4, or
   its record was dropped), RECORDED AND EMPTY (the pair exists because the
   file was imported; the test ran none of the block's statements itself) and
   recorded with statements. A record numbers the statements of the code it
   was made from, so when a fold stores a DIFFERENT digest for a block it
   drops the records of that block's pairs first; the tests in the fold then
   get new ones and the others keep their pair with no record. A reader
   compares the stored digest with the source in front of it before reading a
   record (:func:`tests_of_line`), which covers an edit no fold has seen yet.
   The records are read by :func:`tests_of_line` and
   :func:`statement_records` (``coverage-select line-tests`` /
   ``line-records``) and by the by-statement selector
   (:mod:`~hypergumbo_core.statement_select`).

   THE DROPPED RECORDS ARE KEPT, BY DIGEST. A selector that runs before a
   change is merged needs the records of the block as it was at the BASE of
   the change, and by then the changed code has been run: the fold that
   stored its digest dropped exactly those records. So what a fold drops it
   first copies to ``kept_record``, under the version (``kept_version``: a
   block and the digest the records were made under) they belong to, and :func:`statement_records_at` reads a block's records for a GIVEN
   digest: the current ones when that is the stored digest, with the kept
   ones laid under them. :data:`KEPT_VERSIONS` superseded digests are kept
   per block. These two tables are an addition no reader depends on: a file
   without them gains them empty when opened, a fold by older code simply
   keeps nothing, and :data:`FORMAT` does not move (the file is shared by
   every checkout on the machine, older ones included).

6. **Work done once and shared belongs to every test that used it.** A
   module, class or session fixture runs once per worker, inside the setup
   of whichever test needs it first (its post-yield half inside the teardown
   of whichever is last). Labelled with the running test, the code it ran
   belonged to ONE of the tests that depend on it, and a change to that code
   selected that one. A planted-fault campaign found failing tests the index
   did not pair with the changed function for exactly this reason. So the
   recorder plugin (``scripts/pytest_plugins/pytest_source_reads.py``) runs
   such work under a coverage context of its own
   (:func:`shared_context`: ``|shared|fixture|<key>``) and records which
   tests used it, and :func:`update_from_run` gives the rows of that context
   to every one of them (``shared_users``), statement record included.
   Three things follow from what the rows are. They never make a test
   "measured" (semantic 3): its own call ran none of that code. A shared
   context is never stored as a test. And rows NOBODY IS RECORDED USING (the
   worker was killed before it wrote its record; the fold was given no
   record) are neither dropped nor given to a guess: they are stored as code
   run under no test, the function is marked like one that runs at import
   (semantic 2), and narrowing treats a change to it as import-time code.
   NOT COVERED: a value a function keeps for later callers (a cache), and
   state an object keeps after a shared fixture returned it. Each is work
   done once under one test's label, with no fixture boundary to see.

SOUNDNESS BOUNDARY, stated because it is the thing that would bite. The index
knows "tests I have OBSERVED executing block B", a subset of "tests that execute
B", until every test has run since B appeared. So this selector is sound as of
the last FULL run and under-selects for newly added code between full runs. The
``last_full_run_sha`` marker is written so a caller can report that age rather
than pretend it does not exist, but nothing reads it yet. Narrowing (history:
WI-bolot) may drop only ``droppable_test_files`` outside the keep set
(``touching_test_files`` plus the selected tests' files), and refuses
outright on any ``narrowing_blockers`` reason.

RUN-SET HELPERS AND NARROWING. The index speaks absolute pytest node ids; the
run set is a list of repo-relative test files. ``files_of``, ``rebase_to_repo``
and ``selectable_test_files`` convert one to the other (the last also drops
files that no longer exist, so widening a run never adds a collection error).
Narrowing is stated as data rather than left to a shell pipeline:
``touching_test_files`` is the keep set (tests observed executing ANY block of a
changed file, because the local coverage gate is whole-file);
``droppable_test_files`` is the only droppable set (``speakable_test_files``:
files with coverage rows and no unmeasured test; that are also
``informed_test_files`` about the changed sources, semantic 4);
``narrowing_blockers`` names the reasons to refuse outright
(an unknown coverable source file, a missing changed file, changed import-time
code -- a module or class body, which every importer runs and no coverage row
records); and
``narrow_run_set`` applies them, returning a :class:`Narrowing` whose
``refusal`` is set, with the run set left whole, when a blocker fires or the
result would be empty.

CHECKOUTS THAT ARE GONE. Because a fold no longer deletes edges into files it
did not measure, the rows of a deleted worktree would stay for good. Each fold
removes the files that are no longer on disk and are not source files of its
own checkout, with their blocks and pairs, and the runs of checkout
directories that are gone (``_forget_dead_checkouts``). A missing source file
of the checkout itself keeps its rows: it is a file the change deleted.

STORAGE. Each test, source file and block is stored ONCE and numbered; the
``test -> block`` pairs are two integers each, plus the statement bitmap
(a few bytes: one bit per statement of the block, cut after the last one
run), in a ``WITHOUT ROWID`` table with
a second index by block. Spelling the three names out per pair (a pytest node
id, an absolute path, a block name: about 236 bytes, held again in the
primary-key index) is what made this repository's index 3.8 GB for 4.5 million
pairs. ``PRAGMA user_version`` holds :data:`FORMAT`. :func:`open_index` converts
a file of the earlier, spelled-out format (:func:`convert_format_1`) and refuses
a format it does not know (:class:`IndexFormatError`), because a file read with
the wrong schema looks like an EMPTY index, and to narrowing an empty index
says "no test runs this code". Callers read and write through the functions
here (:func:`store_edges`, :func:`touching_tests`, :func:`measured_tests`,
:func:`statement_records`, :func:`statement_records_at`, :func:`tests_of_line`),
never through the tables.
"""
from __future__ import annotations

import fcntl
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from hypergumbo_core.block_hash import (
    Block,
    blocks_for_source,
    line_owner,
    statement_owner,
)

#: ``(path, block name)`` — the join key. Deliberately not line-based.
BlockKey = tuple[str, str]

#: The storage format, held in ``PRAGMA user_version``. Format 1 predates the
#: marker and reads as 0, like a file SQLite has only just created; the two are
#: told apart by format 1's ``test_block.test_id`` column (:func:`_format_of`).
#: Format 3 is format 2 plus the run tables (``run``, ``run_file``,
#: ``test_run``): it adds tables and changes none, so a format-2 file is
#: opened in place and no test in it has a recorded run.
#: Format 4 is format 3 plus ``test_block.statements`` (semantic 5 of the
#: module docstring): one nullable column, added where the file stands, and
#: every pair already in the file has no record.
FORMAT = 4

#: Formats :func:`open_index` brings up to :data:`FORMAT` by creating the
#: tables, and the column, they lack.
_OPENED_IN_PLACE = frozenset({0, 2, 3, FORMAT})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS file (
    id   INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS block (
    id     INTEGER PRIMARY KEY,
    file   INTEGER NOT NULL,
    name   TEXT NOT NULL,
    digest TEXT NOT NULL,
    UNIQUE (file, name)
);
CREATE TABLE IF NOT EXISTS test (
    id      INTEGER PRIMARY KEY,
    test_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS test_block (
    test       INTEGER NOT NULL,
    block      INTEGER NOT NULL,
    statements BLOB,
    PRIMARY KEY (test, block)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS test_block_by_block ON test_block (block, test);
CREATE TABLE IF NOT EXISTS unmeasured_test (test_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS run (
    id       INTEGER PRIMARY KEY,
    checkout TEXT NOT NULL,
    complete INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS run_file (
    run  INTEGER NOT NULL,
    file INTEGER NOT NULL,
    PRIMARY KEY (run, file)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS test_run (
    test INTEGER NOT NULL,
    run  INTEGER NOT NULL,
    rows INTEGER NOT NULL,
    PRIMARY KEY (test, run)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS test_run_by_run ON test_run (run, test);
CREATE TABLE IF NOT EXISTS at_import_block (
    block INTEGER PRIMARY KEY
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS kept_version (
    id     INTEGER PRIMARY KEY,
    block  INTEGER NOT NULL,
    digest TEXT NOT NULL,
    seq    INTEGER NOT NULL,
    UNIQUE (block, digest)
);
CREATE TABLE IF NOT EXISTS kept_record (
    version    INTEGER NOT NULL,
    test       INTEGER NOT NULL,
    statements BLOB NOT NULL,
    PRIMARY KEY (version, test)
) WITHOUT ROWID;
"""

#: Superseded digests whose records are kept, per block. A function edited
#: and run more often than this inside one change loses the records of its
#: base, and is then selected by block again.
KEPT_VERSIONS = 8

#: The tables :func:`convert_format_1` rewrites. Every other table in the file
#: belongs to another module and is copied as it stands.
_OWN_TABLES = frozenset({
    "block", "test_block", "unmeasured_test", "meta", "run", "run_file", "test_run",
})

# The queries below are module constants so that the tests can ask SQLite for
# each one's plan: all three must be index lookups, never a scan of the pairs.

#: Tests observed executing any block of one file. ``?`` = the file's path.
_TOUCHING = (
    "SELECT DISTINCT t.test_id FROM file f "
    "JOIN block b ON b.file = f.id "
    "JOIN test_block tb ON tb.block = b.id "
    "JOIN test t ON t.id = tb.test "
    "WHERE f.path = ?"
)

#: Tests observed executing one block. ``?, ?`` = the file's path, the block.
_TESTS_OF_BLOCK = (
    "SELECT t.test_id FROM file f "
    "JOIN block b ON b.file = f.id "
    "JOIN test_block tb ON tb.block = b.id "
    "JOIN test t ON t.id = tb.test "
    "WHERE f.path = ? AND b.name = ?"
)

#: Tests with at least one pair: one primary-key probe per test.
_MEASURED = (
    "SELECT t.test_id FROM test t "
    "WHERE EXISTS (SELECT 1 FROM test_block tb WHERE tb.test = t.id)"
)

#: Remove, for the tests in ``temp.scope_test``, the pairs whose block is in
#: ``temp.scope_block`` (the blocks of the files a run measured). ``+block``
#: keeps SQLite from probing the pair table once per measured block and test:
#: it walks each test's own pairs and looks each block up in the temp table.
_DELETE_MEASURED_PAIRS = (
    "DELETE FROM test_block "
    "WHERE test IN (SELECT id FROM temp.scope_test) "
    "AND +block IN (SELECT id FROM temp.scope_block)"
)

#: Each test paired with one block, and its statement record (NULL = none).
#: ``?, ?`` = the file's path, the block.
_RECORDS_OF_BLOCK = (
    "SELECT t.test_id, tb.statements FROM file f "
    "JOIN block b ON b.file = f.id "
    "JOIN test_block tb ON tb.block = b.id "
    "JOIN test t ON t.id = tb.test "
    "WHERE f.path = ? AND b.name = ?"
)

#: Forget what every test ran INSIDE one block; the pairs stay. ``?`` = the
#: block's number.
_DROP_RECORDS_OF_BLOCK = "UPDATE test_block SET statements = NULL WHERE block = ?"

#: The records of block ``?2``, as they stand, kept under version ``?1``. A
#: version is a row of its own so that a kept record does not repeat a digest
#: (measured on this repository's index: 52 bytes a record with it).
_KEEP_RECORDS_OF_BLOCK = (
    "INSERT OR REPLACE INTO kept_record (version, test, statements) "
    "SELECT ?, test, statements FROM test_block "
    "WHERE block = ? AND statements IS NOT NULL"
)

_KEPT_VERSION = "SELECT id FROM kept_version WHERE block = ? AND digest = ?"

#: The kept records of one version.
_KEPT_RECORDS = (
    "SELECT t.test_id, r.statements FROM kept_record r "
    "JOIN test t ON t.id = r.test WHERE r.version = ?"
)

#: The kept versions of the blocks of the files in ``temp.dead_file``.
_DEAD_VERSIONS = (
    "SELECT v.id FROM temp.dead_file d JOIN block b ON b.file = d.id "
    "JOIN kept_version v ON v.block = b.id"
)

#: Pairs; pairs with a record; pairs whose record names a statement; bytes of
#: bitmap. The one query here that reads every pair, and says so in its plan.
_RECORD_COUNTS = (
    "SELECT COUNT(*), COUNT(statements), "
    "COALESCE(SUM(LENGTH(statements) > 0), 0), "
    "COALESCE(SUM(LENGTH(statements)), 0) FROM test_block"
)

#: Every test's latest run in one checkout. ``?`` = the checkout.
_LATEST_RUNS = (
    "SELECT t.test_id, tr.run, tr.rows FROM run r "
    "JOIN test_run tr ON tr.run = r.id "
    "JOIN test t ON t.id = tr.test "
    "WHERE r.checkout = ?"
)

#: Format 1's pairs as integers. ``old`` is the attached format-1 file; a module
#: constant so a test can make the copy lose a pair and watch the count check
#: refuse the result.
_COPY_PAIRS = (
    "INSERT INTO test_block (test, block) "
    "SELECT t.id, b.id FROM old.test_block tb "
    "JOIN test t ON t.test_id = tb.test_id "
    "JOIN file f ON f.path = tb.path "
    "JOIN block b ON b.file = f.id AND b.name = tb.name"
)


class IndexFormatError(RuntimeError):
    """The file is not an index this code can read, or a conversion of one
    did not produce every pair. The file on disk is left as it was."""


@dataclass(frozen=True)
class Selection:
    """What coverage has to say about a set of changed files.

    ``tests`` is a SHORTLIST to union with the other selectors, never a
    replacement for them: ``new_blocks`` and ``unknown_paths`` are precisely the
    cases coverage cannot speak to, and they are surfaced rather than folded
    into an empty answer that would look like "nothing to run".
    """

    tests: frozenset[str]
    changed_blocks: frozenset[BlockKey]
    new_blocks: frozenset[BlockKey]
    unknown_paths: frozenset[str]
    unmeasured: frozenset[str]
    #: Indexed but no longer readable — deleted, moved, or newly unparsable.
    #: Distinct from ``unknown_paths`` (never indexed at all) because the two
    #: warrant opposite responses: nothing is known about an unknown file, while
    #: a file that USED to exist has tests that exercised code which is now
    #: gone, and those are worth running.
    missing_paths: frozenset[str] = frozenset()
    #: Changed or vanished blocks whose statements run at IMPORT (a module
    #: body, a class body outside any function). No test has a coverage row
    #: for those lines, so ``tests`` cannot name who runs them.
    import_time_changed: frozenset[BlockKey] = frozenset()


def _format_of(conn: sqlite3.Connection) -> int:
    """The format a file holds; 0 for a file with no index in it yet."""
    stamped = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if stamped:
        return stamped
    columns = {row[1] for row in conn.execute("PRAGMA table_info(test_block)")}
    return 1 if "test_id" in columns else 0


def open_index(path: Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    """Open the index database, creating or converting it as needed.

    ``timeout`` is how long, in seconds, this connection waits for another
    process's write to finish, from its first statement on (opening stamps the
    format, which is a write). SQLite's own five seconds are the default; a
    caller that stores something large while other checkouts may be doing the
    same passes more.

    A format-1 file is converted first (:func:`convert_format_1`). A format-2
    file gains the run tables where it stands, and a format-2 or format-3
    file the statement-record column. A file stamped with a format
    this code does not know raises :class:`IndexFormatError` and is left
    alone: creating this format's empty tables beside another format's data
    would make every lookup miss.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=timeout)
    found = _format_of(conn)
    if found == 1:
        conn.close()
        convert_format_1(path)
        conn = sqlite3.connect(str(path), timeout=timeout)
        found = _format_of(conn)
    if found not in _OPENED_IN_PLACE:
        conn.close()
        raise IndexFormatError(
            f"{path} holds selection-index format {found}; "
            f"this code reads format {FORMAT}")
    conn.executescript(_SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(test_block)")}
    if "statements" not in columns:
        # An earlier format's table. A nullable column is added to the schema
        # alone: no row is rewritten, and every pair reads as "no record".
        conn.execute("ALTER TABLE test_block ADD COLUMN statements BLOB")
    conn.execute(f"PRAGMA user_version = {FORMAT}")
    conn.commit()
    return conn


def _sibling(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


def _free_backup_name(path: Path) -> Path:
    """``<name>.format-1``, or the first ``<name>.format-1.N`` not taken."""
    first = _sibling(path, ".format-1")
    candidate, n = first, 1
    while candidate.exists():
        n += 1
        candidate = _sibling(first, f".{n}")
    return candidate


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _write_format_2(source: Path, work: Path) -> None:
    """Write ``source``'s (format 1) content to a new file ``work`` in format 2.

    One pass in SQL, reading ``source`` through ATTACH and writing nothing to
    it. Names get their numbers in sorted order, so the tests of one test file
    and the blocks of one source file are neighbours in both pair indexes.
    """
    with closing(sqlite3.connect(str(work))) as new:
        new.executescript(_SCHEMA)
        new.execute("ATTACH DATABASE ? AS old", (str(source),))
        new.execute(
            "INSERT INTO file (path) "
            "SELECT path FROM old.block UNION SELECT path FROM old.test_block")
        new.execute(
            "INSERT INTO block (file, name, digest) "
            "SELECT f.id, b.name, b.digest FROM old.block b "
            "JOIN file f ON f.path = b.path")
        # A pair whose block has no ``block`` row still needs a number.
        new.execute(
            "INSERT OR IGNORE INTO block (file, name, digest) "
            "SELECT DISTINCT f.id, tb.name, '' FROM old.test_block tb "
            "JOIN file f ON f.path = tb.path")
        new.execute(
            "INSERT INTO test (test_id) "
            "SELECT DISTINCT test_id FROM old.test_block")
        new.execute(_COPY_PAIRS)
        want = new.execute("SELECT count(*) FROM old.test_block").fetchone()[0]
        got = new.execute("SELECT count(*) FROM test_block").fetchone()[0]
        if got != want:
            raise IndexFormatError(
                f"converting {source} kept {got} of {want} test-block pairs; "
                "the format-1 file is unchanged")
        new.execute(
            "INSERT INTO unmeasured_test (test_id) "
            "SELECT test_id FROM old.unmeasured_test")
        new.execute(
            "INSERT INTO meta (key, value) SELECT key, value FROM old.meta")
        # Tables other modules keep in this file (the read evidence of
        # prose_only), copied as they stand: tables first, then their indexes.
        others = new.execute(
            "SELECT type, name, tbl_name, sql FROM old.sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type != 'table'").fetchall()
        for kind, name, table, sql in others:
            if table in _OWN_TABLES:
                continue
            new.execute(sql)
            if kind == "table":
                # The one identifier comes from the source file's own schema
                # and is quoted; nothing else is interpolated.
                q = _quoted(name)
                copy = f"INSERT INTO main.{q} SELECT * FROM old.{q}"  # noqa: S608  # nosec B608
                new.execute(copy)
        new.execute(f"PRAGMA user_version = {FORMAT}")
        new.commit()
        new.execute("DETACH DATABASE old")


def convert_format_1(path: Path) -> Optional[Path]:
    """Rewrite a format-1 index at ``path`` as format 2, keeping the old file.

    Returns the path the format-1 file was kept under (``<name>.format-1``, or
    ``.format-1.N`` when that is taken), or ``None`` when ``path`` was not
    format 1 by the time this call held the lock. No test is run: format 1
    holds every fact format 2 needs.

    THE NEW FILE IS WRITTEN BESIDE THE OLD ONE AND SWAPPED IN. Until the swap
    ``path`` is the untouched format-1 file, so a conversion that fails or is
    killed loses nothing, and the next open starts it again. The swap is a hard
    link (the backup name) followed by an atomic replace, so ``path`` exists at
    every instant.

    ONE CONVERTER AT A TIME, by ``flock`` on ``<name>.lock``. An opener that
    waited for the lock re-reads the format under it: converting the file a
    winner has just swapped in would move the NEW index over the backup. The
    lock file is removed only once ``path`` is no longer format 1, because from
    then on nobody takes the lock; removing it after a failed conversion would
    let a waiter and a newcomer hold two different lock files at once.

    A process that is writing to the format-1 file while it is being converted
    loses those writes. smart-test folds a run under its own lock, and this is
    called from inside that, so the case needs two tools run by hand at once.
    """
    path = Path(path)
    lock_path = _sibling(path, ".lock")
    work = _sibling(path, ".converting")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with closing(sqlite3.connect(str(path))) as probe:
            still_format_1 = _format_of(probe) == 1
        kept: Optional[Path] = None
        if still_format_1:
            # A killed conversion leaves the work file and perhaps its journal;
            # SQLite would replay a stale journal into the new work file.
            leftovers = [_sibling(work, s) for s in ("", "-journal", "-wal", "-shm")]
            for leftover in leftovers:
                leftover.unlink(missing_ok=True)
            try:
                _write_format_2(path, work)
                kept = _free_backup_name(path)
                kept.hardlink_to(path)
                work.replace(path)
            finally:
                for leftover in leftovers:
                    leftover.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)
        return kept


def _numbered(
    cur: sqlite3.Cursor, insert: str, select: str, names: Iterable[str],
) -> dict[str, int]:
    """Store each name once and return its number."""
    wanted = sorted(set(names))
    cur.executemany(insert, [(n,) for n in wanted])
    return {n: cur.execute(select, (n,)).fetchone()[0] for n in wanted}


def _fill_temp(cur: sqlite3.Cursor, table: str, ids: Iterable[int]) -> None:
    """(Re)fill ``temp.<table>`` with ``ids``. The name is one of this
    module's own literals."""
    cur.execute(f"CREATE TEMP TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY)")
    cur.execute(f"DELETE FROM temp.{table}")  # noqa: S608  # nosec B608
    cur.executemany(
        f"INSERT OR IGNORE INTO temp.{table} (id) VALUES (?)",  # noqa: S608  # nosec B608
        [(i,) for i in ids])


def store_edges(
    conn: sqlite3.Connection,
    edges: Mapping[str, Iterable[BlockKey]],
    *,
    digests: Optional[Mapping[str, Mapping[str, str]]] = None,
    measured: Optional[Iterable[str]] = None,
    statements: Optional[Mapping[str, Mapping[BlockKey, bytes]]] = None,
) -> None:
    """Replace each test's edges into the measured files; record ``digests``.

    ``edges`` maps a test id to the ``(path, block name)`` keys it executed.
    ``measured`` is the list of source files the run measured. A test named in
    ``edges`` has its earlier edges into THOSE files removed first (semantic 1
    of the module docstring); its edges into any other file, and every edge of
    a test not named, stay as they were. With no ``measured`` list the files
    named in ``edges`` and ``digests`` are the scope, so a caller cannot remove
    an edge into a file it does not mention.

    ``digests`` maps a path to ``{block name: digest}`` and may name blocks no
    test executed. A block that appears only in ``edges`` is stored with an
    empty digest.

    ``statements`` maps a test id to ``{key: bitmap}``: which statements of
    that block the test ran (:func:`_int_bitmap`; semantic 5 of the module
    docstring). A pair it does not name is stored with NO record, which is
    not the same as an empty one. A block whose stored digest is REPLACED by a
    different one here has the records of all its pairs dropped first: they
    number the statements of code that is no longer there. The pairs this
    call writes get their new records afterwards.

    Does not commit: :func:`update_from_run` stores a run's edges, its
    unmeasured tests, its run record and the full-run marker in one
    transaction.
    """
    digests = digests or {}
    keys = {key for per_test in edges.values() for key in per_test}
    named = {*digests, *(path for path, _ in keys)}
    scope = named if measured is None else {*measured, *named}
    cur = conn.cursor()
    file_ids = _numbered(
        cur, "INSERT OR IGNORE INTO file (path) VALUES (?)",
        "SELECT id FROM file WHERE path = ?", scope)
    moved = [
        (block_id, stored)
        for path, per_name in digests.items()
        for block_id, name, stored in cur.execute(
            "SELECT id, name, digest FROM block WHERE file = ?",
            (file_ids[path],)).fetchall()
        if stored and name in per_name and per_name[name] != stored]
    for block_id, superseded in moved:
        _keep_records(cur, block_id, superseded)
    cur.executemany(_DROP_RECORDS_OF_BLOCK, [(block_id,) for block_id, _ in moved])
    cur.executemany(
        "INSERT INTO block (file, name, digest) VALUES (?, ?, ?) "
        "ON CONFLICT (file, name) DO UPDATE SET digest = excluded.digest",
        [(file_ids[path], name, digest)
         for path, per_name in digests.items()
         for name, digest in per_name.items()])
    cur.executemany(
        "INSERT OR IGNORE INTO block (file, name, digest) VALUES (?, ?, '')",
        sorted((file_ids[path], name) for path, name in keys))
    block_ids: dict[BlockKey, int] = {}
    scope_blocks: list[int] = []
    for path in sorted(scope):
        for name, block_id in cur.execute(
                "SELECT name, id FROM block WHERE file = ?", (file_ids[path],)):
            block_ids[(path, name)] = block_id
            scope_blocks.append(block_id)
    test_ids = _numbered(
        cur, "INSERT OR IGNORE INTO test (test_id) VALUES (?)",
        "SELECT id FROM test WHERE test_id = ?", edges)
    _fill_temp(cur, "scope_block", scope_blocks)
    _fill_temp(cur, "scope_test", test_ids.values())
    cur.execute(_DELETE_MEASURED_PAIRS)
    records = statements or {}
    pairs: list[tuple[int, int, Optional[bytes]]] = [
        (test_ids[test_id], block_ids[key], records.get(test_id, {}).get(key))
        for test_id, per_test in edges.items() for key in set(per_test)]
    pairs.sort(key=lambda pair: (pair[0], pair[1]))
    cur.executemany(
        "INSERT INTO test_block (test, block, statements) VALUES (?, ?, ?) "
        "ON CONFLICT (test, block) DO UPDATE SET statements = excluded.statements",
        pairs)


def _keep_records(cur: sqlite3.Cursor, block_id: int, digest: str) -> None:
    """Copy block ``block_id``'s records, about to be dropped, to
    ``kept_record`` under the version they were made for (the block at
    ``digest``), and forget the versions beyond :data:`KEPT_VERSIONS`
    (semantic 5 of the module docstring)."""
    newest = cur.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM kept_version WHERE block = ?",
        (block_id,)).fetchone()[0]
    cur.execute(
        "INSERT INTO kept_version (block, digest, seq) VALUES (?, ?, ?) "
        "ON CONFLICT (block, digest) DO UPDATE SET seq = excluded.seq",
        (block_id, digest, int(newest) + 1))
    version = cur.execute(_KEPT_VERSION, (block_id, digest)).fetchone()[0]
    cur.execute(_KEEP_RECORDS_OF_BLOCK, (version, block_id))
    forgotten = cur.execute(
        "SELECT id FROM kept_version WHERE block = ? ORDER BY seq DESC "
        "LIMIT -1 OFFSET ?", (block_id, KEPT_VERSIONS)).fetchall()
    cur.executemany("DELETE FROM kept_record WHERE version = ?", forgotten)
    cur.executemany("DELETE FROM kept_version WHERE id = ?", forgotten)


def measured_tests(conn: sqlite3.Connection) -> frozenset[str]:
    """Test ids with at least one coverage edge."""
    return frozenset(r[0] for r in conn.execute(_MEASURED))


def touching_tests(
    conn: sqlite3.Connection, paths: Iterable[str],
) -> frozenset[str]:
    """Test ids observed executing ANY block of ``paths`` (as stored)."""
    return frozenset(
        r[0] for path in sorted(set(paths))
        for r in conn.execute(_TOUCHING, (path,)))


def _int_bitmap(value: int) -> bytes:
    """An integer with bit *i* set for ordinal *i*, as a bitmap: bit *i* of
    byte *i // 8*, lowest bit first (coverage's own ``numbits`` layout). As
    long as the highest ordinal needs and no longer, so zero is ``b""``."""
    return value.to_bytes((value.bit_length() + 7) // 8, "little")


def _ordinals(bitmap: Optional[bytes]) -> Optional[frozenset[int]]:
    """The ordinals a bitmap holds; ``None`` (no record) stays ``None``."""
    if bitmap is None:
        return None
    return frozenset(_numbits_to_linenos(bitmap))


def statement_records(
    conn: sqlite3.Connection, path: str, block: str,
) -> dict[str, Optional[frozenset[int]]]:
    """For each test paired with ``block`` of ``path``: the ordinals of the
    statements it ran, or ``None`` when the pair has no record.

    The ordinals number the block AS STORED (its digest in the index). A
    caller holding source must check that digest first, which is what
    :func:`tests_of_line` does.
    """
    return {test_id: _ordinals(bitmap)
            for test_id, bitmap in conn.execute(_RECORDS_OF_BLOCK, (path, block))}


def statement_records_at(
    conn: sqlite3.Connection, path: str, block: str, digest: str,
) -> Optional[dict[str, Optional[frozenset[int]]]]:
    """:func:`statement_records` of ``block`` AS IT WAS under ``digest``.

    For each test paired with the block today, and each test with a kept
    record of that version: the ordinals it ran in that version, or ``None``
    when it has no record of it. Returns ``None`` when the index holds
    nothing of that version at all (it is neither the stored digest nor a
    kept one): "no test has a record" and "this version is unknown here" must
    not look alike.
    """
    found = conn.execute(
        "SELECT b.id, b.digest FROM file f JOIN block b ON b.file = f.id "
        "WHERE f.path = ? AND b.name = ?", (path, block)).fetchone()
    if found is None or not digest:
        return None
    block_id, stored = int(found[0]), str(found[1])
    current = statement_records(conn, path, block)
    version = conn.execute(_KEPT_VERSION, (block_id, digest)).fetchone()
    kept = {} if version is None else {
        str(test_id): _ordinals(bitmap)
        for test_id, bitmap in conn.execute(_KEPT_RECORDS, (version[0],))}
    if stored == digest:
        # A record made under this digest and kept, for a test whose pair
        # now has none (it ran neither then nor since).
        return {test: (ran if ran is not None else kept.get(test))
                for test, ran in {**dict.fromkeys(kept), **current}.items()}
    if version is None:
        return None
    return {**dict.fromkeys(current), **kept}


@dataclass(frozen=True)
class RecordCounts:
    """How much of the index has statement records (:func:`record_counts`)."""

    #: ``test -> block`` pairs in the index.
    pairs: int
    #: Pairs with a record, empty or not.
    recorded: int
    #: Pairs whose record names at least one statement.
    with_statements: int
    #: Bytes of bitmap stored.
    bitmap_bytes: int

    def describe(self) -> str:
        """One line per count, for ``coverage-select line-records``."""
        share = f" ({self.recorded * 100 // self.pairs}%)" if self.pairs else ""
        return "\n".join((
            f"test -> block pairs:            {self.pairs:,}",
            f"  with a statement record:      {self.recorded:,}{share}",
            f"    naming at least one:        {self.with_statements:,}",
            f"    empty (import-time blocks): {self.recorded - self.with_statements:,}",
            f"  with no record:               {self.pairs - self.recorded:,}",
            f"bitmap bytes:                   {self.bitmap_bytes:,}",
        ))


def record_counts(conn: sqlite3.Connection) -> RecordCounts:
    """Count pairs and records. Reads every pair: a report, not a lookup."""
    pairs, recorded, with_statements, size = conn.execute(_RECORD_COUNTS).fetchone()
    return RecordCounts(int(pairs), int(recorded), int(with_statements), int(size))


#: What each :class:`LineTests` status other than ``recorded`` means, in the
#: words ``describe`` prints.
_LINE_STATUS_MEANING = {
    "import_time": "runs when the file is imported, under no test; "
                   "tests observed running anything in the file:",
    "block_changed": "its code has changed since the index last saw it; "
                     "no record applies until its tests run again",
    "block_new": "the index has never seen this block",
    "no_statement": "not a statement (blank, comment, def/class header, "
                    "or past the end of the file)",
    "unknown_file": "the index holds nothing about this file",
    "unreadable": "the file is missing or does not parse",
}


@dataclass(frozen=True)
class LineTests:
    """What the index knows about who ran one line (:func:`tests_of_line`).

    ``status`` is one of:

    - ``recorded``: the line is statement ``statement`` of ``block``, whose
      code is what the index last saw; the three sets below are the answer;
    - ``import_time``: the line runs when the file is imported, under no
      test; ``ran`` is every test observed running anything in the file;
    - ``block_changed``: ``block`` has been edited since the index last saw
      it, so its records number other statements and none is read;
    - ``block_new``: the index has never seen ``block``;
    - ``no_statement``: the line is blank, a comment, or the header of a
      ``def``/``class`` (see ``block_hash``), or is past the end of the file;
    - ``unknown_file``: the index has no block of this file;
    - ``unreadable``: the file is missing or does not parse.
    """

    path: str
    line: int
    status: str
    block: Optional[str] = None
    statement: Optional[int] = None
    #: Tests whose record shows the statement ran.
    ran: frozenset[str] = frozenset()
    #: Tests with a record for the block that did NOT run the statement.
    reached_without: frozenset[str] = frozenset()
    #: Tests paired with the block that have no record: they may have.
    unrecorded: frozenset[str] = frozenset()

    def as_dict(self) -> dict[str, object]:
        """Plain data, test ids sorted, for ``--json``."""
        return {
            "path": self.path, "line": self.line, "status": self.status,
            "block": self.block, "statement": self.statement,
            "ran": sorted(self.ran),
            "reached_without": sorted(self.reached_without),
            "unrecorded": sorted(self.unrecorded),
        }

    def describe(self) -> str:
        """The answer as text, for ``coverage-select line-tests``."""
        where = f"{self.path}:{self.line}"
        if self.block is not None:
            where += f"  statement {self.statement} of {self.block}"
        if self.status != "recorded":
            lines = [f"{where}: {_LINE_STATUS_MEANING[self.status]}"]
            lines += [f"  {test}" for test in sorted(self.ran)]
            return "\n".join(lines)
        lines = [where]
        for label, tests in (
            ("ran it", self.ran),
            ("reached the block without running it", self.reached_without),
            ("reached the block, no statement record", self.unrecorded),
        ):
            lines.append(f"{label}: {len(tests)} test(s)")
            lines += [f"  {test}" for test in sorted(tests)]
        return "\n".join(lines)


def tests_of_line(
    conn: sqlite3.Connection, path: str, line: int, *, source: Optional[str] = None,
) -> LineTests:
    """Which tests ran line ``line`` of ``path``, as far as the index knows.

    The line is resolved against the file as it is NOW (or ``source``, when
    given): which block, which statement of it, and whether that block's
    digest is still the one the index holds. Only then is a record read, so a
    record is never applied to code it was not made from, and an edit the
    digest ignores (a comment, code added above) changes no answer.
    """
    try:
        text = Path(path).read_text() if source is None else source
        blocks = blocks_for_source(path, text)
    except (OSError, SyntaxError, ValueError):
        return LineTests(path, line, "unreadable")
    found = statement_owner(blocks).get(line)
    if found is None:
        return LineTests(path, line, "no_statement")
    name, ordinal = found
    block: Block = next(b for b in blocks if b.name == name)
    stored = dict(conn.execute(
        "SELECT b.name, b.digest FROM file f JOIN block b ON b.file = f.id "
        "WHERE f.path = ?", (path,)))
    if not stored:
        return LineTests(path, line, "unknown_file", name, ordinal)
    if name not in stored:
        return LineTests(path, line, "block_new", name, ordinal)
    if stored[name] != block.digest:
        return LineTests(path, line, "block_changed", name, ordinal)
    records = statement_records(conn, path, name)
    if block.import_time:
        return LineTests(path, line, "import_time", name, ordinal,
                         ran=frozenset(records))
    return LineTests(
        path, line, "recorded", name, ordinal,
        ran=frozenset(t for t, got in records.items() if got is not None and ordinal in got),
        reached_without=frozenset(
            t for t, got in records.items() if got is not None and ordinal not in got),
        unrecorded=frozenset(t for t, got in records.items() if got is None))


def _strip_phase(context: str) -> str:
    """``test|setup`` -> ``test``. pytest-cov records a context per phase."""
    return context.rsplit("|", 1)[0] if "|" in context else context


def without_xdist_group(node_id: str) -> str:
    """``file::test@group`` -> ``file::test``: the id pytest itself knows.

    Under ``--dist loadgroup`` pytest-xdist renames, on its workers, every
    test marked ``xdist_group``: ``@<group>`` is appended to the node id.
    Coverage contexts, the junit report and the recorder's per-test keys are
    written after that; the recorder's list of expected tests (taken at
    collection) and every run without xdist are not. Two spellings of one
    test never join, and the suffixed one is not an id pytest accepts, so
    every way into the stores passes through here.

    The suffix is an ``@`` after the last ``]`` of a parametrized id, or after
    the last ``::`` of a plain one, with a non-empty name behind it (xdist
    appends nothing for an empty group). An ``@`` inside a parameter, or in a
    directory name, is before that point and stays. NOT HANDLED: a group whose
    name contains ``]``; its tests keep two spellings.
    """
    head, sep, group = node_id.rpartition("@")
    if not sep or not group or "]" in group or "::" in group or "::" not in head:
        return node_id
    return head


#: pytest-cov's phases that are not the test's own call: what runs in them is
#: fixture code, and a fixture can run for every test.
_FIXTURE_PHASES = ("|setup", "|teardown")

#: What a coverage context starts with when it belongs to no test: to work
#: done once and shared (semantic 6 of the module docstring). pytest-cov names
#: a context ``<node id>|<phase>`` and a node id starts with a path, so no
#: test's context starts with the separator.
SHARED_CONTEXT = "|shared|"


def shared_context(kind: str, key: str) -> str:
    """The coverage context of one piece of shared work: ``kind`` says what it
    is (``fixture``), ``key`` which one. The recorder plugin spells the same
    name; ``tests/test_shared_fixture_contexts.py`` holds the two together."""
    return f"{SHARED_CONTEXT}{kind}|{key}"


def is_shared_context(context: str) -> bool:
    return context.startswith(SHARED_CONTEXT)


@dataclass(frozen=True)
class SharedWork:
    """What one fold did with the run's shared-work contexts."""

    #: Shared contexts that had a row in the measured files.
    contexts: int
    #: Tests that were given rows of at least one of them.
    inheriting_tests: int
    #: Contexts no test of the run is recorded using. Their rows were stored
    #: as code run under no test.
    unused: tuple[str, ...]


def normalize_test_id(node_id: str, repo_root: Optional[Path]) -> str:
    """Rewrite a pytest node id to be repo-relative, or leave it untouched.

    Node ids are relative to pytest's ROOTDIR, which varies by invocation: a run
    confined to one package makes that package the rootdir (several of ours
    carry ``[tool.pytest.ini_options]``), while a run spanning packages uses the
    repo. The same test therefore has two spellings, and storing both fragments
    the index into rows that never join — the same failure class as a
    ``|setup`` phase suffix left on a context (see ``_strip_phase``), which
    makes no context join at all.

    Resolution is by EXISTENCE, not by string surgery: a candidate is accepted
    only if the file is actually there. If more than one package could claim it
    the id is returned unchanged, because picking one would silently attach a
    test to the wrong file, and an unjoined row is a recoverable under-selection
    where a wrong row is a lie.
    """
    node_id = without_xdist_group(node_id)
    if repo_root is None or "::" not in node_id:
        return node_id
    file_part, _, rest = node_id.partition("::")
    if (repo_root / file_part).exists():
        return node_id
    candidates = [
        pkg for pkg in sorted((repo_root / "packages").glob("*"))
        if (pkg / file_part).exists()
    ]
    if len(candidates) != 1:
        return node_id
    rel = candidates[0].relative_to(repo_root)
    return f"{rel}/{file_part}::{rest}"


def _numbits_to_linenos(numbits: bytes) -> Iterable[int]:
    for byte_index, byte in enumerate(numbits):
        for bit in range(8):
            if byte & (1 << bit):
                yield byte_index * 8 + bit


def update_from_run(
    conn: sqlite3.Connection,
    coverage_db: Path,
    *,
    ran_tests: Optional[Iterable[str]] = None,
    git_sha: Optional[str] = None,
    full_run: bool = False,
    repo_root: Optional[Path] = None,
    scope_complete: bool = False,
    shared_users: Optional[Mapping[str, Iterable[str]]] = None,
) -> SharedWork:
    """Fold one run's coverage contexts into the index.

    Blocks are re-hashed from the CURRENT source. That is correct only if this
    is called promptly after the run that produced ``coverage_db`` — the
    database holds line numbers from the tree as it was, and a file edited in
    between would map its lines onto the wrong blocks. Call it immediately
    after the run, which is what smart-test does.

    WHAT THE RUN MEASURED is read from the coverage database's own file list,
    never assumed: edges are replaced inside that list only
    (:func:`store_edges`), and the list is stored as the run's record
    (semantic 4 of the module docstring). ``scope_complete`` is the caller's
    statement that coverage measured every package of the checkout; a
    ``full_run`` is one by definition. ``repo_root`` names the checkout the
    run belongs to.

    ``shared_users`` maps a test of this run to the shared-work contexts it
    used (:func:`hypergumbo_core.prose_only.shared_work_used` reads it from
    the recorder's files). The rows of such a context go to every test that
    used it; rows of a context no test is recorded using, and every shared
    row when the mapping is absent, are stored as code run under no test
    (semantic 6 of the module docstring). Returns what was done with them.
    """
    if ran_tests is not None:
        ran_tests = [without_xdist_group(test) for test in ran_tests]
    src = sqlite3.connect(f"file:{coverage_db}?mode=ro", uri=True)
    try:
        rows = src.execute(
            "SELECT c.context, f.path, lb.numbits FROM line_bits lb "
            "JOIN context c ON c.id = lb.context_id "
            "JOIN file f ON f.id = lb.file_id"
        ).fetchall()
        measured = [r[0] for r in src.execute("SELECT path FROM file")]
    finally:
        src.close()

    owners: dict[str, dict[int, str]] = {}
    statement_owners: dict[str, dict[int, tuple[str, int]]] = {}
    digests: dict[str, dict[str, str]] = {}
    import_kind: dict[str, set[str]] = {}

    def owner_for(path: str) -> dict[int, str]:
        if path not in owners:
            try:
                blocks = blocks_for_source(path, Path(path).read_text())
            except (OSError, SyntaxError):  # pragma: no cover - removed source
                blocks = []
            owners[path] = line_owner(blocks)
            statement_owners[path] = statement_owner(blocks)
            digests[path] = {b.name: b.digest for b in blocks}
            import_kind[path] = {b.name for b in blocks if b.import_time}
        return owners[path]

    edges: dict[str, set[BlockKey]] = {}
    # The tests with a row from their OWN call (semantic 3 of the module
    # docstring): the only ones this run has said anything about.
    spoke: set[str] = set()
    # Which statements of each block each test ran, as an integer with bit i
    # set for ordinal i. The phases of one test (setup, run, teardown) are
    # separate contexts and are OR-ed into one record.
    ran: dict[str, dict[BlockKey, int]] = {}
    import_blocks: dict[str, set[str]] = {}
    # Function blocks a statement of which ran under no test's context.
    at_import: set[BlockKey] = set()
    def under_no_test(path: str, linenos: list[int], names: set[str]) -> None:
        import_blocks.setdefault(path, set()).update(names)
        ran_statements = {statement_owners[path][n][0]
                          for n in linenos if n in statement_owners[path]}
        at_import.update((path, name) for name in ran_statements - import_kind[path])

    def under_test(test_id: str, path: str, linenos: list[int], names: set[str]) -> None:
        edges.setdefault(test_id, set()).update((path, n) for n in names)
        statement_of = statement_owners[path]
        in_this_phase: dict[BlockKey, int] = {}
        for n in linenos:
            found = statement_of.get(n)
            if found is not None:
                key = (path, found[0])
                in_this_phase[key] = in_this_phase.get(key, 0) | (1 << found[1])
        per_test = ran.setdefault(test_id, {})
        for key, bits in in_this_phase.items():
            per_test[key] = per_test.get(key, 0) | bits

    # Rows of work done once and shared, by its context: handed out below.
    shared_rows: dict[str, list[tuple[str, list[int], set[str]]]] = {}
    for context, path, numbits in rows:
        own = owner_for(path)
        linenos = list(_numbits_to_linenos(numbits))
        names = {own[n] for n in linenos if n in own}
        if not names:
            continue
        if context == "":
            under_no_test(path, linenos, names)
        elif is_shared_context(context):
            shared_rows.setdefault(context, []).append((path, linenos, names))
        else:
            test_id = normalize_test_id(_strip_phase(context), repo_root)
            if not context.endswith(_FIXTURE_PHASES):
                spoke.add(test_id)
            under_test(test_id, path, linenos, names)

    # Shared work belongs to every test recorded using it. It never makes a
    # test one that spoke: the test's own call ran none of it.
    users_of: dict[str, set[str]] = {}
    for test, used in (shared_users or {}).items():
        for context in used:
            users_of.setdefault(context, set()).add(normalize_test_id(test, repo_root))
    inheriting: set[str] = set()
    unused: list[str] = []
    for context, found_rows in sorted(shared_rows.items()):
        users = users_of.get(context)
        if not users:
            # Nobody is recorded using it (a worker that was killed wrote no
            # record). Not dropped and not given to a guess: the selector
            # does not narrow on a function that runs under no test.
            unused.append(context)
            for path, linenos, names in found_rows:
                under_no_test(path, linenos, names)
            continue
        inheriting.update(users)
        for test_id in sorted(users):
            for path, linenos, names in found_rows:
                under_test(test_id, path, linenos, names)

    # Import-time blocks run whenever the file is imported, so they belong to
    # every test that executes anything in that file. See §2 of the module
    # docstring for why this is an implication rather than an approximation.
    for keys in edges.values():
        for path in {p for p, _ in keys}:
            keys.update((path, n) for n in import_blocks.get(path, ()))

    # Every pair of this run gets a record. A pair with no statement of its
    # own under the test (an import-time block attached just above, or a
    # block of which only a nested def's header ran) gets an EMPTY one: the
    # run did measure it, and the test ran none of its statements.
    statements = {
        test_id: {
            key: _int_bitmap(ran.get(test_id, {}).get(key, 0)) for key in keys}
        for test_id, keys in edges.items()}

    # REPLACE per test, only for tests that appear in THIS run, and only
    # inside the files this run measured.
    store_edges(conn, edges, digests=digests, measured=measured,
                statements=statements)
    cur = conn.cursor()
    _store_at_import(cur, at_import, measured, full_run=full_run)
    # A row from the test's own call is positive evidence at ANY coverage
    # scope, so clearing is always safe.
    cur.executemany(
        "DELETE FROM unmeasured_test WHERE test_id = ?",
        [(t,) for t in sorted(spoke)],
    )
    # Recording a test as unmeasured is the opposite: it is a claim that the
    # test ran and produced NOTHING, which is only meaningful when coverage was
    # watching everything. smart-test's targeted path scopes --cov to the
    # CHANGED SOURCE FILES ONLY, so on an ordinary run most tests legitimately
    # touch nothing in scope. Trusting that would mark most of the suite
    # unmeasured after a single targeted run -- and select_tests EXCLUDES
    # unmeasured tests, so most of the suite would become permanently
    # unselectable while every unit test stayed green.
    if ran_tests is not None and full_run:
        cur.executemany(
            "INSERT OR IGNORE INTO unmeasured_test (test_id) VALUES (?)",
            [(t,) for t in sorted(set(ran_tests) - spoke)],
        )
    if full_run:
        cur.execute(
            "INSERT INTO meta (key, value) VALUES ('last_full_run_sha', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (git_sha or "",),
        )
    checkout = str(repo_root) if repo_root is not None else ""
    # A test with rows from fixtures alone is in the run and is not one of
    # its producers: its record says the run told nothing about it.
    _record_run(
        conn, checkout=checkout, measured=measured, producers=spoke,
        ran={*edges, *(ran_tests or ())},
        complete=(scope_complete or full_run) and ran_tests is not None)
    if repo_root is not None:
        _forget_dead_checkouts(conn, checkout)
    conn.commit()
    return SharedWork(contexts=len(shared_rows), inheriting_tests=len(inheriting),
                      unused=tuple(unused))


def _store_at_import(
    cur: sqlite3.Cursor, at_import: Iterable[BlockKey], measured: Iterable[str],
    *, full_run: bool,
) -> None:
    """Mark the function blocks this run saw execute under no test's context.

    A mark is only ever ADDED by a partial run: it loaded some modules, and a
    function it did not see at import may run at the import of a module it
    never loaded. A full run loaded every module a test loads, so there the
    blocks of the files it measured are marked afresh.
    """
    if full_run:
        cur.executemany(
            "DELETE FROM at_import_block WHERE block IN ("
            "SELECT b.id FROM block b JOIN file f ON f.id = b.file WHERE f.path = ?)",
            [(path,) for path in sorted(set(measured))])
    cur.executemany(
        "INSERT OR IGNORE INTO at_import_block (block) "
        "SELECT b.id FROM block b JOIN file f ON f.id = b.file "
        "WHERE f.path = ? AND b.name = ?", sorted(at_import))


def _tests_of_file(cur: sqlite3.Cursor, test_file: str) -> list[int]:
    """Numbers of the tests the index knows in ``test_file``: an index range
    on the node-id prefix (``;`` is the character after ``:``)."""
    return [r[0] for r in cur.execute(
        "SELECT id FROM test WHERE test_id = ? "
        "UNION ALL SELECT id FROM test WHERE test_id >= ? AND test_id < ?",
        (test_file, f"{test_file}::", f"{test_file}:;"))]


def _record_run(
    conn: sqlite3.Connection,
    *,
    checkout: str,
    measured: Iterable[str],
    producers: Iterable[str],
    ran: Optional[Iterable[str]],
    complete: bool,
) -> None:
    """Make this run the latest run, in ``checkout``, of every test file in it.

    ``producers`` are the tests whose own call left coverage rows; ``ran`` is
    every test known to have run. A test that ran and left no such
    row is recorded with ``rows = 0``: the run says nothing about it, and
    :func:`informed_test_files` holds its file back for that.

    A test FILE's earlier records in this checkout are removed whole, so a
    test that is no longer in the file leaves no record behind to hold the
    file back for good. Runs no test points at any more are deleted.
    """
    produced = set(producers)
    seen = produced | set(ran or ())
    cur = conn.cursor()
    cur.execute("INSERT INTO run (checkout, complete) VALUES (?, ?)",
                (checkout, int(complete)))
    run_id = int(cur.lastrowid or 0)
    file_ids = _numbered(
        cur, "INSERT OR IGNORE INTO file (path) VALUES (?)",
        "SELECT id FROM file WHERE path = ?", measured)
    cur.executemany(
        "INSERT INTO run_file (run, file) VALUES (?, ?)",
        sorted((run_id, file_id) for file_id in file_ids.values()))
    test_ids = _numbered(
        cur, "INSERT OR IGNORE INTO test (test_id) VALUES (?)",
        "SELECT id FROM test WHERE test_id = ?", seen)
    stale = {number for test_file in sorted(files_of(seen))
             for number in _tests_of_file(cur, test_file)}
    _fill_temp(cur, "scope_test", stale)
    cur.execute(
        "DELETE FROM test_run WHERE test IN (SELECT id FROM temp.scope_test) "
        "AND run IN (SELECT id FROM run WHERE checkout = ?)", (checkout,))
    cur.executemany(
        "INSERT INTO test_run (test, run, rows) VALUES (?, ?, ?)",
        sorted((test_ids[t], run_id, int(t in produced)) for t in seen))
    _drop_unreferenced_runs(cur)


def _drop_unreferenced_runs(cur: sqlite3.Cursor) -> None:
    cur.execute("DELETE FROM run WHERE id NOT IN (SELECT run FROM test_run)")
    cur.execute("DELETE FROM run_file WHERE run NOT IN (SELECT id FROM run)")


def _forget_dead_checkouts(conn: sqlite3.Connection, checkout: str) -> None:
    """Remove what the index holds about checkouts that no longer exist.

    A fold used to delete a test's edges wherever they pointed, so the rows of
    a deleted worktree went away as its tests ran elsewhere. They no longer
    do, and worktrees are made and removed all the time. A source file outside
    ``checkout`` that is not on disk belongs to a checkout that is gone: its
    blocks and pairs are removed, and so is a run whose checkout directory is
    gone. A missing SOURCE file of ``checkout`` itself (``packages/*/src``
    under its root) keeps its rows: it is a file the change deleted, and its
    rows are how the tests that ran it are found. Any other missing path under
    the root is a worktree that was nested in this checkout
    (``.claude/worktrees/<name>/packages/...``) and goes like the rest.
    """
    inside = f"{checkout}/"

    def own_source(path: str) -> bool:
        return path.startswith(inside) and _is_coverable(path[len(inside):])

    cur = conn.cursor()
    dead = [file_id for file_id, path in cur.execute("SELECT id, path FROM file")
            if not own_source(path) and not Path(path).exists()]
    if dead:
        _fill_temp(cur, "dead_file", dead)
        cur.execute(
            "DELETE FROM test_block WHERE block IN ("
            "SELECT b.id FROM temp.dead_file d JOIN block b ON b.file = d.id)")
        cur.execute(
            "DELETE FROM at_import_block WHERE block IN ("
            "SELECT b.id FROM temp.dead_file d JOIN block b ON b.file = d.id)")
        dead_versions = cur.execute(_DEAD_VERSIONS).fetchall()
        cur.executemany("DELETE FROM kept_record WHERE version = ?", dead_versions)
        cur.executemany("DELETE FROM kept_version WHERE id = ?", dead_versions)
        cur.execute("DELETE FROM run_file WHERE file IN (SELECT id FROM temp.dead_file)")
        cur.execute("DELETE FROM block WHERE file IN (SELECT id FROM temp.dead_file)")
        cur.execute("DELETE FROM file WHERE id IN (SELECT id FROM temp.dead_file)")
        cur.execute("DELETE FROM temp.dead_file")
    gone = [(c,) for (c,) in cur.execute("SELECT DISTINCT checkout FROM run").fetchall()
            if c != checkout and not Path(c).is_dir()]
    if gone:
        cur.executemany(
            "DELETE FROM test_run WHERE run IN (SELECT id FROM run WHERE checkout = ?)",
            gone)
        _drop_unreferenced_runs(cur)


def changed_blocks(
    conn: sqlite3.Connection, base_sources: Mapping[str, Optional[str]],
) -> tuple[frozenset[BlockKey], frozenset[BlockKey]]:
    """``(changed, new)`` block keys, diffing each path against a SUPPLIED base.

    THE BASE COMES FROM THE CALLER, NOT FROM THE INDEX, and that distinction is
    the whole correctness of this function. The digests stored in the index are
    rewritten after every run, so diffing against them moves the baseline every
    time: once a run has stored the working tree, the next comparison is the
    working tree against itself, which finds nothing and selects zero tests.
    That answers "what changed since I last looked" when the question is "what
    changed in this commit".

    So change detection is git's job (``git show <base>:<path>``) and this
    function only diffs. ``None`` means the path did not exist at the base, i.e.
    the commit added it: every block is NEW and therefore has no test history.
    A path that is now unreadable had all of its base blocks removed, so they
    all count as changed — those tests exercised code that is gone.
    """
    changed, new, _import_time = _diff_blocks(base_sources)
    return changed, new


def _diff_blocks(
    base_sources: Mapping[str, Optional[str]],
    ran_at_import: frozenset[BlockKey] = frozenset(),
) -> tuple[frozenset[BlockKey], frozenset[BlockKey], frozenset[BlockKey]]:
    """``(changed, new, import-time changed)``; see :func:`changed_blocks`.

    The third set is the subset of ``changed`` whose statements run at import:
    by the block's kind (a module or class body), or because it is one of
    ``ran_at_import``, the function blocks a run saw execute under no test's
    context (:func:`blocks_run_at_import`). A vanished block is judged by
    what it was at the base, since the current source no longer says.
    """
    changed: set[BlockKey] = set()
    new: set[BlockKey] = set()
    import_time: set[BlockKey] = set()
    for path, base_src in base_sources.items():
        try:
            current = {b.name: b
                       for b in blocks_for_source(path, Path(path).read_text())}
        except (OSError, SyntaxError):
            current = {}
        if base_src is None:
            new.update((path, name) for name in current)
            continue
        try:
            base = {b.name: b for b in blocks_for_source(path, base_src)}
        except SyntaxError:  # pragma: no cover - base commit did not parse
            continue
        for name, block in current.items():
            if name not in base:
                new.add((path, name))
            elif base[name].digest != block.digest:
                changed.add((path, name))
                if block.import_time or (path, name) in ran_at_import:
                    import_time.add((path, name))
        # Vanished blocks are the ONLY signal for a rename, move or deletion,
        # because children are removed from a parent's digest entirely.
        for name in set(base) - set(current):
            changed.add((path, name))
            if base[name].import_time or (path, name) in ran_at_import:
                import_time.add((path, name))
    return frozenset(changed), frozenset(new), frozenset(import_time)


def blocks_run_at_import(
    conn: sqlite3.Connection, paths: Iterable[str],
) -> frozenset[BlockKey]:
    """The function blocks of ``paths`` that some run saw execute under no
    test's context: at import or at collection (semantic 2 of the module
    docstring)."""
    found: set[BlockKey] = set()
    for path in paths:
        found.update((path, str(name)) for (name,) in conn.execute(
            "SELECT b.name FROM at_import_block a JOIN block b ON b.id = a.block "
            "JOIN file f ON f.id = b.file WHERE f.path = ?", (path,)))
    return frozenset(found)


def functions_run_at_import(conn: sqlite3.Connection) -> dict[str, frozenset[str]]:
    """Every marked function block, by file: ``{path: names}``
    (:func:`blocks_run_at_import` for the whole index). The import graph
    reads it: an import inside such a function runs when its file is loaded
    (:mod:`hypergumbo_core.import_closure`)."""
    found: dict[str, set[str]] = {}
    for path, name in conn.execute(
            "SELECT f.path, b.name FROM at_import_block a JOIN block b ON b.id = a.block "
            "JOIN file f ON f.id = b.file"):
        found.setdefault(str(path), set()).add(str(name))
    return {path: frozenset(names) for path, names in found.items()}


def select_tests(
    conn: sqlite3.Connection, base_sources: Mapping[str, Optional[str]],
) -> Selection:
    """Tests attached to blocks that changed relative to the supplied base."""
    unknown = {
        p for p in base_sources
        if not conn.execute(
            "SELECT 1 FROM block b JOIN file f ON f.id = b.file "
            "WHERE f.path = ? LIMIT 1", (p,)).fetchone()
    }
    changed, new, import_time = _diff_blocks(
        base_sources, blocks_run_at_import(conn, base_sources))
    tests: set[str] = set()
    for path, name in changed:
        tests.update(r[0] for r in conn.execute(_TESTS_OF_BLOCK, (path, name)))
    unmeasured = {r[0] for r in conn.execute(
        "SELECT test_id FROM unmeasured_test")}
    missing = {p for p in base_sources
               if p not in unknown and not Path(p).exists()}
    return Selection(
        tests=frozenset(tests - unmeasured),
        changed_blocks=changed,
        new_blocks=new,
        unknown_paths=frozenset(unknown),
        unmeasured=frozenset(unmeasured),
        missing_paths=frozenset(missing),
        import_time_changed=import_time,
    )


# ── Node ids to run-set files ───────────────────────────────────────────────
#
# These live HERE rather than in selection_shadow because they convert the
# INDEX's output into the run set's vocabulary, and they have three
# consumers: the shadow comparison, the union, and narrowing. Importing them
# from `selection_shadow` would make narrowing appear to depend on the shadow,
# which it does not.


def files_of(node_ids: Iterable[str]) -> frozenset[str]:
    """Reduce node ids to the files that contain them."""
    return frozenset(n.split("::")[0] for n in node_ids)


def rebase_to_repo(node_ids: Iterable[str], repo_root: Path) -> frozenset[str]:
    """Rewrite absolute node ids as repo-relative ones.

    The index stores absolute paths; every other selector in smart-test speaks
    repo-relative. Comparing or unioning across the two spellings makes the same
    file look like two different files, so every consumer rebases first.

    The separator is included in the prefix on purpose: without it a repo at
    ``/x/repo`` would also strip ``/x/repo-backup``.
    """
    prefix = f"{repo_root}/"
    return frozenset(
        n[len(prefix):] if n.startswith(prefix) else n for n in node_ids
    )


def selectable_test_files(
    node_ids: Iterable[str], repo_root: Path,
) -> frozenset[str]:
    """Repo-relative test FILES a run may safely be widened with.

    Two narrowings, both required for widening a run safely:

    * node ids reduce to files, because the run set is a list of files;
    * files that do not exist are DROPPED. The index is persistent and
      out-of-repo, nothing prunes it when a test is renamed or deleted, and
      pytest treats a missing path as a collection error rather than a skip.
      Widening a run with a stale entry would redden it, which is the opposite
      of "can only add tests".
    """
    return frozenset(
        rel for rel in files_of(rebase_to_repo(node_ids, repo_root))
        if (repo_root / rel).is_file()
    )


def touching_test_files(
    conn: sqlite3.Connection, paths: Sequence[str], repo_root: Path,
) -> frozenset[str]:
    """Test FILES observed executing ANY block of ``paths`` (absolute).

    NAMED THIS WAY DELIBERATELY. The obvious name, ``tests_touching``, starts
    with ``test`` and so is COLLECTED AS A TEST by pytest in any test module that
    imports it, failing collection with ``fixture 'conn' not found``, an error
    that names nothing about its real cause.

    THE KEEP SET FOR NARROWING, and deliberately wider than the changed-block
    selection. The local coverage gate is whole-file
    (``coverage report --include=<changed files> --fail-under=100``), so keeping
    only the changed blocks' tests leaves the rest of each changed file
    uncovered and fails the gate. These are exactly the tests that produced the
    file's coverage rows.

    ONE INDEXED QUERY PER PATH (:func:`touching_tests`), walking from the file
    to its blocks to their tests, so the cost follows the changed files and not
    the size of the index.
    """
    return files_of(rebase_to_repo(touching_tests(conn, paths), repo_root))


def speakable_test_files(
    conn: sqlite3.Connection, repo_root: Path,
) -> frozenset[str]:
    """Test FILES the index has coverage rows for, none of them unmeasured.

    NECESSARY FOR DROPPING A FILE, NOT SUFFICIENT: rows say what a test ran
    in the files its run measured, so :func:`droppable_test_files` also asks
    what that run measured. A file qualifies here when it has coverage
    rows AND none of its tests are recorded ``unmeasured``. Both halves matter:
    a file absent from the index was never observed at all, and a file with an
    unmeasured test ran without leaving a trace, so in neither case does the
    index's silence mean "this test is irrelevant".

    The unmeasured check is at FILE granularity because the run set is, which
    is conservative in a way worth naming: most files with unmeasured tests
    are only PARTLY unmeasured — a single unmeasured test holds back a file of
    hundreds — so this rule keeps considerably more than node-granularity
    would.
    """
    measured = files_of(rebase_to_repo(measured_tests(conn), repo_root))
    unmeasured = files_of(rebase_to_repo(
        {r[0] for r in conn.execute("SELECT test_id FROM unmeasured_test")},
        repo_root))
    return measured - unmeasured


def informed_test_files(
    conn: sqlite3.Connection, repo_root: Path, paths: Iterable[str],
) -> frozenset[str]:
    """Test FILES whose silence about ``paths`` (absolute) is evidence.

    A file qualifies when its latest run in this checkout was declared
    complete (coverage measured every package), measured every one of
    ``paths``, and left rows for every one of the file's tests that ran in
    it. Then "no row on a path" means the test did not execute it.

    Everything else has rows that prove nothing about ``paths``:

    * a test last run with part of the source measured (a new path from the
      measured code into unmeasured code is not recorded either, so a partial
      run does not inform even about the files it did measure);
    * a test whose rows predate run records, or come from another checkout;
    * a path the run did not measure (an omitted file, or one nothing in the
      run imported), or one the index has never seen;
    * a file with a test that ran and left no row.

    The run's own file list decides "measured"; the caller's declaration
    decides "complete". Neither is inferred from which rows exist.
    """
    checkout = str(repo_root)
    wanted = sorted(set(paths))
    file_ids = [row[0] for path in wanted
                for row in conn.execute("SELECT id FROM file WHERE path = ?", (path,))]
    if len(file_ids) != len(wanted):
        return frozenset()
    vouching = {
        run_id for (run_id,) in conn.execute(
            "SELECT id FROM run WHERE checkout = ? AND complete = 1",
            (checkout,)).fetchall()
        if all(conn.execute(
            "SELECT 1 FROM run_file WHERE run = ? AND file = ?",
            (run_id, file_id)).fetchone() for file_id in file_ids)
    }
    verdict: dict[str, bool] = {}
    for test_id, run_id, rows in conn.execute(_LATEST_RUNS, (checkout,)):
        test_file = test_id.split("::")[0]
        verdict[test_file] = (verdict.get(test_file, True)
                              and bool(rows) and run_id in vouching)
    return frozenset(f for f, informed in verdict.items() if informed)


# ── Narrowing (history: WI-bolot) ───────────────────────────────────────────
#
# Everything above only ever ADDS tests, so being wrong costs time. Narrowing can
# REMOVE one, so the rules are stated as data rather than left implicit in a
# shell pipeline. See tests/test_selection_narrow.py for the three rules and why
# each has its own failure mode.

#: Coverage measures ``packages/*/src``. A changed file outside that is not
#: coverable in-process at all, so the index's silence about it is a structural
#: fact rather than a gap in knowledge — which is why it does not forbid
#: narrowing the way an unknown *coverable* source file does.
_COVERABLE = ("src",)


def _is_test_path(rel: str) -> bool:
    """A changed TEST file is its own cover, so it never forbids narrowing."""
    return "/tests/" in rel or Path(rel).name.startswith(
        ("test_", "BRANCHES_test_"))


def _is_coverable(rel: str) -> bool:
    parts = Path(rel).parts
    return parts[:1] == ("packages",) and any(p in _COVERABLE for p in parts)


def droppable_test_files(
    conn: sqlite3.Connection, repo_root: Path, changed: Iterable[str],
) -> frozenset[str]:
    """THE ONLY TEST FILES NARROWING MAY DROP for a change to ``changed``
    (absolute paths): those the index has rows for, with no unmeasured test
    (:func:`speakable_test_files`), whose latest run in this checkout measured
    every changed coverable source file as part of a complete run
    (:func:`informed_test_files`).

    A changed test file, or a file outside the coverage scope, is not
    something a run measures, so it asks nothing of the run's file list.
    """
    prefix = f"{repo_root}/"
    sources = [
        p for p in changed
        for r in [p[len(prefix):] if p.startswith(prefix) else p]
        if not _is_test_path(r) and _is_coverable(r)
    ]
    return (speakable_test_files(conn, repo_root)
            & informed_test_files(conn, repo_root, sources))


def import_time_changed_sources(
    base_sources: Mapping[str, Optional[str]], repo_root: Path,
    conn: Optional[sqlite3.Connection] = None,
) -> tuple[str, ...]:
    """Repo-relative coverable source files whose import-time code changed.

    These are the files :func:`narrowing_blockers` refuses on, and for the
    same reason they need a selector of their own: a module or class body
    runs under no test's context, so no coverage row says which tests depend
    on it, and the call-edge slice reaches a test only through a call. The
    selector for them is the import graph
    (:mod:`hypergumbo_core.import_closure`): every file that imports one of
    these runs the changed code.

    ``base_sources`` is what :func:`changed_blocks` takes. A deleted file
    counts, since its module body is gone from under its importers. A new
    file does not: nothing imported it at the base, and a file that starts
    importing it has a changed module body of its own.

    With ``conn``, a changed FUNCTION the index saw run at import counts too
    (:func:`blocks_run_at_import`): the modules that call it while they load
    import its file, so that file's importers are a superset of them.
    """
    ran = blocks_run_at_import(conn, base_sources) if conn is not None else frozenset()
    _changed, _new, import_time = _diff_blocks(base_sources, ran)
    prefix = f"{repo_root}/"
    rels = {p[len(prefix):] if p.startswith(prefix) else p for p, _name in import_time}
    return tuple(sorted(r for r in rels if not _is_test_path(r) and _is_coverable(r)))


@dataclass(frozen=True)
class Narrowing:
    """The outcome of one narrowing attempt.

    ``refusal`` is the discriminator: when it is set, ``kept`` is the ORIGINAL
    run set and ``dropped`` is empty. A caller must not have to infer "nothing
    happened" from two set sizes being equal, because that is also what a
    successful narrowing that found nothing to drop looks like, and the two
    warrant different reporting.
    """

    kept: frozenset[str]
    dropped: frozenset[str]
    refusal: Optional[str]


def narrowing_blockers(
    selection: Selection, repo_root: Path,
) -> tuple[str, ...]:
    """Reasons this change set must not be narrowed at all.

    Deliberately NOT triggered by ``new_blocks`` or by unknown test / non-
    coverable paths — see the module-level tests for why each of those looks
    like a blocker and is not.
    """
    def rel(p: str) -> str:
        prefix = f"{repo_root}/"
        return p[len(prefix):] if p.startswith(prefix) else p

    out: list[str] = []
    for p in sorted(selection.unknown_paths):
        r = rel(p)
        if not _is_test_path(r) and _is_coverable(r):
            out.append(f"unknown coverable source: {r}")
    for p in sorted(selection.missing_paths):
        out.append(f"missing changed file: {rel(p)}")
    # AN IMPORT IS NOT A COVERAGE ROW. A module or class body, and a function
    # called from one (semantic 2 of the module docstring), runs when the
    # file is imported, under no test's context, so a test that only imports
    # the file has no row for it, and narrowing would read that absence as
    # proof of irrelevance. Every importer runs the changed code; the index
    # does not hold import edges, so the only safe keep set is the run set.
    # Most commits that modify package source change such a block, so this
    # refuses on most real changes. That is the cost of soundness here, not an
    # accident. A test file is exempt: its importers are not tests, and it
    # enters the run set as a changed test. (history: WI-zunug)
    for p, name in sorted(selection.import_time_changed):
        r = rel(p)
        if not _is_test_path(r) and _is_coverable(r):
            out.append(f"import-time code changed: {r} ({name})")
    return tuple(out)


def narrow_run_set(
    run_set: Iterable[str],
    *,
    keep: Iterable[str],
    droppable: Iterable[str],
    forbidden: Iterable[str],
    changed: Iterable[str] = (),
) -> Narrowing:
    """Shrink ``run_set`` to ``keep`` plus everything not safely droppable.

    ``keep`` is the FILE-TOUCH set — every test observed executing any block of
    a changed file, not merely the changed blocks — because the local coverage
    gate is whole-file. ``droppable`` is the set the index can speak about.
    Anything in ``run_set`` and outside ``droppable`` survives regardless, and
    so does every ``changed`` path in it: a test file the change edits is not in
    the coverage scope, so no row ties it to the change, yet it is the change's
    own evidence and must run.
    """
    original = frozenset(run_set)
    reasons = tuple(forbidden)
    if reasons:
        return Narrowing(kept=original, dropped=frozenset(),
                         refusal="; ".join(reasons))

    keep_set, drop_ok = frozenset(keep) | frozenset(changed), frozenset(droppable)
    kept = frozenset(f for f in original if f in keep_set or f not in drop_ok)
    if not kept:
        # pytest exits 5 — GREEN — when it collects nothing, so a narrowing to
        # zero would read as a pass while running no tests at all.
        return Narrowing(kept=original, dropped=frozenset(),
                         refusal="narrowing would leave an empty run set")
    return Narrowing(kept=kept, dropped=original - kept, refusal=None)
