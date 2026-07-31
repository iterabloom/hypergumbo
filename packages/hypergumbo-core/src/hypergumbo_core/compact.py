# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compact output mode: budget-aware symbol selection + residual summarization.

This module produces LLM-friendly output by selecting a subset of
symbols / edges / files that fit a target token (or coverage) budget,
then summarizing what was omitted so a downstream LLM can decide
whether to request expansion.

Selection algorithm families
----------------------------
The module exposes several selection algorithms; the CLI surface picks
between them based on the requested output mode:

- ``select_by_coverage`` — selects the *fewest* symbols needed to
  capture a target percentage of total centrality mass. Adapts to
  the codebase's centrality distribution (concentrated codebases
  need fewer items; flat codebases need more).
- ``select_by_tokens`` — token-budget tier mode. Emits multiple
  successively-larger views (``DEFAULT_TIERS`` = 4k / 16k / 64k by
  default; configurable via ``--tier``). ``format_tiered_behavior_map``
  + ``generate_tier_filename`` materialize the multi-tier output.
- ``select_by_connectivity`` — UnionFind / frontier algorithm that
  prefers component-bridging edges, so the included subgraph is
  connected rather than a disjoint top-N. Seeded with
  ``CROSS_CUTTING_EDGE_TYPES`` (canonicalized via the ADR-0023 §6
  fold so ``http_calls`` / ``grpc_calls`` / ``graphql_calls`` /
  ``routes_to`` / ``di_resolves`` / ``message_queue`` etc. all map
  to ``calls`` / ``dispatches_to`` / ``event_publishes``).
- Language-proportional selection (``min_per_language``) + under-
  represented-language seeding so a Rust + JS repo doesn't produce a
  Rust-only sketch when the budget is tight.

Pre- and post-selection passes
------------------------------
- Entrypoints are force-included above the confidence threshold (0.7)
  before budget enforcement, then capped to keep the entry-point
  bucket from dominating.
- Filters: test / example / external-boundary symbols are dropped
  via ``selection.filters`` (consuming ``EXCLUDED_KINDS`` /
  ``EXCLUDED_FRAMEWORK_ROLES`` via the ``is_excluded_kind`` dual-shape
  predicate, per ADR-0027 Phase 3 Wave 5).
- Name-deduplication for repeated-symbol cases.
- Post-selection victim removal enforces the final budget, guarded by
  ``_VICTIM_REMOVAL_EXCLUDE_DAMPENERS`` so structurally-important
  symbols aren't dropped by accident.

Residual summarization
----------------------
Omitted items are summarized with cheap extractive signals:
- Word frequency on symbol names (bag-of-words)
- File path pattern analysis
- Kind distribution (functions, classes, methods)

Example output (the omitted-residual summary, ``OmittedSummary.to_dict``):
    {
      "count": 1200,
      "centrality_sum": 0.18,
      "max_centrality": 0.94,
      "top_words": [{"word": "test", "count": 42}, {"word": "mock", "count": 30}],
      "top_paths": [{"pattern": "tests/", "count": 55}, {"pattern": "vendor/", "count": 21}],
      "kinds": {"function": 900, "class": 200, "method": 100},
      "tiers": {"4000": 12, "16000": 40, "64000": 148}
    }

Why Bag-of-Words
----------------
Symbol names are information-dense. Words like "test", "handler",
"parse", "config" reveal what categories of code are being omitted.
This gives LLMs enough context to decide whether to request expansion.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from .ir import Symbol, Edge, is_external_boundary
from .ranking import (
    compute_centrality,
    compute_dampened_centrality,
    apply_tier_weights,
)
from .selection.filters import (
    EXAMPLE_PATH_PATTERNS,  # re-export for backwards compatibility
    EXCLUDED_KINDS,  # re-export: test_compact.py imports this  # noqa: F401
    is_excluded_kind,
    is_test_path as _is_test_path,  # re-export: test_compact.py imports this  # noqa: F401
    is_example_path as _is_example_path,
)
from .paths import is_test_node as _is_test_node
from .metrics import compute_metrics
from .selection.language_proportional import (
    allocate_language_budget,
    find_underrepresented_language_seeds,
    group_symbols_by_language,
)
from .selection.token_budget import (
    CHARS_PER_TOKEN,  # re-export for backwards compatibility
    DEFAULT_TIERS,  # re-export for backwards compatibility
    TOKENS_BEHAVIOR_MAP_OVERHEAD,
    estimate_json_tokens,
    parse_tier_spec,  # re-export for backwards compatibility
)

# Re-exports for backwards compatibility (from selection.* modules)
__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_TIERS",
    "EXAMPLE_PATH_PATTERNS",
    "parse_tier_spec",
]

# Dampeners to exclude when computing victim-removal ordering in
# format_tiered_behavior_map. Victim selection deliberately uses
# tier-weighted centrality only (no noise/utility/etc. dampening), so
# that selection-time signals don't propagate into post-budget pruning.
# See compact.py:format_tiered_behavior_map for rationale.
_VICTIM_REMOVAL_EXCLUDE_DAMPENERS = (
    "noise",
    "utility",
    "common_method",
    "trivial_sink",
    "generated",
    "file_kind",
)

# Edge types that represent cross-cutting concerns (linker-produced edges that
# connect nodes across language, service, or abstraction boundaries).  These are
# the primary value proposition of the linker pipeline.  Compact mode must
# seed their endpoints into the node selection so that these edges survive the
# induced-subgraph filter — without this, centrality-based selection drops the
# peripheral nodes that are endpoints of these edges.
#
# ADR-0023 §6 Phase 3 (WI-vasik-jofiv) folded routes_to and di_resolves
# into 'dispatches_to' (already in the set), and message_dispatch /
# annotated_publishes / crdt_publishes / enqueues / message_send /
# message_queue / etc. into 'event_publishes'. Phase 4b (WI-vomoj-suhaz)
# pruned the deprecated entries from this set; the canonical members
# transparently cover the folds via dispatches_to / event_publishes.
# WI-vumum-juvil folded http_calls / grpc_calls / graphql_calls into
# canonical 'calls' + meta['protocol']; cross-cutting endpoint seeding
# still picks up HTTP/gRPC/GraphQL edges via the canonical name.
CROSS_CUTTING_EDGE_TYPES = frozenset({
    "calls",           # generic invocation (covers FFI/IPC/RPC bridges
                       # and protocol-call family — HTTP, gRPC, GraphQL —
                       # post WI-vumum-juvil)
    "dispatches_to",   # runtime dispatch indirection (covers routes,
                       # delegates, DI resolution, annotation dispatch)
    "event_publishes", # async producer→consumer (covers IPC, websocket,
                       # queue, CRDT, message_bus)
})


# Top-level blocks dropped from the budget-limited projected views to reduce
# payload. The two views differ DELIBERATELY on provenance/quality signals:
#
#   _COMPACT_STRIP_KEYS — the compact view drops only the heavy, view-irrelevant
#     blocks: usage_contexts (spec-mandated stripped from compact/tiered,
#     docs/hypergumbo-spec.md §usage_contexts) and sketch_precomputed (an
#     internal cache artifact consumers must not depend on, spec §707). It KEEPS
#     analysis_runs (provenance) and validation_report (the finalize quality
#     signal) — ADR-0033/ADR-0043 preserve both through the compact projection
#     (test_compact_preserves_validation_report_and_consistency).
#   _TIERED_STRIP_KEYS — the tiered view is the more aggressive budget projection
#     and ALSO drops analysis_runs and validation_report
#     (test_budget_tier_omits_validation_report).
_COMPACT_STRIP_KEYS = frozenset({
    "usage_contexts",
    "sketch_precomputed",
})
_TIERED_STRIP_KEYS = _COMPACT_STRIP_KEYS | frozenset({
    "analysis_runs",
    "validation_report",
})

# WI-pohuf: the compact/tiered views are LLM-friendly, token-budgeted
# projections — NOT the full survey. Emitting the full ~24-field
# ``Symbol.to_dict()`` per node (identity hashes stable_id/shape_id/fingerprint,
# provenance origin/origin_run_id, the ~200-byte supply_chain block, meta, and
# other internals) makes each node cost ~250 tokens, so at small budgets the
# post-selection shrink loop trims the map down to ~1 surviving symbol — which
# also broke compact containment monotonicity (WI-kolal: the single symbol that
# fits at 4k differs from the one at 16k) and centrality coverage (WI-zulij: the
# few survivors hold a tiny fraction of total centrality). This projection keeps
# only the fields a consumer needs to *understand and navigate* a symbol; the
# identity, provenance, and supply-chain details remain in the full survey.
_COMPACT_NODE_FIELDS: tuple[str, ...] = (
    "id", "name", "qualified_name", "kind", "language",
    "path", "span", "signature", "docstring",
)


