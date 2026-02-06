"""Branch coverage tests for slice.py.

Tests specific branch paths that may not be covered by the main test suite.
"""
from typing import List

import pytest

from hypergumbo_core.slice import (
    find_entry_nodes,
    slice_graph,
    SliceQuery,
)
from hypergumbo_core.ir import Symbol, Edge, Span


def make_symbol(
    id_: str,
    name: str,
    path: str = "src/main.py",
    kind: str = "function",
    language: str = "python",
    supply_chain_tier: int = 1,
) -> Symbol:
    """Create a test symbol."""
    return Symbol(
        id=id_,
        name=name,
        kind=kind,
        path=path,
        language=language,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        supply_chain_tier=supply_chain_tier,
    )


def make_edge(
    src: str,
    dst: str,
    edge_type: str = "calls",
    confidence: float = 1.0,
) -> Edge:
    """Create a test edge."""
    return Edge(
        id=f"{src}->{dst}",
        src=src,
        dst=dst,
        edge_type=edge_type,
        line=1,
        confidence=confidence,
    )


class TestFindEntryNodes:
    """Branch coverage for find_entry_nodes function."""

    def test_path_suffix_no_match(self) -> None:
        """Test path suffix lookup with no matches (branch 220->224)."""
        nodes = [
            make_symbol("id1", "main", path="src/app/main.py"),
            make_symbol("id2", "helper", path="src/lib/helper.py"),
        ]

        # Path suffix that doesn't match anything
        result = find_entry_nodes(nodes, "other/nonexistent.py", None)
        # Should fall through to name matching
        assert len(result) == 0


class TestSliceGraph:
    """Branch coverage for slice_graph function."""

    def test_hop_limit_already_hit(self) -> None:
        """Test when hop_limit is hit multiple times (branch 346->348)."""
        # Create a chain of nodes that exceeds hop limit
        nodes = [
            make_symbol("n1", "start", path="a.py"),
            make_symbol("n2", "mid1", path="b.py"),
            make_symbol("n3", "mid2", path="c.py"),
            make_symbol("n4", "end", path="d.py"),
        ]
        edges = [
            make_edge("n1", "n2"),
            make_edge("n2", "n3"),
            make_edge("n3", "n4"),
        ]

        query = SliceQuery(
            entrypoint="start",
            max_hops=1,  # Will hit limit after n2
            max_files=100,
        )

        result = slice_graph(nodes, edges, query)
        # hop_limit should be in limits_hit exactly once
        assert "hop_limit" in result.limits_hit
        assert result.limits_hit.count("hop_limit") == 1

    def test_tier_limit_already_hit(self) -> None:
        """Test when tier_limit is hit multiple times (branch 382->384)."""
        nodes = [
            make_symbol("n1", "first_party", path="a.py", supply_chain_tier=1),
            make_symbol("n2", "external1", path="b.py", supply_chain_tier=3),
            make_symbol("n3", "external2", path="c.py", supply_chain_tier=3),
        ]
        edges = [
            make_edge("n1", "n2"),
            make_edge("n1", "n3"),
        ]

        query = SliceQuery(
            entrypoint="first_party",
            max_hops=10,
            max_tier=2,  # Will exclude tier 3 nodes
            max_files=100,
        )

        result = slice_graph(nodes, edges, query)
        # tier_limit should be in limits_hit exactly once
        assert "tier_limit" in result.limits_hit
        assert result.limits_hit.count("tier_limit") == 1

    def test_file_limit_already_hit(self) -> None:
        """Test when file_limit is hit multiple times (branch 389->391)."""
        nodes = [
            make_symbol("n1", "start", path="a.py"),
            make_symbol("n2", "f2", path="b.py"),
            make_symbol("n3", "f3", path="c.py"),
            make_symbol("n4", "f4", path="d.py"),
        ]
        edges = [
            make_edge("n1", "n2"),
            make_edge("n1", "n3"),
            make_edge("n1", "n4"),
        ]

        query = SliceQuery(
            entrypoint="start",
            max_hops=10,
            max_files=2,  # Will hit limit on 3rd file
        )

        result = slice_graph(nodes, edges, query)
        # file_limit should be in limits_hit exactly once
        assert "file_limit" in result.limits_hit
        assert result.limits_hit.count("file_limit") == 1

    def test_import_edge_below_confidence(self) -> None:
        """Test import edges filtered by confidence (branch 323->321)."""
        nodes = [
            make_symbol("n1", "main", path="main.py"),
            make_symbol("n2", "helper", path="helper.py"),
        ]

        # Create file node for import edges
        file_id = "python:main.py:1-1:file:file"

        edges = [
            make_edge("n1", "n2"),  # Regular call edge
            Edge(
                id="import1",
                src=file_id,
                dst="helper.py",
                edge_type="imports",
                line=1,
                confidence=0.3,  # Low confidence
            ),
        ]

        query = SliceQuery(
            entrypoint="main",
            max_hops=10,
            min_confidence=0.5,  # Higher than import edge
            max_files=100,
        )

        result = slice_graph(nodes, edges, query)
        # Import edge should not be included due to low confidence
        assert "import1" not in result.edge_ids

    def test_non_import_edge_from_file_node(self) -> None:
        """Test non-import edges from file nodes skipped (branch 322->321)."""
        nodes = [
            make_symbol("n1", "main", path="main.py"),
            make_symbol("n2", "helper", path="helper.py"),
        ]

        file_id = "python:main.py:1-1:file:file"

        edges = [
            make_edge("n1", "n2"),
            Edge(
                id="ref1",
                src=file_id,
                dst="helper.py",
                edge_type="references",  # Not an import edge
                line=1,
                confidence=1.0,
            ),
        ]

        query = SliceQuery(
            entrypoint="main",
            max_hops=10,
            max_files=100,
        )

        result = slice_graph(nodes, edges, query)
        # References edge from file node should not be included
        assert "ref1" not in result.edge_ids
