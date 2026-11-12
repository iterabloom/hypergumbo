# SPDX-License-Identifier: AGPL-3.0-or-later
"""The prose-only rule: classification, stored evidence, and the tests selected.

Each rule in ``hypergumbo_core.prose_only`` leaves tests out of a run, so each
is pinned from both sides: the case where a test is left out, and the case one
fact away where it must run.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from hypergumbo_core import prose_only as po
from hypergumbo_core.selection_index import open_index

BASE = '''"""Module doc."""
import os


def f(x):
    """Old doc."""
    # old comment
    return os.path.join(x, "a")  # pragma: no cover


class C:
    """Class doc."""

    async def g(self):
        return "not a docstring"
'''


# ── classify ────────────────────────────────────────────────────────────────


def test_a_comment_edit_is_comments() -> None:
    assert po.classify(BASE, BASE.replace("# old comment", "# new\n    # words")) == po.COMMENTS


def test_a_docstring_edit_is_docstrings() -> None:
    for old in ('"""Module doc."""', '"""Old doc."""', '"""Class doc."""'):
        assert po.classify(BASE, BASE.replace(old, '"""Rewritten."""')) == po.DOCSTRINGS


def test_a_code_edit_is_code() -> None:
    assert po.classify(BASE, BASE.replace('"a"', '"b"')) == po.CODE
    # A string that is not the first statement is code, not a docstring.
    assert po.classify(BASE, BASE.replace("not a docstring", "changed")) == po.CODE


def test_a_machine_read_comment_edit_is_code() -> None:
    """Removing ``# pragma: no cover`` changes the coverage gate's answer."""
    assert po.classify(BASE, BASE.replace("  # pragma: no cover", "")) == po.CODE
    assert po.classify(BASE, BASE + "# covers: scripts/x\n") == po.CODE


def test_new_deleted_and_unparsable_files_are_code() -> None:
    assert po.classify(None, BASE) == po.CODE
    assert po.classify(BASE, None) == po.CODE
    assert po.classify(BASE, "def (:\n") == po.CODE
    assert po.classify("def (:\n", BASE) == po.CODE


def test_a_docstring_only_body_keeps_its_shape() -> None:
    """A function whose whole body is a docstring still parses once it is removed."""
    one = 'def f():\n    """Only a docstring."""\n'
    assert po.classify(one, one.replace("Only", "Just")) == po.DOCSTRINGS
    assert po.classify(one, "def f():\n    return 1\n") == po.CODE


def test_a_quoted_directive_counts_but_a_word_in_prose_does_not() -> None:
    """Directives are matched by pattern. One quoted in a docstring errs
    toward ``code``; a comment that merely contains the word is prose."""
    quoted = 'def f():\n    """Mark it ``# noqa`` to silence."""\n'
    assert po.classify(quoted, quoted.replace("silence", "quiet it")) == po.CODE
    prose = "x = 1  # the node type: a leaf\n"
    assert po.classify(prose, prose.replace("a leaf", "a branch")) == po.COMMENTS
    chained = "x = 1  # noqa: E501  # pragma: no cover\n"
    assert po.classify(chained, chained.replace("  # pragma: no cover", "")) == po.CODE
    shebang = "#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\nx = 1\n"
    assert po.classify(shebang, shebang.replace("python3", "python")) == po.CODE
    assert po.classify(shebang, shebang.replace("utf-8", "latin-1")) == po.CODE


# ── changed_docstrings ──────────────────────────────────────────────────────

NESTED = '''"""Module."""


class A:
    """A."""

    class B:
        """B."""

    def m(self):
        """m."""

        def inner():
            """inner."""

    @property
    def p(self):
        """getter."""

    @p.setter
    def p(self, value):
        """setter."""


async def co():
    """co."""


if True:
    def guarded():
        """guarded."""
'''


def test_changed_docstrings_names_owners_as_python_does() -> None:
    """The names must be ``__qualname__``s: that is what the recorder stores
    for ``x.__doc__``."""
    for old, owner in (("Module.", "<module>"), ("A.", "A"), ("B.", "A.B"),
                       ("m.", "A.m"), ("inner.", "A.m.<locals>.inner"),
                       ("co.", "co"), ("guarded.", "guarded")):
        changed = NESTED.replace(f'"""{old}"""', '"""Rewritten."""', 1)
        assert po.changed_docstrings(NESTED, changed) == {owner}, owner


