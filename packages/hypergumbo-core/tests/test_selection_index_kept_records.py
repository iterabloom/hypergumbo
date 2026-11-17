# SPDX-License-Identifier: AGPL-3.0-or-later
"""The index keeps a block's statement records when a new version supersedes them.

A fold that stores another digest for a block drops the records of that
block's pairs: they number the statements of code that is gone. But the
by-statement selector, which runs before a change is merged and after the
changed code has been run, needs exactly those: the records of the block as it
was at the base of the change. So the fold first copies them aside under their
digest, and ``statement_records_at`` reads a block's records for a GIVEN
version.

What is pinned: the dropped records are kept and read back by digest; the
current records are unchanged by any of it; a version the index holds nothing
of is ``None`` and not "no test has a record"; a block reverted to an earlier
version gets its earlier records back; only ``KEPT_VERSIONS`` versions are
kept, the longest-superseded forgotten first; a file that has gone takes its kept records with it; and a file written
before the tables existed opens with nothing kept and no format change.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional

import pytest

from hypergumbo_core import selection_index as si
from hypergumbo_core.selection_index import statement_records, statement_records_at, store_edges

M = "/repo/src/m.py"
ALPHA = (M, "alpha")


def _bits(ordinals: Iterable[int]) -> bytes:
    return si._int_bitmap(sum(1 << ordinal for ordinal in ordinals))


def _fold(conn: sqlite3.Connection, digest: str, ran: dict[str, Optional[set[int]]]) -> None:
    """One run: ``alpha`` has ``digest`` now, and each test in ``ran`` reached
    it and ran those statements (``None``: reached it, no record)."""
    store_edges(
        conn, {test: [ALPHA] for test in ran}, digests={M: {"alpha": digest}},
        statements={test: {ALPHA: _bits(ordinals)} for test, ordinals in ran.items()
                    if ordinals is not None})


def _index(tmp_path: Path) -> sqlite3.Connection:
    conn = si.open_index(tmp_path / "index.sqlite")
    _fold(conn, "d1", {"test_a": {0, 1}, "test_b": {0, 2}, "test_plain": None})
    return conn


def test_the_records_a_fold_drops_are_kept_under_their_digest(tmp_path: Path) -> None:
    conn = _index(tmp_path)
    # alpha is edited and only test_a is run on the new code.
    _fold(conn, "d2", {"test_a": {0, 3}})
    # As before: the pairs stay, the records of the old version are gone.
    assert statement_records(conn, M, "alpha") == {
        "test_a": {0, 3}, "test_b": None, "test_plain": None}
    # And the old version can still be asked about.
    assert statement_records_at(conn, M, "alpha", "d1") == {
        "test_a": {0, 1}, "test_b": {0, 2}, "test_plain": None}
    assert statement_records_at(conn, M, "alpha", "d2") == {
        "test_a": {0, 3}, "test_b": None, "test_plain": None}


def test_a_version_the_index_holds_nothing_of_is_none(tmp_path: Path) -> None:
    """Absent is not empty: ``{}`` or all-``None`` would read as "every test
    reached it and none has a record"."""
    conn = _index(tmp_path)
    assert statement_records_at(conn, M, "alpha", "never-seen") is None
    assert statement_records_at(conn, M, "no_such_block", "d1") is None
    assert statement_records_at(conn, "/repo/src/other.py", "alpha", "d1") is None
    assert statement_records_at(conn, M, "alpha", "") is None
    # Asked for the version it holds, with nothing superseded yet.
    assert statement_records_at(conn, M, "alpha", "d1") == statement_records(conn, M, "alpha")


def test_a_block_put_back_to_an_earlier_version_gets_its_records_back(tmp_path: Path) -> None:
    conn = _index(tmp_path)
    _fold(conn, "d2", {"test_a": {0, 3}})
    # The edit is undone and only test_b is run.
    _fold(conn, "d1", {"test_b": {0, 2, 4}})
    assert statement_records(conn, M, "alpha")["test_a"] is None, "the current record was dropped"
    assert statement_records_at(conn, M, "alpha", "d1") == {
        # test_b's newest record of d1 wins over the kept one; test_a's kept
        # record of d1 fills the place the fold emptied.
        "test_a": {0, 1}, "test_b": {0, 2, 4}, "test_plain": None}
    # d2's records were kept when d1 came back.
    assert statement_records_at(conn, M, "alpha", "d2")["test_a"] == {0, 3}


