# SPDX-License-Identifier: AGPL-3.0-or-later
"""One answer to "is this node test code", in every view.

There is one PATH predicate, ``paths.is_test_file`` (``selection.filters.
is_test_path`` is the same function by another name), and one NODE predicate,
``paths.is_test_node``: the path predicate or a test annotation. Every consumer
deciding about a node calls ``is_test_node``. A view that asks the path alone
gets annotated test code wrong: a Rust ``#[test]`` function inside
``mod tests`` in ``src/`` would be a test to one view and a production function
(a dead-code candidate, a test-coverage cold spot) to another.
(history: WI-dibuf, WI-borib, docs/audits/0026)
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.paths import is_test_file, is_test_node
from hypergumbo_core.selection.filters import is_test_path

_LIB = '''pub fn escape(s: &str) -> String {
    s.replace('\\\\', "\\\\\\\\")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backslash() {
        assert_eq!(escape("\\\\"), "\\\\\\\\");
    }
}
'''


def _run(args: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(args)
    return json.loads(buf.getvalue())


def _crate(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[package]\nname = "esc"\nversion = "0.1.0"\n')
    (repo / "src" / "lib.rs").write_text(_LIB)
    return repo


@pytest.mark.parametrize("path", [
    "polls/tests.py", "src/main/java/a/FooTest.java", "app/src/FooTests.kt",
    "Sources/App/RouteTests.swift", "web/types.test-d.ts", "lib/testFixtures/a.java",
    "src/integration/java/a/A.java", "app/intTest/kotlin/A.kt", "app/integrationTest/java/A.java",
    "pkg/unittest/a.go", "tests/test_a.py", "pkg/a_test.go", "src/lib.rs",
    "polls/views.py", "src/main/java/a/Foo.java",
])
def test_the_path_predicates_are_one(path: str) -> None:
    assert is_test_path(path) == is_test_file(path) == is_test_node(path, None)


def test_a_test_annotation_makes_a_node_test_code() -> None:
    meta = {"annotations": [{"name": "test", "args": []}]}
    assert is_test_node("src/lib.rs", meta) and not is_test_file("src/lib.rs")


@pytest.mark.parametrize("annotation, is_test", [
    ("@Test", True), ("#[test]", True), ("#[cfg(test)]", True), ("@deprecated", False),
    ("@RestController", False), (42, False),
])
def test_text_annotations_are_read_too(annotation: object, is_test: bool) -> None:
    """Some maps carry decorators as text, which dead-code's
    ``--exclude-annotated`` reads; the node predicate must, not crash."""
    assert is_test_node("src/App.java", {"decorators": [annotation]}) is is_test


def test_a_test_prefix_is_not_a_test_file() -> None:
    """``TestConfig.java`` is as often support or config (test_sketch's pin)."""
    assert not is_test_file("src/TestConfig.java")


def test_a_test_function_in_src_is_not_dead_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _crate(tmp_path)
    out = _run(["dead-code-maybe", str(repo), "--format", "json"], tmp_path, monkeypatch)
    names = {c["name"] for c in out["dead_candidates"]}
    assert "backslash" not in names, names


def test_a_test_function_in_src_is_a_test_to_test_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _crate(tmp_path)
    out = _run(["test-coverage", str(repo), "--format", "json"], tmp_path, monkeypatch)
    listed = {s["name"] for s in out["cold_spots"] + out["test_dense"]}
    assert "backslash" not in listed, listed  # not a production function
    assert out["summary"]["total_tests"] >= 1
    assert "escape" in {s["name"] for s in out["test_dense"]}, out  # the test reaches it