def test_changed_docstrings_sees_each_definition_of_one_name() -> None:
    assert po.changed_docstrings(NESTED, NESTED.replace("setter.", "sets it.")) == {"A.p"}
    assert po.changed_docstrings(NESTED, NESTED.replace("getter.", "gets it.")) == {"A.p"}


def test_changed_docstrings_counts_added_and_removed() -> None:
    bare = "def f():\n    return 1\n"
    with_doc = 'def f():\n    """New."""\n    return 1\n'
    assert po.changed_docstrings(bare, with_doc) == {"f"}
    assert po.changed_docstrings(with_doc, bare) == {"f"}
    assert po.changed_docstrings(NESTED, NESTED) == frozenset()


def test_changed_docstrings_of_unparsable_source_is_empty() -> None:
    assert po.changed_docstrings("def (:\n", NESTED) == frozenset()
    assert po.changed_docstrings(NESTED, "def (:\n") == frozenset()


# ── the stored evidence ─────────────────────────────────────────────────────

TEST_A = "tests/test_a.py"
TEST_B = "tests/test_b.py"
SRC = "pkg/mod.py"


def _repo(tmp_path: Path, *test_files: str) -> Path:
    root = tmp_path / "repo"
    for rel in test_files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("def test_one():\n    pass\n")
    return root


def _bucket(**events: list[str]) -> dict[str, list[str]]:
    return {"reads": [], "opaque": [], "docs": [], "lines": [], "fixtures": [], **events}


def _fold(conn: sqlite3.Connection, root: Path, tmp_path: Path,
          tests: dict[str, dict[str, list[str]]], *, full_run: bool = False,
          **extra: Any) -> int:
    """Fold one process's record. ``expected`` defaults to exactly ``tests``."""
    reads = tmp_path / f"reads{len(list(tmp_path.glob('reads*')))}"
    reads.mkdir(parents=True)
    expected: dict[str, list[str]] = {}
    for test_id in tests:
        expected.setdefault(test_id.split("::")[0], []).append(test_id)
    payload = {"version": po.RECORDER_VERSION, "observed": True,
               "expected": expected, "tests": tests, "fixtures": {},
               "modules": {}, "unattributed": _bucket(), **extra}
    (reads / "main-1.json").write_text(json.dumps(payload))
    return po.fold_run(conn, reads, root, full_run=full_run)


def _index(tmp_path: Path) -> sqlite3.Connection:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return open_index(tmp_path / "index.sqlite")


def _edges(conn: sqlite3.Connection, test_id: str) -> set[tuple[str, str]]:
    return set(conn.execute(
        "SELECT kind, target FROM read_edge WHERE test_id = ?", (test_id,)))


def test_fold_stores_each_kind_of_event(tmp_path: Path) -> None:
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    stored = _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket(
        reads=[SRC], docs=[f"{SRC}::f"], lines=["pkg/other.py"],
        opaque=["subprocess.Popen:git"])})
    assert stored == 1
    assert _edges(conn, f"{TEST_A}::test_one") == {
        (po.READ, SRC), (po.DOC, f"{SRC}::f"), (po.LINE, "pkg/other.py")}
    assert conn.execute("SELECT opaque, observed FROM read_test").fetchall() == [
        ("subprocess.Popen:git", 1)]


def test_fold_gives_a_test_its_modules_and_fixtures_events(tmp_path: Path) -> None:
    """A module-scoped fixture and the module's import run once; every test
    that relies on them inherits what they saw."""
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    _fold(conn, root, tmp_path,
          {f"{TEST_A}::test_one": _bucket(fixtures=["::built", "::never_recorded"]),
           f"{TEST_A}::test_two": _bucket()},
          fixtures={"::built": _bucket(reads=["pkg/by_fixture.py"])},
          modules={TEST_A: _bucket(docs=[po.ANY])})
    assert _edges(conn, f"{TEST_A}::test_one") == {
        (po.READ, "pkg/by_fixture.py"), (po.DOC, po.ANY)}
    assert _edges(conn, f"{TEST_A}::test_two") == {(po.DOC, po.ANY)}


def test_fold_replaces_a_tests_record_and_leaves_the_others(tmp_path: Path) -> None:
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A, TEST_B)
    _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket(reads=[SRC]),
                                 f"{TEST_B}::test_one": _bucket(reads=[SRC])})
    _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket()})
    assert _edges(conn, f"{TEST_A}::test_one") == set()
    assert _edges(conn, f"{TEST_B}::test_one") == {(po.READ, SRC)}


