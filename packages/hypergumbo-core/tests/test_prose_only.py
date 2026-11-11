# SPDX-License-Identifier: AGPL-3.0-or-later
"""The prose-only rule: classification, stored evidence, and what may be dropped.

Each rule in ``hypergumbo_core.prose_only`` removes tests from a run, so each
is pinned from both sides: the case where a test is dropped, and the case one
fact away where it must be kept.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

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


def test_prose_that_quotes_a_machine_comment_counts_as_one() -> None:
    """The comments are matched by pattern, so this errs toward ``code``."""
    quoted = 'def f():\n    """Mark it ``# noqa`` to silence."""\n'
    assert po.classify(quoted, quoted.replace("silence", "quiet it")) == po.CODE


# ── introspection ───────────────────────────────────────────────────────────


def test_introspection_finds_each_channel() -> None:
    found = po.introspection(
        "import inspect\n"
        "from linecache import getline\n"
        "A = __doc__\n"
        "B = f.__doc__\n"
        "C = getattr(f, '__doc__')\n"
        "D = inspect.getdoc(f)\n"
        "E = frame.f_lineno\n"
        "F = '%(lineno)d'\n"
        "G = inspect.getsource(f)\n"
        "help(f)\n"
        "import pydoc.x\n"
    )
    assert found.own_doc
    assert found.doc == {4, 5, 6, 10, 11}
    assert found.line == {7, 8}
    assert found.source == {2, 9}
    assert found.any()


def test_introspection_ignores_ordinary_code() -> None:
    # "lineno" passes the cheap word test; the AST finds no marker.
    assert not po.introspection("x = node.lineno\nhelp_text = 1\nobj.help()\n").any()
    assert not po.introspection("x = 1\n").any()
    assert not po.introspection("__doc__ (\n").any()


# ── fold_run ────────────────────────────────────────────────────────────────


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "packages" / "p" / "src" / "p").mkdir(parents=True)
    for name in ("test_a.py", "test_b.py", "test_c.py"):
        (root / "tests" / name).write_text("def test_x():\n    pass\n")
    return root


def _conn(tmp_path: Path) -> sqlite3.Connection:
    return open_index(tmp_path / "index.sqlite")


def _write_run(directory: Path, name: str, **payload: object) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps(payload))


def _bucket(reads: tuple[str, ...] = (), opaque: tuple[str, ...] = (),
            fixtures: tuple[str, ...] = ()) -> dict[str, list[str]]:
    return {"reads": list(reads), "opaque": list(opaque), "fixtures": list(fixtures)}


def _edges(conn: sqlite3.Connection) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for test_id, path in conn.execute("SELECT test_id, path FROM read_edge"):
        out.setdefault(test_id, set()).add(path)
    return out


def test_fold_unions_fixture_and_module_reads_across_processes(tmp_path: Path) -> None:
    root, conn, run = _repo(tmp_path), _conn(tmp_path), tmp_path / "run"
    _write_run(
        run, "gw0",
        expected={"tests/test_a.py": ["tests/test_a.py::test_x"]},
        tests={"tests/test_a.py::test_x": _bucket(("own.py",), fixtures=("::built", "::unknown"))},
        fixtures={"::built": _bucket()},
        modules={"tests/test_a.py": _bucket(("at_import.py",), ("os.system",))},
        unattributed=_bucket(("everyone.py",)),
    )
    # Another worker built the fixture this test used.
    _write_run(run, "gw1", fixtures={"::built": _bucket(("built_from.py",), ("os.fork",))})
    (run / "broken.json").write_text("{")
    assert po.fold_run(conn, run, root) == 1
    assert _edges(conn) == {
        "tests/test_a.py::test_x": {"own.py", "at_import.py", "built_from.py"}}
    assert conn.execute("SELECT opaque FROM read_test").fetchall() == [("os.fork,os.system",)]
    assert conn.execute("SELECT path FROM read_global").fetchall() == [("everyone.py",)]


def test_fold_replaces_per_test_and_leaves_absent_tests(tmp_path: Path) -> None:
    root, conn = _repo(tmp_path), _conn(tmp_path)
    _write_run(tmp_path / "r1", "main", tests={
        "tests/test_a.py::test_x": _bucket(("old.py",)),
        "tests/test_b.py::test_x": _bucket(("kept.py",)),
    })
    po.fold_run(conn, tmp_path / "r1", root)
    _write_run(tmp_path / "r2", "main", tests={"tests/test_a.py::test_x": _bucket(("new.py",))})
    po.fold_run(conn, tmp_path / "r2", root)
    assert _edges(conn) == {"tests/test_a.py::test_x": {"new.py"},
                            "tests/test_b.py::test_x": {"kept.py"}}


