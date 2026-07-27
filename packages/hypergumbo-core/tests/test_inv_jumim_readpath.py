# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavioral tests for INV-jumim: map-driven sketch read-path (``--input``).

When ``sketch --input <survey.json>`` is given, :func:`generate_sketch` must
summarize the MAP's file universe — deriving test-file counts, languages,
source files, and the Structure tree from the loaded behavior map's node
paths — instead of re-walking the current working directory via
``repo_root.rglob()``. The re-walk caused two filed defects:

* a 60+s wall-clock hang on populated repos (the ``_analyze_test_files`` ->
  ``find_files`` -> ``rglob`` bottleneck), and
* a wrong-universe bug: a package-scoped 902-node map fed from a larger repo
  root produced a sketch of the ENTIRE surrounding repo (structure rooted at
  the CWD), discarding the map's node universe.

The fix synthesizes a :class:`FileIndex` from the map's node paths and scopes
the read-path to it (``find_files`` and the Structure helpers are already
``FileIndex``-aware). See INV-papuj (umbrella) / INV-jumim.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.discovery import (
    FileIndex,
    get_file_index,
    set_file_index,
)
from hypergumbo_core.sketch import (
    _dir_children_from_index,
    _file_index_from_map,
    _map_source_paths,
    _peek_cached_results,
    generate_sketch,
)
from hypergumbo_core.sketch_embeddings import _get_results_cache_dir


def _node(path: str, name: str, start: int, end: int, *, is_test: bool = False,
          language: str = "python", kind: str = "function") -> dict:
    """Build a serialized behavior-map node matching the live schema."""
    return {
        "id": f"{language}:{path}:{start}-{end}:{name}:{kind}",
        "name": name,
        "kind": kind,
        "language": language,
        "path": path,
        "line_span": max(1, end - start + 1),
        "span": {"start_line": start, "end_line": end, "start_col": 0, "end_col": 0},
        "origin": language,
        "supply_chain": {"tier": 1, "reason": "first_party", "is_test_file": is_test},
    }


def _map(nodes: list[dict], edges: list[dict] | None = None,
         languages: dict | None = None) -> dict:
    return {
        "profile": {
            "languages": languages or {"python": {"files": 1, "loc": 10}},
            "frameworks": [],
            "framework_mode": "auto",
        },
        "nodes": nodes,
        "edges": edges or [],
    }


# --------------------------------------------------------------------------
# FileIndex.from_paths — build an index from an explicit path list (no walk)
# --------------------------------------------------------------------------


class TestFileIndexFromPaths:
    def test_indexes_all_files_and_repo_root(self, tmp_path: Path) -> None:
        paths = [tmp_path / "src" / "a.py", tmp_path / "src" / "b.js",
                 tmp_path / "README.md"]
        idx = FileIndex.from_paths(tmp_path, paths)
        assert idx.repo_root == tmp_path
        assert set(idx.all_files()) == set(paths)

    def test_match_pattern_by_extension(self, tmp_path: Path) -> None:
        idx = FileIndex.from_paths(
            tmp_path, [tmp_path / "src" / "a.py", tmp_path / "src" / "b.js"]
        )
        assert list(idx.match_pattern("*.py")) == [tmp_path / "src" / "a.py"]

    def test_match_pattern_by_name(self, tmp_path: Path) -> None:
        idx = FileIndex.from_paths(tmp_path, [tmp_path / "Makefile"])
        assert list(idx.match_pattern("Makefile")) == [tmp_path / "Makefile"]

    def test_dedupes_repeated_paths(self, tmp_path: Path) -> None:
        p = tmp_path / "a.py"
        idx = FileIndex.from_paths(tmp_path, [p, p])
        assert idx.all_files() == [p]


# --------------------------------------------------------------------------
# _map_source_paths / _file_index_from_map — extract the map's file universe
# --------------------------------------------------------------------------


class TestMapSourcePaths:
    def test_distinct_absolute_paths_skip_sentinels(self, tmp_path: Path) -> None:
        cached = _map([
            _node("src/a.py", "f", 1, 3),
            _node("src/a.py", "g", 5, 7),          # 2nd symbol, same file
            _node("tests/test_a.py", "t", 1, 2, is_test=True),
            _node("<external>", "ext", 0, 0),      # sentinel -> skipped
        ])
        paths = set(_map_source_paths(tmp_path, cached))
        rels = {p.relative_to(tmp_path).as_posix() for p in paths}
        assert rels == {"src/a.py", "tests/test_a.py"}

    def test_empty_when_no_map(self, tmp_path: Path) -> None:
        assert list(_map_source_paths(tmp_path, None)) == []

    def test_empty_when_only_sentinels(self, tmp_path: Path) -> None:
        cached = _map([_node("<external>", "ext", 0, 0)])
        assert list(_map_source_paths(tmp_path, cached)) == []

    def test_skips_node_without_path(self, tmp_path: Path) -> None:
        cached = _map([
            {"id": "x", "name": "n", "kind": "function", "language": "python",
             "line_span": 1, "span": {}, "origin": "python",
             "supply_chain": {}},                       # no "path" key
            _node("src/a.py", "f", 1, 2),
        ])
        rels = {p.relative_to(tmp_path).as_posix() for p in _map_source_paths(tmp_path, cached)}
        assert rels == {"src/a.py"}


