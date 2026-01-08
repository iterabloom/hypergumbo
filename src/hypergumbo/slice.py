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
- **reverse**: Direction of traversal. False = forward (what does X call?),
  True = reverse (what calls X?).

Forward vs Reverse Slicing
--------------------------
Forward slicing (reverse=False, default) answers "what does this function call?"
by following edges from caller to callee. Useful for understanding dependencies
and downstream effects.

Reverse slicing (reverse=True) answers "what calls this function?" by following
edges from callee to caller. Useful for impact analysis - understanding what
code might be affected by changes to a function.

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
from typing import Dict, List, Set

from .ir import Symbol, Edge
from .ranking import compute_centrality, apply_tier_weights


class AmbiguousEntryError(Exception):
    """Raised when entry spec matches multiple symbols in different files.

    This error helps users disambiguate by showing the matching candidates
    with their file paths and node IDs.

    Attributes:
        entry_spec: The entry specification that was ambiguous.
        candidates: List of Symbol objects that matched.
    """

    def __init__(self, entry_spec: str, candidates: List[Symbol]) -> None:
        self.entry_spec = entry_spec
        self.candidates = candidates

        # Build helpful error message
        lines = [
            f"Ambiguous entry '{entry_spec}' matches {len(candidates)} symbols "
            f"in different files:",
        ]
        for sym in candidates:
            lines.append(f"  [{sym.language}] {sym.path}:{sym.span.start_line}")
            lines.append(f"    ID: {sym.id}")
        lines.append("")
        lines.append("Use a full node ID to disambiguate, or filter with --language.")

        super().__init__("\n".join(lines))


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
        reverse: If True, find callers of the entry point (backward traversal).
                 If False (default), find callees (forward traversal).
        max_tier: Maximum supply chain tier to include (1-4). None means no
                  tier filtering. Lower tiers are higher priority.
        language: Filter entry point matches to this language (e.g., "python").
    """

    entrypoint: str
    max_hops: int = 3
    max_files: int = 20
    min_confidence: float = 0.0
    exclude_tests: bool = False
    method: str = "bfs"
    reverse: bool = False
    max_tier: int | None = None
    language: str | None = None

    def to_dict(self) -> dict:
        """Serialize query to dict for feature output."""
        result = {
            "method": self.method,
            "entrypoint": self.entrypoint,
            "hops": self.max_hops,
            "max_files": self.max_files,
            "exclude_tests": self.exclude_tests,
            "reverse": self.reverse,
        }
        if self.max_tier is not None:
            result["max_tier"] = self.max_tier
        if self.language is not None:
            result["language"] = self.language
        return result


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


def find_entry_nodes(
    nodes: List[Symbol], entry_spec: str, language: str | None = None
) -> List[Symbol]:
    """Find nodes matching the entry specification.

    Matching rules (in order of priority):
    1. Exact match on node ID
    2. Exact match on file path
    3. Exact match on symbol name
    4. Partial match (contains) on symbol name

    Args:
        nodes: All available nodes.
        entry_spec: Entry point specification (name, path, or ID).
        language: Optional language filter (e.g., "python").

    Returns:
        List of matching nodes.
    """
    # Apply language filter if specified
    if language:
        nodes = [n for n in nodes if n.language == language]

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
    """Check if a path looks like a test file.

    Excludes:
    - Files starting with test_ or ending with _test.py/_test.js/etc.
    - Go test files (*_test.go)
    - Mock/fake files (*_mock.*, *_fake.*, fake_*.*, mock_*.*)
    - Files in tests/, test/, spec/, fakes/, mocks/, fixtures/ directories
    """
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

    # Go test files
    if "_test.go" in path:
        return True

    # Mock/fake file patterns
    path_lower = path.lower()
    if "_mock." in path_lower or "_fake." in path_lower:
        return True
    if "/mock_" in path_lower or "/fake_" in path_lower:
        return True

    # Mock/fake directories
    path_parts = path.replace("\\", "/").split("/")
    test_dirs = {
        "fakes", "mocks", "testfakes", "testmocks",
        "fixtures", "testdata", "testutils",
    }
    # Also match compound names like "transportfakes" that end with "fakes"/"mocks"
    for part in path_parts:
        part_lower = part.lower()
        if part_lower in test_dirs:
            return True
        if part_lower.endswith("fakes") or part_lower.endswith("mocks"):
            return True
    return False


def slice_graph(
    nodes: List[Symbol],
    edges: List[Edge],
    query: SliceQuery,
) -> SliceResult:
    """Perform BFS graph traversal from entry points.

    For forward slicing (reverse=False), follows edges from caller to callee
    to answer "what does X call?"

    For reverse slicing (reverse=True), follows edges from callee to caller
    to answer "what calls X?"

    Args:
        nodes: All nodes in the graph.
        edges: All edges in the graph.
        query: Slice configuration.

    Returns:
        SliceResult containing the subgraph.
    """
    # Build lookup structures
    node_by_id: Dict[str, Symbol] = {n.id: n for n in nodes}

    # Build edge maps for both directions
    edges_from: Dict[str, List[Edge]] = {}  # src -> edges (for forward traversal)
    edges_to: Dict[str, List[Edge]] = {}    # dst -> edges (for reverse traversal)
    for edge in edges:
        if edge.src not in edges_from:
            edges_from[edge.src] = []
        edges_from[edge.src].append(edge)

        if edge.dst not in edges_to:
            edges_to[edge.dst] = []
        edges_to[edge.dst].append(edge)

    # Build file path -> file node IDs mapping for import edge lookup
    # Import edges source from file nodes with ID format: {lang}:{path}:1-1:file:file
    # We collect all unique (path, language) combinations from nodes
    file_node_ids: Dict[str, List[str]] = {}
    for node in nodes:
        if node.path not in file_node_ids:
            file_node_ids[node.path] = []
        # Construct the file node ID that import edges use as source
        file_id = f"{node.language}:{node.path}:1-1:file:file"
        if file_id not in file_node_ids[node.path]:
            file_node_ids[node.path].append(file_id)

    # Find entry nodes
    entry_nodes = find_entry_nodes(nodes, query.entrypoint, query.language)
    if not entry_nodes:
        return SliceResult(
            entry_nodes=[],
            node_ids=set(),
            edge_ids=set(),
            query=query,
            limits_hit=[],
        )

    # Check for ambiguous entry: multiple matches in different files
    # This is only an issue for name-based matches, not exact ID matches
    if len(entry_nodes) > 1:
        # Check if the entry was an exact ID match (not ambiguous)
        is_exact_id = any(n.id == query.entrypoint for n in entry_nodes)
        if not is_exact_id:
            # Check if matches are in different files
            unique_files = {n.path for n in entry_nodes}
            if len(unique_files) > 1:
                raise AmbiguousEntryError(query.entrypoint, entry_nodes)

    # Track results
    visited_nodes: Set[str] = set()
    visited_edges: Set[str] = set()
    files_seen: Set[str] = set()
    files_with_imports_added: Set[str] = set()  # Track files whose imports we've added
    limits_hit: List[str] = []

    def add_file_imports(file_path: str) -> None:
        """Add import edges from the file node(s) for the given path."""
        if file_path in files_with_imports_added:
            return
        files_with_imports_added.add(file_path)

        # Find file node IDs (may have multiple for different languages)
        file_ids = file_node_ids.get(file_path, [])

        # Add all import edges from these file nodes
        for file_node_id in file_ids:
            for edge in edges_from.get(file_node_id, []):
                if edge.edge_type == "imports":
                    if edge.confidence >= query.min_confidence:
                        visited_edges.add(edge.id)

    # BFS state: (node_id, current_hop)
    queue: deque[tuple[str, int]] = deque()

    # Initialize with entry nodes
    for entry in entry_nodes:
        if query.exclude_tests and _is_test_file(entry.path):
            continue
        queue.append((entry.id, 0))
        visited_nodes.add(entry.id)
        files_seen.add(entry.path)
        # Add import edges from this file (forward only)
        if not query.reverse:
            add_file_imports(entry.path)

    # BFS traversal
    while queue:
        current_id, hop = queue.popleft()

        # Check hop limit for next level
        if hop >= query.max_hops:
            if "hop_limit" not in limits_hit:
                limits_hit.append("hop_limit")
            continue

        # Get edges to follow based on direction
        if query.reverse:
            # Reverse: follow edges TO this node (find callers)
            relevant_edges = edges_to.get(current_id, [])
        else:
            # Forward: follow edges FROM this node (find callees)
            relevant_edges = edges_from.get(current_id, [])

        for edge in relevant_edges:
            # Filter by confidence
            if edge.confidence < query.min_confidence:
                continue

            # Get the node at the other end of the edge
            if query.reverse:
                # Reverse: we're following edges TO current, so next is src
                next_node = node_by_id.get(edge.src)
            else:
                # Forward: we're following edges FROM current, so next is dst
                next_node = node_by_id.get(edge.dst)

            if next_node is None:
                continue

            # Filter test files
            if query.exclude_tests and _is_test_file(next_node.path):
                continue

            # Check tier limit
            if query.max_tier is not None:
                node_tier = getattr(next_node, 'supply_chain_tier', 1)
                if node_tier > query.max_tier:
                    if "tier_limit" not in limits_hit:
                        limits_hit.append("tier_limit")
                    continue

            # Check file limit
            if next_node.path not in files_seen:
                if len(files_seen) >= query.max_files:
                    if "file_limit" not in limits_hit:
                        limits_hit.append("file_limit")
                    continue
                files_seen.add(next_node.path)

            # Visit edge and node
            visited_edges.add(edge.id)

            if next_node.id not in visited_nodes:
                visited_nodes.add(next_node.id)
                queue.append((next_node.id, hop + 1))
                # Add import edges from the visited file (forward only)
                if not query.reverse:
                    add_file_imports(next_node.path)

    return SliceResult(
        entry_nodes=[n.id for n in entry_nodes],
        node_ids=visited_nodes,
        edge_ids=visited_edges,
        query=query,
        limits_hit=limits_hit,
    )


def rank_slice_nodes(
    result: SliceResult,
    nodes: List[Symbol],
    edges: List[Edge],
    first_party_priority: bool = True,
) -> List[str]:
    """Rank nodes in a slice by importance.

    Uses centrality and tier weighting to order the slice nodes from
    most to least important. This enables more informative output for
    LLMs and users.

    Args:
        result: The slice result containing node_ids to rank.
        nodes: All nodes in the graph (for looking up symbols).
        edges: All edges in the graph (for computing centrality).
        first_party_priority: If True, boost first-party code ranking.

    Returns:
        List of node IDs ordered by importance (highest first).
    """
    # Filter to only nodes in the slice
    node_by_id = {n.id: n for n in nodes}
    slice_nodes = [node_by_id[nid] for nid in result.node_ids if nid in node_by_id]

    if not slice_nodes:
        return sorted(result.node_ids)

    # Filter edges to only those within the slice
    slice_node_ids = result.node_ids
    slice_edges = [
        e for e in edges
        if e.src in slice_node_ids and e.dst in slice_node_ids
    ]

    # Compute centrality on the subgraph
    centrality = compute_centrality(slice_nodes, slice_edges)

    # Apply tier weighting if enabled
    if first_party_priority:
        weighted = apply_tier_weights(centrality, slice_nodes)
    else:
        weighted = centrality

    # Sort by weighted centrality (highest first), then by name for stability
    sorted_nodes = sorted(
        slice_nodes,
        key=lambda s: (-weighted.get(s.id, 0), s.name)
    )

    return [n.id for n in sorted_nodes]