def test_fold_discards_records_taken_at_other_code(tmp_path: Path) -> None:
    """A code edit to the test file invalidates its other tests' records; a
    prose edit does not."""
    root, conn = _repo(tmp_path), _conn(tmp_path)
    test_a = root / "tests" / "test_a.py"
    test_a.write_text("def test_x():\n    pass\n\ndef test_y():\n    pass\n")
    _write_run(tmp_path / "r1", "main", tests={
        "tests/test_a.py::test_x": _bucket(), "tests/test_a.py::test_y": _bucket(("y.py",))})
    po.fold_run(conn, tmp_path / "r1", root)
    _write_run(tmp_path / "r2", "main", tests={"tests/test_a.py::test_x": _bucket()})

    test_a.write_text(test_a.read_text() + "# a comment\n")
    po.fold_run(conn, tmp_path / "r2", root)
    assert "tests/test_a.py::test_y" in _edges(conn)

    test_a.write_text(test_a.read_text() + "X = 1\n")
    po.fold_run(conn, tmp_path / "r2", root)
    assert _edges(conn) == {}
    assert [r[0] for r in conn.execute("SELECT test_id FROM read_test")] == [
        "tests/test_a.py::test_x"]


def test_fold_global_facts_accumulate_until_a_full_run(tmp_path: Path) -> None:
    root, conn = _repo(tmp_path), _conn(tmp_path)
    one = {"tests/test_a.py::test_x": _bucket()}
    _write_run(tmp_path / "r1", "main", tests=one, unattributed=_bucket(("a.py",), ("os.system",)))
    _write_run(tmp_path / "r2", "main", tests=one, unattributed=_bucket(("b.py",), ("os.fork",)))
    _write_run(tmp_path / "r3", "main", tests=one)
    po.fold_run(conn, tmp_path / "r1", root)
    assert "os.system" in (po.global_refusal(conn) or "")
    po.fold_run(conn, tmp_path / "r2", root)
    assert {r[0] for r in conn.execute("SELECT path FROM read_global")} == {"a.py", "b.py"}
    assert "os.fork,os.system" in (po.global_refusal(conn) or "")
    po.fold_run(conn, tmp_path / "r3", root, full_run=True)
    assert conn.execute("SELECT path FROM read_global").fetchall() == []
    assert po.global_refusal(conn) is None


def test_global_refusal_without_any_evidence(tmp_path: Path) -> None:
    assert "no read evidence" in (po.global_refusal(_conn(tmp_path)) or "")


def test_a_test_file_that_cannot_be_read_or_parsed_still_gets_a_digest(tmp_path: Path) -> None:
    root, conn = _repo(tmp_path), _conn(tmp_path)
    (root / "tests" / "test_b.py").write_text("def (:\n")
    _write_run(tmp_path / "r1", "main", tests={
        "tests/test_gone.py::test_x": _bucket(), "tests/test_b.py::test_x": _bucket()})
    po.fold_run(conn, tmp_path / "r1", root)
    digests = dict(conn.execute("SELECT test_file, digest FROM read_file"))
    assert digests["tests/test_gone.py"] == ""
    assert len(digests["tests/test_b.py"]) == 64


# ── eligible_prose_files ────────────────────────────────────────────────────