def test_fold_forgets_a_test_file_whose_code_changed(tmp_path: Path) -> None:
    """The old records describe other code, including for the tests this run
    did not repeat."""
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket(),
                                 f"{TEST_A}::test_two": _bucket(reads=[SRC])})
    (root / TEST_A).write_text("def test_one():\n    assert True\n")
    _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket()})
    assert [r[0] for r in conn.execute("SELECT test_id FROM read_test")] == [
        f"{TEST_A}::test_one"]
    assert conn.execute("SELECT COUNT(*) FROM read_edge").fetchone() == (0,)


def test_fold_skips_another_versions_record_and_a_broken_file(tmp_path: Path) -> None:
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    assert _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket()},
                 version=po.RECORDER_VERSION - 1) == 0
    reads = tmp_path / "broken"
    reads.mkdir()
    (reads / "main-1.json").write_text("{not json")
    assert po.fold_run(conn, reads, root) == 0
    assert conn.execute("SELECT COUNT(*) FROM read_test").fetchone() == (0,)


def test_fold_marks_a_test_unobserved_if_any_process_did_not_watch(tmp_path: Path) -> None:
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    reads = tmp_path / "two"
    reads.mkdir()
    for name, observed in (("gw0-1.json", True), ("gw1-2.json", False)):
        (reads / name).write_text(json.dumps({
            "version": po.RECORDER_VERSION, "observed": observed,
            "expected": {TEST_A: [f"{TEST_A}::test_one"]},
            "tests": {f"{TEST_A}::test_one": _bucket()}}))
    po.fold_run(conn, reads, root)
    assert conn.execute("SELECT observed FROM read_test").fetchall() == [(0,)]


def test_unattributed_events_accumulate_until_a_full_run(tmp_path: Path) -> None:
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    one = {f"{TEST_A}::test_one": _bucket()}
    _fold(conn, root, tmp_path, one, unattributed=_bucket(
        reads=["pkg/a.py"], opaque=["os.system"]))
    _fold(conn, root, tmp_path, one, unattributed=_bucket(
        docs=[po.ANY], opaque=["os.fork"]))
    assert set(conn.execute("SELECT kind, target FROM read_global")) == {
        (po.READ, "pkg/a.py"), (po.DOC, po.ANY)}
    assert "os.fork,os.system" in (po.global_refusal(conn) or "")
    _fold(conn, root, tmp_path, one, full_run=True,
          unattributed=_bucket(lines=["pkg/b.py"]))
    assert set(conn.execute("SELECT kind, target FROM read_global")) == {
        (po.LINE, "pkg/b.py")}
    assert po.global_refusal(conn) is None


def test_the_rule_refuses_without_evidence_but_not_for_an_inert_spawn(tmp_path: Path) -> None:
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    assert "no read evidence" in (po.global_refusal(conn) or "")
    _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket()},
          unattributed=_bucket(opaque=["subprocess.Popen:rust-analyzer"]))
    assert po.global_refusal(conn) is None


def test_evidence_of_another_layout_or_recorder_is_discarded(tmp_path: Path) -> None:
    """An old record cannot show that a test does NOT do something the old
    recorder never looked for."""
    conn = sqlite3.connect(tmp_path / "old.sqlite")
    conn.executescript(
        "CREATE TABLE read_edge (test_id TEXT, path TEXT);"
        "INSERT INTO read_edge VALUES ('t', 'p');"
        "CREATE TABLE read_meta (key TEXT PRIMARY KEY, value TEXT);")
    po.ensure_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM read_edge").fetchone() == (0,)
    conn.execute("INSERT INTO read_edge VALUES ('t', 'r', 'p')")
    po.ensure_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM read_edge").fetchone() == (1,)
    conn.execute("UPDATE read_meta SET value = '1' WHERE key = 'recorder'")
    po.ensure_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM read_edge").fetchone() == (0,)


# ── which files are prose-only ──────────────────────────────────────────────


def test_a_file_seen_outside_any_test_is_not_prose_only(tmp_path: Path) -> None:
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket()},
          unattributed=_bucket(reads=["pkg/read.py"], lines=["pkg/lined.py"],
                               docs=["pkg/doc.py::f", "pkg/own.py::*"]))
    kinds = {"pkg/read.py": po.COMMENTS, "pkg/lined.py": po.COMMENTS,
             "pkg/doc.py": po.DOCSTRINGS, "pkg/own.py": po.DOCSTRINGS,
             "pkg/free.py": po.DOCSTRINGS, "pkg/code.py": po.CODE}
    docs = {"pkg/doc.py": frozenset({"f"}), "pkg/own.py": frozenset({"g"}),
            "pkg/free.py": frozenset({"f"})}
    prose, rejected = po.eligible_prose_files(conn, kinds, docs)
    assert prose == {"pkg/free.py": po.DOCSTRINGS}
    assert set(rejected) == {"pkg/read.py", "pkg/lined.py", "pkg/doc.py", "pkg/own.py"}
    assert "read outside any test" in rejected["pkg/read.py"]
    assert "line numbers" in rejected["pkg/lined.py"]
    assert "docstring" in rejected["pkg/doc.py"]


