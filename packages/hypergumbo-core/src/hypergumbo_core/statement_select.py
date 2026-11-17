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
* the index's records are of the block AS IT WAS before the change (its
  stored digest is the old block's). The index drops a block's records when a
  run stores another digest for it, so after the changed code has been run
  once the records of the old block are gone; then, and for a pair that never
  had a record, the test is selected. ``Selection.answers`` says, per block,
  which of these happened, so that "it selected everything" is never mistaken
  for "it had records and they said so".

WHAT IT CANNOT KNOW, AND SAYS. "Every test that reached the block" is read
from the index's pairs, and the index has never seen some blocks (on the day
this was written, 362 of this repository's 7,319 functions: added since the
last full run and run only by runs that were not folded in). For such a
block no pair is not "no test reaches it". It is answered ``UNKNOWN`` and
listed in ``Selection.unknown``; a caller must not narrow a run on a
selection whose ``unknown`` is not empty.

WHAT IT DOES NOT DECIDE. Import-time blocks (a module body, a class body):
no record says which tests ran them; they are listed in ``import_time`` for
the caller's import rule. A new function has no test of its own (a changed
caller's). A new METHOD is given every test that reached any block of its
class in the same file: an unchanged call may now reach it in place of an
inherited one. That last rule is a guess at "the tests that use this class",
not a measurement; a subclass in another file that inherits the new method is
reached only if its tests also reach the class's own code.

NOTHING ACTS ON THIS YET. ``coverage-select shadow`` stores its answer beside
what a run really ran and which tests failed; whether it may select is decided
on that record.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from .selection_index import BlockKey, statement_records, touching_tests
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


def _stored_digests(conn: sqlite3.Connection, path: str) -> dict[str, str]:
    return {str(name): str(digest) for name, digest in conn.execute(
        "SELECT b.name, b.digest FROM file f JOIN block b ON b.file = f.id "
        "WHERE f.path = ?", (path,))}


def select_by_statement(
    conn: sqlite3.Connection, base_sources: Mapping[str, Optional[str]],
) -> Selection:
    """The tests a change needs, by statement where the records allow.

    ``base_sources`` maps each changed file (as the index spells its path) to
    its source at the base commit, ``None`` for a file the change adds: the
    same input as :func:`selection_index.select_tests`. The current source is
    read from disk; a file that is not there was deleted.
    """
    tests: set[str] = set()
    by_block: set[str] = set()
    answers: dict[BlockKey, str] = {}
    import_time: set[BlockKey] = set()
    unknown: set[BlockKey] = set()
    unmeasured = {str(row[0]) for row in conn.execute("SELECT test_id FROM unmeasured_test")}
    for path, base in base_sources.items():
        try:
            current: Optional[str] = Path(path).read_text(encoding="utf-8")
        except OSError:
            current = None
        try:
            changes = statement_changes(path, base, current)
        except SyntaxError:
            reached = touching_tests(conn, [path])
            tests |= reached
            by_block |= reached
            answers[(path, "")] = UNPARSABLE
            continue
        stored = _stored_digests(conn, path)
        for name, change in changes.items():
            key = (path, name)
            if change.kind is Kind.PROSE:
                answers[key] = PROSE
                continue
            if change.import_time:
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
                reached = set()
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
            records = statement_records(conn, path, name)
            by_block |= set(records)
            if change.kind is Kind.GONE:
                tests |= set(records)
                answers[key] = GONE
            elif change.kind is Kind.WHOLE:
                tests |= set(records)
                answers[key] = f"whole: {change.why}"
            elif stored.get(name) != change.old_digest:
                tests |= set(records)
                answers[key] = NO_BASE_RECORD
            else:
                tests |= {test for test, ran in records.items()
                          if ran is None or ran & change.ran}
                answers[key] = BY_STATEMENT
    return Selection(
        tests=frozenset(tests - unmeasured), by_block=frozenset(by_block - unmeasured),
        answers=answers, import_time=frozenset(import_time), unknown=frozenset(unknown))