def compact_node(symbol: Symbol) -> dict[str, Any]:
    """Project a ``Symbol`` to the slim compact/tiered-view node representation.

    Keeps only the LLM-meaningful navigation/understanding fields
    (``_COMPACT_NODE_FIELDS``) and omits null-valued optional ones, dropping the
    identity hashes, provenance ids, and supply-chain internals that would
    otherwise dominate the token budget (WI-pohuf). ``centrality`` is added
    separately by ``_annotate_node_centrality`` on the paths that annotate it.
    """
    full = symbol.to_dict()
    return {k: full[k] for k in _COMPACT_NODE_FIELDS if full.get(k) is not None}


def _recompute_view_metrics(view_map: dict[str, Any]) -> None:
    """Recompute a projected view's ``metrics`` block from its OWN nodes/edges.

    A budget-limited projection (compact/tiered) shallow-copies the source map,
    so without this it would echo the FULL-repo ``metrics`` (total_nodes,
    total_edges, total_files, by_supply_chain_tier) that ``compute_metrics``
    produced BEFORE projection — a view that lies about itself (WI-pizat).
    Recompute in place so the counts describe the projected arrays. Only fires
    when the source carried a ``metrics`` block (the projection mirrors the
    source's structure — it does not invent one). Deliberately does NOT touch
    ``analysis_incomplete``: per spec §726 that flag is analyzer-scope (set only
    on early termination / errors / resource limits), not a view-truncation
    signal.
    """
    if "metrics" in view_map:
        view_map["metrics"] = compute_metrics(
            view_map["nodes"], view_map["edges"],
            profile=view_map.get("profile"),
        )


@dataclass
class CompactConfig:
    """Configuration for compact output mode.

    Attributes:
        target_coverage: Centrality coverage target (0.0-1.0). Include symbols
            until this fraction of total centrality is captured. Default 0.8.
        max_symbols: Hard cap on included symbols. Default 100.
        min_symbols: Minimum symbols to include even if coverage met. Default 10.
        top_words_count: Number of top words to include in summary. Default 10.
        top_paths_count: Number of top path patterns to include. Default 5.
        first_party_priority: Apply tier weighting. Default True.
        language_proportional: Use language-stratified selection. Default True.
            When enabled, symbol budget is allocated proportionally by language
            to ensure multi-language projects have representation from each.
        min_per_language: Minimum symbols per language (floor guarantee).
            Only used when language_proportional=True. Default 1.
    """

    target_coverage: float = 0.8
    max_symbols: int = 100
    min_symbols: int = 10
    top_words_count: int = 10
    top_paths_count: int = 5
    first_party_priority: bool = True
    language_proportional: bool = True
    min_per_language: int = 1


@dataclass
class IncludedSummary:
    """Summary of included symbols."""

    count: int
    centrality_sum: float
    coverage: float
    symbols: List[Symbol]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "count": self.count,
            "centrality_sum": round(self.centrality_sum, 4),
            "coverage": round(self.coverage, 4),
        }


@dataclass
class OmittedSummary:
    """Summary of omitted symbols with semantic flavor."""

    count: int
    centrality_sum: float
    max_centrality: float
    top_words: List[Tuple[str, int]]
    top_paths: List[Tuple[str, int]]
    kinds: Dict[str, int]
    tiers: Dict[int, int]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "count": self.count,
            "centrality_sum": round(self.centrality_sum, 4),
            "max_centrality": round(self.max_centrality, 4),
            "top_words": [{"word": w, "count": c} for w, c in self.top_words],
            "top_paths": [{"pattern": p, "count": c} for p, c in self.top_paths],
            "kinds": self.kinds,
            "tiers": {str(k): v for k, v in self.tiers.items()},
        }


@dataclass
class CompactResult:
    """Result of compact selection."""

    included: IncludedSummary
    omitted: OmittedSummary
    config: CompactConfig = field(default_factory=CompactConfig)
    # Per-node centrality (id -> score) for the SELECTED symbols, on THIS
    # selection mode's own centrality basis. Internal — surfaced onto the
    # compact node dicts by format_compact_behavior_map (WI-zotam); NOT
    # serialized in to_dict.
    centrality: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "included": self.included.to_dict(),
            "omitted": self.omitted.to_dict(),
        }


@dataclass
class ConnectivityResult:
    """Result of connectivity-aware selection.

    Unlike CompactResult, this includes the induced subgraph edges.
    """

    included: IncludedSummary
    omitted: OmittedSummary
    included_edges: List[Edge] = field(default_factory=list)
    # Per-node centrality (id -> score) for the SELECTED symbols, on the
    # connectivity mode's own centrality basis. Internal — surfaced onto the
    # compact node dicts by format_compact_behavior_map (WI-zotam); NOT
    # serialized in to_dict.
    centrality: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "included": self.included.to_dict(),
            "omitted": self.omitted.to_dict(),
            "included_edges_count": len(self.included_edges),
        }


class UnionFind:
    """Disjoint Set Union data structure for tracking connected components.

    Used by connectivity-aware selection to efficiently:
    - Track which nodes are in the same component
    - Compute component sizes
    - Find the largest component
    - Determine if adding a node would merge components

    Uses path compression and union by rank for near-O(1) operations.
    """

    def __init__(self, elements: list[str] | None = None):
        """Initialize Union-Find with optional initial elements.

        Args:
            elements: Initial elements to add. Each starts in its own component.
        """
        self._parent: Dict[str, str] = {}
        self._rank: Dict[str, int] = {}
        self._size: Dict[str, int] = {}
        self._largest_size = 0

        if elements:
            for elem in elements:
                self.add(elem)

    def add(self, elem: str) -> None:
        """Add a new element as its own component.

        Args:
            elem: Element ID to add.
        """
        if elem not in self._parent:
            self._parent[elem] = elem
            self._rank[elem] = 0
            self._size[elem] = 1
            if self._largest_size < 1:
                self._largest_size = 1

    def find(self, elem: str) -> str:
        """Find the root of the component containing elem.

        Uses path compression for efficiency.

        Args:
            elem: Element to find root of.

        Returns:
            Root element ID of the component.
        """
        if self._parent[elem] != elem:
            self._parent[elem] = self.find(self._parent[elem])  # Path compression
        return self._parent[elem]

    def union(self, a: str, b: str) -> bool:
        """Merge the components containing a and b.

        Uses union by rank to keep trees balanced.

        Args:
            a: First element.
            b: Second element.

        Returns:
            True if components were merged, False if already in same component.
        """
        root_a = self.find(a)
        root_b = self.find(b)

        if root_a == root_b:
            return False  # Already in same component

        # Union by rank
        if self._rank[root_a] < self._rank[root_b]:
            root_a, root_b = root_b, root_a

        self._parent[root_b] = root_a
        self._size[root_a] += self._size[root_b]

        if self._rank[root_a] == self._rank[root_b]:
            self._rank[root_a] += 1

        # Update largest component tracking
        if self._size[root_a] > self._largest_size:
            self._largest_size = self._size[root_a]

        return True

    def component_size(self, elem: str) -> int:
        """Get the size of the component containing elem.

        Args:
            elem: Element to check.

        Returns:
            Number of elements in the component.
        """
        return self._size[self.find(elem)]

    def largest_component_size(self) -> int:
        """Get the size of the largest component.

        Returns:
            Size of the largest component, or 0 if empty.
        """
        return self._largest_size

    def connected(self, a: str, b: str) -> bool:  # pragma: no cover
        """Check if two elements are in the same component.

        Args:
            a: First element.
            b: Second element.

        Returns:
            True if a and b are in the same component.
        """
        return self.find(a) == self.find(b)


# Common stop words to filter from symbol name analysis
STOP_WORDS = {
    "a", "an", "the", "of", "to", "in", "for", "on", "with", "at", "by",
    "from", "is", "it", "as", "be", "this", "that", "are", "was", "were",
    "get", "set", "new", "init", "self", "cls", "args", "kwargs",
}

# Minimum word length to consider
MIN_WORD_LENGTH = 3


def tokenize_name(name: str) -> List[str]:
    """Extract words from a symbol name.

    Handles camelCase, snake_case, and PascalCase.
    Filters stop words and short tokens.

    Args:
        name: Symbol name to tokenize.

    Returns:
        List of lowercase word tokens.
    """
    # Split on underscores and non-alphanumeric
    parts = re.split(r'[_\W]+', name)

    # Split camelCase/PascalCase
    tokens = []
    for part in parts:
        # Insert split before uppercase letters (except at start)
        split = re.sub(r'([a-z])([A-Z])', r'\1 \2', part)
        tokens.extend(split.lower().split())

    # Filter stop words and short tokens
    return [
        t for t in tokens
        if len(t) >= MIN_WORD_LENGTH and t not in STOP_WORDS
    ]


