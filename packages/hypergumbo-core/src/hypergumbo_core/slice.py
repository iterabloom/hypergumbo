# SPDX-License-Identifier: AGPL-3.0-or-later
"""Graph slicing for LLM context extraction.

This module implements BFS-based graph traversal to extract relevant
subgraphs from a behavior map, suitable for providing focused context
to AI coding agents.

How It Works
------------
Given an entrypoint (function name, file path, or node ID), the slicer
performs breadth-first traversal following edges (calls, imports) to
collect related nodes. Traversal respects configurable limits:

- **max_hops**: Depth limit (default None = unlimited). When unset, max_files
  and hub_threshold bound the slice.
- **max_files**: File count limit (default 20). Keeps context focused.
- **min_confidence**: Edge confidence threshold. Filters speculative edges.
- **exclude_tests**: Skips test files to focus on production code.
- **reverse**: Direction of traversal. False = forward (what does X call?),
  True = reverse (what calls X?).

Forward vs Reverse Slicing
--------------------------
Forward slicing (reverse=False, default) answers "what does this function call?"
by following edges from caller to callee. Useful for understanding dependencies
and downstream effects. Structural edges (extends, implements, contains) are
excluded from forward BFS to prevent explosion through shared ancestors and
containment hierarchies (e.g., reaching a class via forward BFS would otherwise
fan out to ALL its member methods via "contains" edges).

Reverse slicing (reverse=True) answers "what calls this function?" by following
edges from callee to caller. Useful for impact analysis - understanding what
code might be affected by changes to a function.

Class-Level Slice Expansion
----------------------------
When slicing from a class/interface entry point (either forward or reverse),
the slicer automatically expands the starting set to include all member methods
(discovered via "contains" edges). For reverse slices, this finds callers of
``findById``, ``search``, etc. For forward slices, this is necessary because
"contains" edges are excluded from BFS traversal, so without expansion a class
entry would not reach its own methods.

The result is a "feature" - a subgraph with a stable ID derived from
the query parameters (sha256 of JSON-serialized query). Same query
always produces same feature ID, enabling caching and reproducibility.

Why BFS (not DFS)
-----------------
BFS explores by distance from entry, so if we hit max_hops, we've seen
all nodes within N hops. DFS might go deep down one path and miss
nearby relevant code. For context extraction, "nearby" code is usually
more relevant than "deep" code.

Dataflow Slicing (ADR-0015)
----------------------------
When ``dataflow=True``, forward slicing follows only write/mutate edges as
the primary BFS chain ("what does this symbol write to, transitively?"). On
top of this, it admits **one-hop downstream reads** from writer nodes: after
visiting a node W that has at least one outgoing write/mutate edge, W's
outgoing read edges are added to the slice as terminal hops. The read-edge
destination enters ``node_ids`` and the edge enters ``edge_ids``, but the
destination is NOT enqueued for further BFS expansion. This captures "data
flows OUT: write site → downstream reads of what was written" — the
semantics ADR-0015 §6 describes — without exploding the slice into
unbounded reader chains. (See WI-saful for the option 1/2/3 tradeoff and
the separate tracker item for the longer-term option 2/3 direction.)

Reverse dataflow slicing follows read edges as its primary chain and does
not apply the one-hop write rule: it is already symmetric in the opposite
direction.

Entry Matching
--------------
The entrypoint spec is matched flexibly:
1. Exact node ID match (most specific)
2. Exact file path match (all symbols in that file)
3. Path suffix match (relative paths match absolute paths ending with same suffix)
4. Exact symbol name match
5. Partial name match (contains)

This lets users say `--entry login` and find `user_login`, `login_handler`, etc.
Path suffix matching enables `--entry src/main.go` to match `/home/user/repo/src/main.go`.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Set

from .ir import Symbol, Edge
from .paths import normalize_path, path_ends_with, is_test_node, is_utility_file
from .ranking import compute_centrality, apply_tier_weights, apply_test_weights

# Structural edges excluded from forward slice BFS traversal.
# These cause BFS explosion through shared ancestors or containment:
# - extends/implements: forward-slicing from VoiceController would follow
#   "extends" to ApplicationController, then fan out to ALL other controllers.
# - contains: reaching a class via forward BFS would fan out to ALL member
#   methods, even siblings unrelated to the slice entry point.
# dispatches_to is NOT excluded: forward slices should traverse from
# interface methods to concrete implementations. Without this, forward
# slices dead-end at every interface call site. The hub_threshold
# parameter handles fan-out for interfaces with many implementations.
# Reverse slices still follow extends/implements (useful for "who inherits
# from this?" and "which interface does this implement?").
# When the entry point IS a container type, forward slice class expansion
# seeds the BFS with member methods so they are still reachable.
_STRUCTURAL_EDGE_TYPES = frozenset({
    "extends", "implements", "contains",
})


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
        max_hops: Maximum traversal depth. None (default) means unlimited —
                  max_files and hub_threshold bound the slice instead.
        max_files: Maximum number of files to include (default: 100).
        min_confidence: Minimum edge confidence to follow (default: 0.0).
        exclude_tests: Whether to exclude test files (default: False).
        exclude_utility: Whether to exclude utility files (docs, examples, scripts).
        method: Traversal method, currently only "bfs" supported.
        reverse: If True, find callers of the entry point (backward traversal).
                 If False (default), find callees (forward traversal).
        max_tier: Maximum supply chain tier to include (1-4). None means no
                  tier filtering. Lower tiers are higher priority.
        language: Filter entry point matches to this language (e.g., "python").
        hub_threshold: Maximum out-degree (forward) or in-degree (reverse) a
                       node may have before it is pruned: included in the slice
                       but NOT traversed through. Default 50 prunes only the
                       top ~1% of nodes by degree. None disables pruning.
        exclude_imports: If True, exclude import edges from both traversal and
                        output. This produces a call-graph-only slice, removing
                        file-level package dependencies that can constitute 60%+
                        of edges in large codebases. Default False.
        pass_through_kinds: Node kinds to traverse through during BFS but
                           exclude from the output node_ids. These are typically
                           synthetic routing nodes (event_publisher, event_subscriber)
                           that connect real code through IPC channels. The BFS
                           still follows their edges, but the final slice only
                           contains real code nodes. Edges touching filtered nodes
                           are also excluded. Default: {event_publisher, event_subscriber}.
        dataflow: If True, only follow edges where a write/mutate at the source
                  connects to a read at the destination (ADR-0015). Produces
                  tighter slices of actual data dependencies. Edges without
                  access_mode metadata are still followed (to avoid breaking
                  slices when annotation is incomplete). Default False.
    """

    entrypoint: str
    max_hops: int | None = None
    max_files: int = 100
    min_confidence: float = 0.0
    exclude_tests: bool = False
    exclude_utility: bool = False
    method: str = "bfs"
    reverse: bool = False
    max_tier: int | None = None
    language: str | None = None
    hub_threshold: int | None = 50
    exclude_imports: bool = False
    pass_through_kinds: frozenset[str] = frozenset({
        "event_publisher", "event_subscriber",
    })
    dataflow: bool = False

    def to_dict(self) -> dict:
        """Serialize query to dict for feature output."""
        result = {
            "method": self.method,
            "entrypoint": self.entrypoint,
            "hops": self.max_hops,
            "max_files": self.max_files,
            "exclude_tests": self.exclude_tests,
            "exclude_utility": self.exclude_utility,
            "reverse": self.reverse,
        }
        if self.max_tier is not None:
            result["max_tier"] = self.max_tier
        if self.language is not None:
            result["language"] = self.language
        if self.hub_threshold is not None:
            result["hub_threshold"] = self.hub_threshold
        if self.exclude_imports:
            result["exclude_imports"] = True
        if self.pass_through_kinds:
            result["pass_through_kinds"] = sorted(self.pass_through_kinds)
        if self.dataflow:
            result["dataflow"] = True
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
        admission_stats: Per-rule edge-admission counters for forward dataflow
            BFS (WI-hukoh Phase A telemetry). Empty dict unless the query has
            ``dataflow=True``. Keys:
            ``admitted_writer_src`` (edge admitted because access_mode in
            {write, mutate}), ``admitted_downstream_read`` (WI-saful option 1
            terminal admission from writer node), ``admitted_no_annotation``
            (edge had no access_mode — graceful-degrade admission),
            ``admitted_reverse_read`` (reverse-mode read-edge admission),
            ``rejected_read_from_non_writer`` (source is a reader outside any
            writer chain — the case option 3 would catch),
            ``rejected_other`` (rejected for another mode reason, e.g. delete),
            ``would_admit_dst_reader`` (subset of rejected edges whose
            dest_access_mode is read/mutate — predictive counter for option 2
            impact before it is implemented).
    """

    entry_nodes: List[str]
    node_ids: Set[str]
    edge_ids: Set[str]
    query: SliceQuery
    limits_hit: List[str] = field(default_factory=list)
    node_depths: Dict[str, int] = field(default_factory=dict)
    node_tiers: Dict[str, int] = field(default_factory=dict)
    admission_stats: Dict[str, int] = field(default_factory=dict)

    @property
    def feature_id(self) -> str:
        """Compute stable feature ID from query spec."""
        query_json = json.dumps(self.query.to_dict(), sort_keys=True)
        hash_hex = hashlib.sha256(query_json.encode()).hexdigest()
        return f"sha256:{hash_hex}"

    def to_dict(self) -> dict:
        """Serialize to spec-compliant feature structure."""
        result = {
            "id": self.feature_id,
            "name": self.query.entrypoint,
            "entry_nodes": self.entry_nodes,
            "node_ids": sorted(self.node_ids),
            "edge_ids": sorted(self.edge_ids),
            "query": self.query.to_dict(),
            "limits_hit": self.limits_hit,
        }
        if self.node_depths:
            result["node_depths"] = dict(sorted(self.node_depths.items()))
        if self.node_tiers:
            result["node_tiers"] = dict(sorted(self.node_tiers.items()))
        if self.admission_stats:
            result["admission_stats"] = dict(sorted(self.admission_stats.items()))
        return result


def find_entry_nodes(
    nodes: List[Symbol], entry_spec: str, language: str | None = None
) -> List[Symbol]:
    """Find nodes matching the entry specification.

    Matching rules (in order of priority):
    1. Exact match on node ID
    2. Exact match on file path
    3. Path suffix match (relative path matches absolute path ending with suffix)
    4. Exact match on symbol name
    5. Partial match (contains) on symbol name

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

    # Try path suffix match (handles relative paths like "src/main.go")
    # Only if entry_spec looks like a path (contains / or \)
    if "/" in entry_spec or "\\" in entry_spec:
        normalized_spec = normalize_path(entry_spec)
        suffix_matches = [
            n for n in nodes
            if path_ends_with(n.path, normalized_spec)
        ]
        if suffix_matches:
            return suffix_matches

    # Try exact name match
    exact_name_matches = [n for n in nodes if n.name == entry_spec]
    if exact_name_matches:
        return exact_name_matches

    # Try partial name match (contains)
    partial_matches = [n for n in nodes if entry_spec in n.name]
    return partial_matches


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

    # ADR-0015 §6 / WI-saful option (1): precompute "writer" nodes for the
    # one-hop downstream read admission rule. A node is a writer if it has at
    # least one outgoing write/mutate edge. In forward dataflow mode, after a
    # writer is visited by BFS, its outgoing read edges are admitted as
    # one-hop terminals: the edge and the destination node enter the slice
    # but the destination is NOT enqueued (so reader chains don't explode).
    # This implements "data flows OUT (write site → downstream reads of what
    # was written)" without requiring per-edge dest_access_mode annotation
    # (which is the option-2/option-3 longer-term direction tracked separately).
    writer_node_ids: Set[str] = set()
    if query.dataflow and not query.reverse:
        for edge in edges:
            if edge.meta is not None and "access_mode" in edge.meta:
                if edge.meta["access_mode"] in ("write", "mutate"):
                    writer_node_ids.add(edge.src)

    # WI-hukoh Phase A: per-rule admission counters for dataflow BFS. Empty
    # unless dataflow mode is active. See SliceResult.admission_stats docstring
    # for key semantics. These counts are incremented at the dataflow decision
    # point only — downstream filters (confidence, tier, hub threshold, file
    # limit) may still reject edges that were admitted here, so the admission
    # totals are an upper bound on what actually lands in the slice.
    admission_stats: Dict[str, int] = {}
    if query.dataflow:
        admission_stats = {
            "admitted_writer_src": 0,
            "admitted_downstream_read": 0,
            "admitted_no_annotation": 0,
            "admitted_reverse_read": 0,
            "rejected_read_from_non_writer": 0,
            "rejected_other": 0,
            "would_admit_dst_reader": 0,
        }

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
    entry_node_ids: Set[str] = {n.id for n in entry_nodes}
    node_depths: Dict[str, int] = {}
    node_tiers: Dict[str, int] = {}

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

    # Container kinds that should be expanded to member methods for reverse slicing.
    # When reverse-slicing from a class/interface, we also want to find callers of
    # its methods, not just edges pointing directly to the class node.
    _CONTAINER_KINDS = {"class", "interface", "module", "struct", "trait", "enum"}

    # Initialize with entry nodes
    for entry in entry_nodes:
        if query.exclude_tests and is_test_node(entry.path, entry.meta):
            continue
        if query.exclude_utility and is_utility_file(entry.path):
            continue
        queue.append((entry.id, 0))
        visited_nodes.add(entry.id)
        node_depths[entry.id] = 0
        node_tiers[entry.id] = getattr(entry, 'supply_chain_tier', 1)
        files_seen.add(entry.path)
        # Add import edges from this file (forward only, unless imports excluded)
        if not query.reverse and not query.exclude_imports:
            add_file_imports(entry.path)

    # Class expansion: when entry nodes include container types (class,
    # interface, etc.), auto-expand to include member methods as additional
    # starting points. For reverse slices, this finds callers of member
    # methods (e.g., --reverse --entry OwnerRepository finds callers of
    # findById, search, etc.). For forward slices, this is needed because
    # 'contains' edges are excluded from forward BFS traversal, so without
    # expansion the class entry would not reach its own methods.
    for entry in entry_nodes:
        if entry.kind not in _CONTAINER_KINDS:
            continue
        # Follow 'contains' edges FROM this class to find member methods
        for edge in edges_from.get(entry.id, []):
            if edge.edge_type != "contains":
                continue
            member = node_by_id.get(edge.dst)
            if member is None:  # pragma: no cover - edge dst always in node_by_id
                continue
            if query.exclude_tests and is_test_node(member.path, member.meta):
                continue
            if query.exclude_utility and is_utility_file(member.path):
                continue
            if member.id not in visited_nodes:
                visited_nodes.add(member.id)
                node_depths[member.id] = 0
                node_tiers[member.id] = getattr(member, 'supply_chain_tier', 1)
                files_seen.add(member.path)
                queue.append((member.id, 0))
                if not query.reverse and not query.exclude_imports:
                    add_file_imports(member.path)

    # BFS traversal
    while queue:
        current_id, hop = queue.popleft()

        # Check hop limit for next level (None means unlimited)
        if query.max_hops is not None and hop >= query.max_hops:
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

        # Hub node pruning: skip traversal for high-degree nodes.
        # Entry nodes and their immediate neighbors (depth ≤ 1) are exempt.
        # This prevents the common "main → run()" pattern from producing
        # nearly-empty slices when run() is a large orchestrator function.
        # dispatches_to edges are exempt from the count: they represent
        # intentional architectural fan-out (registry dispatch), not noisy
        # utility calls. Without this exemption, dispatch sites like
        # run_all_analyzers (100+ handlers) get hub-pruned and the slice
        # misses all registered handlers.
        if (
            query.hub_threshold is not None
            and current_id not in entry_node_ids
            and hop >= 2
        ):
            non_dispatch_edges = [
                e for e in relevant_edges
                if e.edge_type != "dispatches_to"
            ]
            if len(non_dispatch_edges) > query.hub_threshold:
                if "hub_pruned" not in limits_hit:
                    limits_hit.append("hub_pruned")
                # Still follow dispatches_to edges even when hub-pruned
                relevant_edges = [
                    e for e in relevant_edges
                    if e.edge_type == "dispatches_to"
                ]
                if not relevant_edges:
                    continue

        for edge in relevant_edges:
            # Filter by confidence
            if edge.confidence < query.min_confidence:
                continue

            # Skip structural edges to prevent BFS explosion:
            # - Forward: structural edges (extends, implements, contains)
            #   are excluded to prevent fan-out through shared ancestors
            #   (e.g., all controllers sharing ApplicationController).
            #   Note: dispatches_to is NOT structural — it IS followed.
            # - Reverse: 'contains' edges are excluded to prevent false positives.
            #   Without this, reverse slice from method M would traverse
            #   M → Class (via contains) → unrelated callers of Class.
            #   extends/implements are kept in reverse (useful for "who
            #   inherits from this?" queries).
            if not query.reverse and edge.edge_type in _STRUCTURAL_EDGE_TYPES:
                continue
            if query.reverse and edge.edge_type == "contains":
                continue

            # Skip import edges when exclude_imports is set.
            # Import edges are file-level package dependencies, not
            # function-level call relationships.
            if query.exclude_imports and edge.edge_type in ("imports", "imports_module"):
                continue

            # ADR-0015: dataflow mode — only follow data-dependency chains.
            # Forward: follow write/mutate edges (find what this symbol writes to).
            #   PLUS one-hop downstream read admission from writer nodes
            #   (WI-saful option 1): when current_id is a writer (has any
            #   outgoing write/mutate edge), its outgoing read edges are
            #   admitted as terminals — the dst is added to the slice but
            #   NOT enqueued for further BFS expansion.
            # Reverse: follow read edges (find who reads from this symbol).
            # Edges without access_mode metadata are still followed (graceful
            # degradation when annotation coverage is incomplete).
            #
            # WI-hukoh Phase A: every branch increments a counter in
            # admission_stats so we can measure per-rule admission frequency
            # on real repos and predict option-2 impact before implementing it.
            terminal = False
            if query.dataflow and edge.meta is not None and "access_mode" in edge.meta:
                mode = edge.meta["access_mode"]
                if query.reverse:
                    if mode not in ("read",):
                        admission_stats["rejected_other"] += 1
                        continue
                    admission_stats["admitted_reverse_read"] += 1
                else:
                    if mode in ("write", "mutate"):
                        admission_stats["admitted_writer_src"] += 1
                    elif (
                        mode == "read"
                        and current_id in writer_node_ids
                    ):
                        admission_stats["admitted_downstream_read"] += 1
                        terminal = True
                    else:
                        # Rejected by the dataflow rule. Record whether
                        # option 2 (dst_mode OR-check) would have admitted
                        # this edge — this is the predictive counter for
                        # WI-hukoh Phase C decision gate.
                        dest_mode = edge.meta.get("dest_access_mode")
                        if dest_mode in ("read", "mutate"):
                            admission_stats["would_admit_dst_reader"] += 1
                        if mode == "read":
                            admission_stats["rejected_read_from_non_writer"] += 1
                        else:
                            admission_stats["rejected_other"] += 1
                        continue
            elif query.dataflow:
                # No access_mode on the edge: graceful degradation admits it.
                admission_stats["admitted_no_annotation"] += 1

            # Get the node at the other end of the edge
            if query.reverse:
                # Reverse: we're following edges TO current, so next is src
                next_node = node_by_id.get(edge.src)
            else:
                # Forward: we're following edges FROM current, so next is dst
                next_node = node_by_id.get(edge.dst)

            if next_node is None:
                continue

            # Filter test nodes (by path or annotation, e.g. Rust #[test])
            if query.exclude_tests and is_test_node(next_node.path, next_node.meta):
                continue

            # Filter utility files (docs, examples, scripts)
            if query.exclude_utility and is_utility_file(next_node.path):
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
                node_depths[next_node.id] = hop + 1
                node_tiers[next_node.id] = getattr(
                    next_node, 'supply_chain_tier', 1,
                )
                # Terminal edges (one-hop downstream reads from writers)
                # admit the destination but do NOT enqueue it for further
                # BFS expansion. See WI-saful option (1).
                if not terminal:
                    queue.append((next_node.id, hop + 1))
                    # Add import edges from the visited file (forward only,
                    # unless imports excluded)
                    if not query.reverse and not query.exclude_imports:
                        add_file_imports(next_node.path)

    # Filter pass-through synthetic nodes: they were traversed during BFS
    # but should not appear in the output (they represent IPC channels,
    # not real code).  Edges touching filtered nodes are also removed.
    if query.pass_through_kinds:
        pass_through_ids = {
            nid for nid in visited_nodes
            if node_by_id.get(nid) is not None
            and node_by_id[nid].kind in query.pass_through_kinds
        }
        if pass_through_ids:
            visited_nodes -= pass_through_ids
            # Build edge lookup for fast membership check
            edge_by_id = {e.id: e for e in edges}
            visited_edges = {
                eid for eid in visited_edges
                if eid in edge_by_id
                and edge_by_id[eid].src not in pass_through_ids
                and edge_by_id[eid].dst not in pass_through_ids
            }
            for nid in pass_through_ids:
                node_depths.pop(nid, None)
                node_tiers.pop(nid, None)

    return SliceResult(
        entry_nodes=[n.id for n in entry_nodes],
        node_ids=visited_nodes,
        edge_ids=visited_edges,
        query=query,
        limits_hit=limits_hit,
        node_depths=node_depths,
        node_tiers=node_tiers,
        admission_stats=admission_stats,
    )


def rank_slice_nodes(
    result: SliceResult,
    nodes: List[Symbol],
    edges: List[Edge],
    first_party_priority: bool = True,
    test_weight: float | None = None,
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
        test_weight: If set, multiply test file node centrality by this value.
            Useful for reverse slicing where production callers should rank
            higher than test callers. Default None (no test weighting).

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

    # Apply test file weighting if specified
    if test_weight is not None:
        weighted = apply_test_weights(weighted, slice_nodes, test_weight)

    # Sort by weighted centrality (highest first), then by name for stability
    sorted_nodes = sorted(
        slice_nodes,
        key=lambda s: (-weighted.get(s.id, 0), s.name)
    )

    return [n.id for n in sorted_nodes]
