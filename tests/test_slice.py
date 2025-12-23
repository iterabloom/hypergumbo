"""Tests for the slice module (graph slicing for LLM context)."""
import hashlib
import json
from typing import List

import pytest

from hypergumbo.ir import Symbol, Edge, Span
from hypergumbo.slice import (
    slice_graph,
    SliceQuery,
    SliceResult,
    find_entry_nodes,
)


def make_symbol(
    name: str,
    path: str = "src/main.py",
    kind: str = "function",
    start_line: int = 1,
    end_line: int = 5,
) -> Symbol:
    """Helper to create test symbols."""
    span = Span(start_line=start_line, end_line=end_line, start_col=0, end_col=10)
    sym_id = f"python:{path}:{start_line}-{end_line}:{name}:{kind}"
    return Symbol(
        id=sym_id,
        name=name,
        kind=kind,
        language="python",
        path=path,
        span=span,
        origin="python-ast-v1",
        origin_run_id="uuid:test",
    )


def make_edge(
    src: Symbol,
    dst: Symbol,
    edge_type: str = "calls",
    confidence: float = 0.85,
) -> Edge:
    """Helper to create test edges."""
    return Edge.create(
        src=src.id,
        dst=dst.id,
        edge_type=edge_type,
        line=src.span.start_line,
        origin="python-ast-v1",
        origin_run_id="uuid:test",
        confidence=confidence,
    )


class TestFindEntryNodes:
    """Tests for finding entry nodes by various match criteria."""

    def test_find_by_exact_name(self) -> None:
        """Match entry by exact function name."""
        sym_a = make_symbol("login")
        sym_b = make_symbol("logout")
        nodes = [sym_a, sym_b]

        matches = find_entry_nodes(nodes, "login")

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_by_partial_name(self) -> None:
        """Match entry by partial name (contains)."""
        sym_a = make_symbol("user_login")
        sym_b = make_symbol("logout")
        nodes = [sym_a, sym_b]

        matches = find_entry_nodes(nodes, "login")

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_by_file_path(self) -> None:
        """Match entry by file path."""
        sym_a = make_symbol("func_a", path="src/auth.py")
        sym_b = make_symbol("func_b", path="src/db.py")
        nodes = [sym_a, sym_b]

        matches = find_entry_nodes(nodes, "src/auth.py")

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_by_node_id(self) -> None:
        """Match entry by exact node ID."""
        sym_a = make_symbol("login", path="src/auth.py", start_line=10, end_line=20)
        sym_b = make_symbol("logout")
        nodes = [sym_a, sym_b]

        matches = find_entry_nodes(nodes, sym_a.id)

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_multiple_matches(self) -> None:
        """Multiple nodes can match the same entry spec."""
        sym_a = make_symbol("handle_login")
        sym_b = make_symbol("login_user")
        sym_c = make_symbol("logout")
        nodes = [sym_a, sym_b, sym_c]

        matches = find_entry_nodes(nodes, "login")

        assert len(matches) == 2
        ids = {m.id for m in matches}
        assert sym_a.id in ids
        assert sym_b.id in ids

    def test_find_no_match(self) -> None:
        """Returns empty list when no match found."""
        sym_a = make_symbol("login")
        nodes = [sym_a]

        matches = find_entry_nodes(nodes, "nonexistent")

        assert matches == []