def extract_path_pattern(path: str) -> str:
    """Extract a representative pattern from a file path.

    Returns the first directory component, or the file extension pattern.

    Args:
        path: File path to analyze.

    Returns:
        Pattern string like "tests/", "vendor/", or "*.min.js".
    """
    # Check for minified/bundled file patterns first (more specific)
    if ".min." in path:
        return "*.min.*"
    if ".bundle." in path:
        return "*.bundle.*"

    # Split path into parts
    parts = path.replace("\\", "/").split("/")

    # Check for common directory patterns
    common_dirs = {
        "test", "tests", "__tests__", "spec", "specs",
        "vendor", "node_modules", "third_party", "external",
        "dist", "build", "out", "target",
        "generated", "gen", "auto",
    }

    for part in parts:
        if part.lower() in common_dirs:
            return f"{part}/"

    # Return first directory or filename
    if len(parts) > 1:
        return f"{parts[0]}/"
    return parts[0]


def compute_word_frequencies(symbols: List[Symbol]) -> Counter[str]:
    """Compute word frequencies across symbol names.

    Args:
        symbols: List of symbols to analyze.

    Returns:
        Counter of word frequencies.
    """
    counter: Counter[str] = Counter()
    for sym in symbols:
        tokens = tokenize_name(sym.name)
        counter.update(tokens)
    return counter


def compute_path_frequencies(symbols: List[Symbol]) -> Counter[str]:
    """Compute path pattern frequencies.

    Args:
        symbols: List of symbols to analyze.

    Returns:
        Counter of path pattern frequencies.
    """
    counter: Counter[str] = Counter()
    for sym in symbols:
        pattern = extract_path_pattern(sym.path)
        counter[pattern] += 1
    return counter


def compute_kind_distribution(symbols: List[Symbol]) -> Dict[str, int]:
    """Compute distribution of symbol kinds.

    Args:
        symbols: List of symbols to analyze.

    Returns:
        Dictionary mapping kind to count.
    """
    counter: Counter[str] = Counter()
    for sym in symbols:
        counter[sym.kind] += 1
    return dict(counter)


def compute_tier_distribution(symbols: List[Symbol]) -> Dict[int, int]:
    """Compute distribution of supply chain tiers.

    Args:
        symbols: List of symbols to analyze.

    Returns:
        Dictionary mapping tier to count.
    """
    # Keyed by `supply_chain_tier`, an int — NOT str like its sibling
    # distribution helpers above. The declared return type says so.
    counter: Counter[int] = Counter()
    for sym in symbols:
        tier = getattr(sym, 'supply_chain_tier', 1)
        counter[tier] += 1
    return dict(counter)


def _build_adjacency_list(
    edges: List[Edge],
) -> Tuple[Dict[str, set[str]], Dict[str, set[str]]]:
    """Build bidirectional adjacency lists from edges.

    Args:
        edges: List of edges.

    Returns:
        Tuple of (outgoing adjacency, incoming adjacency).
        outgoing[src] = set of dst nodes
        incoming[dst] = set of src nodes
    """
    outgoing: Dict[str, set[str]] = {}
    incoming: Dict[str, set[str]] = {}

    for edge in edges:
        # Skip self-loops — they don't represent useful connectivity
        # and inflate centrality scores. Common source: visitor patterns,
        # name collision in multi-file repos (e.g., D's accept() methods).
        if edge.src == edge.dst:
            continue

        if edge.src not in outgoing:
            outgoing[edge.src] = set()
        outgoing[edge.src].add(edge.dst)

        if edge.dst not in incoming:
            incoming[edge.dst] = set()
        incoming[edge.dst].add(edge.src)

    return outgoing, incoming


def _compute_connectivity_score(
    node_id: str,
    selected_ids: set[str],
    uf: UnionFind,
    outgoing: Dict[str, set[str]],
    incoming: Dict[str, set[str]],
    centrality: Dict[str, float],
) -> Tuple[int, int, float]:
    """Compute score for adding a node to the selected set.

    Score prioritizes:
    1. Largest component growth (bridges disconnected components)
    2. Edge count added (densifies the graph)
    3. Centrality (importance fallback)

    Args:
        node_id: Node to score.
        selected_ids: Currently selected node IDs.
        uf: Union-Find tracking components of selected nodes.
        outgoing: Outgoing adjacency list.
        incoming: Incoming adjacency list.
        centrality: Centrality scores.

    Returns:
        Tuple of (component_growth, edges_added, centrality) for sorting.
        Higher is better for all three.
    """
    # Find neighbors in the selected set
    neighbors_in_selected = set()
    for dst in outgoing.get(node_id, set()):
        if dst in selected_ids:
            neighbors_in_selected.add(dst)
    for src in incoming.get(node_id, set()):
        if src in selected_ids:
            neighbors_in_selected.add(src)

    edges_added = len(neighbors_in_selected)

    if edges_added == 0:  # pragma: no cover
        # No connection to selected set - would be isolated
        # (Defensive: frontier nodes are by definition adjacent to selected set)
        return (0, 0, centrality.get(node_id, 0))

    # Compute component growth if we add this node
    # Find which components the neighbors belong to
    component_roots = set()
    for neighbor in neighbors_in_selected:
        component_roots.add(uf.find(neighbor))

    if len(component_roots) == 1:
        # All neighbors in same component - just adds 1 to that component
        component_growth = 1
    else:
        # Bridges multiple components - compute merged size
        total_size = 1  # The new node itself
        for root in component_roots:
            total_size += uf._size[root]
        # Growth is the new largest vs current largest
        current_largest = uf.largest_component_size()
        new_largest = max(current_largest, total_size)
        component_growth = new_largest - current_largest + 1  # +1 for adding node

    return (component_growth, edges_added, centrality.get(node_id, 0))


