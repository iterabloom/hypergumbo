# SPDX-License-Identifier: AGPL-3.0-or-later
"""A test in a pytest-xdist group has ONE spelling in everything stored.

With ``--dist loadgroup`` pytest-xdist renames every test that carries
``@pytest.mark.xdist_group("g")``: its node id becomes ``<id>@g`` on the
workers. Coverage contexts, the junit report and the recorder's per-test
record are written after that and carry the suffix. The recorder's list of
the tests it expected is taken at collection and does not. A run without
xdist carries it nowhere.

Stored as they came, the two spellings never join: the prose rule read the
record of such a file as incomplete (every expected test "had no record"),
the index kept two rows per test, and ``<id>@g`` is not an id pytest accepts
on its command line. These tests pin that every way into the stores drops the
suffix.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hypergumbo_core import prose_only as po
from hypergumbo_core.selection_index import (
    normalize_test_id,
    open_index,
    statement_records,
    update_from_run,
    without_xdist_group,
)
from hypergumbo_core.selection_shadow import failed_tests_from_junit, ran_tests_from_junit


@pytest.mark.parametrize(("stored", "node_id"), [
    ("tests/test_x.py::test_one", "tests/test_x.py::test_one@group"),
    ("tests/test_x.py::Klass::test_one", "tests/test_x.py::Klass::test_one@a-group.name"),
    ("tests/test_x.py::test_one[a]", "tests/test_x.py::test_one[a]@group"),
    # ``::`` inside a parameter: the suffix is what follows the LAST bracket.
    ("tests/test_x.py::test_one[std::net-bind]", "tests/test_x.py::test_one[std::net-bind]@g"),
])
def test_the_group_suffix_is_dropped(stored: str, node_id: str) -> None:
    assert without_xdist_group(node_id) == stored
    assert without_xdist_group(stored) == stored


@pytest.mark.parametrize("node_id", [
    "tests/test_x.py::test_one",
    # An ``@`` inside a parameter is the parameter's.
    "tests/test_x.py::test_one[user@example.org]",
    "tests/test_x.py::test_one[a]",
    # Not a test id at all: a file, and a directory with an ``@`` in its name.
    "tests/test_x.py",
    "some@dir/test_x.py",
    "some@dir/test_x.py::test_one",
])
def test_an_id_with_no_group_suffix_is_left_alone(node_id: str) -> None:
    assert without_xdist_group(node_id) == node_id


def test_an_empty_suffix_is_not_a_group() -> None:
    """xdist does not rename a test whose group name is empty."""
    assert without_xdist_group("tests/test_x.py::test_one@") == "tests/test_x.py::test_one@"


def test_normalising_an_id_drops_the_suffix_and_then_finds_the_file(tmp_path: Path) -> None:
    test_file = tmp_path / "packages" / "p" / "tests" / "test_x.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_one():\n    pass\n")
    assert normalize_test_id("tests/test_x.py::test_one@g", tmp_path) == (
        "packages/p/tests/test_x.py::test_one")
    assert normalize_test_id("tests/test_x.py::test_one@g", None) == "tests/test_x.py::test_one"


_GENERATE = '''\
import importlib
import json
import sys
import coverage

db, spec = sys.argv[1], json.loads(sys.argv[2])
cov = coverage.Coverage(data_file=db, source=spec["measure"])
cov.start()
sys.path[:0] = spec["measure"]
for context, calls in spec["calls"]:
    cov.switch_context(context)
    for call in calls:
        module, function = call.split(".")
        getattr(importlib.import_module(module), function)()
cov.stop()
cov.save()
'''


def test_the_coverage_fold_stores_one_spelling(tmp_path: Path) -> None:
    """One run under xdist (suffix) and one without: the same test."""
    source = tmp_path / "repo" / "packages" / "p" / "src" / "p" / "mod.py"
    source.parent.mkdir(parents=True)
    source.write_text("def own():\n    return 1\n")
    conn = open_index(tmp_path / "index.sqlite")
    test = "tests/test_x.py::test_one"
    for n, context in enumerate((f"{test}@group|run", f"{test}|run")):
        db = tmp_path / f"run-{n}.coverage"
        spec = {"measure": [str(source.parent)], "calls": [(context, ["mod.own"])]}
        subprocess.run([sys.executable, "-c", textwrap.dedent(_GENERATE), str(db),
                        json.dumps(spec)], check=True, capture_output=True, text=True)
        update_from_run(conn, db, ran_tests=[f"{test}@group"], repo_root=tmp_path / "repo",
                        full_run=True)
        assert set(statement_records(conn, str(source), "own")) == {test}
    assert [row[0] for row in conn.execute("SELECT test_id FROM test")] == [test]
    assert not list(conn.execute("SELECT test_id FROM unmeasured_test"))


_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2">
<testcase classname="pkg.tests.test_x.Klass" name="test_one@group" time="0.1"/>
<testcase classname="pkg.tests.test_x" name="test_two[a]@group" time="0.1">
<failure message="boom">boom</failure>
</testcase>
</testsuite></testsuites>
"""


