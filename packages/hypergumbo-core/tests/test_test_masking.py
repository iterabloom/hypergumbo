# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the slow test masking module."""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.test_masking import (
    DEFAULT_THRESHOLD_SECONDS,
    _node_to_pytest_id,
    compute_deselections,
    find_latest_behavior_map,
    load_test_timings,
    main,
)


class TestNodeToPytestId:
    """Tests for behavior map node → pytest node ID conversion."""

    def test_method_node(self) -> None:
        """Class method names are split on dot."""
        result = _node_to_pytest_id(
            "TestFoo.test_bar", "packages/core/tests/test_foo.py",
        )
        assert result == "packages/core/tests/test_foo.py::TestFoo::test_bar"

    def test_function_node(self) -> None:
        """Standalone functions use name directly."""
        result = _node_to_pytest_id(
            "test_something", "packages/core/tests/test_foo.py",
        )
        assert result == "packages/core/tests/test_foo.py::test_something"

    def test_nested_class_method(self) -> None:
        """Only splits on first dot."""
        result = _node_to_pytest_id(
            "TestOuter.test_inner", "tests/test_x.py",
        )
        assert result == "tests/test_x.py::TestOuter::test_inner"


class TestLoadTestTimings:
    """Tests for loading and filtering test timings."""

    def test_returns_slow_tests_above_threshold(self, tmp_path: Path) -> None:
        """Only tests above threshold are returned."""
        timings = {
            "tests/test_a.py::test_fast": {
                "runs": [{"seconds": 0.01, "timestamp": "t"}],
            },
            "tests/test_a.py::test_slow": {
                "runs": [{"seconds": 5.0, "timestamp": "t"}],
            },
            "tests/test_a.py::test_medium": {
                "runs": [{"seconds": 0.15, "timestamp": "t"}],
            },
        }
        p = tmp_path / "timings.json"
        p.write_text(json.dumps(timings))

        result = load_test_timings(p, threshold=0.1)
        assert "tests/test_a.py::test_slow" in result
        assert result["tests/test_a.py::test_slow"] == 5.0
        assert "tests/test_a.py::test_medium" in result
        assert "tests/test_a.py::test_fast" not in result

    def test_computes_mean_duration(self, tmp_path: Path) -> None:
        """Mean is computed across all runs."""
        timings = {
            "tests/test_a.py::test_x": {
                "runs": [
                    {"seconds": 1.0, "timestamp": "t"},
                    {"seconds": 3.0, "timestamp": "t"},
                ],
            },
        }
        p = tmp_path / "timings.json"
        p.write_text(json.dumps(timings))

        result = load_test_timings(p, threshold=0.1)
        assert result["tests/test_a.py::test_x"] == pytest.approx(2.0)

    def test_skips_empty_runs(self, tmp_path: Path) -> None:
        """Tests with no runs are skipped."""
        timings = {"tests/test_a.py::test_x": {"runs": []}}
        p = tmp_path / "timings.json"
        p.write_text(json.dumps(timings))

        result = load_test_timings(p, threshold=0.1)
        assert len(result) == 0


class TestFindLatestBehaviorMap:
    """Tests for behavior map cache discovery."""

    def test_finds_newest_result(self, tmp_path: Path) -> None:
        """Should return the most recently modified behavior map."""
        import time

        with patch(
            "hypergumbo_core.sketch_embeddings._get_repo_fingerprint",
            return_value="abc123",
        ), patch(
            "hypergumbo_core.sketch_embeddings._get_xdg_cache_base",
            return_value=tmp_path,
        ):
            # Create two state dirs
            old_dir = tmp_path / "abc123" / "results" / "old"
            old_dir.mkdir(parents=True)
            old_file = old_dir / "hypergumbo.results.json"
            old_file.write_text("{}")

            time.sleep(0.05)

            new_dir = tmp_path / "abc123" / "results" / "new"
            new_dir.mkdir(parents=True)
            new_file = new_dir / "hypergumbo.results.json"
            new_file.write_text("{}")

            result = find_latest_behavior_map(tmp_path / "repo")
            assert result == new_file

    def test_returns_none_when_no_cache(self, tmp_path: Path) -> None:
        """Should return None when no results directory exists."""
        with patch(
            "hypergumbo_core.sketch_embeddings._get_repo_fingerprint",
            return_value="abc123",
        ), patch(
            "hypergumbo_core.sketch_embeddings._get_xdg_cache_base",
            return_value=tmp_path,
        ):
            result = find_latest_behavior_map(tmp_path / "repo")
            assert result is None

    def test_returns_none_when_no_json_files(self, tmp_path: Path) -> None:
        """Should return None when results dir exists but has no JSON."""
        with patch(
            "hypergumbo_core.sketch_embeddings._get_repo_fingerprint",
            return_value="abc123",
        ), patch(
            "hypergumbo_core.sketch_embeddings._get_xdg_cache_base",
            return_value=tmp_path,
        ):
            results_dir = tmp_path / "abc123" / "results" / "state1"
            results_dir.mkdir(parents=True)
            # No JSON file

            result = find_latest_behavior_map(tmp_path / "repo")
            assert result is None