def select_by_connectivity(
    symbols: List[Symbol],
    edges: List[Edge],
    seed_ids: set[str],
    max_additional: int,
    centrality: Dict[str, float] | None = None,
) -> ConnectivityResult:
    """Select symbols to maximize connectivity of the induced subgraph.

    Uses a greedy frontier-based algorithm:
    1. Start with seed nodes (e.g., entrypoints)
    2. Build frontier of nodes adjacent to selected set
    3. Score each frontier node by:
       - Primary: component growth (bridges isolated seeds)
       - Secondary: edges added (densifies graph)
       - Tertiary: centrality (importance fallback)
    4. Add best node, update frontier, repeat until budget exhausted

    This produces connected output even when seeds are disconnected,
    by preferring "bridge" nodes that unify components.

    Args:
        symbols: All symbols to consider.
        edges: All edges for building adjacency.
        seed_ids: Initial nodes to include (e.g., entrypoint IDs).
        max_additional: Maximum additional nodes to add beyond seeds.
        centrality: Optional pre-computed centrality. If None, computes it.

    Returns:
        ConnectivityResult with selected symbols and induced edges.
    """
    symbol_by_id = {s.id: s for s in symbols}

    # Build adjacency lists
    outgoing, incoming = _build_adjacency_list(edges)

    # Compute centrality if not provided. WI-tahum: shared helper applies
    # rank_symbols' tuned compute_centrality params plus the canonical
    # 8-stage dampener stack (tier → noise → utility → common-method →
    # sibling-impl → trivial-sink → generated → file-kind). When the
    # caller supplies centrality, it is trusted as-is.
    if centrality is None:
        centrality = compute_dampened_centrality(symbols, edges)

    # Initialize selected set with seeds
    selected_ids: set[str] = set()
    selected_symbols: List[Symbol] = []

    # WI-nivuj: iterate seeds in sorted order so the seed prefix of the output
    # node list is reproducible (seed_ids is a set — its iteration order is
    # PYTHONHASHSEED-dependent).
    for sid in sorted(seed_ids):
        if sid in symbol_by_id:
            selected_ids.add(sid)
            selected_symbols.append(symbol_by_id[sid])

    # Handle empty seed case: start with highest-centrality node
    if not selected_ids and symbols:
        best_sym = max(symbols, key=lambda s: centrality.get(s.id, 0))
        selected_ids.add(best_sym.id)
        selected_symbols.append(best_sym)
        max_additional -= 1

    # Initialize Union-Find with selected nodes
    uf = UnionFind(list(selected_ids))

    # Connect seeds that share edges
    for sid in selected_ids:
        for dst in outgoing.get(sid, set()):
            if dst in selected_ids:
                uf.union(sid, dst)
        for src in incoming.get(sid, set()):
            if src in selected_ids:
                uf.union(sid, src)

    # Build initial frontier: nodes adjacent to selected set
    frontier: set[str] = set()
    for sid in selected_ids:
        for dst in outgoing.get(sid, set()):
            if dst not in selected_ids and dst in symbol_by_id:
                frontier.add(dst)
        for src in incoming.get(sid, set()):
            if src not in selected_ids and src in symbol_by_id:
                frontier.add(src)

    # Greedy selection loop
    added = 0
    while added < max_additional and frontier:
        # Score all frontier nodes
        best_node = None
        best_score = (-1, -1, -1.0)

        # WI-nivuj: iterate the frontier in sorted order so a SCORE TIE resolves
        # to the lexicographically-smallest node deterministically (frontier is a
        # set; without sorting the winner depends on PYTHONHASHSEED).
        for node_id in sorted(frontier):
            score = _compute_connectivity_score(
                node_id, selected_ids, uf, outgoing, incoming, centrality
            )
            if score > best_score:
                best_score = score
                best_node = node_id

        if best_node is None:  # pragma: no cover
            # Defensive: frontier should always have scoreable nodes
            break

        # Add best node
        selected_ids.add(best_node)
        selected_symbols.append(symbol_by_id[best_node])
        uf.add(best_node)

        # Connect to existing components
        for dst in outgoing.get(best_node, set()):
            if dst in selected_ids:
                uf.union(best_node, dst)
        for src in incoming.get(best_node, set()):
            if src in selected_ids:
                uf.union(best_node, src)

        # Update frontier
        frontier.discard(best_node)
        for dst in outgoing.get(best_node, set()):
            if dst not in selected_ids and dst in symbol_by_id:
                frontier.add(dst)
        for src in incoming.get(best_node, set()):
            if src not in selected_ids and src in symbol_by_id:
                frontier.add(src)

        added += 1

    # Compute induced subgraph edges. Iterate the edge LIST (not a
    # (src, dst)-keyed dict) so PARALLEL edges between the same node pair are
    # all retained — a (src, dst) dict collapses them, dropping every parallel
    # but the last (WI-hakom induced-subgraph leak; the coverage and tiered
    # branches already iterate the list directly). Self-loops (src == dst) waste
    # budget without adding connectivity, so they stay excluded, matching those
    # branches.
    included_edges: List[Edge] = [
        e for e in edges
        if e.src != e.dst and e.src in selected_ids and e.dst in selected_ids
    ]

    # Compute centrality sums
    included_centrality = sum(centrality.get(s.id, 0) for s in selected_symbols)
    total_centrality = sum(centrality.values()) or 1.0

    # Build omitted summary
    omitted_symbols = [s for s in symbols if s.id not in selected_ids]
    omitted_centrality = sum(centrality.get(s.id, 0) for s in omitted_symbols)
    max_omitted_centrality = max(
        (centrality.get(s.id, 0) for s in omitted_symbols), default=0.0
    )

    # Bag-of-words analysis on omitted symbols
    word_freq = compute_word_frequencies(omitted_symbols)
    path_freq = compute_path_frequencies(omitted_symbols)
    kind_dist = compute_kind_distribution(omitted_symbols)
    tier_dist = compute_tier_distribution(omitted_symbols)

    return ConnectivityResult(
        included=IncludedSummary(
            count=len(selected_symbols),
            centrality_sum=included_centrality,
            coverage=included_centrality / total_centrality,
            symbols=selected_symbols,
        ),
        omitted=OmittedSummary(
            count=len(omitted_symbols),
            centrality_sum=omitted_centrality,
            max_centrality=max_omitted_centrality,
            top_words=word_freq.most_common(10),
            top_paths=path_freq.most_common(5),
            kinds=kind_dist,
            tiers=tier_dist,
        ),
        included_edges=included_edges,
        centrality=centrality,
    )


def select_by_coverage(
    symbols: List[Symbol],
    edges: List[Edge],
    config: CompactConfig,
    force_include_ids: set[str] | None = None,
) -> CompactResult:
    """Select symbols by centrality coverage with residual summarization.

    Selects the fewest symbols needed to capture target_coverage of total
    centrality mass, respecting min/max bounds. Summarizes omitted symbols
    with bag-of-words analysis for semantic flavor.

    Args:
        symbols: All symbols to consider.
        edges: Edges for centrality computation.
        config: Compact configuration.
        force_include_ids: Optional set of symbol IDs that must be included
            (e.g., entrypoint symbol_ids). These are included first, then
            remaining budget is filled with highest-centrality symbols.

    Returns:
        CompactResult with included symbols and omitted summary.
    """
    if force_include_ids is None:
        force_include_ids = set()

    if not symbols:
        return CompactResult(
            included=IncludedSummary(
                count=0, centrality_sum=0.0, coverage=1.0, symbols=[]
            ),
            omitted=OmittedSummary(
                count=0, centrality_sum=0.0, max_centrality=0.0,
                top_words=[], top_paths=[], kinds={}, tiers={}
            ),
            config=config,
            centrality={},
        )

    # WI-tahum: shared helper applies rank_symbols' tuned
    # compute_centrality params plus the canonical 8-stage dampener
    # stack (tier → noise → utility → common-method → sibling-impl →
    # trivial-sink → generated → file-kind).
    centrality = compute_dampened_centrality(
        symbols, edges,
        first_party_priority=config.first_party_priority,
    )

    # Compute total centrality
    total_centrality = sum(centrality.values())
    if total_centrality == 0:
        total_centrality = 1.0  # Avoid division by zero

    # Select symbols using appropriate strategy
    if config.language_proportional:
        # Language-proportional selection: allocate budget by language
        lang_groups = group_symbols_by_language(symbols)
        budgets = allocate_language_budget(
            lang_groups, config.max_symbols, config.min_per_language
        )

        # Select top symbols from each language
        candidates: List[Symbol] = []
        for lang, budget in budgets.items():
            lang_symbols = lang_groups.get(lang, [])
            # Sort by centrality within language
            sorted_lang = sorted(
                lang_symbols,
                key=lambda s: (-centrality.get(s.id, 0), s.name)
            )
            candidates.extend(sorted_lang[:budget])

        # Sort combined candidates by centrality
        sorted_symbols = sorted(
            candidates,
            key=lambda s: (-centrality.get(s.id, 0), s.name)
        )
    else:
        # Original behavior: sort all symbols by centrality
        sorted_symbols = sorted(
            symbols,
            key=lambda s: (-centrality.get(s.id, 0), s.name)
        )

    # Select by coverage from the (possibly pre-filtered) candidates
    included: List[Symbol] = []
    included_centrality = 0.0
    included_ids: set[str] = set()

    # First, force-include any must-include symbols (e.g., entrypoints)
    # These are semantically important and should always be included
    if force_include_ids:
        symbol_by_id = {s.id: s for s in symbols}
        for sid in sorted(force_include_ids):  # WI-nivuj: reproducible order
            if sid in symbol_by_id and sid not in included_ids:
                sym = symbol_by_id[sid]
                included.append(sym)
                included_centrality += centrality.get(sym.id, 0)
                included_ids.add(sid)

    # Then fill remaining budget with highest-centrality symbols
    for sym in sorted_symbols:
        # Skip if already included (force-included)
        if sym.id in included_ids:
            continue

        # Check if we've met all stopping conditions
        coverage = included_centrality / total_centrality
        at_min = len(included) >= config.min_symbols
        at_coverage = coverage >= config.target_coverage
        at_max = len(included) >= config.max_symbols

        if at_max:
            break
        if at_min and at_coverage:
            break

        included.append(sym)
        included_centrality += centrality.get(sym.id, 0)
        included_ids.add(sym.id)

    # Compute omitted symbols (included_ids already built above)
    omitted = [s for s in symbols if s.id not in included_ids]

    # Compute summaries
    omitted_centrality = sum(centrality.get(s.id, 0) for s in omitted)
    max_omitted = max((centrality.get(s.id, 0) for s in omitted), default=0.0)

    # Bag-of-words analysis on omitted symbols
    word_freq = compute_word_frequencies(omitted)
    path_freq = compute_path_frequencies(omitted)
    kind_dist = compute_kind_distribution(omitted)
    tier_dist = compute_tier_distribution(omitted)

    return CompactResult(
        included=IncludedSummary(
            count=len(included),
            centrality_sum=included_centrality,
            coverage=included_centrality / total_centrality,
            symbols=included,
        ),
        omitted=OmittedSummary(
            count=len(omitted),
            centrality_sum=omitted_centrality,
            max_centrality=max_omitted,
            top_words=word_freq.most_common(config.top_words_count),
            top_paths=path_freq.most_common(config.top_paths_count),
            kinds=kind_dist,
            tiers=tier_dist,
        ),
        config=config,
        centrality=centrality,
    )