# --------------------------------------------------------------------------
# _dir_children_from_index — directory listing derived from the global index
# --------------------------------------------------------------------------


class TestDirChildrenFromIndex:
    def test_none_when_no_index(self, tmp_path: Path) -> None:
        before = get_file_index()
        set_file_index(None)
        try:
            assert _dir_children_from_index(tmp_path) is None
        finally:
            set_file_index(before)

    def test_none_when_dir_outside_index_root(self, tmp_path: Path) -> None:
        idx = FileIndex.from_paths(tmp_path / "root", [tmp_path / "root" / "a.py"])
        before = get_file_index()
        set_file_index(idx)
        try:
            # A sibling directory not under the index root -> None (fall back).
            assert _dir_children_from_index(tmp_path / "elsewhere") is None
        finally:
            set_file_index(before)

    def test_classifies_files_and_dirs(self, tmp_path: Path) -> None:
        idx = FileIndex.from_paths(tmp_path, [
            tmp_path / "top.py",
            tmp_path / "pkg" / "a.py",
            tmp_path / "pkg" / "sub" / "b.py",
        ])
        before = get_file_index()
        set_file_index(idx)
        try:
            children = dict(_dir_children_from_index(tmp_path))
            assert children == {"top.py": False, "pkg": True}
        finally:
            set_file_index(before)


class TestStructureFallbackIndexBranch:
    def test_index_branch_drops_excluded_dirs_and_root_tests(self, tmp_path: Path) -> None:
        """The fallback's map-scoped listing must drop excluded directories and
        (under -x) root-level test files."""
        from hypergumbo_core.discovery import DEFAULT_EXCLUDES
        from hypergumbo_core.sketch import _format_structure_tree_fallback

        idx = FileIndex.from_paths(tmp_path, [
            tmp_path / "dist" / "bundle.py",   # excluded dir -> skipped
            tmp_path / "test_root.py",         # root test file -> skipped under -x
            tmp_path / "pkg" / "core.py",      # kept
        ])
        before = get_file_index()
        set_file_index(idx)
        try:
            out = _format_structure_tree_fallback(
                tmp_path, list(DEFAULT_EXCLUDES), exclude_tests=True
            )
        finally:
            set_file_index(before)
        assert "pkg" in out
        assert "dist" not in out
        assert "test_root.py" not in out


# --------------------------------------------------------------------------
# _peek_cached_results — read-only warm-cache discovery (no auto-run)
# --------------------------------------------------------------------------


class TestPeekCachedResults:
    def test_miss_returns_none(self, tmp_path: Path) -> None:
        assert _peek_cached_results(tmp_path) is None

    def test_hit_returns_map(self, tmp_path: Path) -> None:
        import json
        cache_dir = _get_results_cache_dir(tmp_path)
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = _map([_node("src/a.py", "f", 1, 2)])
        (cache_dir / "hypergumbo.results.json").write_text(json.dumps(payload))
        got = _peek_cached_results(tmp_path)
        assert got is not None
        assert got["nodes"][0]["path"] == "src/a.py"


class TestFileIndexFromMap:
    def test_builds_index_from_node_paths(self, tmp_path: Path) -> None:
        cached = _map([_node("src/a.py", "f", 1, 3),
                       _node("tests/test_a.py", "t", 1, 2, is_test=True)])
        idx = _file_index_from_map(tmp_path, cached)
        assert idx is not None
        rels = {p.relative_to(tmp_path).as_posix() for p in idx.all_files()}
        assert rels == {"src/a.py", "tests/test_a.py"}

    def test_none_when_no_map(self, tmp_path: Path) -> None:
        assert _file_index_from_map(tmp_path, None) is None

    def test_none_when_no_usable_paths(self, tmp_path: Path) -> None:
        assert _file_index_from_map(tmp_path, _map([])) is None


# --------------------------------------------------------------------------
# generate_sketch — behavioral: scope the read-path to the map's universe
# --------------------------------------------------------------------------