def test_eligibility(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    po.ensure_schema(conn)
    conn.execute("INSERT INTO read_global (path) VALUES ('everyone.py')")
    prose, rejected = po.eligible_prose_files(
        conn,
        {"a.py": po.COMMENTS, "b.py": po.DOCSTRINGS, "code.py": po.CODE,
         "everyone.py": po.COMMENTS, "cli.py": po.DOCSTRINGS, "cli2.py": po.COMMENTS},
        {"cli.py": "p = Parser(description=__doc__)\n",
         "cli2.py": "p = Parser(description=__doc__)\n"},
    )
    # A file using its own __doc__ is rejected only when its docstrings changed.
    assert prose == {"a.py": po.COMMENTS, "b.py": po.DOCSTRINGS, "cli2.py": po.COMMENTS}
    assert set(rejected) == {"everyone.py", "cli.py"}


# ── narrow_for_prose ────────────────────────────────────────────────────────

SRC = "packages/p/src/p/mod.py"
INTRO = "packages/p/src/p/intro.py"
INTRO_TEXT = (
    "import inspect\n"
    "def version(m):\n"
    "    return inspect.getsource(m)\n"
    "def doc(k):\n"
    "    return k.__doc__\n"
    "def where(frame):\n"
    "    return frame.f_lineno\n"
)


def _evidence(tmp_path: Path, tests: dict[str, dict[str, list[str]]],
              expected: dict[str, list[str]] | None = None) -> tuple[Path, sqlite3.Connection]:
    root, conn = _repo(tmp_path), _conn(tmp_path)
    if expected is None:
        expected = {}
        for test_id in tests:
            expected.setdefault(test_id.split("::")[0], []).append(test_id)
    _write_run(tmp_path / "run", "main", tests=tests, expected=expected)
    po.fold_run(conn, tmp_path / "run", root, full_run=True)
    # The coverage index knows every recorded test unless a test says otherwise.
    conn.executemany("INSERT OR IGNORE INTO unmeasured_test (test_id) VALUES (?)",
                     [(t,) for t in tests])
    conn.commit()
    return root, conn


ALL = ("tests/test_a.py", "tests/test_b.py", "tests/test_c.py")
PLAIN = {f"{f}::test_x": {"reads": [], "opaque": [], "fixtures": []} for f in ALL}


def _narrow(root: Path, conn: sqlite3.Connection, prose: dict[str, str],
            sources: dict[str, str] | None = None, **kw: object) -> po.ProseNarrowing:
    return po.narrow_for_prose(conn, root, ALL, prose,
                               tracked_sources=sources or {}, **kw)  # type: ignore[arg-type]


def test_a_test_with_complete_clean_evidence_is_dropped(tmp_path: Path) -> None:
    root, conn = _evidence(tmp_path, PLAIN)
    result = _narrow(root, conn, {SRC: po.COMMENTS})
    assert result.dropped == set(ALL)
    assert result.kept == frozenset()
    assert result.reasons == {}


def test_a_reader_is_kept_and_an_unselected_reader_is_added(tmp_path: Path) -> None:
    tests = {**PLAIN,
             "tests/test_a.py::test_x": _bucket((SRC,)),
             "tests/test_gate.py::test_x": _bucket((SRC,)),
             "tests/test_deleted.py::test_x": _bucket((SRC,))}
    root, conn = _evidence(tmp_path, tests)
    (root / "tests" / "test_gate.py").write_text("def test_x():\n    pass\n")
    result = _narrow(root, conn, {SRC: po.COMMENTS})
    assert result.reasons == {"tests/test_a.py": f"reads {SRC}"}
    # The gate was not in the run set; the deleted reader is not resurrected.
    assert result.added == {"tests/test_gate.py"}
    assert result.kept == {"tests/test_a.py", "tests/test_gate.py"}


def test_a_reader_of_some_other_file_is_dropped(tmp_path: Path) -> None:
    root, conn = _evidence(tmp_path, {**PLAIN, "tests/test_a.py::test_x": _bucket(("other.py",))})
    assert _narrow(root, conn, {SRC: po.COMMENTS}).dropped == set(ALL)


def test_an_opaque_test_is_kept(tmp_path: Path) -> None:
    root, conn = _evidence(tmp_path, {
        **PLAIN, "tests/test_a.py::test_x": _bucket(opaque=("subprocess.Popen:git",))})
    result = _narrow(root, conn, {SRC: po.COMMENTS})
    assert result.reasons == {"tests/test_a.py": "starts a process (subprocess.Popen:git)"}


def test_missing_stale_and_incomplete_records_are_kept(tmp_path: Path) -> None:
    tests = {k: v for k, v in PLAIN.items() if not k.startswith("tests/test_a.py")}
    expected = {"tests/test_b.py": ["tests/test_b.py::test_x", "tests/test_b.py::test_never_ran"],
                "tests/test_c.py": ["tests/test_c.py::test_x"]}
    root, conn = _evidence(tmp_path, tests, expected)
    (root / "tests" / "test_c.py").write_text("def test_x():\n    assert True\n")
    result = _narrow(root, conn, {SRC: po.COMMENTS})
    assert result.reasons == {
        "tests/test_a.py": "no read record",
        "tests/test_b.py": "read record is incomplete",
        "tests/test_c.py": "read record predates a code change to the test",
    }
    assert result.dropped == frozenset()


def test_a_record_with_no_collection_list_is_incomplete(tmp_path: Path) -> None:
    root, conn = _evidence(tmp_path, PLAIN, expected={})
    assert set(_narrow(root, conn, {SRC: po.COMMENTS}).reasons.values()) == {
        "read record is incomplete"}


def test_a_test_the_coverage_index_does_not_know_is_kept(tmp_path: Path) -> None:
    root, conn = _evidence(tmp_path, PLAIN)
    conn.execute("DELETE FROM unmeasured_test WHERE test_id LIKE 'tests/test_a.py%'")
    result = _narrow(root, conn, {SRC: po.COMMENTS})
    assert result.reasons == {
        "tests/test_a.py": "coverage index does not know every test in it"}


def test_the_non_prose_selection_is_kept_whole(tmp_path: Path) -> None:
    root, conn = _evidence(tmp_path, PLAIN)
    result = _narrow(root, conn, {SRC: po.COMMENTS}, keep=["tests/test_b.py", "tests/test_z.py", ""])
    assert result.kept == {"tests/test_b.py", "tests/test_z.py"}
    assert result.dropped == {"tests/test_a.py", "tests/test_c.py"}


def test_a_test_file_that_introspects_is_kept(tmp_path: Path) -> None:
    root, conn = _evidence(tmp_path, PLAIN)
    result = _narrow(root, conn, {SRC: po.COMMENTS},
                     {"tests/test_a.py": "def test_x():\n    assert f.__doc__\n"})
    assert result.reasons == {
        "tests/test_a.py": "introspects docstrings, line numbers or source itself"}


def _block_edge(conn: sqlite3.Connection, root: Path, test_id: str, block: str) -> None:
    conn.execute("INSERT INTO test_block (test_id, path, name) VALUES (?, ?, ?)",
                 (f"{root}/{test_id}", str(root / INTRO), block))
    conn.commit()


def test_line_number_introspection_keeps_the_tests_that_execute_it(tmp_path: Path) -> None:
    root, conn = _evidence(tmp_path, PLAIN)
    _block_edge(conn, root, "tests/test_a.py::test_x", "where")
    result = _narrow(root, conn, {SRC: po.COMMENTS}, {INTRO: INTRO_TEXT})
    assert result.reasons == {
        "tests/test_a.py": "executes code that introspects docstrings, line numbers or source"}
    assert result.dropped == {"tests/test_b.py", "tests/test_c.py"}


def test_docstring_introspection_counts_only_when_docstrings_changed(tmp_path: Path) -> None:
    root, conn = _evidence(tmp_path, PLAIN)
    _block_edge(conn, root, "tests/test_a.py::test_x", "doc")
    assert _narrow(root, conn, {SRC: po.COMMENTS}, {INTRO: INTRO_TEXT}).dropped == set(ALL)
    assert "tests/test_a.py" in _narrow(
        root, conn, {SRC: po.DOCSTRINGS}, {INTRO: INTRO_TEXT}).reasons


def test_a_getsource_block_counts_only_if_one_of_its_tests_read_the_file(tmp_path: Path) -> None:
    """linecache serves later callers a copy, so the first caller's read stands
    for all of them -- and if none read the file, none can be served it."""
    tests = {**PLAIN, "tests/test_b.py::test_x": _bucket(("elsewhere.py",))}
    root, conn = _evidence(tmp_path, tests)
    _block_edge(conn, root, "tests/test_a.py::test_x", "version")
    _block_edge(conn, root, "tests/test_b.py::test_x", "version")
    assert _narrow(root, conn, {SRC: po.COMMENTS}, {INTRO: INTRO_TEXT}).dropped == set(ALL)

    result = _narrow(root, conn, {"elsewhere.py": po.COMMENTS}, {INTRO: INTRO_TEXT})
    assert result.reasons["tests/test_b.py"] == "reads elsewhere.py"
    assert result.reasons["tests/test_a.py"].startswith("executes code that introspects")
    assert result.dropped == {"tests/test_c.py"}


def test_a_getsource_block_counts_when_a_test_executing_it_has_no_record(tmp_path: Path) -> None:
    root, conn = _evidence(tmp_path, PLAIN)
    _block_edge(conn, root, "tests/test_a.py::test_x", "version")
    _block_edge(conn, root, "tests/test_unrecorded.py::test_x", "version")
    assert "tests/test_a.py" in _narrow(root, conn, {SRC: po.COMMENTS}, {INTRO: INTRO_TEXT}).reasons


def test_introspection_outside_coverage_is_matched_by_place_and_name(tmp_path: Path) -> None:
    root, conn = _evidence(tmp_path, PLAIN)
    line_marker = "x = frame.f_lineno\n"
    by_name = _narrow(root, conn, {SRC: po.COMMENTS}, {
        "scripts/measure-thing.py": line_marker,
        "tests/test_a.py": "run('scripts/measure-thing.py')\n",
        "tests/test_b.py": "import measure_thing\n",
    })
    assert by_name.reasons == {
        "tests/test_a.py": "may execute introspecting code in scripts/measure-thing.py",
        "tests/test_b.py": "may execute introspecting code in scripts/measure-thing.py",
    }
    by_place = _narrow(root, conn, {SRC: po.COMMENTS}, {"tests/conftest.py": line_marker})
    assert set(by_place.reasons) == set(ALL)
    # A file that only uses its own __doc__ introspects nobody else.
    assert _narrow(root, conn, {SRC: po.DOCSTRINGS},
                   {"scripts/tool.py": "d = __doc__\n"}).dropped == set(ALL)