def test_a_docstring_read_outside_tests_blocks_only_that_docstring(tmp_path: Path) -> None:
    """The control for the test above: another owner's docstring changed, or
    only comments did."""
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket()},
          unattributed=_bucket(docs=["pkg/doc.py::f"]))
    prose, rejected = po.eligible_prose_files(
        conn, {"pkg/doc.py": po.DOCSTRINGS, "pkg/c.py": po.COMMENTS},
        {"pkg/doc.py": frozenset({"g"})})
    assert (set(prose), rejected) == ({"pkg/doc.py", "pkg/c.py"}, {})


def test_an_untied_read_outside_tests_blocks_every_file(tmp_path: Path) -> None:
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket()},
          unattributed=_bucket(docs=[po.ANY]))
    prose, rejected = po.eligible_prose_files(
        conn, {"pkg/d.py": po.DOCSTRINGS, "pkg/c.py": po.COMMENTS},
        {"pkg/d.py": frozenset({"f"})})
    assert (set(prose), set(rejected)) == ({"pkg/c.py"}, {"pkg/d.py"})
    _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket()},
          unattributed=_bucket(lines=[po.ANY]))
    prose, rejected = po.eligible_prose_files(conn, {"pkg/c.py": po.COMMENTS}, {})
    assert (prose, set(rejected)) == ({}, {"pkg/c.py"})


# ── which tests a prose-only change runs ────────────────────────────────────

PROSE = {SRC: po.DOCSTRINGS}
DOCS = {SRC: frozenset({"f"})}


def _selected(tmp_path: Path, bucket: dict[str, list[str]],
              prose: dict[str, str] | None = None,
              docs: dict[str, frozenset[str]] | None = None, **extra: Any) -> dict[str, str]:
    """What a change selects when TEST_A's one test has ``bucket`` on record."""
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": bucket}, **extra)
    return po.tests_for_prose(conn, root, [TEST_A, ""], PROSE if prose is None else prose,
                              DOCS if docs is None else docs)


def test_a_test_with_a_clean_record_is_left_out(tmp_path: Path) -> None:
    """The reach check for every test below: the same setup with nothing on
    record selects nothing."""
    assert _selected(tmp_path, _bucket()) == {}


def test_a_test_that_reads_the_file_runs(tmp_path: Path) -> None:
    assert _selected(tmp_path, _bucket(reads=[SRC])) == {TEST_A: f"reads {SRC}"}


def test_a_read_of_another_file_does_not_select(tmp_path: Path) -> None:
    assert _selected(tmp_path, _bucket(reads=["pkg/other.py"])) == {}


def test_a_test_that_reads_the_changed_docstring_runs(tmp_path: Path) -> None:
    for n, target in enumerate((f"{SRC}::f", f"{SRC}::*", po.ANY)):
        got = _selected(tmp_path / str(n), _bucket(docs=[target]))
        assert list(got) == [TEST_A] and "docstring" in got[TEST_A], target


def test_a_read_of_an_unchanged_docstring_does_not_select(tmp_path: Path) -> None:
    assert _selected(tmp_path / "a", _bucket(docs=[f"{SRC}::g"])) == {}
    assert _selected(tmp_path / "b", _bucket(docs=["pkg/other.py::f"])) == {}
    # Comments changed and no docstring did: nothing for a docstring reader.
    comments = {SRC: po.COMMENTS}
    assert _selected(tmp_path / "c", _bucket(docs=[po.ANY, f"{SRC}::*"]),
                     prose=comments, docs={}) == {}


def test_a_test_that_reads_line_numbers_runs(tmp_path: Path) -> None:
    for n, target in enumerate((SRC, po.ANY)):
        got = _selected(tmp_path / str(n), _bucket(lines=[target]),
                        prose={SRC: po.COMMENTS}, docs={})
        assert list(got) == [TEST_A] and "line numbers" in got[TEST_A], target
    assert _selected(tmp_path / "other", _bucket(lines=["pkg/other.py"])) == {}