def _reproject_features(
    features: List[dict[str, Any]],
    included_node_ids: set[str],
    included_edge_ids: set[str],
) -> List[dict[str, Any]]:
    """Re-project feature slices onto the compacted node/edge set (INV-titid).

    The compact pass selects a budget-limited subset of nodes and edges, but
    the source map's ``features[]`` carry full-graph ``node_ids``/``edge_ids``
    references. Left unchanged, the great majority of those references dangle
    in the compact output -- the feature claims to describe a slice of the
    compact graph yet points at pruned content. This rewrites each surviving
    feature's references to the retained sets and drops any feature whose
    every entry node was pruned, mirroring the entrypoint-filtering precedent
    (an entrypoint whose symbol was pruned is dropped; so is a feature whose
    anchor was pruned). The result is the twin of INV-tarol's slice fix:
    feature scope is re-derived from the emitted graph rather than copied
    wholesale. ``admission_stats`` (not node-keyed) passes through unchanged.
    """
    reprojected: List[dict[str, Any]] = []
    for feat in features:
        entry_nodes = [
            n for n in feat.get("entry_nodes", []) if n in included_node_ids
        ]
        # A feature whose every entry node was pruned no longer describes
        # anything in the compact graph -- drop it (parallel to entrypoints).
        if not entry_nodes:
            continue
        new_feat = dict(feat)
        new_feat["entry_nodes"] = entry_nodes
        new_feat["node_ids"] = [
            n for n in feat.get("node_ids", []) if n in included_node_ids
        ]
        new_feat["edge_ids"] = [
            e for e in feat.get("edge_ids", []) if e in included_edge_ids
        ]
        if "node_depths" in feat:
            new_feat["node_depths"] = {
                k: v for k, v in feat["node_depths"].items()
                if k in included_node_ids
            }
        if "node_tiers" in feat:
            new_feat["node_tiers"] = {
                k: v for k, v in feat["node_tiers"].items()
                if k in included_node_ids
            }
        reprojected.append(new_feat)
    return reprojected


def _annotate_node_centrality(
    nodes: list[dict[str, Any]], centrality: Dict[str, float],
) -> None:
    """Stamp each projected node dict with its centrality score (WI-zotam).

    A budget-limited projection previously emitted no per-node centrality, so a
    consumer could not rank the retained nodes or cross-check them against the
    summary's aggregate. Each node now carries the selection mode's own
    centrality (rounded), so ``nodes`` and ``nodes_summary`` agree.
    """
    for n in nodes:
        # `n.get("id")` alone is `Any | None`, and a None key can never be in
        # `centrality` — so an id-less node silently scored 0.0 rather than
        # being distinguishable from a node genuinely at 0.0. Defaulting to ""
        # keeps that behaviour identical while making the lookup key well-typed;
        # the ambiguity itself is a projection concern, not one this helper can
        # resolve.
        n["centrality"] = round(centrality.get(n.get("id", ""), 0.0), 4)


def _array_projection_summary(original_len: int, emitted_len: int) -> dict[str, Any]:
    """Included/omitted counts for a top-level array truncated by the compact
    projection — the entrypoints/features analogue of ``nodes_summary``
    (WI-kulan). The compact view filters entrypoints to retained nodes and drops
    features whose anchors were all pruned, so without a companion summary a
    consumer cannot tell how much of each array was omitted.
    """
    return {
        "included": {"count": emitted_len},
        "omitted": {"count": max(0, original_len - emitted_len)},
    }