def _make_behavior_map(
    nodes: list[dict],
    edges: list[dict],
) -> dict:
    """Build a minimal behavior map dict."""
    return {"nodes": nodes, "edges": edges}


def _make_node(
    node_id: str, name: str, path: str, kind: str = "function",
) -> dict:
    return {"id": node_id, "name": name, "path": path, "kind": kind}


def _make_edge(src: str, dst: str, edge_type: str = "calls") -> dict:
    return {
        "id": f"e-{src}-{dst}",
        "src": src,
        "dst": dst,
        "type": edge_type,
        "meta": {"evidence_type": "direct"},
    }


class TestComputeDeselections:
    """Tests for the core masking algorithm."""

    def _setup(
        self,
        tmp_path: Path,
        nodes: list[dict],
        edges: list[dict],
        timings: dict,
        changed_files: list[str],
        affected_test_files: list[str],
        threshold: float = 0.1,
    ) -> list[str]:
        """Helper to set up files and run compute_deselections."""
        bmap_path = tmp_path / "bmap.json"
        bmap_path.write_text(json.dumps(_make_behavior_map(nodes, edges)))

        timings_path = tmp_path / "timings.json"
        timings_path.write_text(json.dumps(timings))

        return compute_deselections(
            behavior_map_path=bmap_path,
            changed_files=changed_files,
            timings_path=timings_path,
            affected_test_files=affected_test_files,
            threshold=threshold,
        )

    def test_deselects_slow_unconnected_test(self, tmp_path: Path) -> None:
        """A slow test not connected to changed code should be deselected."""
        # Source symbols
        src_a = _make_node("src-a", "func_a", "src/module_a.py")
        src_b = _make_node("src-b", "func_b", "src/module_b.py")
        # Test symbols
        test_connected = _make_node(
            "t1", "TestX.test_connected", "tests/test_x.py", kind="method",
        )
        test_unconnected = _make_node(
            "t2", "TestX.test_unconnected", "tests/test_x.py", kind="method",
        )

        edges = [
            _make_edge("t1", "src-a"),  # test_connected calls func_a
            _make_edge("t2", "src-b"),  # test_unconnected calls func_b
        ]

        timings = {
            "tests/test_x.py::TestX::test_connected": {
                "runs": [{"seconds": 5.0, "timestamp": "t"}],
            },
            "tests/test_x.py::TestX::test_unconnected": {
                "runs": [{"seconds": 10.0, "timestamp": "t"}],
            },
        }

        result = self._setup(
            tmp_path,
            nodes=[src_a, src_b, test_connected, test_unconnected],
            edges=edges,
            timings=timings,
            changed_files=["src/module_a.py"],  # Only module_a changed
            affected_test_files=["tests/test_x.py"],
        )

        # test_unconnected is slow but doesn't connect to module_a
        assert "--deselect=tests/test_x.py::TestX::test_unconnected" in result.deselections
        # test_connected IS connected to module_a — should NOT be deselected
        assert "--deselect=tests/test_x.py::TestX::test_connected" not in result.deselections
        assert result.estimated_seconds_saved == pytest.approx(10.0)
        assert result.total_slow_in_scope == 2

    def test_keeps_slow_connected_test(self, tmp_path: Path) -> None:
        """A slow test connected to changed code should NOT be deselected."""
        src = _make_node("src-a", "func_a", "src/a.py")
        test = _make_node("t1", "TestA.test_slow", "tests/test_a.py", kind="method")

        result = self._setup(
            tmp_path,
            nodes=[src, test],
            edges=[_make_edge("t1", "src-a")],
            timings={
                "tests/test_a.py::TestA::test_slow": {
                    "runs": [{"seconds": 30.0, "timestamp": "t"}],
                },
            },
            changed_files=["src/a.py"],
            affected_test_files=["tests/test_a.py"],
        )

        assert result.deselections == []

    def test_never_deselects_fast_tests(self, tmp_path: Path) -> None:
        """Fast tests should never be deselected even if unconnected."""
        src = _make_node("src-a", "func_a", "src/a.py")
        test = _make_node("t1", "test_fast", "tests/test_a.py")

        result = self._setup(
            tmp_path,
            nodes=[src, test],
            edges=[],  # no connection
            timings={
                "tests/test_a.py::test_fast": {
                    "runs": [{"seconds": 0.01, "timestamp": "t"}],
                },
            },
            changed_files=["src/a.py"],
            affected_test_files=["tests/test_a.py"],
        )

        assert result.deselections == []

    def test_transitive_connection_keeps_test(self, tmp_path: Path) -> None:
        """Tests connected transitively should NOT be deselected."""
        src_changed = _make_node("src-c", "changed_fn", "src/changed.py")
        src_middle = _make_node("src-m", "middle_fn", "src/middle.py")
        test = _make_node("t1", "TestT.test_indirect", "tests/test_t.py", kind="method")

        edges = [
            _make_edge("src-m", "src-c"),  # middle calls changed
            _make_edge("t1", "src-m"),      # test calls middle
        ]

        result = self._setup(
            tmp_path,
            nodes=[src_changed, src_middle, test],
            edges=edges,
            timings={
                "tests/test_t.py::TestT::test_indirect": {
                    "runs": [{"seconds": 15.0, "timestamp": "t"}],
                },
            },
            changed_files=["src/changed.py"],
            affected_test_files=["tests/test_t.py"],
        )

        assert result.deselections == []

    def test_empty_behavior_map(self, tmp_path: Path) -> None:
        """Empty behavior map should return no deselections."""
        result = self._setup(
            tmp_path,
            nodes=[],
            edges=[],
            timings={},
            changed_files=["src/a.py"],
            affected_test_files=["tests/test_a.py"],
        )
        assert result.deselections == []

    def test_no_changed_symbols_found(self, tmp_path: Path) -> None:
        """If no symbols match changed files, return empty."""
        src = _make_node("src-a", "func_a", "src/other.py")
        src_b = _make_node("src-b", "func_b", "src/b.py")

        result = self._setup(
            tmp_path,
            nodes=[src, src_b],
            edges=[_make_edge("src-a", "src-b")],  # need edges to pass early check
            timings={},
            changed_files=["src/nonexistent.py"],
            affected_test_files=["tests/test_a.py"],
        )
        assert result.deselections == []

    def test_no_slow_tests(self, tmp_path: Path) -> None:
        """If no tests are slow, return empty."""
        src = _make_node("src-a", "func_a", "src/a.py")
        test = _make_node("t1", "test_fast", "tests/test_a.py")

        result = self._setup(
            tmp_path,
            nodes=[src, test],
            edges=[_make_edge("t1", "src-a")],
            timings={
                "tests/test_a.py::test_fast": {
                    "runs": [{"seconds": 0.01, "timestamp": "t"}],
                },
            },
            changed_files=["src/a.py"],
            affected_test_files=["tests/test_a.py"],
        )
        assert result.deselections == []

    def test_test_not_in_affected_files_is_ignored(self, tmp_path: Path) -> None:
        """Tests in files NOT selected by slice --files should be ignored."""
        src = _make_node("src-a", "func_a", "src/a.py")
        test = _make_node(
            "t1", "TestX.test_slow_other", "tests/test_other.py", kind="method",
        )

        result = self._setup(
            tmp_path,
            nodes=[src, test],
            edges=[],
            timings={
                "tests/test_other.py::TestX::test_slow_other": {
                    "runs": [{"seconds": 20.0, "timestamp": "t"}],
                },
            },
            changed_files=["src/a.py"],
            affected_test_files=["tests/test_a.py"],  # test_other NOT in list
        )
        assert result.deselections == []

    def test_standalone_function_test(self, tmp_path: Path) -> None:
        """Standalone test_ functions (not in class) should be handled."""
        src = _make_node("src-a", "func_a", "src/a.py")
        src_b = _make_node("src-b", "func_b", "src/b.py")
        test = _make_node("t1", "test_standalone_slow", "tests/test_a.py")

        result = self._setup(
            tmp_path,
            nodes=[src, src_b, test],
            edges=[_make_edge("t1", "src-b")],  # calls b, not a
            timings={
                "tests/test_a.py::test_standalone_slow": {
                    "runs": [{"seconds": 5.0, "timestamp": "t"}],
                },
            },
            changed_files=["src/a.py"],
            affected_test_files=["tests/test_a.py"],
        )

        assert "--deselect=tests/test_a.py::test_standalone_slow" in result.deselections

    def test_suffix_match_for_changed_files(self, tmp_path: Path) -> None:
        """Changed files should match via path suffix when exact match fails."""
        # Node has full path, changed file is relative
        src = _make_node("src-a", "func_a", "packages/core/src/module_a.py")
        test = _make_node("t1", "TestX.test_slow", "tests/test_x.py", kind="method")

        result = self._setup(
            tmp_path,
            nodes=[src, test],
            edges=[_make_edge("t1", "src-a")],
            timings={
                "tests/test_x.py::TestX::test_slow": {
                    "runs": [{"seconds": 5.0, "timestamp": "t"}],
                },
            },
            changed_files=["src/module_a.py"],  # suffix of full path
            affected_test_files=["tests/test_x.py"],
        )
        # test IS connected to changed file via suffix match → not deselected
        assert result.deselections == []

    def test_suffix_match_for_affected_test_files(self, tmp_path: Path) -> None:
        """Test file paths should match via suffix against affected list."""
        src = _make_node("src-a", "func_a", "src/a.py")
        src_b = _make_node("src-b", "func_b", "src/b.py")
        # Node path is full, affected_test_files is relative
        test = _make_node(
            "t1", "TestX.test_slow", "packages/core/tests/test_x.py", kind="method",
        )

        result = self._setup(
            tmp_path,
            nodes=[src, src_b, test],
            edges=[_make_edge("t1", "src-b")],
            timings={
                "packages/core/tests/test_x.py::TestX::test_slow": {
                    "runs": [{"seconds": 5.0, "timestamp": "t"}],
                },
            },
            changed_files=["src/a.py"],
            affected_test_files=["tests/test_x.py"],  # suffix of full path
        )
        assert "--deselect=packages/core/tests/test_x.py::TestX::test_slow" in result.deselections

    def test_skips_nodes_with_empty_path(self, tmp_path: Path) -> None:
        """Nodes with no path should be silently skipped."""
        src = _make_node("src-a", "func_a", "src/a.py")
        no_path = {"id": "np", "name": "test_orphan", "path": "", "kind": "function"}

        result = self._setup(
            tmp_path,
            nodes=[src, no_path],
            edges=[_make_edge("np", "src-a")],
            timings={
                "::test_orphan": {
                    "runs": [{"seconds": 5.0, "timestamp": "t"}],
                },
            },
            changed_files=["src/a.py"],
            affected_test_files=["tests/test_a.py"],
        )
        assert result.deselections == []

    def test_skips_non_test_nodes(self, tmp_path: Path) -> None:
        """Non-test nodes (classes, helpers) should be skipped."""
        src = _make_node("src-a", "func_a", "src/a.py")
        helper = _make_node("h1", "setup_fixtures", "tests/test_a.py")
        cls = _make_node("c1", "TestFoo", "tests/test_a.py", kind="class")

        result = self._setup(
            tmp_path,
            nodes=[src, helper, cls],
            edges=[_make_edge("h1", "src-a"), _make_edge("c1", "src-a")],
            timings={
                "tests/test_a.py::setup_fixtures": {
                    "runs": [{"seconds": 5.0, "timestamp": "t"}],
                },
            },
            changed_files=["src/a.py"],
            affected_test_files=["tests/test_a.py"],
        )
        assert result.deselections == []

    def test_skips_fast_test_in_slow_check(self, tmp_path: Path) -> None:
        """Fast test nodes are skipped even though they pass is_test check."""
        src = _make_node("src-a", "func_a", "src/a.py")
        src_b = _make_node("src-b", "func_b", "src/b.py")
        fast_test = _make_node(
            "t1", "TestX.test_fast_one", "tests/test_a.py", kind="method",
        )
        slow_test = _make_node(
            "t2", "TestX.test_slow_one", "tests/test_a.py", kind="method",
        )

        result = self._setup(
            tmp_path,
            nodes=[src, src_b, fast_test, slow_test],
            edges=[
                _make_edge("t1", "src-b"),  # fast test calls b (unconnected)
                _make_edge("t2", "src-b"),  # slow test calls b (unconnected)
            ],
            timings={
                "tests/test_a.py::TestX::test_fast_one": {
                    "runs": [{"seconds": 0.01, "timestamp": "t"}],
                },
                "tests/test_a.py::TestX::test_slow_one": {
                    "runs": [{"seconds": 5.0, "timestamp": "t"}],
                },
            },
            changed_files=["src/a.py"],
            affected_test_files=["tests/test_a.py"],
        )
        # Only the slow unconnected test should be deselected
        assert "--deselect=tests/test_a.py::TestX::test_slow_one" in result.deselections
        # Fast test should NOT be deselected (even though unconnected)
        assert "--deselect=tests/test_a.py::TestX::test_fast_one" not in result.deselections

    def test_bfs_dedup(self, tmp_path: Path) -> None:
        """BFS should handle duplicate nodes in current_level."""
        # Two changed symbols (a1, a2) both have the same caller (mid)
        # so mid appears in next_level from both. When processing mid,
        # the second encounter should hit the dedup skip.
        src_a1 = _make_node("src-a1", "func_a1", "src/a.py")
        src_a2 = _make_node("src-a2", "func_a2", "src/a.py")
        src_mid = _make_node("src-mid", "func_mid", "src/mid.py")
        test = _make_node(
            "t1", "TestX.test_slow", "tests/test_x.py", kind="method",
        )

        edges = [
            _make_edge("src-mid", "src-a1"),  # mid calls a1
            _make_edge("src-mid", "src-a2"),  # mid calls a2
            _make_edge("t1", "src-mid"),       # test calls mid
        ]

        result = self._setup(
            tmp_path,
            nodes=[src_a1, src_a2, src_mid, test],
            edges=edges,
            timings={
                "tests/test_x.py::TestX::test_slow": {
                    "runs": [{"seconds": 5.0, "timestamp": "t"}],
                },
            },
            changed_files=["src/a.py"],
            affected_test_files=["tests/test_x.py"],
        )
        # test is connected to a (via mid) — should NOT be deselected
        assert result.deselections == []

    def test_results_sorted(self, tmp_path: Path) -> None:
        """Deselection list should be sorted for determinism."""
        src = _make_node("src-a", "func_a", "src/a.py")
        t1 = _make_node("t1", "TestZ.test_z", "tests/test_a.py", kind="method")
        t2 = _make_node("t2", "TestA.test_a", "tests/test_a.py", kind="method")

        result = self._setup(
            tmp_path,
            nodes=[src, t1, t2],
            edges=[],
            timings={
                "tests/test_a.py::TestZ::test_z": {
                    "runs": [{"seconds": 5.0, "timestamp": "t"}],
                },
                "tests/test_a.py::TestA::test_a": {
                    "runs": [{"seconds": 5.0, "timestamp": "t"}],
                },
            },
            changed_files=["src/a.py"],
            affected_test_files=["tests/test_a.py"],
        )

        assert result.deselections == sorted(result.deselections)