def test_a_test_that_no_longer_reaches_the_block_keeps_its_record_of_the_old_version(
        tmp_path: Path) -> None:
    conn = _index(tmp_path)
    # test_a runs again under d2 and no longer reaches alpha at all.
    store_edges(conn, {"test_a": []}, digests={M: {"alpha": "d2"}}, measured=[M])
    assert "test_a" not in statement_records(conn, M, "alpha")
    assert statement_records_at(conn, M, "alpha", "d1")["test_a"] == {0, 1}


def test_only_so_many_superseded_versions_are_kept(tmp_path: Path) -> None:
    conn = _index(tmp_path)
    for n in range(2, si.KEPT_VERSIONS + 3):
        _fold(conn, f"d{n}", {"test_a": {n}})
    newest = si.KEPT_VERSIONS + 2
    # Superseded: d1 .. d<newest-1>, one more than is kept: d1 is forgotten.
    assert statement_records_at(conn, M, "alpha", "d1") is None
    assert statement_records_at(conn, M, "alpha", "d2")["test_a"] == {2}
    assert statement_records_at(conn, M, "alpha", f"d{newest}")["test_a"] == {newest}
    assert conn.execute("SELECT COUNT(*) FROM kept_version").fetchone()[0] == si.KEPT_VERSIONS
    assert conn.execute(
        "SELECT COUNT(DISTINCT version) FROM kept_record").fetchone()[0] == si.KEPT_VERSIONS


def test_a_version_kept_again_is_the_newest_again(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The version that is forgotten is the one superseded longest ago, and a
    version the block came back to and left again was superseded just now."""
    monkeypatch.setattr(si, "KEPT_VERSIONS", 2)
    conn = _index(tmp_path)                       # d1
    _fold(conn, "d2", {"test_a": {2}})            # kept: d1
    _fold(conn, "d1", {"test_a": {0, 1}})         # kept: d1, d2
    _fold(conn, "d3", {"test_a": {3}})            # kept: d2, d1 (again)
    _fold(conn, "d4", {"test_a": {4}})            # kept: d1, d3; d2 is forgotten
    assert statement_records_at(conn, M, "alpha", "d2") is None
    assert statement_records_at(conn, M, "alpha", "d1")["test_a"] == {0, 1}
    assert statement_records_at(conn, M, "alpha", "d3")["test_a"] == {3}
    assert conn.execute("SELECT COUNT(*) FROM kept_version").fetchone()[0] == 2


def test_a_block_whose_digest_was_never_stored_keeps_nothing(tmp_path: Path) -> None:
    """A pair written with no digest for its block: there is no version to
    keep its records under."""
    conn = si.open_index(tmp_path / "index.sqlite")
    store_edges(conn, {"test_a": [ALPHA]}, statements={"test_a": {ALPHA: _bits({0})}})
    # "No digest" names no version: it is what a block that is new is asked with.
    assert statement_records_at(conn, M, "alpha", "") is None
    _fold(conn, "d1", {"test_b": {1}})
    assert conn.execute("SELECT COUNT(*) FROM kept_record").fetchone()[0] == 0
    assert statement_records_at(conn, M, "alpha", "d1") == {"test_a": {0}, "test_b": {1}}


def test_a_file_that_is_gone_takes_its_kept_records_with_it(tmp_path: Path) -> None:
    conn = _index(tmp_path)
    _fold(conn, "d2", {"test_a": {0, 3}})
    assert conn.execute("SELECT COUNT(*) FROM kept_record").fetchone()[0] == 2
    # /repo/src/m.py is not on disk and is no source file of this checkout.
    si._forget_dead_checkouts(conn, str(tmp_path))
    assert conn.execute("SELECT COUNT(*) FROM kept_record").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM kept_version").fetchone()[0] == 0


def test_a_file_from_before_the_tables_opens_with_nothing_kept_and_the_same_format(
        tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    conn = _index(tmp_path)
    conn.execute("DROP TABLE kept_record")
    conn.execute("DROP TABLE kept_version")
    conn.commit()
    conn.close()
    again = si.open_index(path)
    assert again.execute("PRAGMA user_version").fetchone()[0] == si.FORMAT == 4
    assert statement_records_at(again, M, "alpha", "d1") == {
        "test_a": {0, 1}, "test_b": {0, 2}, "test_plain": None}
    _fold(again, "d2", {"test_a": {0, 3}})
    assert statement_records_at(again, M, "alpha", "d1")["test_b"] == {0, 2}