def format_compact_behavior_map(
    behavior_map: dict[str, Any],
    symbols: List[Symbol],
    edges: List[Edge],
    config: CompactConfig,
    force_include_entrypoints: bool = True,
    connectivity_aware: bool = False,
) -> dict[str, Any]:
    """Format a behavior map in compact mode.

    Replaces the full nodes list with a compact selection plus summary.

    Args:
        behavior_map: Original behavior map dictionary.
        symbols: Symbol objects (for analysis).
        edges: Edge objects (for centrality).
        config: Compact configuration.
        force_include_entrypoints: If True, always include entrypoint symbols
            in the selection. This ensures semantic anchors are preserved.
            Default True.
        connectivity_aware: If True, use connectivity-aware selection that
            prioritizes nodes which bridge disconnected components. This
            produces connected subgraphs even when entrypoints don't directly
            call each other. Default False.

    Returns:
        Modified behavior map with compact output.
    """
    # Extract entrypoint symbol_ids to force-include them
    force_include_ids: set[str] = set()
    if force_include_entrypoints:
        symbol_ids = {s.id for s in symbols}
        entrypoints_with_ids = []
        for ep in behavior_map.get("entrypoints", []):
            sid = ep.get("symbol_id")
            if sid and sid in symbol_ids:
                entrypoints_with_ids.append(ep)

        # Cap entrypoints to leave room for bridge nodes in connectivity mode.
        # Without this cap, repos with many entrypoints (e.g., keycloak with
        # 500 JAX-RS handlers) consume most of the node budget, leaving
        # insufficient room for frontier expansion.  This causes fragmentation:
        # keycloak had 30 components and 19 singletons in 100-node compact.
        #
        # Use adaptive cap: when entrypoints exceed max_symbols (indicating a
        # large repo with many entry points), cap aggressively (1/3) to leave
        # room for bridging.  Otherwise use the gentler 1/2 cap.
        if len(entrypoints_with_ids) > config.max_symbols:
            max_forced = max(1, config.max_symbols // 3)
        else:
            max_forced = max(1, config.max_symbols // 2)
        if len(entrypoints_with_ids) > max_forced:
            # Sort by confidence (descending) and take top entries
            sorted_eps = sorted(
                entrypoints_with_ids,
                key=lambda ep: (-ep.get("confidence", 0), ep.get("symbol_id", ""))
            )
            entrypoints_with_ids = sorted_eps[:max_forced]

        force_include_ids = {ep.get("symbol_id") for ep in entrypoints_with_ids}

    # Seed cross-cutting edge endpoints so linker-produced edges survive
    # the induced-subgraph filter.  Without this, centrality-based selection
    # drops peripheral nodes (route definitions, dispatch targets, FFI endpoints)
    # that are endpoints of these high-value edges.
    symbol_id_set = {s.id for s in symbols}
    cross_cutting_ids: set[str] = set()
    for e in behavior_map.get("edges", []):
        # Edge dicts use "type" key (from Edge.to_dict()), not "edge_type"
        if e.get("type") in CROSS_CUTTING_EDGE_TYPES:
            src, dst = e.get("src"), e.get("dst")
            if src in symbol_id_set:
                cross_cutting_ids.add(src)
            if dst in symbol_id_set:
                cross_cutting_ids.add(dst)

    # Cap cross-cutting seeds to avoid dominating the budget.  The combined
    # total of entrypoints + cross-cutting seeds must leave at least half of
    # max_symbols for frontier expansion.
    remaining_seed_budget = max(0, config.max_symbols // 2 - len(force_include_ids))
    if len(cross_cutting_ids) > remaining_seed_budget:
        # Prefer endpoints with higher edge count (more cross-cutting connections)
        cc_edge_count: Counter[str] = Counter()
        for e in behavior_map.get("edges", []):
            if e.get("type") in CROSS_CUTTING_EDGE_TYPES:
                src, dst = e.get("src"), e.get("dst")
                if src in cross_cutting_ids:
                    cc_edge_count[src] += 1
                if dst in cross_cutting_ids:
                    cc_edge_count[dst] += 1
        # Sort by edge count descending, then alphabetically for stability
        ranked = sorted(cross_cutting_ids, key=lambda x: (-cc_edge_count[x], x))
        cross_cutting_ids = set(ranked[:remaining_seed_budget])

    force_include_ids |= cross_cutting_ids

    if connectivity_aware:
        # Use connectivity-aware selection
        # Budget is remaining slots after entrypoints
        max_additional = max(0, config.max_symbols - len(force_include_ids))
        conn_result = select_by_connectivity(
            symbols, edges, force_include_ids, max_additional
        )

        # Create compact output
        compact_map = {
            k: v for k, v in behavior_map.items()
            if k not in _COMPACT_STRIP_KEYS
        }
        compact_map["view"] = "compact"
        compact_map["nodes"] = [compact_node(s) for s in conn_result.included.symbols]
        _annotate_node_centrality(compact_map["nodes"], conn_result.centrality)
        compact_map["nodes_summary"] = conn_result.to_dict()

        # Use the induced edges from connectivity selection
        compact_map["edges"] = [e.to_dict() for e in conn_result.included_edges]

        # Filter entrypoints to only those whose symbol_id exists in included nodes
        included_ids = {s.id for s in conn_result.included.symbols}
        all_entrypoints = behavior_map.get("entrypoints", [])
        compact_map["entrypoints"] = [
            ep for ep in all_entrypoints
            if ep.get("symbol_id") in included_ids
        ]
        compact_map["entrypoints_summary"] = _array_projection_summary(
            len(all_entrypoints), len(compact_map["entrypoints"])
        )

        # Re-project features onto the compacted graph (INV-titid).
        included_edge_ids = {e.get("id") for e in compact_map["edges"]}
        all_features = behavior_map.get("features", [])
        compact_map["features"] = _reproject_features(
            all_features, included_ids, included_edge_ids
        )
        compact_map["features_summary"] = _array_projection_summary(
            len(all_features), len(compact_map["features"])
        )
    else:
        # Use original coverage-based selection
        result = select_by_coverage(symbols, edges, config, force_include_ids)

        # Create compact output
        compact_map = {
            k: v for k, v in behavior_map.items()
            if k not in _COMPACT_STRIP_KEYS
        }
        compact_map["view"] = "compact"
        compact_map["nodes"] = [compact_node(s) for s in result.included.symbols]
        _annotate_node_centrality(compact_map["nodes"], result.centrality)
        compact_map["nodes_summary"] = result.to_dict()

        # Keep only edges where BOTH endpoints exist in the included set and
        # src != dst (self-loops waste budget without useful connectivity).
        included_ids = {s.id for s in result.included.symbols}
        compact_map["edges"] = [
            e for e in behavior_map.get("edges", [])
            if e.get("src") in included_ids and e.get("dst") in included_ids
            and e.get("src") != e.get("dst")
        ]
        # Symmetry with the connectivity branch (WI-zotam): the coverage-shaped
        # summary (CompactResult.to_dict) omits included_edges_count, so add it
        # here from the emitted edges — both compact modes now report it.
        compact_map["nodes_summary"]["included_edges_count"] = len(
            compact_map["edges"]
        )

        # Filter entrypoints to only those whose symbol_id exists in included nodes
        all_entrypoints = behavior_map.get("entrypoints", [])
        compact_map["entrypoints"] = [
            ep for ep in all_entrypoints
            if ep.get("symbol_id") in included_ids
        ]
        compact_map["entrypoints_summary"] = _array_projection_summary(
            len(all_entrypoints), len(compact_map["entrypoints"])
        )

        # Re-project features onto the compacted graph (INV-titid).
        included_edge_ids = {e.get("id") for e in compact_map["edges"]}
        all_features = behavior_map.get("features", [])
        compact_map["features"] = _reproject_features(
            all_features, included_ids, included_edge_ids
        )
        compact_map["features_summary"] = _array_projection_summary(
            len(all_features), len(compact_map["features"])
        )

    _recompute_view_metrics(compact_map)
    return compact_map


# Backwards compatibility aliases for functions that were moved
def estimate_node_tokens(node_dict: dict[str, Any]) -> int:
    """Estimate tokens for a serialized node. Alias for estimate_json_tokens."""
    return estimate_json_tokens(node_dict)


def estimate_behavior_map_tokens(behavior_map: dict[str, Any]) -> int:
    """Estimate total tokens for a behavior map. Alias for estimate_json_tokens."""
    return estimate_json_tokens(behavior_map)


def select_by_tokens(
    symbols: List[Symbol],
    edges: List[Edge],
    target_tokens: int,
    first_party_priority: bool = True,
    exclude_tests: bool = True,
    exclude_non_code: bool = True,
    deduplicate_names: bool = True,
    exclude_examples: bool = True,
    language_proportional: bool = True,
    min_per_language: int = 1,
    force_include_ids: set[str] | None = None,
) -> CompactResult:
    """Select symbols to fit within a token budget.

    Uses centrality ranking to select the most important symbols that
    fit within the target token count.

    Args:
        symbols: All symbols to consider.
        edges: Edges for centrality computation.
        target_tokens: Target token budget.
        first_party_priority: Apply tier weighting. Default True.
        exclude_tests: Exclude symbols from test files. Default True.
        exclude_non_code: Exclude non-code kinds (deps, files). Default True.
        deduplicate_names: Skip symbols with already-included names. Default True.
            Prevents "push" appearing 4 times from different files.
        exclude_examples: Exclude symbols from example directories. Default True.
            Prevents example handlers from polluting tiers.
        language_proportional: Use language-stratified selection. Default True.
            When enabled, selects symbols proportionally by language to ensure
            multi-language projects have representation from each.
        min_per_language: Minimum symbols per language (floor guarantee).
            Only used when language_proportional=True. Default 1.
        force_include_ids: Optional set of symbol IDs that must be included
            (e.g., entrypoint symbol_ids). These are included first, then
            remaining budget is filled with highest-centrality symbols.

    Returns:
        CompactResult with symbols fitting the budget.
    """
    if force_include_ids is None:
        force_include_ids = set()
    if not symbols:
        return CompactResult(
            included=IncludedSummary(
                count=0, centrality_sum=0.0, coverage=1.0, symbols=[]
            ),
            omitted=OmittedSummary(
                count=0, centrality_sum=0.0, max_centrality=0.0,
                top_words=[], top_paths=[], kinds={}, tiers={}
            ),
        )

    # Filter symbols for tiered output quality
    # These are excluded from selection but still count toward "omitted"
    eligible_symbols = symbols
    if exclude_non_code:
        # WI-jukav slice 2: dual-shape predicate forward-compat with
        # ADR-0027 §"Phase 3" Wave 5 framework_role fold (post-fold
        # synthetic ``method``/``function`` symbols carry the legacy
        # role on ``meta["framework_role"]``).
        eligible_symbols = [
            s for s in eligible_symbols
            if not is_excluded_kind(s.kind, s.meta)
        ]
    if exclude_tests:
        eligible_symbols = [
            s for s in eligible_symbols
            if not _is_test_node(s.path, s.meta)
        ]
    if exclude_examples:
        eligible_symbols = [s for s in eligible_symbols if not _is_example_path(s.path)]

    # Compute centrality on ALL symbols (for accurate coverage)
    raw_centrality = compute_centrality(symbols, edges)

    if first_party_priority:
        centrality = apply_tier_weights(raw_centrality, symbols)
    else:
        centrality = raw_centrality

    # Compute total centrality for coverage calculation
    total_centrality = sum(centrality.values())
    if total_centrality == 0:
        total_centrality = 1.0

    # Apply language-proportional pre-selection if enabled
    if language_proportional:
        # Group eligible symbols by language
        lang_groups = group_symbols_by_language(eligible_symbols)
        # Estimate max symbols that could fit (rough estimate for budget allocation)
        avg_tokens_per_symbol = 50  # Conservative estimate
        estimated_max_symbols = (target_tokens - TOKENS_BEHAVIOR_MAP_OVERHEAD) // avg_tokens_per_symbol
        budgets = allocate_language_budget(
            lang_groups, max(estimated_max_symbols, 10), min_per_language
        )

        # Select top symbols from each language
        candidates: List[Symbol] = []
        for lang, budget in budgets.items():
            lang_symbols = lang_groups.get(lang, [])
            sorted_lang = sorted(
                lang_symbols,
                key=lambda s: (-centrality.get(s.id, 0), s.name)
            )
            candidates.extend(sorted_lang[:budget])

        # Sort combined candidates by centrality
        sorted_symbols = sorted(
            candidates,
            key=lambda s: (-centrality.get(s.id, 0), s.name)
        )
    else:
        # Original behavior: sort all eligible symbols by centrality
        sorted_symbols = sorted(
            eligible_symbols,
            key=lambda s: (-centrality.get(s.id, 0), s.name)
        )

    # Select symbols until we approach the token budget
    # Reserve tokens for overhead and summary
    available_tokens = target_tokens - TOKENS_BEHAVIOR_MAP_OVERHEAD - 200  # summary

    included: List[Symbol] = []
    included_centrality = 0.0
    tokens_used = 0
    seen_names: set[str] = set()  # For deduplication
    included_ids: set[str] = set()

    # First, force-include any must-include symbols (e.g., entrypoints)
    # These are semantically important but still subject to the token budget.
    # When there are more entrypoints than the budget allows (e.g., FastAPI
    # with ~1400 routes), we cap them to fit.
    if force_include_ids:
        symbol_by_id = {s.id: s for s in symbols}
        # Sort forced symbols by centrality so the most important ones
        # are included first when the budget is tight
        forced_syms = [
            symbol_by_id[sid]
            for sid in force_include_ids
            if sid in symbol_by_id
        ]
        forced_syms.sort(
            key=lambda s: (-centrality.get(s.id, 0), s.name)
        )
        for sym in forced_syms:
            node_dict = sym.to_dict()
            node_tokens = estimate_node_tokens(node_dict)
            if tokens_used + node_tokens > available_tokens:
                break
            included.append(sym)
            included_centrality += centrality.get(sym.id, 0)
            tokens_used += node_tokens
            seen_names.add(sym.name)
            included_ids.add(sym.id)

    # Then fill remaining budget with highest-centrality symbols
    for sym in sorted_symbols:
        # Skip if already included (force-included)
        if sym.id in included_ids:
            continue

        # Skip duplicate names if deduplication is enabled
        if deduplicate_names and sym.name in seen_names:
            continue

        node_dict = sym.to_dict()
        node_tokens = estimate_node_tokens(node_dict)

        if tokens_used + node_tokens > available_tokens:
            break

        included.append(sym)
        included_centrality += centrality.get(sym.id, 0)
        tokens_used += node_tokens
        seen_names.add(sym.name)
        included_ids.add(sym.id)

    # Compute omitted symbols (included_ids already built above)
    omitted = [s for s in symbols if s.id not in included_ids]

    # Compute summaries
    omitted_centrality = sum(centrality.get(s.id, 0) for s in omitted)
    max_omitted = max((centrality.get(s.id, 0) for s in omitted), default=0.0)

    # Bag-of-words analysis on omitted symbols
    word_freq = compute_word_frequencies(omitted)
    path_freq = compute_path_frequencies(omitted)
    kind_dist = compute_kind_distribution(omitted)
    tier_dist = compute_tier_distribution(omitted)

    return CompactResult(
        included=IncludedSummary(
            count=len(included),
            centrality_sum=included_centrality,
            coverage=included_centrality / total_centrality,
            symbols=included,
        ),
        omitted=OmittedSummary(
            count=len(omitted),
            centrality_sum=omitted_centrality,
            max_centrality=max_omitted,
            top_words=word_freq.most_common(10),
            top_paths=path_freq.most_common(5),
            kinds=kind_dist,
            tiers=tier_dist,
        ),
    )


def recompute_view_summary(
    view_map: dict[str, Any],
    population: List[Symbol],
    centrality: Dict[str, float],
    *,
    emit_edge_count: bool,
) -> None:
    """Re-derive ``view_map["nodes_summary"]`` from the FINAL on-disk arrays (INV-pazur).

    The tiered projection assembles ``nodes_summary`` from the pre-shrink connectivity
    selection, then a post-selection shrink loop prunes ``view_map["nodes"]`` /
    ``["edges"]`` to fit the token budget — but never re-derives the summary, so its
    ``included.count`` / ``included_edges_count`` and the whole ``omitted`` distribution
    drift from the arrays actually written to disk (INV-pazur). This helper recomputes the
    summary block as a pure function of the *authoritative* post-shrink arrays
    (``len(view_map["nodes"])`` / ``len(view_map["edges"])``) plus the selection-time
    centrality, so the counts can never again disagree with the arrays — by construction.

    It re-derives the summary ONLY; it does not touch ``view_map["nodes"]`` / ``["edges"]``
    / ``["entrypoints"]`` (the shrink loop already produced the correct sets), so the
    emitted node/edge/entrypoint membership is provably unchanged — only the summary moves.

    The omitted universe (``population``) and ``centrality`` are caller-supplied rather than
    hardcoded so the tiered caller can pass its ``eligible_symbols`` (tests/examples/
    boundary nodes pre-filtered) without baking that filter into the helper — a future
    compact caller (WI-zotam) would pass its own universe. Iteration is over the
    ``population`` list, so the bag-of-words tie-order (``Counter.most_common``) and the
    whole summary are independent of ``PYTHONHASHSEED``. (The *selection* upstream is still
    seed-dependent on score ties — that is WI-nivuj, deliberately out of scope here.)

    ``emit_edge_count`` gates the ``included_edges_count`` key, which belongs only to the
    connectivity-shaped summary (``ConnectivityResult.to_dict``); the coverage-shaped
    summary (``CompactResult.to_dict``) omits it.
    """
    final_ids = {n["id"] for n in view_map["nodes"]}
    total_centrality = sum(centrality.values()) or 1.0
    selected = [s for s in population if s.id in final_ids]
    omitted = [s for s in population if s.id not in final_ids]
    included_centrality = sum(centrality.get(s.id, 0.0) for s in selected)

    included = IncludedSummary(
        # Read the count straight off the on-disk array so INV-pazur holds by construction.
        count=len(view_map["nodes"]),
        centrality_sum=included_centrality,
        coverage=included_centrality / total_centrality,
        symbols=selected,
    )
    omitted_summary = OmittedSummary(
        count=len(omitted),
        centrality_sum=sum(centrality.get(s.id, 0.0) for s in omitted),
        max_centrality=max(
            (centrality.get(s.id, 0.0) for s in omitted), default=0.0
        ),
        top_words=compute_word_frequencies(omitted).most_common(10),
        top_paths=compute_path_frequencies(omitted).most_common(5),
        kinds=compute_kind_distribution(omitted),
        tiers=compute_tier_distribution(omitted),
    )

    # Heterogeneous by construction: two nested summaries plus an optional
    # int edge count added below.
    summary: dict[str, Any] = {
        "included": included.to_dict(), "omitted": omitted_summary.to_dict(),
    }
    if emit_edge_count:
        summary["included_edges_count"] = len(view_map["edges"])
    view_map["nodes_summary"] = summary


def format_tiered_behavior_map(
    behavior_map: dict[str, Any],
    symbols: List[Symbol],
    edges: List[Edge],
    target_tokens: int,
    force_include_entrypoints: bool = True,
) -> dict[str, Any]:
    """Format a behavior map for a specific token tier.

    Args:
        behavior_map: Original full behavior map.
        symbols: Symbol objects.
        edges: Edge objects.
        target_tokens: Target token budget.
        force_include_entrypoints: If True, always include entrypoint symbols
            in the selection. This ensures semantic anchors are preserved.
            Default True.

    Returns:
        Behavior map formatted for the token tier.
    """
    # Extract entrypoint symbol_ids to force-include them.
    # Only force-include high-confidence entrypoints (>= 0.7) to prevent
    # test main() functions from crowding out bridge nodes that provide
    # edges.  Test penalty (0.5x) brings test mains from 0.9 to 0.45,
    # but the connectivity boost (up to +0.25) can push them to ~0.70.
    # A threshold of 0.7 cleanly separates real entrypoints (0.8+) from
    # test/utility code.  Low-confidence entrypoints still compete on
    # centrality in the regular fill phase.
    # Cap total force-includes to half the estimated node capacity so
    # bridge nodes always get budget, mirroring compact mode (line ~900).
    _FORCE_INCLUDE_CONFIDENCE_THRESHOLD = 0.7
    force_include_ids: set[str] = set()
    if force_include_entrypoints:
        symbol_ids = {s.id for s in symbols}
        avg_tokens_per_symbol = 75
        estimated_capacity = max(
            1, (target_tokens - TOKENS_BEHAVIOR_MAP_OVERHEAD) // avg_tokens_per_symbol
        )
        max_forced = max(1, estimated_capacity // 2)

        # Sort by confidence descending, cap count
        eligible_eps = sorted(
            behavior_map.get("entrypoints", []),
            key=lambda ep: (-ep.get("confidence", 0), ep.get("symbol_id", "")),
        )
        for ep in eligible_eps:
            if len(force_include_ids) >= max_forced:
                break
            conf = ep.get("confidence", 0)
            if conf < _FORCE_INCLUDE_CONFIDENCE_THRESHOLD:
                break
            sid = ep.get("symbol_id")
            if sid and sid in symbol_ids:
                force_include_ids.add(sid)

    # Use connectivity-aware selection to ensure the induced subgraph
    # has edges.  The old centrality-only approach (select_by_tokens)
    # picked high-centrality nodes independently, producing disconnected
    # output: all three bakeoff repos had 0 edges in the 4k view.
    # Connectivity-aware selection starts from entrypoints (seeds) and
    # expands via the frontier, so selected nodes share edges by design.
    #
    # Estimate max_additional from the token budget.  With the WI-pohuf slim
    # ``compact_node`` projection, an emitted node costs ~80 tokens (was ~250
    # when the full ~24-field ``to_dict()`` was emitted); reserve 50% of budget
    # for edges, entrypoints, and overhead.  The post-selection shrink loop
    # (below) enforces the exact budget, so over-estimating here is safe.
    _AVG_TOKENS_PER_NODE = 80
    node_budget_tokens = target_tokens // 2
    max_additional = max(1, node_budget_tokens // _AVG_TOKENS_PER_NODE)

    # Filter symbols the same way select_by_tokens does: exclude tests,
    # non-code, example paths, and boundary nodes so they don't pollute
    # the tiered view.  Boundary nodes (external_symbol, path=<external>)
    # are now serialized in behavior_map["nodes"] for disk-load consumers
    # (slice / verify-claims / test-coverage) to reason about external
    # edges, but the tiered compact view must still hide them because
    # they have no source code.
    # WI-jukav slice 2 dual-shape: see comment on the eligible_symbols
    # filter ~225 lines above.
    eligible_symbols = [
        s for s in symbols
        if not is_excluded_kind(s.kind, s.meta)
        and not _is_test_node(s.path, s.meta)
        and not _is_example_path(s.path)
        and not is_external_boundary(s)
    ]

    # Language-proportional seeding: inject seeds for dominant languages
    # whose entrypoints have no outgoing edges (e.g., C main() dispatching
    # via function pointer tables invisible to tree-sitter).  Without this,
    # BFS frontier is 100% in languages with dense entrypoints, and the
    # dominant language gets 0 nodes in the sketch.
    lang_seeds = find_underrepresented_language_seeds(
        eligible_symbols, edges, force_include_ids
    )
    force_include_ids |= lang_seeds

    # Compute the selection-time dampened centrality ONCE and reuse it for both the
    # connectivity selection and the post-shrink summary re-derive (INV-pazur), so both
    # reflect the identical centrality and we don't pay for it twice. This is the FULL
    # canonical dampener stack — distinct from the victim-removal centrality computed in
    # the shrink loop below, which excludes selection-time dampeners and only orders victims.
    selection_centrality = compute_dampened_centrality(eligible_symbols, edges)
    conn_result = select_by_connectivity(
        eligible_symbols, edges, force_include_ids, max_additional,
        centrality=selection_centrality,
    )

    # Build the initial tiered output, stripping large non-essential fields
    # validation_report describes the FULL substrate; a budget-tier is a shrunk projection,
    # so the report's counts wouldn't match the tier's nodes — and it wastes token budget.
    # (Before the ADR-0043 finalize rewire, validate_ir ran after tier generation, so tier
    # files never carried it; stripping preserves that shape now that finalize sets it early.)
    tiered_map = {
        k: v for k, v in behavior_map.items()
        if k not in _TIERED_STRIP_KEYS
    }
    tiered_map["view"] = "tiered"
    tiered_map["tier_tokens"] = target_tokens

    included_symbols = list(conn_result.included.symbols)
    included_ids = {s.id for s in included_symbols}

    # Induced edges: both endpoints in included set, no self-loops
    all_bmap_edges = behavior_map.get("edges", [])
    induced_edges = [
        e for e in all_bmap_edges
        if e.get("src") in included_ids and e.get("dst") in included_ids
        and e.get("src") != e.get("dst")
    ]

    # Filtered entrypoints
    all_bmap_eps = behavior_map.get("entrypoints", [])
    filtered_eps = [
        ep for ep in all_bmap_eps
        if ep.get("symbol_id") in included_ids
    ]

    tiered_map["nodes"] = [compact_node(s) for s in included_symbols]
    tiered_map["edges"] = induced_edges
    tiered_map["entrypoints"] = filtered_eps
    # Re-project features onto the SELECTED set before the shrink loop so the
    # in-loop token estimate reflects the small re-projected features, not the
    # full ``features[]`` (WI-pohuf — often the single largest field; the shrink
    # loop only removes nodes, so an un-projected 25k-token features field would
    # keep the map over budget no matter how many nodes are trimmed). Refined to
    # the final node set after the loop (see the authoritative re-projection
    # below). The projection stays small as the loop shrinks the node set.
    _all_features = behavior_map.get("features", [])
    tiered_map["features"] = _reproject_features(
        _all_features, included_ids, {e.get("id") for e in induced_edges}
    )
    # Pre-shrink summary: scratch for the in-loop token estimate only. The authoritative
    # nodes_summary is re-derived from the FINAL arrays after the shrink loop (INV-pazur).
    tiered_map["nodes_summary"] = conn_result.to_dict()

    # --- Post-selection budget enforcement ---
    # Check if the assembled output fits.  If not, shrink by removing
    # nodes that contribute least to the induced subgraph.  This is the
    # defence-in-depth that the old code lacked: edges and entrypoints
    # are now accounted for.
    #
    # Removal ordering considers LOCAL edge degree to preserve connectivity.
    # Prior approach sorted only by (force_include, global_centrality), which
    # removed non-forced nodes with low global centrality first — even if
    # those nodes were the only ones providing edges.  Bakeoff cohort #5
    # (iceberg) showed 169 nodes with 0 edges because all frontier-expanded
    # production nodes (low global centrality) were removed before
    # disconnected force-included test entrypoints.
    actual_tokens = estimate_behavior_map_tokens(tiered_map)

    if actual_tokens > target_tokens and len(included_symbols) > 1:
        # Compute centrality for removal ordering. WI-tahum: shared
        # helper applies rank_symbols' tuned compute_centrality params
        # so victim selection reflects the same graph-structural
        # dampening as the upstream connectivity selection. Only tier
        # weighting is applied here — non-tier dampeners are excluded
        # so selection-time signals don't propagate into post-budget
        # pruning.
        centrality = compute_dampened_centrality(
            symbols, edges,
            exclude_dampeners=_VICTIM_REMOVAL_EXCLUDE_DAMPENERS,
        )

        while actual_tokens > target_tokens and len(included_symbols) > 1:
            # Compute local edge degree from CURRENT induced edges.
            # This changes each iteration as nodes are removed.
            local_degree: dict[str, int] = {}
            for e in induced_edges:
                src, dst = e.get("src"), e.get("dst")
                local_degree[src] = local_degree.get(src, 0) + 1
                local_degree[dst] = local_degree.get(dst, 0) + 1

            # Pick victim: prefer removing nodes that contribute least.
            # Sort key (ascending = remove first):
            #   1. has_edges: False < True → remove 0-edge singletons first
            #   2. is_forced: False < True → remove non-forced first
            #   3. centrality: low centrality removed first
            victim = min(
                included_symbols,
                key=lambda s: (
                    local_degree.get(s.id, 0) > 0,
                    s.id in force_include_ids,
                    centrality.get(s.id, 0),
                ),
            )
            included_ids.discard(victim.id)
            included_symbols = [s for s in included_symbols if s.id != victim.id]

            # Incrementally filter edges: remove edges touching victim.
            # This is O(current_edges) per step instead of O(all_edges).
            induced_edges = [
                e for e in induced_edges
                if e.get("src") != victim.id and e.get("dst") != victim.id
            ]
            filtered_eps = [
                ep for ep in all_bmap_eps
                if ep.get("symbol_id") in included_ids
            ]

            tiered_map["nodes"] = [compact_node(s) for s in included_symbols]
            tiered_map["edges"] = induced_edges
            tiered_map["entrypoints"] = filtered_eps
            actual_tokens = estimate_behavior_map_tokens(tiered_map)

    # Re-project features onto the shrunk tier (INV-titid), mirroring the
    # compact path. WI-pohuf: the full ``features[]`` is copied wholesale into
    # the initial tiered_map and is frequently the single largest field (on
    # apollo-server it is ~25k tokens — a lone feature referencing thousands of
    # nodes), which swamps even a 16k budget and forces the shrink loop to trim
    # the map to ~1 node while the map stays far over budget. Filtering each
    # feature's node/edge pointers to the retained sets — and dropping features
    # whose nodes were all pruned — keeps the tier within budget.
    included_edge_ids = {e.get("id") for e in tiered_map["edges"]}
    all_features = behavior_map.get("features", [])
    tiered_map["features"] = _reproject_features(
        all_features, included_ids, included_edge_ids
    )
    tiered_map["features_summary"] = _array_projection_summary(
        len(all_features), len(tiered_map["features"])
    )

    # INV-pazur: re-derive nodes_summary from the FINAL (post-shrink) on-disk arrays so its
    # included.count / included_edges_count and the omitted distribution can never disagree
    # with tiered_map["nodes"] / ["edges"]. Population is eligible_symbols (the same universe
    # select_by_connectivity saw); centrality is the selection-time dampened centrality
    # computed once above. The pre-shrink summary set during assembly was scratch only.
    recompute_view_summary(
        tiered_map, eligible_symbols, selection_centrality, emit_edge_count=True
    )
    _recompute_view_metrics(tiered_map)
    return tiered_map


def generate_tier_filename(base_path: str, tier_spec: str) -> str:
    """Generate filename for a tier output file.

    Args:
        base_path: Base output path like "survey.json"
        tier_spec: Tier spec like "4k", "16k"

    Returns:
        Tier-specific filename like "survey.4k.json"
    """
    import os
    base, ext = os.path.splitext(base_path)
    return f"{base}.{tier_spec}{ext}"