def test_the_junit_readers_drop_the_suffix(tmp_path: Path) -> None:
    test_file = tmp_path / "pkg" / "tests" / "test_x.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("")
    junit = tmp_path / "junit.xml"
    junit.write_text(_JUNIT)
    assert ran_tests_from_junit(junit, tmp_path) == {
        "pkg/tests/test_x.py::Klass::test_one", "pkg/tests/test_x.py::test_two[a]"}
    assert failed_tests_from_junit(junit, tmp_path) == {"pkg/tests/test_x.py::test_two[a]"}


TEST_FILE = "tests/test_a.py"
ONE = f"{TEST_FILE}::test_one"
TWO = f"{TEST_FILE}::test_two"


def _bucket(**events: list[str]) -> dict[str, list[str]]:
    return {"reads": [], "opaque": [], "docs": [], "lines": [], "fixtures": [], **events}


def _fold(tmp_path: Path, tests: dict[str, dict[str, list[str]]], *,
          failed: tuple[str, ...] = (), skip: tuple[str, ...] = (),
          expected: tuple[str, ...] = (ONE, TWO)):
    """Fold a record as an xdist worker writes it: ``expected`` without the
    suffix (collection), everything else with it."""
    root = tmp_path / "repo"
    (root / TEST_FILE).parent.mkdir(parents=True, exist_ok=True)
    (root / TEST_FILE).write_text("def test_one():\n    pass\n\n\ndef test_two():\n    pass\n")
    reads = tmp_path / "reads"
    reads.mkdir()
    (reads / "gw0-1.json").write_text(json.dumps({
        "version": po.RECORDER_VERSION, "observed": True,
        "expected": {TEST_FILE: list(expected)}, "failed": list(failed),
        "tests": tests, "fixtures": {}, "modules": {}, "unattributed": _bucket()}))
    conn = open_index(tmp_path / "index.sqlite")
    po.fold_run(conn, reads, root, skip=skip)
    return conn, root


def test_the_read_fold_stores_one_spelling_and_the_record_is_complete(tmp_path: Path) -> None:
    conn, root = _fold(tmp_path, {f"{ONE}@group": _bucket(), f"{TWO}@group": _bucket()})
    assert {row[0] for row in conn.execute("SELECT test_id FROM read_test")} == {ONE, TWO}
    assert {row[0] for row in conn.execute("SELECT test_id FROM read_expected")} == {ONE, TWO}
    # The file has a complete record that shows no way of seeing the change:
    # a prose-only change leaves it out. Read as incomplete, it would run.
    assert po.tests_for_prose(conn, root, [TEST_FILE], {"pkg/mod.py": po.DOCSTRINGS},
                              {"pkg/mod.py": frozenset({"f"})}) == {}


def test_a_failed_test_named_with_the_suffix_is_still_not_stored(tmp_path: Path) -> None:
    tests = {f"{ONE}@group": _bucket(), f"{TWO}@group": _bucket()}
    conn, _ = _fold(tmp_path, tests, failed=(f"{ONE}@group",))
    assert {row[0] for row in conn.execute("SELECT test_id FROM read_test")} == {TWO}


def test_a_test_the_caller_says_to_skip_is_not_stored_in_either_spelling(tmp_path: Path) -> None:
    tests = {f"{ONE}@group": _bucket(), f"{TWO}@group": _bucket()}
    conn, _ = _fold(tmp_path, tests, skip=(f"{TWO}@group",))
    assert {row[0] for row in conn.execute("SELECT test_id FROM read_test")} == {ONE}


def test_two_processes_records_of_one_test_merge_across_spellings(tmp_path: Path) -> None:
    """A worker under a group and a process without xdist name the same test."""
    conn, _ = _fold(tmp_path, {f"{ONE}@group": _bucket(reads=["pkg/a.py"]),
                               ONE: _bucket(reads=["pkg/b.py"]), TWO: _bucket()})
    assert set(conn.execute(
        "SELECT target FROM read_edge WHERE test_id = ?", (ONE,))) == {
            ("pkg/a.py",), ("pkg/b.py",)}


def test_an_expected_list_written_after_the_renaming_is_stored_the_same(tmp_path: Path) -> None:
    """The recorder takes its list before xdist renames the tests because its
    collection hook runs first. Nothing here depends on that order."""
    conn, root = _fold(tmp_path, {f"{ONE}@group": _bucket(), f"{TWO}@group": _bucket()},
                       expected=(f"{ONE}@group", f"{TWO}@group"))
    assert {row[0] for row in conn.execute("SELECT test_id FROM read_expected")} == {ONE, TWO}
    assert po.tests_for_prose(conn, root, [TEST_FILE], {"pkg/mod.py": po.DOCSTRINGS},
                              {"pkg/mod.py": frozenset({"f"})}) == {}