def test_a_test_that_starts_a_process_runs_unless_the_program_is_inert(tmp_path: Path) -> None:
    got = _selected(tmp_path / "a", _bucket(
        opaque=["subprocess.Popen:python", "subprocess.Popen:rust-analyzer"]))
    assert got == {TEST_A: "starts a process (subprocess.Popen:python)"}
    assert _selected(tmp_path / "b", _bucket(
        opaque=["subprocess.Popen:rust-analyzer", "subprocess.Popen:sleep"])) == {}
    assert _selected(tmp_path / "c", _bucket(opaque=["os.fork"])) == {
        TEST_A: "starts a process (os.fork)"}


def test_a_test_recorded_without_runtime_observation_runs(tmp_path: Path) -> None:
    got = _selected(tmp_path, _bucket(), observed=False)
    assert list(got) == [TEST_A] and "runtime observation" in got[TEST_A]


def test_a_changed_test_file_runs_itself(tmp_path: Path) -> None:
    got = _selected(tmp_path, _bucket(), prose={TEST_A: po.COMMENTS}, docs={})
    assert got == {TEST_A: "is one of the changed files"}


def test_a_test_file_with_no_record_runs(tmp_path: Path) -> None:
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A, TEST_B)
    _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket()})
    assert po.tests_for_prose(conn, root, [TEST_A, TEST_B], PROSE, DOCS) == {
        TEST_B: "no read record"}


def test_a_record_older_than_the_tests_code_does_not_count(tmp_path: Path) -> None:
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    _fold(conn, root, tmp_path, {f"{TEST_A}::test_one": _bucket()})
    (root / TEST_A).write_text("def test_one():\n    assert True\n")
    assert po.tests_for_prose(conn, root, [TEST_A], PROSE, DOCS) == {
        TEST_A: "read record predates a code change to the test"}
    # A prose-only edit of the test file keeps the record current.
    (root / TEST_A).write_text("def test_one():\n    pass  # same code\n")
    assert po.tests_for_prose(conn, root, [TEST_A], PROSE, DOCS) == {}
    (root / TEST_A).write_text("def (:\n")
    assert list(po.tests_for_prose(conn, root, [TEST_A], PROSE, DOCS)) == [TEST_A]
    (root / TEST_A).unlink()
    assert list(po.tests_for_prose(conn, root, [TEST_A], PROSE, DOCS)) == [TEST_A]


def test_a_file_with_a_test_that_never_ran_is_incomplete(tmp_path: Path) -> None:
    """A run cut short records some of a file's tests; the others may read
    anything."""
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A, TEST_B)
    _fold(conn, root, tmp_path,
          {f"{TEST_A}::test_one": _bucket(), f"{TEST_B}::test_one": _bucket()},
          expected={TEST_A: [f"{TEST_A}::test_one", f"{TEST_A}::test_two"]})
    assert po.tests_for_prose(conn, root, [TEST_A, TEST_B], PROSE, DOCS) == {
        TEST_A: "read record is incomplete", TEST_B: "read record is incomplete"}


# ── select_for_prose ────────────────────────────────────────────────────────

OLD = 'def f():\n    """Old."""\n    return 1\n'
NEW = 'def f():\n    """New."""\n    return 1\n'


def test_select_for_prose_splits_the_change_and_selects_from_evidence(tmp_path: Path) -> None:
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A, TEST_B)
    _fold(conn, root, tmp_path,
          {f"{TEST_A}::test_one": _bucket(docs=[f"{SRC}::f"]),
           f"{TEST_B}::test_one": _bucket(docs=[f"{SRC}::g"])},
          unattributed=_bucket(reads=["pkg/global.py"]))
    result = po.select_for_prose(conn, root, {
        SRC: (OLD, NEW),
        "pkg/code.py": (OLD, OLD.replace("return 1", "return 2")),
        "pkg/new.py": (None, NEW),
        "pkg/global.py": (OLD, OLD + "# trailing comment\n"),
    }, [TEST_A, TEST_B])
    assert result.files == {SRC: po.DOCSTRINGS}
    assert set(result.rejected) == {"pkg/global.py"}
    assert result.tests == {TEST_A: f"reads the docstring of {SRC}::f"}


def test_select_for_prose_with_no_prose_only_file_selects_nothing(tmp_path: Path) -> None:
    """The tests of a code change are the caller's to select."""
    conn, root = _index(tmp_path), _repo(tmp_path, TEST_A)
    result = po.select_for_prose(
        conn, root, {SRC: (OLD, OLD.replace("return 1", "return 2"))}, [TEST_A])
    assert (result.files, result.rejected, result.tests) == ({}, {}, {})