class TestMapScopedReadPath:
    def _restore_index(self):
        """Snapshot and restore the process-global file index around a test."""
        return get_file_index()

    def test_structure_reflects_map_not_cwd(self, tmp_path: Path) -> None:
        """A package-scoped map fed from a larger root must yield a sketch of
        the MAP's universe, not the surrounding working tree (the wrong-universe
        repro: package map -> whole-repo structure)."""
        # On disk: the map's package PLUS unrelated noise NOT in the map.
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "core.py").write_text("def run():\n    helper()\n")
        (tmp_path / "pkg" / "util.py").write_text("def helper():\n    pass\n")
        (tmp_path / "vendor_noise").mkdir()
        for i in range(6):
            (tmp_path / "vendor_noise" / f"n{i}.py").write_text("x = 1\n")
        (tmp_path / "stray_top.py").write_text("y = 2\n")

        cached = _map(
            [_node("pkg/core.py", "run", 1, 2),
             _node("pkg/util.py", "helper", 1, 2)],
            edges=[{
                "src": "python:pkg/core.py:1-2:run:function",
                "dst": "python:pkg/util.py:1-2:helper:function",
                "type": "calls", "line": 2,
            }],
            languages={"python": {"files": 2, "loc": 4}},
        )

        before = get_file_index()
        try:
            sketch = generate_sketch(tmp_path, max_tokens=4000, cached_results=cached)
        finally:
            set_file_index(before)

        assert "pkg" in sketch
        assert "vendor_noise" not in sketch
        assert "stray_top.py" not in sketch

    def test_test_count_from_map_not_disk(self, tmp_path: Path) -> None:
        """The Tests section must count only the test files NAMED BY THE MAP,
        proving _analyze_test_files used the map index rather than rglob-ing the
        working tree (the 60s-hang short-circuit)."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_in_map.py").write_text(
            "import pytest\n\n\ndef test_a():\n    assert True\n"
        )
        # Off-map test files on disk that MUST NOT be counted:
        for i in range(4):
            (tmp_path / "tests" / f"test_off_map_{i}.py").write_text(
                "def test_x():\n    pass\n"
            )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("def f():\n    pass\n")

        cached = _map(
            [_node("src/a.py", "f", 1, 2),
             _node("tests/test_in_map.py", "test_a", 4, 5, is_test=True)],
            edges=[],
            languages={"python": {"files": 2, "loc": 6}},
        )

        before = get_file_index()
        try:
            sketch = generate_sketch(tmp_path, max_tokens=4000, cached_results=cached)
        finally:
            set_file_index(before)

        # Exactly one test file (the in-map one), never the 5 on disk.
        assert "1 test file" in sketch
        assert "5 test files" not in sketch

    def test_global_file_index_restored(self, tmp_path: Path) -> None:
        """generate_sketch must restore the prior global FileIndex so a
        map-scoped invocation does not leak its synthetic index to later
        callers."""
        sentinel = FileIndex.from_paths(tmp_path, [tmp_path / "sentinel.py"])
        before = get_file_index()
        set_file_index(sentinel)
        try:
            cached = _map([_node("src/a.py", "f", 1, 2)])
            (tmp_path / "src").mkdir()
            (tmp_path / "src" / "a.py").write_text("def f():\n    pass\n")
            generate_sketch(tmp_path, max_tokens=2000, cached_results=cached)
            assert get_file_index() is sentinel
        finally:
            set_file_index(before)

    def test_warm_cache_without_input_scopes_read_path(self, tmp_path: Path) -> None:
        """Even without ``--input``, a warm on-disk map scopes the read-path
        (the wrapper peeks the cache), so noise on disk stays out of the
        sketch."""
        import json

        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "core.py").write_text("def run():\n    helper()\n")
        (tmp_path / "pkg" / "util.py").write_text("def helper():\n    pass\n")
        (tmp_path / "vendor_noise").mkdir()
        for i in range(6):
            (tmp_path / "vendor_noise" / f"n{i}.py").write_text("x = 1\n")

        payload = _map(
            [_node("pkg/core.py", "run", 1, 2),
             _node("pkg/util.py", "helper", 1, 2)],
            edges=[{
                "src": "python:pkg/core.py:1-2:run:function",
                "dst": "python:pkg/util.py:1-2:helper:function",
                "type": "calls", "line": 2,
            }],
            languages={"python": {"files": 2, "loc": 4}},
        )
        cache_dir = _get_results_cache_dir(tmp_path)
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "hypergumbo.results.json").write_text(json.dumps(payload))

        before = get_file_index()
        set_file_index(None)
        try:
            sketch = generate_sketch(tmp_path, max_tokens=4000)  # no cached_results
            assert "pkg" in sketch
            assert "vendor_noise" not in sketch
            assert get_file_index() is None  # restored
        finally:
            set_file_index(before)

    def test_no_map_leaves_read_path_unscoped(self, tmp_path: Path) -> None:
        """Without a usable map the global index is untouched (impl runs its
        normal filesystem read-path)."""
        (tmp_path / "a.py").write_text("def f():\n    pass\n")
        before = get_file_index()
        set_file_index(None)
        try:
            # nodes=[] -> no usable paths -> no synthetic index installed
            generate_sketch(tmp_path, max_tokens=1500,
                            cached_results=_map([]))
            assert get_file_index() is None
        finally:
            set_file_index(before)
