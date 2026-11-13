# SPDX-License-Identifier: AGPL-3.0-or-later
"""The selection index's storage format, and the conversion from format 1.

WHAT FORMAT 2 IS FOR. Format 1 spelled every ``test -> block`` pair out as three
strings (a pytest node id, an absolute path, a block name) and repeated them in
the primary-key index, about 236 bytes a pair on this repository's own index.
Format 2 stores each test, file and block once and keeps the pairs as two
integers. The pairs are the same; only their spelling changed.

WHAT THESE TESTS PIN.

* **A format-1 file is converted, never read as empty.** New code opening an old
  file without converting would create empty new tables beside the old ones and
  answer "no test runs this" for every change, which narrowing reads as
  permission to drop tests. ``TestOpeningAFormat1File`` asserts the converted
  content pair by pair.
* **The conversion loses nothing**: a pair whose block has no ``block`` row, the
  unmeasured tests, the full-run marker, and every table another module keeps in
  the same file (the read evidence of :mod:`hypergumbo_core.prose_only`).
* **The old file survives**, byte for byte, under a ``.format-1`` name, and an
  earlier backup is never overwritten.
* **A conversion that fails or is killed leaves the format-1 file in place**
  and the next open converts it.
* **Two openers convert once.**
* **A file of a format this code does not know is refused**, not rewritten.
* **The selection and narrowing queries are index lookups.** A full scan of the
  pair table on every narrowed run is what the format-1 queries did.

The format-1 file is built here with the format-1 schema written out, because
the code under test no longer contains it.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from hypergumbo_core import selection_index
from hypergumbo_core.selection_index import (
    FORMAT,
    IndexFormatError,
    measured_tests,
    open_index,
    select_tests,
    speakable_test_files,
    store_edges,
    stored_edges,
    touching_test_files,
    touching_tests,
)

_FORMAT_1_SCHEMA = """
CREATE TABLE block (
    path   TEXT NOT NULL,
    name   TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY (path, name)
);
CREATE TABLE test_block (
    test_id TEXT NOT NULL,
    path    TEXT NOT NULL,
    name    TEXT NOT NULL,
    PRIMARY KEY (test_id, path, name)
);
CREATE INDEX test_block_by_block ON test_block (path, name);
CREATE TABLE unmeasured_test (test_id TEXT PRIMARY KEY);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE read_test (test_id TEXT PRIMARY KEY, test_file TEXT NOT NULL);
CREATE INDEX read_test_by_file ON read_test (test_file);
"""

#: ``(test, path, block)``. The last pair's block has NO ``block`` row.
_PAIRS = [
    ("pkg/tests/test_a.py::one", "/r/pkg/src/x.py", "alpha"),
    ("pkg/tests/test_a.py::one", "/r/pkg/src/x.py", "<module>"),
    ("pkg/tests/test_a.py::two", "/r/pkg/src/y.py", "beta"),
    ("pkg/tests/test_b.py::three", "/r/pkg/src/x.py", "alpha"),
    ("pkg/tests/test_b.py::three", "/r/pkg/src/z.py", "no_block_row"),
]
_BLOCKS = [
    ("/r/pkg/src/x.py", "alpha", "d-alpha"),
    ("/r/pkg/src/x.py", "<module>", "d-module"),
    ("/r/pkg/src/y.py", "beta", "d-beta"),
    ("/r/pkg/src/y.py", "never_run", "d-never"),
]
_UNMEASURED = ["pkg/tests/test_c.py::subprocess_driven"]
_READS = [("pkg/tests/test_a.py::one", "pkg/tests/test_a.py")]


def _format_1(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.executescript(_FORMAT_1_SCHEMA)
    conn.executemany("INSERT INTO block VALUES (?, ?, ?)", _BLOCKS)
    conn.executemany("INSERT INTO test_block VALUES (?, ?, ?)", _PAIRS)
    conn.executemany("INSERT INTO unmeasured_test VALUES (?)",
                     [(t,) for t in _UNMEASURED])
    conn.execute("INSERT INTO meta VALUES ('last_full_run_sha', 'abc123')")
    conn.executemany("INSERT INTO read_test VALUES (?, ?)", _READS)
    conn.commit()
    conn.close()
    return path


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.format-1*"))


@pytest.fixture
def old(tmp_path: Path) -> Path:
    return _format_1(tmp_path / "index.sqlite")


class TestAFreshIndex:
    def test_it_is_stamped_with_the_format(self, tmp_path: Path) -> None:
        open_index(tmp_path / "new.sqlite").close()
        assert _user_version(tmp_path / "new.sqlite") == FORMAT == 2

    def test_reopening_keeps_what_was_stored(self, tmp_path: Path) -> None:
        conn = open_index(tmp_path / "i.sqlite")
        store_edges(conn, {"t::a": [("/s/x.py", "f")]})
        conn.commit()
        conn.close()
        again = open_index(tmp_path / "i.sqlite")
        assert set(stored_edges(again)) == {("t::a", "/s/x.py", "f")}
        again.close()
        assert _backups(tmp_path / "i.sqlite") == []

    def test_each_name_is_stored_once(self, tmp_path: Path) -> None:
        """The size property itself: two tests on one block, and one test on
        two blocks of one file, repeat no string."""
        conn = open_index(tmp_path / "i.sqlite")
        store_edges(conn, {
            "t::a": [("/s/x.py", "f"), ("/s/x.py", "g")],
            "t::b": [("/s/x.py", "f")],
        })
        assert conn.execute("SELECT count(*) FROM file").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM block").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM test").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM test_block").fetchone()[0] == 3
        conn.close()


class TestStoreEdges:
    def test_it_replaces_a_tests_edges(self, tmp_path: Path) -> None:
        conn = open_index(tmp_path / "i.sqlite")
        store_edges(conn, {"t::a": [("/s/x.py", "f")], "t::b": [("/s/x.py", "f")]})
        store_edges(conn, {"t::a": [("/s/x.py", "g")]})
        assert set(stored_edges(conn)) == {
            ("t::a", "/s/x.py", "g"), ("t::b", "/s/x.py", "f")}
        conn.close()

    def test_a_digest_given_later_updates_the_block(self, tmp_path: Path) -> None:
        conn = open_index(tmp_path / "i.sqlite")
        store_edges(conn, {"t::a": [("/s/x.py", "f")]})
        store_edges(conn, {}, digests={"/s/x.py": {"f": "d1", "unrun": "d2"}})
        got = dict(conn.execute("SELECT name, digest FROM block"))
        assert got == {"f": "d1", "unrun": "d2"}
        conn.close()


class TestOpeningAFormat1File:
    def test_every_pair_survives(self, old: Path) -> None:
        conn = open_index(old)
        assert sorted(stored_edges(conn)) == sorted(_PAIRS)
        conn.close()
        assert _user_version(old) == FORMAT

    def test_it_is_not_read_as_an_empty_index(self, old: Path) -> None:
        """The failure the format marker exists to prevent: a known source file
        reported unknown, and no test attached to it."""
        (old.parent / "x.py").write_text("def alpha():\n    return 2\n")
        conn = sqlite3.connect(str(old))
        conn.execute("UPDATE block SET path = ?", (str(old.parent / "x.py"),))
        conn.execute("UPDATE test_block SET path = ?", (str(old.parent / "x.py"),))
        conn.commit()
        conn.close()
        conn = open_index(old)
        sel = select_tests(
            conn, {str(old.parent / "x.py"): "def alpha():\n    return 1\n"})
        conn.close()
        assert sel.unknown_paths == frozenset()
        assert "pkg/tests/test_a.py::one" in sel.tests

    def test_block_digests_carry_over(self, old: Path) -> None:
        conn = open_index(old)
        got = set(conn.execute(
            "SELECT f.path, b.name, b.digest FROM block b "
            "JOIN file f ON f.id = b.file"))
        conn.close()
        assert set(_BLOCKS) <= got

    def test_a_pair_with_no_block_row_is_kept(self, old: Path) -> None:
        conn = open_index(old)
        assert ("pkg/tests/test_b.py::three", "/r/pkg/src/z.py",
                "no_block_row") in set(stored_edges(conn))
        assert touching_tests(conn, ["/r/pkg/src/z.py"]) == frozenset(
            {"pkg/tests/test_b.py::three"})
        conn.close()

    def test_unmeasured_tests_and_the_full_run_marker_carry_over(
        self, old: Path,
    ) -> None:
        conn = open_index(old)
        assert [r[0] for r in conn.execute(
            "SELECT test_id FROM unmeasured_test")] == _UNMEASURED
        assert dict(conn.execute("SELECT key, value FROM meta")) == {
            "last_full_run_sha": "abc123"}
        conn.close()

    def test_another_modules_table_and_its_index_carry_over(
        self, old: Path,
    ) -> None:
        """The read evidence lives in the same file; losing it would make the
        prose-only rule give way until a full run rebuilt it."""
        conn = open_index(old)
        assert list(conn.execute("SELECT * FROM read_test")) == _READS
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' "
            "AND name = 'read_test_by_file'").fetchone()
        conn.close()

    def test_the_old_file_is_kept_byte_for_byte(self, old: Path) -> None:
        before = old.read_bytes()
        open_index(old).close()
        (backup,) = _backups(old)
        assert backup.name == "index.sqlite.format-1"
        assert backup.read_bytes() == before

    def test_an_earlier_backup_is_not_overwritten(self, old: Path) -> None:
        earlier = old.with_name(old.name + ".format-1")
        earlier.write_bytes(b"an earlier backup")
        open_index(old).close()
        assert earlier.read_bytes() == b"an earlier backup"
        assert [p.name for p in _backups(old)] == [
            "index.sqlite.format-1", "index.sqlite.format-1.2"]

    def test_no_work_file_or_lock_is_left_behind(self, old: Path) -> None:
        open_index(old).close()
        assert sorted(p.name for p in old.parent.iterdir()) == [
            "index.sqlite", "index.sqlite.format-1"]

    def test_the_narrowing_answers_match_a_format_2_index_of_the_same_pairs(
        self, old: Path, tmp_path: Path,
    ) -> None:
        converted = open_index(old)
        native = open_index(tmp_path / "native.sqlite")
        edges: dict[str, list[tuple[str, str]]] = {}
        for test, path, name in _PAIRS:
            edges.setdefault(test, []).append((path, name))
        store_edges(native, edges)
        native.executemany("INSERT INTO unmeasured_test VALUES (?)",
                           [(t,) for t in _UNMEASURED])
        root = Path("/r")
        for conn in (converted, native):
            assert measured_tests(conn) == frozenset(t for t, _, _ in _PAIRS)
            assert touching_test_files(conn, ["/r/pkg/src/x.py"], root) == {
                "pkg/tests/test_a.py", "pkg/tests/test_b.py"}
            assert speakable_test_files(conn, root) == {
                "pkg/tests/test_a.py", "pkg/tests/test_b.py"}
        converted.close()
        native.close()


class TestAConversionThatDoesNotFinish:
    def test_a_failure_leaves_the_format_1_file_and_no_work_file(
        self, old: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The lock file stays after a failure, on purpose: removing it while
        the path is still format 1 would let a waiter and a newcomer each hold
        a different lock file. The next successful conversion removes it."""
        before = old.read_bytes()
        monkeypatch.setattr(selection_index, "_COPY_PAIRS", "SELECT nonsense")
        with pytest.raises(sqlite3.Error):
            open_index(old)
        assert old.read_bytes() == before
        assert sorted(p.name for p in old.parent.iterdir()) == [
            "index.sqlite", "index.sqlite.lock"]

    def test_the_next_open_converts(
        self, old: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with monkeypatch.context() as patched:
            patched.setattr(selection_index, "_COPY_PAIRS", "SELECT nonsense")
            with pytest.raises(sqlite3.Error):
                open_index(old)
        conn = open_index(old)
        assert sorted(stored_edges(conn)) == sorted(_PAIRS)
        conn.close()

    def test_a_work_file_left_by_a_killed_conversion_is_replaced(
        self, old: Path,
    ) -> None:
        old.with_name(old.name + ".converting").write_bytes(b"half a database")
        old.with_name(old.name + ".converting-journal").write_bytes(b"its journal")
        conn = open_index(old)
        assert sorted(stored_edges(conn)) == sorted(_PAIRS)
        conn.close()
        assert sorted(p.name for p in old.parent.iterdir()) == [
            "index.sqlite", "index.sqlite.format-1"]

    def test_a_conversion_that_drops_a_pair_is_refused(
        self, old: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The count check is the last thing between a conversion bug and an
        index that under-selects. Make the copy lose one pair and watch it
        refuse, with the format-1 file untouched."""
        before = old.read_bytes()
        monkeypatch.setattr(
            selection_index, "_COPY_PAIRS",
            selection_index._COPY_PAIRS + " LIMIT 4")
        with pytest.raises(IndexFormatError, match="4 of 5"):
            open_index(old)
        assert old.read_bytes() == before
        assert _user_version(old) == 0
        assert _backups(old) == []


class TestTwoOpenersAtOnce:
    def test_they_convert_once(self, old: Path) -> None:
        errors: list[BaseException] = []

        def opener() -> None:
            try:
                open_index(old).close()
            except BaseException as exc:  # reported below
                errors.append(exc)

        threads = [threading.Thread(target=opener) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert errors == []
        assert [p.name for p in _backups(old)] == ["index.sqlite.format-1"]
        conn = open_index(old)
        assert sorted(stored_edges(conn)) == sorted(_PAIRS)
        conn.close()

    def test_an_opener_that_lost_the_race_does_not_convert_again(
        self, old: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The second opener saw format 1, then waited for the lock. By the time
        it holds the lock the path is format 2, and converting THAT would move
        the new file over the backup."""
        open_index(old).close()
        monkeypatch.setattr(
            selection_index, "_COPY_PAIRS", "SELECT must not run")
        selection_index.convert_format_1(old)
        assert [p.name for p in _backups(old)] == ["index.sqlite.format-1"]


class TestAnUnknownFormat:
    def test_a_newer_format_is_refused_and_left_alone(self, tmp_path: Path) -> None:
        path = tmp_path / "future.sqlite"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE something_else (x)")
        conn.execute(f"PRAGMA user_version = {FORMAT + 1}")
        conn.commit()
        conn.close()
        before = path.read_bytes()
        with pytest.raises(IndexFormatError, match=f"format {FORMAT + 1}"):
            open_index(path)
        assert path.read_bytes() == before


class TestTheQueriesAreIndexLookups:
    """Each plan is asserted on a populated index. ``SCAN`` of the pair table
    is the format-1 cost: every row, on every narrowed run."""

    @pytest.fixture
    def conn(self, tmp_path: Path):
        conn = open_index(tmp_path / "i.sqlite")
        store_edges(conn, {
            f"t/test_{i}.py::t": [(f"/s/m{i % 3}.py", "f")] for i in range(30)})
        conn.commit()
        yield conn
        conn.close()

    @staticmethod
    def _plan(conn: sqlite3.Connection, sql: str, args: tuple) -> str:
        return "\n".join(
            r[3] for r in conn.execute(f"EXPLAIN QUERY PLAN {sql}", args))

    def test_tests_touching_a_file(self, conn) -> None:
        plan = self._plan(conn, selection_index._TOUCHING, ("/s/m0.py",))
        assert "SCAN" not in plan, plan
        assert "test_block_by_block" in plan, plan

    def test_tests_of_one_block(self, conn) -> None:
        plan = self._plan(conn, selection_index._TESTS_OF_BLOCK, ("/s/m0.py", "f"))
        assert "SCAN" not in plan, plan

    def test_measured_tests_never_scan_the_pair_table(self, conn) -> None:
        plan = self._plan(conn, selection_index._MEASURED, ())
        assert "SCAN tb" not in plan and "SCAN test_block" not in plan, plan
        assert "SEARCH tb" in plan, plan

    def test_the_control_a_pair_table_scan_is_what_the_assertions_reject(
        self, conn,
    ) -> None:
        plan = self._plan(conn, "SELECT DISTINCT test FROM test_block", ())
        assert "SCAN test_block" in plan, plan