class TestMain:
    """Tests for the CLI entry point."""

    def _write_file(self, path: Path, lines: list[str]) -> None:
        path.write_text("\n".join(lines) + "\n")

    def test_graceful_degradation_missing_timings(self, tmp_path: Path) -> None:
        """Should return 0 with no output when timings file is missing."""
        changed = tmp_path / "changed.txt"
        changed.write_text("src/a.py\n")
        affected = tmp_path / "affected.txt"
        affected.write_text("tests/test_a.py\n")

        stdout = io.StringIO()
        rc = main([
            "--changed-files", str(changed),
            "--timings", str(tmp_path / "nonexistent.json"),
            "--affected-tests", str(affected),
            "--repo-root", str(tmp_path),
        ], stdout=stdout)

        assert rc == 0
        assert stdout.getvalue() == ""

    def test_graceful_degradation_no_behavior_map(self, tmp_path: Path) -> None:
        """Should return 0 with no output when no behavior map cached."""
        changed = tmp_path / "changed.txt"
        changed.write_text("src/a.py\n")
        affected = tmp_path / "affected.txt"
        affected.write_text("tests/test_a.py\n")
        timings = tmp_path / "timings.json"
        timings.write_text("{}")

        stdout = io.StringIO()
        with patch(
            "hypergumbo_core.test_masking.find_latest_behavior_map",
            return_value=None,
        ):
            rc = main([
                "--changed-files", str(changed),
                "--timings", str(timings),
                "--affected-tests", str(affected),
                "--repo-root", str(tmp_path),
            ], stdout=stdout)

        assert rc == 0
        assert stdout.getvalue() == ""

    def test_outputs_deselections(self, tmp_path: Path) -> None:
        """Should output --deselect lines for slow unconnected tests."""
        # Build minimal behavior map
        bmap = _make_behavior_map(
            nodes=[
                _make_node("src-a", "func_a", "src/a.py"),
                _make_node("src-b", "func_b", "src/b.py"),
                _make_node("t1", "TestX.test_slow_unconnected", "tests/test_x.py", kind="method"),
            ],
            edges=[_make_edge("t1", "src-b")],
        )
        bmap_path = tmp_path / "bmap.json"
        bmap_path.write_text(json.dumps(bmap))

        changed = tmp_path / "changed.txt"
        changed.write_text("src/a.py\n")
        affected = tmp_path / "affected.txt"
        affected.write_text("tests/test_x.py\n")
        timings = tmp_path / "timings.json"
        timings.write_text(json.dumps({
            "tests/test_x.py::TestX::test_slow_unconnected": {
                "runs": [{"seconds": 20.0, "timestamp": "t"}],
            },
        }))

        stdout = io.StringIO()
        rc = main([
            "--changed-files", str(changed),
            "--timings", str(timings),
            "--affected-tests", str(affected),
            "--behavior-map", str(bmap_path),
            "--repo-root", str(tmp_path),
        ], stdout=stdout)

        assert rc == 0
        assert "--deselect=tests/test_x.py::TestX::test_slow_unconnected" in stdout.getvalue()

    def test_empty_changed_files(self, tmp_path: Path) -> None:
        """Should return 0 with no output when changed files list is empty."""
        bmap_path = tmp_path / "bmap.json"
        bmap_path.write_text(json.dumps(_make_behavior_map([], [])))
        changed = tmp_path / "changed.txt"
        changed.write_text("\n")
        affected = tmp_path / "affected.txt"
        affected.write_text("tests/test_a.py\n")
        timings = tmp_path / "timings.json"
        timings.write_text("{}")

        stdout = io.StringIO()
        rc = main([
            "--changed-files", str(changed),
            "--timings", str(timings),
            "--affected-tests", str(affected),
            "--behavior-map", str(bmap_path),
            "--repo-root", str(tmp_path),
        ], stdout=stdout)

        assert rc == 0
        assert stdout.getvalue() == ""

    def test_empty_affected_tests(self, tmp_path: Path) -> None:
        """Should return 0 with no output when no affected tests."""
        bmap_path = tmp_path / "bmap.json"
        bmap_path.write_text(json.dumps(_make_behavior_map([], [])))
        changed = tmp_path / "changed.txt"
        changed.write_text("src/a.py\n")
        affected = tmp_path / "affected.txt"
        affected.write_text("\n")
        timings = tmp_path / "timings.json"
        timings.write_text("{}")

        stdout = io.StringIO()
        rc = main([
            "--changed-files", str(changed),
            "--timings", str(timings),
            "--affected-tests", str(affected),
            "--behavior-map", str(bmap_path),
            "--repo-root", str(tmp_path),
        ], stdout=stdout)

        assert rc == 0
        assert stdout.getvalue() == ""

    def test_missing_changed_files_path(self, tmp_path: Path) -> None:
        """Should return 0 when changed files path doesn't exist."""
        affected = tmp_path / "affected.txt"
        affected.write_text("tests/test_a.py\n")
        timings = tmp_path / "timings.json"
        timings.write_text("{}")

        stdout = io.StringIO()
        rc = main([
            "--changed-files", str(tmp_path / "nope.txt"),
            "--timings", str(timings),
            "--affected-tests", str(affected),
            "--repo-root", str(tmp_path),
        ], stdout=stdout)

        assert rc == 0

    def test_missing_affected_tests_path(self, tmp_path: Path) -> None:
        """Should return 0 when affected tests path doesn't exist."""
        changed = tmp_path / "changed.txt"
        changed.write_text("src/a.py\n")
        timings = tmp_path / "timings.json"
        timings.write_text("{}")

        stdout = io.StringIO()
        rc = main([
            "--changed-files", str(changed),
            "--timings", str(timings),
            "--affected-tests", str(tmp_path / "nope.txt"),
            "--repo-root", str(tmp_path),
        ], stdout=stdout)

        assert rc == 0

    def test_stderr_summary_output(self, tmp_path: Path) -> None:
        """Should print summary stats to stderr when tests are masked."""
        bmap = _make_behavior_map(
            nodes=[
                _make_node("src-a", "func_a", "src/a.py"),
                _make_node("src-b", "func_b", "src/b.py"),
                _make_node("t1", "TestX.test_slow1", "tests/test_x.py", kind="method"),
                _make_node("t2", "TestX.test_slow2", "tests/test_x.py", kind="method"),
            ],
            edges=[
                _make_edge("t1", "src-b"),
                _make_edge("t2", "src-b"),
            ],
        )
        bmap_path = tmp_path / "bmap.json"
        bmap_path.write_text(json.dumps(bmap))

        changed = tmp_path / "changed.txt"
        changed.write_text("src/a.py\n")
        affected = tmp_path / "affected.txt"
        affected.write_text("tests/test_x.py\n")
        timings = tmp_path / "timings.json"
        timings.write_text(json.dumps({
            "tests/test_x.py::TestX::test_slow1": {
                "runs": [{"seconds": 25.0, "timestamp": "t"}],
            },
            "tests/test_x.py::TestX::test_slow2": {
                "runs": [{"seconds": 40.0, "timestamp": "t"}],
            },
        }))

        stdout = io.StringIO()
        stderr = io.StringIO()
        main([
            "--changed-files", str(changed),
            "--timings", str(timings),
            "--affected-tests", str(affected),
            "--behavior-map", str(bmap_path),
            "--repo-root", str(tmp_path),
        ], stdout=stdout, stderr=stderr)

        summary = stderr.getvalue()
        assert "masked 2 slow" in summary
        assert "~1.1m saved" in summary
        assert "0 slow tests kept" in summary

    def test_explicit_behavior_map_path(self, tmp_path: Path) -> None:
        """--behavior-map flag should override auto-discovery."""
        bmap = _make_behavior_map(nodes=[], edges=[])
        bmap_path = tmp_path / "custom_bmap.json"
        bmap_path.write_text(json.dumps(bmap))

        changed = tmp_path / "changed.txt"
        changed.write_text("src/a.py\n")
        affected = tmp_path / "affected.txt"
        affected.write_text("tests/test_a.py\n")
        timings = tmp_path / "timings.json"
        timings.write_text("{}")

        stdout = io.StringIO()
        rc = main([
            "--changed-files", str(changed),
            "--timings", str(timings),
            "--affected-tests", str(affected),
            "--behavior-map", str(bmap_path),
            "--repo-root", str(tmp_path),
        ], stdout=stdout)

        assert rc == 0