class TestSliceGraph:
    """Tests for BFS graph slicing."""

    def test_slice_single_node_no_edges(self) -> None:
        """Slice from a node with no outgoing edges."""
        sym_a = make_symbol("isolated")
        nodes = [sym_a]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="isolated", max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 1
        assert sym_a.id in result.node_ids
        assert len(result.edge_ids) == 0
        assert result.limits_hit == []

    def test_slice_follows_calls(self) -> None:
        """Slice follows call edges."""
        sym_a = make_symbol("caller", start_line=1, end_line=5)
        sym_b = make_symbol("callee", start_line=10, end_line=15)
        edge = make_edge(sym_a, sym_b, "calls")
        nodes = [sym_a, sym_b]
        edges = [edge]

        query = SliceQuery(entrypoint="caller", max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 2
        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert len(result.edge_ids) == 1
        assert edge.id in result.edge_ids

    def test_slice_follows_imports(self) -> None:
        """Slice follows import edges."""
        sym_a = make_symbol("main", path="src/main.py")
        sym_b = make_symbol("helper", path="src/utils.py")
        edge = make_edge(sym_a, sym_b, "imports")
        nodes = [sym_a, sym_b]
        edges = [edge]

        query = SliceQuery(entrypoint="main", max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 2
        assert sym_b.id in result.node_ids

    def test_slice_respects_hop_limit(self) -> None:
        """Slice stops at max_hops depth."""
        # Create chain: a -> b -> c -> d
        sym_a = make_symbol("a", start_line=1, end_line=2)
        sym_b = make_symbol("b", start_line=3, end_line=4)
        sym_c = make_symbol("c", start_line=5, end_line=6)
        sym_d = make_symbol("d", start_line=7, end_line=8)

        edge_ab = make_edge(sym_a, sym_b)
        edge_bc = make_edge(sym_b, sym_c)
        edge_cd = make_edge(sym_c, sym_d)

        nodes = [sym_a, sym_b, sym_c, sym_d]
        edges = [edge_ab, edge_bc, edge_cd]

        query = SliceQuery(entrypoint="a", max_hops=2, max_files=20)
        result = slice_graph(nodes, edges, query)

        # With max_hops=2: a (hop 0) -> b (hop 1) -> c (hop 2), but NOT d
        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_c.id in result.node_ids
        assert sym_d.id not in result.node_ids
        assert "hop_limit" in result.limits_hit

    def test_slice_respects_file_limit(self) -> None:
        """Slice stops when max_files is reached."""
        # Create nodes in different files
        sym_a = make_symbol("a", path="file1.py")
        sym_b = make_symbol("b", path="file2.py")
        sym_c = make_symbol("c", path="file3.py")

        edge_ab = make_edge(sym_a, sym_b)
        edge_bc = make_edge(sym_b, sym_c)

        nodes = [sym_a, sym_b, sym_c]
        edges = [edge_ab, edge_bc]

        query = SliceQuery(entrypoint="a", max_hops=10, max_files=2)
        result = slice_graph(nodes, edges, query)

        # Should only include nodes from 2 files
        files_in_result = {n.split(":")[1] for n in result.node_ids}
        assert len(files_in_result) <= 2
        assert "file_limit" in result.limits_hit

    def test_slice_filters_low_confidence(self) -> None:
        """Slice can exclude edges below confidence threshold."""
        sym_a = make_symbol("caller", start_line=1, end_line=2)
        sym_b = make_symbol("callee_high", start_line=3, end_line=4)
        sym_c = make_symbol("callee_low", start_line=5, end_line=6)

        edge_high = make_edge(sym_a, sym_b, confidence=0.90)
        edge_low = make_edge(sym_a, sym_c, confidence=0.40)

        nodes = [sym_a, sym_b, sym_c]
        edges = [edge_high, edge_low]

        query = SliceQuery(
            entrypoint="caller",
            max_hops=3,
            max_files=20,
            min_confidence=0.50,
        )
        result = slice_graph(nodes, edges, query)

        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_c.id not in result.node_ids
        assert edge_high.id in result.edge_ids
        assert edge_low.id not in result.edge_ids

    def test_slice_excludes_tests(self) -> None:
        """Slice can exclude test files."""
        sym_a = make_symbol("main", path="src/main.py")
        sym_b = make_symbol("helper", path="src/utils.py")
        sym_test = make_symbol("test_main", path="tests/test_main.py")

        edge_to_helper = make_edge(sym_a, sym_b)
        edge_to_test = make_edge(sym_a, sym_test)

        nodes = [sym_a, sym_b, sym_test]
        edges = [edge_to_helper, edge_to_test]

        query = SliceQuery(
            entrypoint="main",
            max_hops=3,
            max_files=20,
            exclude_tests=True,
        )
        result = slice_graph(nodes, edges, query)

        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_test.id not in result.node_ids

    def test_slice_handles_cycles(self) -> None:
        """Slice handles cyclic references without infinite loop."""
        sym_a = make_symbol("a", start_line=1, end_line=2)
        sym_b = make_symbol("b", start_line=3, end_line=4)

        edge_ab = make_edge(sym_a, sym_b)
        edge_ba = make_edge(sym_b, sym_a)

        nodes = [sym_a, sym_b]
        edges = [edge_ab, edge_ba]

        query = SliceQuery(entrypoint="a", max_hops=10, max_files=20)
        result = slice_graph(nodes, edges, query)

        # Should visit both nodes exactly once
        assert len(result.node_ids) == 2
        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids


class TestSliceResult:
    """Tests for SliceResult structure and feature ID generation."""

    def test_feature_id_is_deterministic(self) -> None:
        """Same query produces same feature ID."""
        sym_a = make_symbol("entry")
        nodes = [sym_a]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="entry", max_hops=3, max_files=20)

        result1 = slice_graph(nodes, edges, query)
        result2 = slice_graph(nodes, edges, query)

        assert result1.feature_id == result2.feature_id

    def test_feature_id_changes_with_query(self) -> None:
        """Different queries produce different feature IDs."""
        sym_a = make_symbol("entry")
        nodes = [sym_a]
        edges: List[Edge] = []

        query1 = SliceQuery(entrypoint="entry", max_hops=3, max_files=20)
        query2 = SliceQuery(entrypoint="entry", max_hops=5, max_files=20)

        result1 = slice_graph(nodes, edges, query1)
        result2 = slice_graph(nodes, edges, query2)

        assert result1.feature_id != result2.feature_id

    def test_to_dict_produces_valid_feature(self) -> None:
        """SliceResult.to_dict produces spec-compliant feature structure."""
        sym_a = make_symbol("entry")
        sym_b = make_symbol("callee", start_line=10, end_line=15)
        edge = make_edge(sym_a, sym_b)
        nodes = [sym_a, sym_b]
        edges = [edge]

        query = SliceQuery(entrypoint="entry", max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)
        feature = result.to_dict()

        assert "id" in feature
        assert feature["id"].startswith("sha256:")
        assert feature["name"] == "entry"
        assert "entry_nodes" in feature
        assert "node_ids" in feature
        assert "edge_ids" in feature
        assert "query" in feature
        assert feature["query"]["method"] == "bfs"
        assert feature["query"]["entrypoint"] == "entry"
        assert feature["query"]["hops"] == 3
        assert feature["query"]["max_files"] == 20
        assert "limits_hit" in feature


class TestSliceQuery:
    """Tests for SliceQuery dataclass."""

    def test_query_defaults(self) -> None:
        """Query has sensible defaults."""
        query = SliceQuery(entrypoint="foo")

        assert query.max_hops == 3
        assert query.max_files == 20
        assert query.min_confidence == 0.0
        assert query.exclude_tests is False
        assert query.method == "bfs"

    def test_query_to_dict(self) -> None:
        """Query serializes to dict for feature output."""
        query = SliceQuery(
            entrypoint="foo",
            max_hops=5,
            max_files=10,
            exclude_tests=True,
        )

        d = query.to_dict()

        assert d["method"] == "bfs"
        assert d["entrypoint"] == "foo"
        assert d["hops"] == 5
        assert d["max_files"] == 10
        assert d["exclude_tests"] is True


class TestIsTestFile:
    """Tests for test file detection patterns."""

    def test_test_underscore_prefix(self) -> None:
        """Detect test_ prefix in path."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("test_main.py")

    def test_tests_dir_prefix(self) -> None:
        """Detect tests/ directory prefix."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("tests/main.py")

    def test_underscore_test_suffix(self) -> None:
        """Detect _test.py suffix."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("main_test.py")
        assert _is_test_file("main_test.js")
        assert _is_test_file("main_test.ts")

    def test_dot_test_suffix(self) -> None:
        """Detect .test.py suffix."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("main.test.py")
        assert _is_test_file("main.test.js")
        assert _is_test_file("main.test.ts")

    def test_spec_patterns(self) -> None:
        """Detect spec patterns."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("src/spec/main.py")
        assert _is_test_file("main_spec.py")
        assert _is_test_file("main.spec.js")

    def test_not_test_file(self) -> None:
        """Non-test files return False."""
        from hypergumbo.slice import _is_test_file
        assert not _is_test_file("src/main.py")
        assert not _is_test_file("utils.py")


class TestSliceEdgeCases:
    """Edge case tests for slice functionality."""

    def test_entry_node_is_test_file_excluded(self) -> None:
        """Entry node in test file is excluded when exclude_tests=True."""
        sym = make_symbol("test_main", path="tests/test_main.py")
        nodes = [sym]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="test_main", max_hops=3, exclude_tests=True)
        result = slice_graph(nodes, edges, query)

        # Entry node should be excluded, so no nodes in result
        assert len(result.node_ids) == 0

    def test_edge_to_nonexistent_node(self) -> None:
        """Edge pointing to non-existent node is skipped."""
        sym_a = make_symbol("caller")
        # Create edge to non-existent node
        edge = Edge.create(
            src=sym_a.id,
            dst="python:nonexistent.py:1-5:missing:function",
            edge_type="calls",
            line=1,
        )

        nodes = [sym_a]
        edges = [edge]

        query = SliceQuery(entrypoint="caller", max_hops=3)
        result = slice_graph(nodes, edges, query)

        # Should only have the source node
        assert len(result.node_ids) == 1
        assert sym_a.id in result.node_ids
        # Edge should not be included since dst doesn't exist
        assert len(result.edge_ids) == 0

    def test_no_matching_entry(self) -> None:
        """Slice with no matching entry returns empty result."""
        sym = make_symbol("real_function")
        nodes = [sym]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="nonexistent", max_hops=3)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 0
        assert len(result.edge_ids) == 0
        assert result.entry_nodes == []
