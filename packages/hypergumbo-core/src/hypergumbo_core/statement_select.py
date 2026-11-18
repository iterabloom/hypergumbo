# SPDX-License-Identifier: AGPL-3.0-or-later
"""The by-statement test selector: the tests that ran a changed statement.

WHAT IT IS. The coverage selector (:func:`selection_index.select_tests`) picks
every test that reached a changed function. Most of them ran lines of the
function the change did not touch. The index also records WHICH statements of
a function each test ran, and :mod:`statement_diff` says which old statements
a change makes a test answer for. This module joins the two: for each changed
function, the tests whose record holds one of those statements.

IT ONLY EVER NARROWS, AND ONLY WHERE IT CAN SEE. For each changed block the
answer is the block-level one (every test paired with it) unless there is
evidence for less:

* the change is one :mod:`statement_diff` can name statements for, AND
* the index holds records of the block AS IT WAS before the change: its
  stored digest is the old block's, or the old block's records were kept
  when a run stored another digest (:func:`selection_index.statement_records_at`;
  the index keeps a few superseded versions per block, so a change that has
  been run still has its base to compare with). When it holds nothing of
  that version, and for a test with no record of it, the test is selected.
  ``Selection.answers`` says, per block, which of these happened, so that
  "it selected everything" is never mistaken for "it had records and they
  said so".

"EVERY TEST THAT REACHED THE BLOCK" is the tests paired with it now AND the
tests with a kept record of the old version: a test that reached the block at
the base and stopped reaching it in a later run of the same change is still a
test the change, measured from its base, affects.

WHAT IT CANNOT KNOW, AND SAYS. "Every test that reached the block" is read
from the index's pairs, and the index has never seen some blocks (on the day
this was written, 362 of this repository's 7,319 functions: added since the
last full run and run only by runs that were not folded in). For such a
block no pair is not "no test reaches it". It is answered ``UNKNOWN`` and
listed in ``Selection.unknown``; a caller must not narrow a run on a
selection whose ``unknown`` is not empty.

WHAT IT DOES NOT DECIDE. Import-time blocks (a module body, a class body,
and a function the index saw run while a module was imported): no record
says which tests ran them; they are listed in ``import_time`` for the
caller's import rule. A new function has no test of its own (a changed
caller's). A new METHOD is given every test that reached any block of its
class in the same file: an unchanged call may now reach it in place of an
inherited one. That last rule is a guess at "the tests that use this class",
not a measurement; a subclass in another file that inherits the new method is
reached only if its tests also reach the class's own code.

WHOM IT NEVER NARROWS. A test coverage cannot speak for
(:func:`selection_index.coverage_cannot_speak_for`): one whose own call ran
no measured code, one recorded starting a program that can run Python, one
with no read record. Coverage did not see what such a test's child ran, so
"it reached the function and ran none of the changed statements" is not
known of it. Where such a test reached a changed block it is selected whole;
where it has no row at all the selector says nothing about it, and
``Selection.cannot_speak`` names every one of them so that a caller narrowing
a run keeps those another rule selected. (The unmeasured ones are also left
out of ``tests``, as before: their rows are their fixtures', and the rules
for unmeasured tests select them.)

WHOM IT ADDS WHATEVER THE CHANGE. A test recorded reading a changed file as
data (``prose_only.test_ids_reading``: a gate that scans source text, a test
that pins a line) ran none of its statements and depends on every one of
them, and on its comments and docstrings. Coverage has no row for it, so
every changed file's readers are selected, for any kind of change, and
listed in ``Selection.readers`` (campaign 4 of 2026-11-18: four faults
failed ``test_kept_work_guard.py::test_live_tree_is_clean`` and no other
rule selected it). The read record is as old as each reader's last recorded
run, the limit ``prose_only.tests_reading`` states.

WHAT ACTS ON IT. A local targeted smart-test run deselects, inside the files
it runs, the tests this selector leaves out (:mod:`statement_narrow`, which
says when it refuses and which tests it puts back to keep the coverage gate
measured; switched on 2026-11-18 at the owner's bar). The CI manifest still
lists files. ``coverage-select run-record`` stores its answer beside what a
run really ran and which tests failed.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .prose_only import test_ids_reading
from .selection_index import (
    BlockKey,
    blocks_run_at_import,
    coverage_cannot_speak_for,
    index_spellings,
    statement_records,
    statement_records_at,
    touching_tests,
)
from .statement_diff import Kind, statement_changes

#: ``Selection.answers`` values that are not of the form ``whole: <why>``.
BY_STATEMENT = "by statement"
PROSE = "prose: no test"
NEW = "new function: no test of its own"
NEW_METHOD = "new method: every test that reached its class"
GONE = "gone: every test that reached it"
NO_BASE_RECORD = "no record of the block as it was: every test that reached it"
UNKNOWN = "the index has never seen this block: it cannot say which tests reach it"
IMPORT_TIME = "import time: the import rule"
UNPARSABLE = "does not parse: every test that touched the file"


@dataclass(frozen=True)
class Selection:
    """What the by-statement selector would run for a change."""

    #: Test ids as the index spells them.
    tests: frozenset[str]
    #: Every test that reached a changed block: what selecting by block gives
    #: for the same blocks. ``tests`` is a subset.
    by_block: frozenset[str]
    #: How each changed block was answered (the constants above).
    answers: Mapping[BlockKey, str]
    #: Changed blocks that run at import: the caller's import rule answers.
    import_time: frozenset[BlockKey]
    #: Changed blocks the index has never seen. Not empty: ``tests`` is NOT
    #: every test the change needs, and nothing may be narrowed on it.
    unknown: frozenset[BlockKey]
    #: Every test that reached a block answered ``BY_STATEMENT``: the tests
    #: the selector had the chance to leave out. A failed test in here that
    #: is also in ``tests`` is the selector holding where it could have
    #: missed; a failed test outside it was never this selector's to lose.
    narrowed: frozenset[str]
    #: Every test coverage cannot speak for, in the index's spelling. The
    #: selector did not narrow any of them, and a test here that is not in
    #: ``tests`` is one it says nothing about: not one the change spares.
    cannot_speak: frozenset[str] = frozenset()
    #: Tests recorded reading a changed file as data, in the index's
    #: spelling: all in ``tests``, whatever their coverage says.
    readers: frozenset[str] = frozenset()


def _stored_digests(conn: sqlite3.Connection, path: str) -> dict[str, str]:
    return {str(name): str(digest) for name, digest in conn.execute(
        "SELECT b.name, b.digest FROM file f JOIN block b ON b.file = f.id "
        "WHERE f.path = ?", (path,))}


def select_by_statement(
    conn: sqlite3.Connection, base_sources: Mapping[str, Optional[str]],
    repo_root: Optional[Path] = None,
) -> Selection:
    """The tests a change needs, by statement where the records allow.

    ``base_sources`` maps each changed file (as the index spells its path) to
    its source at the base commit, ``None`` for a file the change adds: the
    same input as :func:`selection_index.select_tests`. The current source is
    read from disk; a file that is not there was deleted. ``repo_root`` lets
    an index that spells a test id absolutely be matched to the read record,
    which spells it relative to the repository.
    """
    tests: set[str] = set()
    by_block: set[str] = set()
    answers: dict[BlockKey, str] = {}
    import_time: set[BlockKey] = set()
    unknown: set[BlockKey] = set()
    narrowed: set[str] = set()
    blind = coverage_cannot_speak_for(conn, repo_root)
    unmeasured, held = blind.unmeasured, blind.never_narrowed
    for path, base in base_sources.items():
        try:
            current: Optional[str] = Path(path).read_text(encoding="utf-8")
        except OSError:
            current = None
        try:
            changes = statement_changes(path, base, current)
        except SyntaxError:
            touched = touching_tests(conn, [path])
            tests |= touched
            by_block |= touched
            answers[(path, "")] = UNPARSABLE
            continue
        stored = _stored_digests(conn, path)
        ran_at_import = blocks_run_at_import(conn, [path])
        for name, change in changes.items():
            key = (path, name)
            if change.kind is Kind.PROSE:
                answers[key] = PROSE
                continue
            # A function some run saw execute at import is import-time code
            # too: a test that uses what it built never ran it.
            if change.import_time or key in ran_at_import:
                import_time.add(key)
                answers[key] = IMPORT_TIME
                continue
            if change.kind is Kind.NEW:
                answers[key] = NEW
                continue
            if change.kind is Kind.NEW_METHOD:
                owner = name.rsplit(".", 1)[0]
                siblings = [block for block in stored
                            if block == owner or block.startswith(owner + ".")]
                class_is_new = owner in changes and changes[owner].kind is Kind.NEW
                if not siblings and not class_is_new:
                    unknown.add(key)
                    answers[key] = UNKNOWN
                    continue
                reached: set[str] = set()
                for sibling in siblings:
                    reached |= set(statement_records(conn, path, sibling))
                tests |= reached
                by_block |= reached
                answers[key] = NEW_METHOD
                continue
            if name not in stored:
                unknown.add(key)
                answers[key] = UNKNOWN
                continue
            was = statement_records_at(conn, path, name, change.old_digest)
            reached = set(statement_records(conn, path, name)) | set(was or ())
            by_block |= reached
            if change.kind is Kind.GONE:
                tests |= reached
                answers[key] = GONE
            elif change.kind is Kind.WHOLE:
                tests |= reached
                answers[key] = f"whole: {change.why}"
            elif was is None:
                tests |= reached
                answers[key] = NO_BASE_RECORD
            else:
                tests |= {test for test, ran in was.items()
                          if ran is None or ran & change.ran}
                tests |= reached & held
                narrowed |= reached - held
                answers[key] = BY_STATEMENT
    readers = _readers(conn, base_sources, repo_root)
    return Selection(
        tests=frozenset(tests - unmeasured) | readers,
        by_block=frozenset(by_block - unmeasured),
        answers=answers, import_time=frozenset(import_time), unknown=frozenset(unknown),
        narrowed=frozenset(narrowed - unmeasured), cannot_speak=blind.tests, readers=readers)


def _readers(conn: sqlite3.Connection, paths: Iterable[str],
             repo_root: Optional[Path]) -> frozenset[str]:
    """The tests recorded reading one of ``paths`` as data, as the index
    spells them. The record spells paths relative to the repository."""
    def relative(path: str) -> str:
        if repo_root is not None and Path(path).is_absolute():
            try:
                return Path(path).relative_to(repo_root).as_posix()
            except ValueError:
                return path
        return path

    read = test_ids_reading(conn, [relative(path) for path in paths])
    spelled = index_spellings(conn, repo_root)
    return frozenset(test for recorded in read for test in spelled.get(recorded, {recorded}))
