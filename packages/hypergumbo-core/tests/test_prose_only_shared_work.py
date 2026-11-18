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


# ── cached functions ────────────────────────────────────────────────────────

CACHED = "pkg/mod.py::table"


def _write_cached(reads: Path, name: str, tests: dict[str, list[str]], *,
                  nested: dict[str, list[str]] | None = None,
                  cache_events: dict[str, Any] | None = None,
                  unwrapped: list[str] | None = None,
                  version: int = po.RECORDER_VERSION) -> None:
    """A record as the plugin writes it: per test the cached functions it
    called, and per cached function what its body read or started."""
    reads.mkdir(parents=True, exist_ok=True)
    expected: dict[str, list[str]] = {}
    for test in tests:
        expected.setdefault(test.split("::")[0], []).append(test)
    payload: dict[str, Any] = {
        "version": version, "observed": True, "expected": expected,
        "tests": {test: {**_bucket(), "caches": called} for test, called in tests.items()},
        "fixtures": {}, "caches": cache_events or {}, "modules": {},
        "nested": nested or {}, "unattributed": _bucket()}
    if unwrapped is not None:
        payload["unwrapped_caches"] = unwrapped
    (reads / name).write_text(json.dumps(payload))


def test_a_cached_function_a_test_called_is_shared_work_it_used(tmp_path: Path) -> None:
    _write_cached(tmp_path, "main-1.json", {FIRST: [CACHED], SECOND: []})
    assert po.shared_work_used(tmp_path) == {
        FIRST: frozenset({shared_context("cache", CACHED)}), SECOND: frozenset()}


def test_fixtures_and_caches_nest_in_each_other(tmp_path: Path) -> None:
    """A module fixture calls a cached function, which calls another."""
    other = "pkg/mod.py::beneath"
    reads = tmp_path / "reads"
    reads.mkdir()
    (reads / "main-1.json").write_text(json.dumps({
        "version": po.RECORDER_VERSION, "observed": True, "expected": {},
        "tests": {FIRST: _bucket(OUTER)}, "fixtures": {}, "modules": {},
        "nested": {f"fixture|{OUTER}": [f"cache|{CACHED}"], f"cache|{CACHED}": [f"cache|{other}"]},
        "unattributed": _bucket()}))
    assert po.shared_work_used(reads)[FIRST] == frozenset({
        _ctx(OUTER), shared_context("cache", CACHED), shared_context("cache", other)})


def test_the_caches_the_recorder_could_not_wrap_are_read_from_every_process(
        tmp_path: Path) -> None:
    _write_cached(tmp_path, "gw0-1.json", {FIRST: []}, unwrapped=["pkg/early.py::before"])
    _write_cached(tmp_path, "gw1-2.json", {SECOND: []}, unwrapped=["pkg/early.py::K.m"])
    _write_cached(tmp_path, "gw2-3.json", {}, unwrapped=["pkg/old.py::gone"],
                  version=po.RECORDER_VERSION - 1)
    (tmp_path / "gw3-4.json").write_text("{not json")
    _write_cached(tmp_path, "gw4-5.json", {})  # a record with no such list
    assert po.unwrapped_caches(tmp_path) == {"pkg/early.py::before", "pkg/early.py::K.m"}
    assert po.unwrapped_caches(tmp_path / "absent") == frozenset()


def test_what_a_cached_body_read_or_started_is_every_callers(tmp_path: Path) -> None:
    """The body opened a source file as data and started a process, once, in
    whichever test filled the cache. The prose-only rule must see both for
    every test that was handed the stored value."""
    import sqlite3

    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_x.py").write_text("def test_first():\n    pass\n")
    events = {CACHED: {"reads": ["pkg/data.py"], "opaque": ["subprocess.Popen:python"],
                       "docs": [], "lines": []}}
    _write_cached(tmp_path / "reads", "gw0-1.json", {FIRST: [CACHED]}, cache_events=events)
    _write_cached(tmp_path / "reads", "gw1-2.json", {SECOND: [CACHED], "tests/test_x.py::t3": []})
    conn = sqlite3.connect(tmp_path / "index.sqlite")
    po.fold_run(conn, tmp_path / "reads", root)
    rows = dict(conn.execute("SELECT test_id, opaque FROM read_test"))
    assert rows == {FIRST: "subprocess.Popen:python", SECOND: "subprocess.Popen:python",
                    "tests/test_x.py::t3": ""}
    reads = set(conn.execute("SELECT test_id, target FROM read_edge WHERE kind = ?", (po.READ,)))
    assert reads == {(FIRST, "pkg/data.py"), (SECOND, "pkg/data.py")}

