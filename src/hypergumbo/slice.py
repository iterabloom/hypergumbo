"""Graph slicing for LLM context extraction.

This module implements BFS-based graph traversal to extract relevant
subgraphs from a behavior map, suitable for providing focused context
to AI coding agents.

How It Works
------------
Given an entrypoint (function name, file path, or node ID), the slicer
performs breadth-first traversal following edges (calls, imports) to
collect related nodes. Traversal respects configurable limits:

- **max_hops**: Depth limit (default 3). Prevents unbounded exploration.
- **max_files**: File count limit (default 20). Keeps context focused.
- **min_confidence**: Edge confidence threshold. Filters speculative edges.
- **exclude_tests**: Skips test files to focus on production code.

The result is a "feature" - a subgraph with a stable ID derived from
the query parameters (sha256 of JSON-serialized query). Same query
always produces same feature ID, enabling caching and reproducibility.

Why BFS (not DFS)
-----------------
BFS explores by distance from entry, so if we hit max_hops, we've seen
all nodes within N hops. DFS might go deep down one path and miss
nearby relevant code. For context extraction, "nearby" code is usually
more relevant than "deep" code.

Entry Matching
--------------
The entrypoint spec is matched flexibly:
1. Exact node ID match (most specific)
2. Exact file path match (all symbols in that file)
3. Exact symbol name match
4. Partial name match (contains)

This lets users say `--entry login` and find `user_login`, `login_handler`, etc.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .ir import Symbol, Edge


@dataclass
class SliceQuery:
    """Configuration for a graph slice operation.

    Attributes:
        entrypoint: Symbol name, file path, or node ID to start from.
        max_hops: Maximum traversal depth (default: 3).
        max_files: Maximum number of files to include (default: 20).
        min_confidence: Minimum edge confidence to follow (default: 0.0).
        exclude_tests: Whether to exclude test files (default: False).
        method: Traversal method, currently only "bfs" supported.
    """

    entrypoint: str
    max_hops: int = 3
    max_files: int = 20
    min_confidence: float = 0.0
    exclude_tests: bool = False
    method: str = "bfs"

    def to_dict(self) -> dict:
        """Serialize query to dict for feature output."""
        return {
            "method": self.method,
            "entrypoint": self.entrypoint,
            "hops": self.max_hops,
            "max_files": self.max_files,
            "exclude_tests": self.exclude_tests,
        }


@dataclass
class SliceResult:
    """Result of a graph slice operation.

    Attributes:
        entry_nodes: IDs of the entry point nodes.
        node_ids: IDs of all nodes in the slice.
        edge_ids: IDs of all edges in the slice.
        query: The query that produced this result.
        limits_hit: List of limits that were reached (e.g., "hop_limit").
    """

    entry_nodes: List[str]
    node_ids: Set[str]
    edge_ids: Set[str]
    query: SliceQuery
    limits_hit: List[str] = field(default_factory=list)

    @property
    def feature_id(self) -> str:
        """Compute stable feature ID from query spec."""
        query_json = json.dumps(self.query.to_dict(), sort_keys=True)
        hash_hex = hashlib.sha256(query_json.encode()).hexdigest()
        return f"sha256:{hash_hex}"

    def to_dict(self) -> dict:
        """Serialize to spec-compliant feature structure."""
        return {
            "id": self.feature_id,
            "name": self.query.entrypoint,
            "entry_nodes": self.entry_nodes,
            "node_ids": sorted(self.node_ids),
            "edge_ids": sorted(self.edge_ids),
            "query": self.query.to_dict(),
            "limits_hit": self.limits_hit,
        }


def find_entry_nodes(nodes: List[Symbol], entry_spec: str) -> List[Symbol]:
    """Find nodes matching the entry specification.

    Matching rules (in order of priority):
    1. Exact match on node ID
    2. Exact match on file path
    3. Exact match on symbol name
    4. Partial match (contains) on symbol name

    Args:
        nodes: All available nodes.
        entry_spec: Entry point specification (name, path, or ID).

    Returns:
        List of matching nodes.
    """
    # Try exact ID match first
    exact_id_matches = [n for n in nodes if n.id == entry_spec]
    if exact_id_matches:
        return exact_id_matches

    # Try exact file path match
    exact_path_matches = [n for n in nodes if n.path == entry_spec]
    if exact_path_matches:
        return exact_path_matches

    # Try exact name match
    exact_name_matches = [n for n in nodes if n.name == entry_spec]
    if exact_name_matches:
        return exact_name_matches

    # Try partial name match (contains)
    partial_matches = [n for n in nodes if entry_spec in n.name]
    return partial_matches


def _is_test_file(path: str) -> bool:
    """Check if a path looks like a test file."""
    # Common test file patterns
    if "/test_" in path or "/tests/" in path:
        return True
    if path.startswith("test_") or path.startswith("tests/"):
        return True
    if "_test.py" in path or "_test.js" in path or "_test.ts" in path:
        return True
    if ".test.py" in path or ".test.js" in path or ".test.ts" in path:
        return True
    if "/spec/" in path or "_spec.py" in path or ".spec.js" in path:
        return True
    return False


def slice_graph(
    nodes: List[Symbol],
    edges: List[Edge],
    query: SliceQuery,
) -> SliceResult:
    """Perform BFS graph traversal from entry points.

    Args:
        nodes: All nodes in the graph.
        edges: All edges in the graph.
        query: Slice configuration.

    Returns:
        SliceResult containing the subgraph.
    """
    # Build lookup structures
    node_by_id: Dict[str, Symbol] = {n.id: n for n in nodes}
    edges_from: Dict[str, List[Edge]] = {}
    for edge in edges:
        if edge.src not in edges_from:
            edges_from[edge.src] = []
        edges_from[edge.src].append(edge)

    # Find entry nodes
    entry_nodes = find_entry_nodes(nodes, query.entrypoint)
    if not entry_nodes:
        return SliceResult(
            entry_nodes=[],
            node_ids=set(),
            edge_ids=set(),
            query=query,
            limits_hit=[],
        )

    # Track results
    visited_nodes: Set[str] = set()
    visited_edges: Set[str] = set()
    files_seen: Set[str] = set()
    limits_hit: List[str] = []

    # BFS state: (node_id, current_hop)
    queue: deque[tuple[str, int]] = deque()

    # Initialize with entry nodes
    for entry in entry_nodes:
        if query.exclude_tests and _is_test_file(entry.path):
            continue
        queue.append((entry.id, 0))
        visited_nodes.add(entry.id)
        files_seen.add(entry.path)

    # BFS traversal
    while queue:
        current_id, hop = queue.popleft()

        # Check hop limit for next level
        if hop >= query.max_hops:
            if "hop_limit" not in limits_hit:
                limits_hit.append("hop_limit")
            continue

        # Get outgoing edges
        outgoing = edges_from.get(current_id, [])

        for edge in outgoing:
            # Filter by confidence
            if edge.confidence < query.min_confidence:
                continue

            # Get destination node
            dst_node = node_by_id.get(edge.dst)
            if dst_node is None:
                continue

            # Filter test files
            if query.exclude_tests and _is_test_file(dst_node.path):
                continue

            # Check file limit
            if dst_node.path not in files_seen:
                if len(files_seen) >= query.max_files:
                    if "file_limit" not in limits_hit:
                        limits_hit.append("file_limit")
                    continue
                files_seen.add(dst_node.path)

            # Visit edge and node
            visited_edges.add(edge.id)

            if dst_node.id not in visited_nodes:
                visited_nodes.add(dst_node.id)
                queue.append((dst_node.id, hop + 1))

    return SliceResult(
        entry_nodes=[n.id for n in entry_nodes],
        node_ids=visited_nodes,
        edge_ids=visited_edges,
        query=query,
        limits_hit=limits_hit,
    )
