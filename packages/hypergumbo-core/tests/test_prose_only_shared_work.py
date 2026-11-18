# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which shared work each test of a run used, read from the recorder's files.

The recorder plugin writes one file per pytest process. Per test it lists the
keys of the module, class and session fixtures the test used; ``nested`` lists,
per piece of shared work, the shared work done INSIDE it, each named
``<kind>|<key>`` (a module fixture that fetches a session fixture by name). A test that uses the outer
one depends on the inner one's code too, and pytest does not put the inner one
in that test's own list when the outer fixture was already built.

``shared_work_used`` turns that into the coverage contexts whose rows each
test inherits (``selection_index.update_from_run`` does the inheriting).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypergumbo_core import prose_only as po
from hypergumbo_core.selection_index import shared_context

FIRST = "tests/test_x.py::test_first"
SECOND = "tests/test_x.py::test_second"
OUTER = "tests/test_x.py::outer"
INNER = "::inner"
DEEPEST = "tests::deepest"


def _bucket(*fixtures: str) -> dict[str, list[str]]:
    return {"reads": [], "opaque": [], "docs": [], "lines": [], "fixtures": list(fixtures)}


def _write(reads: Path, name: str, tests: dict[str, Any], *,
           nested: dict[str, list[str]] | None = None,
           version: int = po.RECORDER_VERSION) -> None:
    reads.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"version": version, "observed": True, "expected": {},
                               "tests": tests, "fixtures": {}, "modules": {},
                               "unattributed": _bucket()}
    if nested is not None:
        payload["nested"] = {f"fixture|{key}": [f"fixture|{inner}" for inner in inside]
                             for key, inside in nested.items()}
    (reads / name).write_text(json.dumps(payload))


def _ctx(key: str) -> str:
    return shared_context("fixture", key)


def test_each_test_gets_the_contexts_of_the_fixtures_it_used(tmp_path: Path) -> None:
    _write(tmp_path, "main-1.json", {FIRST: _bucket(OUTER), SECOND: _bucket()})
    assert po.shared_work_used(tmp_path) == {FIRST: frozenset({_ctx(OUTER)}), SECOND: frozenset()}


def test_a_fixture_built_inside_another_reaches_the_outer_ones_users(tmp_path: Path) -> None:
    """SECOND found ``outer`` already built: its own list names ``outer`` only."""
    _write(tmp_path, "main-1.json", {FIRST: _bucket(OUTER, INNER), SECOND: _bucket(OUTER)},
           nested={OUTER: [INNER], INNER: [DEEPEST]})
    assert po.shared_work_used(tmp_path)[SECOND] == frozenset(
        {_ctx(OUTER), _ctx(INNER), _ctx(DEEPEST)})


def test_nesting_recorded_by_another_process_counts(tmp_path: Path) -> None:
    """One worker builds the fixture; a test on another worker names it. The
    code beneath it is the same code whichever worker ran it."""
    _write(tmp_path, "gw0-1.json", {FIRST: _bucket(OUTER)}, nested={OUTER: [INNER]})
    _write(tmp_path, "gw1-2.json", {SECOND: _bucket(OUTER)}, nested={})
    used = po.shared_work_used(tmp_path)
    assert used[SECOND] == frozenset({_ctx(OUTER), _ctx(INNER)})
    assert used[FIRST] == used[SECOND]


def test_a_test_two_processes_recorded_gets_both_lists(tmp_path: Path) -> None:
    _write(tmp_path, "gw0-1.json", {FIRST: _bucket(OUTER)})
    _write(tmp_path, "gw1-2.json", {FIRST: _bucket(INNER)})
    assert po.shared_work_used(tmp_path)[FIRST] == frozenset({_ctx(OUTER), _ctx(INNER)})


def test_nesting_that_loops_ends(tmp_path: Path) -> None:
    _write(tmp_path, "main-1.json", {FIRST: _bucket(OUTER)},
           nested={OUTER: [INNER], INNER: [OUTER]})
    assert po.shared_work_used(tmp_path)[FIRST] == frozenset({_ctx(OUTER), _ctx(INNER)})


def test_a_record_of_another_version_or_unreadable_says_nothing(tmp_path: Path) -> None:
    """Nothing, not 'used nothing': the test is absent from the answer, and
    the fold treats rows nobody is recorded using as run under no test."""
    _write(tmp_path, "main-1.json", {FIRST: _bucket(OUTER)}, version=po.RECORDER_VERSION - 1)
    (tmp_path / "main-2.json").write_text("{not json")
    assert po.shared_work_used(tmp_path) == {}


def test_a_record_written_before_nesting_was_recorded_still_reads(tmp_path: Path) -> None:
    _write(tmp_path, "main-1.json", {FIRST: _bucket(OUTER)}, nested=None)
    assert po.shared_work_used(tmp_path) == {FIRST: frozenset({_ctx(OUTER)})}


def test_no_directory_is_no_record(tmp_path: Path) -> None:
    assert po.shared_work_used(tmp_path / "absent") == {}
