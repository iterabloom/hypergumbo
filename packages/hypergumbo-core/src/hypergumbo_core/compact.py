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
- Token-budget tier mode emits successively-larger views
  (``DEFAULT_TIERS`` = 4k / 16k / 64k by default; configurable via
  ``--tier``). ``format_tiered_behavior_map`` + ``generate_tier_filename``
  materialize them, selecting each tier with ``select_by_connectivity``.
  ``select_by_tokens``, a centrality-only selector, is kept but has no
  production caller.
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
- Seeds: the tiered path force-includes entrypoints at confidence >= 0.7,
  capped at half the tier's estimated capacity. The compact view's seed order
  and budgeting are described under "Compact entry point" below.
- Filters: test / example / external-boundary symbols are dropped
  via ``selection.filters`` (consuming ``EXCLUDED_KINDS`` /
  ``EXCLUDED_FRAMEWORK_ROLES`` via the ``is_excluded_kind`` dual-shape
  predicate, per ADR-0027 Phase 3 Wave 5).
- Name-deduplication (``deduplicate_names``) exists only in
  ``select_by_tokens``, so no production path applies it.
- Post-selection victim removal enforces a tier's final budget, which is
  the budget of the FILE as written (``token_budget.written_json_text``).
  Nodes leave in an order that does not depend on the budget: edgeless
  symbols first, then non-forced ones, then the lowest centrality, where
  centrality is computed without the selection-time dampeners
  (``_VICTIM_REMOVAL_EXCLUDE_DAMPENERS``) so those signals do not propagate
  into post-budget pruning. How many leave is found by bisection, each probe
  measuring the real file, and the result is recorded as ``tier_tokens_used``.

Compact entry point
-------------------
``format_compact_behavior_map`` builds the ``view: "compact"`` map. Both of its
selection paths draw seeds from one fixed, budget-independent order
(entrypoints by confidence, then cross-cutting endpoints by edge count), which
keeps a smaller ``--max-symbols`` budget's nodes contained in a larger one's:

- Centrality path (``connectivity_aware=False``, the function default;
  ``--no-connectivity`` on the CLI) narrows the population to ``key_symbols``
  and drops test-originated edges (``production_edges``, run on the unfiltered
  symbol list first), so it ranks the same population sketch does and a
  non-key file node cannot become a seed. Seeds are a prefix of the order under
  a quota of half the symbol budget; ``select_by_coverage`` fills the rest.
- Connectivity path (the CLI default) drops external-boundary placeholders,
  which would manufacture connectivity the code does not have, and test-path
  symbols with the edges they originate (a test joins two entrypoints in the
  call graph, not in the code; WI-zitag), and calls
  ``select_by_connectivity`` with ``interleave=True``: seeds alternate with
  greedy bridge picks against the total budget, so seeds are metered rather
  than preloaded. Only key symbols may enter as seeds
  (``seed_eligible=is_key_symbol``); bridge picks are otherwise unfiltered by
  kind, so a file node can still join two islands.

Projected-view shape
--------------------
The compact view and the tiered view share one projection:

- Top-level blocks in ``_COMPACT_STRIP_KEYS`` (``usage_contexts``,
  ``sketch_precomputed``, ``request_ingress``) are dropped; ``_TIERED_STRIP_KEYS`` also drops
  ``analysis_runs`` and ``validation_report``.
- Each node is the slim ``compact_node`` projection (navigation fields only,
  no identity hashes, provenance or supply-chain block); compact nodes also
  carry their selection-time ``centrality``.
- Edges are the induced subgraph, entrypoints are filtered to retained nodes,
  and ``features`` are re-projected onto the retained node/edge ids
  (``_reproject_features``; a feature whose entry nodes were all pruned is
  dropped). ``features_summary`` (plus ``entrypoints_summary`` in the compact
  view) reports included/omitted counts for those arrays.
- Summaries are re-derived from the emitted arrays rather than copied from the
  full map: ``_recompute_view_metrics`` recomputes ``metrics``, and the tiered
  path calls ``recompute_view_summary`` to rebuild ``nodes_summary`` for the
  selection it settles on.
- The tiered view goes further (``tier_projection``): its edges, entrypoints
  and features are slimmed like its nodes, ``limits`` records that differ in
  one key are grouped, and every repository-sized list outside the node
  selection is cut to a budget-dependent cap with ``<key>_total`` beside it.
  The compact view keeps those parts whole.

Residual summarization
----------------------
Omitted items are summarized with cheap extractive signals:
- Word frequency on symbol names (bag-of-words)
- File path pattern analysis
- Kind distribution (functions, classes, methods)
- Supply-chain tier distribution (``tiers``, keyed by ``supply_chain_tier``)

Example output (the omitted-residual summary, ``OmittedSummary.to_dict``):
    {
      "count": 1200,
      "centrality_sum": 0.18,
      "max_centrality": 0.94,
      "top_words": [{"word": "test", "count": 42}, {"word": "mock", "count": 30}],
      "top_paths": [{"pattern": "tests/", "count": 55}, {"pattern": "vendor/", "count": 21}],
      "kinds": {"function": 900, "class": 200, "method": 100},
      "tiers": {"1": 1100, "2": 60, "3": 40}
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
from typing import Any, Callable, Dict, List, Sequence, Tuple

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
    is_key_symbol,
    is_example_path as _is_example_path,
    key_symbols,
    production_edges,
)
from .paths import is_test_node as _is_test_node
from .metrics import view_metrics
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
    estimate_written_json_tokens,
    parse_tier_spec,  # re-export for backwards compatibility
)
from .tier_projection import (
    compact_edge,
    compact_entrypoint,
    compact_feature,
    project_fixed_sections,
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
# Only canonical edge types are listed; the ADR-0023 §6 folds make them
# cover the specialised types: routes_to and di_resolves are 'dispatches_to';
# message_dispatch / annotated_publishes / crdt_publishes / enqueues /
# message_send / message_queue / etc. are 'event_publishes'; and
# http_calls / grpc_calls / graphql_calls are 'calls' with meta['protocol'].
# (WI-vasik-jofiv, WI-vomoj-suhaz, WI-vumum-juvil)
CROSS_CUTTING_EDGE_TYPES = frozenset({
    "calls",           # generic invocation (covers FFI/IPC/RPC bridges
                       # and protocol-call family — HTTP, gRPC, GraphQL)
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
#     docs/hypergumbo-spec.md §usage_contexts), sketch_precomputed (an
#     internal cache artifact consumers must not depend on, spec §707) and
#     request_ingress (the UNCAPPED request-handler list verify-claims reads
#     from the full survey, which can run to hundreds of entries; the capped
#     ``entrypoints`` overview stays in the view; WI-vumig). It KEEPS
#     analysis_runs (provenance) and validation_report (the finalize quality
#     signal) — ADR-0033/ADR-0043 preserve both through the compact projection
#     (test_compact_preserves_validation_report_and_consistency).
#   _TIERED_STRIP_KEYS — the tiered view is the more aggressive budget projection
#     and ALSO drops analysis_runs and validation_report
#     (test_budget_tier_omits_validation_report).
_COMPACT_STRIP_KEYS = frozenset({
    "usage_contexts",
    "sketch_precomputed",
    "request_ingress",
})
_TIERED_STRIP_KEYS = _COMPACT_STRIP_KEYS | frozenset({
    "analysis_runs",
    "validation_report",
})

# The compact/tiered views are LLM-friendly, token-budgeted projections — NOT
# the full survey. The full ~24-field ``Symbol.to_dict()`` (identity hashes
# stable_id/shape_id/fingerprint, provenance origin/origin_run_id, the
# ~200-byte supply_chain block, meta, and other internals) costs ~250 tokens
# per node, so at small budgets the post-selection shrink loop would trim the
# map to about one surviving symbol: a different one at each budget (breaking
# containment monotonicity) and holding a tiny fraction of total centrality.
# This projection keeps only the fields a consumer needs to *understand and
# navigate* a symbol; the identity, provenance, and supply-chain details remain
# in the full survey. (WI-pohuf; see also WI-kolal, WI-zulij)
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


def _recompute_view_metrics(
    view_map: dict[str, Any],
    selected: list[Symbol],
    source_map: dict[str, Any],
) -> None:
    """Recompute a projected view's ``metrics`` block from its OWN selection.

    A budget-limited projection (compact/tiered) shallow-copies the source map,
    so without this it would echo the FULL-repo ``metrics`` (total_nodes,
    total_edges, total_files, by_supply_chain_tier) that ``compute_metrics``
    produced BEFORE projection — a view that lies about itself (WI-pizat).
    Recompute in place so the counts describe the projected arrays.

    ``selected`` is the Symbols behind ``view_map["nodes"]``. The block is
    computed from their full records, never from the slim ones the view
    writes: a slim node has no ``supply_chain`` block, and a count read off
    it puts every node in tier ``unknown`` (WI-lokuj;
    :func:`hypergumbo_core.metrics.view_metrics`).

    Only fires when the source carried a ``metrics`` block (the projection
    mirrors the source's structure — it does not invent one), and reads that
    block rather than the view's, so the tiered search may call it for every
    candidate selection. Deliberately does NOT touch ``analysis_incomplete``:
    per spec §726 that flag is analyzer-scope (set only on early termination /
    errors / resource limits), not a view-truncation signal.
    """
    if "metrics" in source_map:
        view_map["metrics"] = view_metrics(
            [symbol.to_dict() for symbol in selected],
            view_map["edges"], source_map["metrics"],
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
    seed_ids: "set[str] | Sequence[str]",
    max_additional: int,
    centrality: Dict[str, float] | None = None,
    interleave: bool = False,
    bridges_per_seed: int = 2,
    seed_eligible: "Callable[[Symbol], bool] | None" = None,
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
        seed_ids: Initial nodes to include (e.g., entrypoint IDs). A SET is
            sorted for reproducibility; a SEQUENCE is consumed in the given
            order, which is what ``interleave`` needs.
        max_additional: Maximum additional nodes beyond the seeds — except
            under ``interleave``, where seeds are part of the metered stream
            and this is the TOTAL emission budget.
        centrality: Optional pre-computed centrality. If None, computes it.
        bridges_per_seed: greedy picks taken after each seed under
            ``interleave``. TUNED, not arbitrary: at 1:1 a seed can be admitted
            and then have both its neighbours out-scored globally, stranding it
            with no edge in the induced subgraph; 1:2 leaves at most the final
            emitted node stranded (a boundary artifact of truncating any
            stream), and 1:3 / 1:4 buy nothing further. Any FIXED ratio
            preserves containment, since the stream stays budget-independent;
            the ratio is therefore a pure quality knob. It was tuned on a
            population that included external-boundary placeholders; without
            them the ratio alone does not prevent stranding, which the ANCHOR
            pick (below) handles structurally (WI-fadab).
        interleave: When True, alternate seed / greedy-pick instead of
            preloading every seed first, making the emitted list at a smaller
            budget an ordered prefix of the list at a larger one (containment;
            WI-zulij). Requires an ordered ``seed_ids``. The default preloads
            every seed, for callers that need every seed present regardless
            of budget.
        seed_eligible: Optional predicate a node must satisfy to be ADMITTED
            WITHOUT AN EDGE — i.e. as a seed or as the no-seed bootstrap.
            Greedy bridge picks are never filtered by it: a seed is chosen for
            what it IS, a bridge for what it CONNECTS, and only the first is a
            claim about importance (WI-fadab). When no symbol satisfies it, the
            bootstrap falls back to the whole population so a repo of only
            non-eligible nodes still yields output. ``None`` admits everything.

    Returns:
        ConnectivityResult with selected symbols and induced edges.
    """
    symbol_by_id = {s.id: s for s in symbols}

    # Build adjacency lists
    outgoing, incoming = _build_adjacency_list(edges)

    # Compute centrality if not provided. The shared helper applies
    # rank_symbols' tuned compute_centrality params plus the canonical
    # dampener stack (ranking.py::_CANONICAL_DAMPENERS; WI-tahum). When the
    # caller supplies centrality, it is trusted as-is.
    if centrality is None:
        centrality = compute_dampened_centrality(symbols, edges)

    # Initialize selected set with seeds
    selected_ids: set[str] = set()
    selected_symbols: List[Symbol] = []

    # A SET's iteration order is PYTHONHASHSEED-dependent, so a set is sorted
    # to keep the seed prefix reproducible (WI-nivuj). A SEQUENCE is taken in
    # the caller's order — that is the interleaved policy's fixed,
    # budget-independent seed order, and sorting it would destroy exactly the
    # property it is there to provide (WI-zulij).
    seed_seq: List[str] = (
        sorted(seed_ids) if isinstance(seed_ids, (set, frozenset))
        else list(seed_ids)
    )
    # One gate for every edge-less admission (WI-fadab). Filtering the sequence
    # (not testing inside each loop) keeps the order of the survivors, so the
    # interleave's budget-independent stream -- and with it containment -- is
    # untouched: the predicate reads the symbol, never the budget.
    bootstrap_pool: List[Symbol] = symbols
    if seed_eligible is not None:
        seed_seq = [
            sid for sid in seed_seq
            if sid in symbol_by_id and seed_eligible(symbol_by_id[sid])
        ]
        bootstrap_pool = [s for s in symbols if seed_eligible(s)] or symbols

    uf = UnionFind([])
    frontier: set[str] = set()

    def _admit(node_id: str) -> None:
        """Select *node_id*, union it into its components, extend the frontier.

        One admission step for seeds and greedy picks alike. The interleaved
        policy makes them strictly alternate, so sharing a single step keeps
        the two from drifting apart.
        """
        selected_ids.add(node_id)
        selected_symbols.append(symbol_by_id[node_id])
        uf.add(node_id)
        for dst in outgoing.get(node_id, set()):
            if dst in selected_ids:
                uf.union(node_id, dst)
            elif dst in symbol_by_id:
                frontier.add(dst)
        for src in incoming.get(node_id, set()):
            if src in selected_ids:
                uf.union(node_id, src)
            elif src in symbol_by_id:
                frontier.add(src)
        frontier.discard(node_id)

    def _best_frontier_node(candidates: "set[str] | None" = None) -> str | None:
        """Argmax over *candidates* (default: the whole frontier) by
        (component_growth, edges_added, centrality).

        Candidates are iterated in SORTED order with a strict ``>``, so a score
        tie resolves to the lexicographically-smallest id rather than to
        whatever PYTHONHASHSEED put first. Both halves of that pair are
        load-bearing — keep them together (WI-nivuj).
        """
        best_node = None
        best_score = (-1, -1, -1.0)
        for node_id in sorted(frontier if candidates is None else candidates):
            score = _compute_connectivity_score(
                node_id, selected_ids, uf, outgoing, incoming, centrality
            )
            if score > best_score:
                best_score = score
                best_node = node_id
        return best_node

    if interleave:
        # The budget is pure TRUNCATION of one budget-independent stream —
        # next seed, best bridge, next seed, best bridge — so step t depends
        # only on the first t-1 emissions and a fixed seed order. The emitted
        # list at a smaller budget is therefore a literal ordered PREFIX of the
        # list at a larger one, which is containment by construction (WI-zulij).
        #
        # The greedy picks never read the budget (`max_additional` appears only
        # in the loop guards), so bridge expansion is prefix-nested on its own.
        # What would break containment is admitting a budget-SIZED seed set
        # before the first pick: a larger budget would hand the scorer a
        # different union-find state and produce a different SEQUENCE of picks,
        # not a longer one. A monotone seed quota (as the centrality path uses)
        # does not fix that here; metering the seeds in the stream does.
        #
        # ``max_additional`` is the TOTAL emission budget here, not a count of
        # additions beyond free seeds: seeds are part of the stream, so they
        # are metered like anything else.
        # Same bootstrap the preload path uses: with no seeds at all there is no
        # frontier to grow from and the stream would emit nothing. The
        # highest-centrality node is budget-independent, so seeding with it costs
        # containment nothing.
        if not seed_seq and bootstrap_pool:
            _admit(max(bootstrap_pool, key=lambda s: centrality.get(s.id, 0)).id)

        seed_cursor = 0
        while len(selected_ids) < max_additional:
            progressed = False
            just_seeded: str | None = None
            while seed_cursor < len(seed_seq):
                sid = seed_seq[seed_cursor]
                seed_cursor += 1
                if sid in symbol_by_id and sid not in selected_ids:
                    _admit(sid)
                    just_seeded = sid
                    progressed = True
                    break
            for _ in range(bridges_per_seed):
                if len(selected_ids) >= max_additional:
                    break
                # ANCHOR: a seed admitted into no existing component spends the
                # first pick on its own best neighbour. The scorer's primary key
                # is growth of the LARGEST component, so joining a singleton seed
                # to its neighbour scores the minimum and loses to any node
                # touching the main component — the seed is stranded. External-
                # boundary placeholders, when present, glue a seed to the main
                # component through a shared stdlib callee; callers exclude them,
                # and without them the 1:2 ratio (above) alone leaves many seeds
                # stranded. The anchor reads only stream state, never the
                # budget, so containment holds. (WI-fadab)
                best_node = None
                if just_seeded is not None and uf.component_size(just_seeded) == 1:
                    best_node = _best_frontier_node(
                        (outgoing.get(just_seeded, set())
                         | incoming.get(just_seeded, set())) & frontier
                    )
                just_seeded = None
                if best_node is None:
                    best_node = _best_frontier_node()
                if best_node is None:
                    break
                _admit(best_node)
                progressed = True
            if not progressed:
                # Both streams dry: no seeds left and no reachable frontier.
                break
    else:
        for sid in seed_seq:
            if sid in symbol_by_id and sid not in selected_ids:
                _admit(sid)

        # Handle empty seed case: start with highest-centrality node
        if not selected_ids and bootstrap_pool:
            best_sym = max(bootstrap_pool, key=lambda s: centrality.get(s.id, 0))
            _admit(best_sym.id)
            max_additional -= 1

        added = 0
        while added < max_additional and frontier:
            best_node = _best_frontier_node()
            if best_node is None:  # pragma: no cover
                # Defensive: frontier should always have scoreable nodes
                break
            _admit(best_node)
            added += 1

    # Compute induced subgraph edges. Iterate the edge LIST (not a
    # (src, dst)-keyed dict) so PARALLEL edges between the same node pair are
    # all retained — a (src, dst) dict collapses them, dropping every parallel
    # but the last (WI-hakom). Self-loops (src == dst) waste budget without
    # adding connectivity, so they are excluded, matching the coverage and
    # tiered paths, which also iterate the edge list directly.
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

    # The shared helper applies rank_symbols' tuned compute_centrality
    # params plus the canonical dampener stack
    # (ranking.py::_CANONICAL_DAMPENERS; WI-tahum).
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
        # No language stratification: sort all symbols by centrality
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
        for sid in sorted(force_include_ids):  # reproducible order (WI-nivuj)
            if sid in symbol_by_id and sid not in included_ids:
                sym = symbol_by_id[sid]
                included.append(sym)
                included_centrality += centrality.get(sym.id, 0)
                included_ids.add(sid)

    # Then fill remaining budget with highest-centrality symbols.
    #
    # The stop rule must be BUDGET-INDEPENDENT to preserve containment.
    # Counting force-included (seed) centrality toward coverage would let a
    # larger budget's bigger seed set inflate the starting coverage and stop
    # this fill EARLIER, dropping ranked symbols a smaller budget kept.  So
    # stop_pos is computed from the fixed global order alone (positions and
    # cumulative ranked mass): it is one number for every budget, and the walk
    # below is a prefix of a fixed list truncated by the budget — nested by
    # construction.  min_symbols is positional for the same reason; the
    # included COUNT is always >= the positions walked (a union with the
    # seeds), so the "at least min_symbols included" guarantee holds.
    # (WI-vofud)
    stop_pos = len(sorted_symbols)
    walked_mass = 0.0
    for pos, sym in enumerate(sorted_symbols):
        walked_mass += centrality.get(sym.id, 0)
        if (pos + 1 >= config.min_symbols
                and walked_mass / total_centrality >= config.target_coverage):
            stop_pos = pos + 1
            break

    for sym in sorted_symbols[:stop_pos]:
        if len(included) >= config.max_symbols:
            break
        # Skip if already included (force-included)
        if sym.id in included_ids:
            continue
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
    anchor was pruned). Feature scope is thus re-derived from the emitted
    graph rather than copied wholesale (compare INV-tarol for slices).
    ``admission_stats`` and ``limits_detail`` (counts, not
    node-keyed) pass through unchanged: ``limits_detail`` sizes what the
    BFS limits cut from the full-graph slice, not what this view's budget
    cut.
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

    Each node carries the selection mode's own centrality (rounded), so a
    consumer can rank the retained nodes and cross-check them against the
    summary's aggregate: ``nodes`` and ``nodes_summary`` agree.
    """
    for n in nodes:
        # The "" default makes the lookup key well-typed (`n.get("id")` alone
        # is `Any | None`). Either way an id-less node scores 0.0 and is
        # indistinguishable from a node genuinely at 0.0; that ambiguity is a
        # projection concern, not one this helper can resolve.
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
    # The DEFAULT (centrality) path ranks the same population sketch does:
    # key symbols over test-source-free edges. Both surfaces advertise "the
    # important symbols" and both rank with compute_dampened_centrality, so
    # matching the population is what makes their top symbols agree (WI-zulij).
    # Both filters are needed, and the edge filter matters most: without it,
    # symbols are credited for being called by their own test suites.
    #
    # Order matters — production_edges must see the UNFILTERED symbol list,
    # because it resolves each edge's src id to a path, and a src that was
    # filtered out would otherwise resolve to "" and be kept as non-test.
    # sketch builds both from the same unfiltered list for exactly this reason.
    #
    # Narrowing here rather than at the ranking call site is deliberate: it also
    # constrains which symbols can become force-included SEEDS, keeping non-key
    # noise such as shell-script ``file`` nodes and shell completions out of
    # the lead of the output.
    #
    # Connectivity mode does not narrow to key symbols: its contract is
    # bridging disconnected components, and a non-key node (often a file node)
    # is frequently the only thing joining two islands, so narrowing would
    # remove the bridges the mode exists to find. That exemption covers
    # BRIDGES only. Connectivity mode still:
    # (a) drops external-boundary nodes from its population, as the tiered view
    #     does — they have no source, and two callers of ``time.monotonic`` are
    #     not structurally joined by it, so a boundary "bridge" manufactures
    #     connectivity the code does not have (WI-fadab);
    # (b) admits only key symbols as seeds or as the bootstrap (``seed_eligible``
    #     below), because the seed list is the "important symbols" claim and
    #     the bridge picks are not (WI-fadab);
    # (c) drops test-path symbols from its population and test-sourced edges
    #     from its graph (same order rule as above): a test that exercises two
    #     entrypoints joins them in the call graph, not in the production code
    #     (WI-zitag).
    # The centrality path needs no separate handling for (a) or (b):
    # ``key_symbols`` already excludes both shapes.
    if not connectivity_aware:
        edges = production_edges(symbols, edges)
        symbols = key_symbols(symbols)
    else:
        edges = production_edges(symbols, edges)
        symbols = [
            s for s in symbols
            if not is_external_boundary(s) and not _is_test_node(s.path, s.meta)
        ]

    # Extract entrypoint symbol_ids to force-include them, ranked by
    # (-confidence, symbol_id) for a stable, budget-independent order.
    symbol_id_set = {s.id for s in symbols}
    sorted_eps: list[str] = []
    if force_include_entrypoints:
        entrypoints_with_ids = []
        for ep in behavior_map.get("entrypoints", []):
            sid = ep.get("symbol_id")
            if sid and sid in symbol_id_set:
                entrypoints_with_ids.append(ep)
        sorted_eps = [
            ep.get("symbol_id")
            for ep in sorted(
                entrypoints_with_ids,
                key=lambda ep: (-ep.get("confidence", 0), ep.get("symbol_id", ""))
            )
        ]

    # Cross-cutting edge endpoints, ranked by (-edge_count, id): seeded so
    # linker-produced edges survive the induced-subgraph filter.  Without
    # this, centrality-based selection drops peripheral nodes (route
    # definitions, dispatch targets, FFI endpoints) that are endpoints of
    # these high-value edges.
    cc_edge_count: Counter[str] = Counter()
    for e in behavior_map.get("edges", []):
        # Edge dicts use "type" key (from Edge.to_dict()), not "edge_type"
        if e.get("type") in CROSS_CUTTING_EDGE_TYPES:
            src, dst = e.get("src"), e.get("dst")
            if src in symbol_id_set:
                cc_edge_count[src] += 1
            if dst in symbol_id_set:
                cc_edge_count[dst] += 1
    ranked_cc = sorted(cc_edge_count, key=lambda x: (-cc_edge_count[x], x))

    # The fixed, budget-independent seed order both policies draw from:
    # entrypoints by (-confidence, id), then cross-cutting endpoints by
    # (-edge_count, id). Building it once, above the branch, is what lets the
    # connectivity path hand the WHOLE order to the interleave and the
    # centrality path take a monotone prefix of it.
    seed_order: List[str] = list(sorted_eps)
    _seen_seeds = set(seed_order)
    for _cc in ranked_cc:
        if _cc not in _seen_seeds:
            seed_order.append(_cc)
            _seen_seeds.add(_cc)

    force_include_ids: set[str] = set()
    if connectivity_aware:
        # NO seed cap here. The interleave meters seeds against the budget, so
        # a small budget simply never reaches the tail of the order. A capped
        # seed set preloaded before the first greedy pick would break
        # containment: a larger budget would seed MORE nodes and so change the
        # union-find state the scorer reads, producing a different SEQUENCE of
        # picks rather than a longer one. Alternation also keeps a repo with
        # hundreds of entrypoints (e.g. JAX-RS handlers) from crowding out the
        # bridging budget, since bridges are guaranteed their slots after every
        # seed rather than whatever is left over. (WI-zulij)
        force_include_ids = set()
    else:
        # The DEFAULT path's contract is --max-symbols containment
        # monotonicity (nodes(K1) ⊆ nodes(K2) for K1 <= K2).  Seeds are
        # therefore a prefix of ONE fixed-order list (seed_order, built above:
        # entrypoints by confidence, then cross-cutting endpoints by edge
        # count) under ONE quota that can only grow with the budget — nested
        # by construction.  A cap that switches regimes with the budget, or a
        # cross-cutting allowance that shrinks as the forced set grows, would
        # let a larger budget drop seeds a smaller budget kept.  The quota of
        # half the budget keeps seeds from crowding out the centrality
        # frontier.  (WI-vofud, WI-kolal)
        # The floor-of-one slot belongs to ENTRYPOINT seeds only (a semantic
        # anchor survives even max_symbols=1); a pure cross-cutting seed
        # list gets no floor (max//2 floors to zero at tiny budgets), so the
        # top-centrality symbol still wins the last slot.  The branch
        # condition is budget-independent, so quota stays monotone in
        # max_symbols either way.
        if sorted_eps:
            seed_quota = max(1, config.max_symbols // 2)
        else:
            seed_quota = config.max_symbols // 2
        force_include_ids = set(seed_order[:seed_quota])

    if connectivity_aware:
        # Hand over the WHOLE ordered seed list and the TOTAL budget. The
        # interleave truncates the stream, so seeds are metered rather than
        # preloaded — which is what makes a smaller budget's node list an
        # ordered prefix of a larger one's (WI-zulij).
        conn_result = select_by_connectivity(
            symbols, edges, seed_order, config.max_symbols, interleave=True,
            seed_eligible=is_key_symbol,
        )

        # Create compact output
        compact_map = {
            k: v for k, v in behavior_map.items()
            if k not in _COMPACT_STRIP_KEYS
        }
        compact_map["view"] = "compact"
        selected = conn_result.included.symbols
        compact_map["nodes"] = [compact_node(s) for s in selected]
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
        # Coverage-based selection
        result = select_by_coverage(symbols, edges, config, force_include_ids)

        # Create compact output
        compact_map = {
            k: v for k, v in behavior_map.items()
            if k not in _COMPACT_STRIP_KEYS
        }
        compact_map["view"] = "compact"
        selected = result.included.symbols
        compact_map["nodes"] = [compact_node(s) for s in selected]
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
        # Symmetry with the connectivity branch: the coverage-shaped summary
        # (CompactResult.to_dict) omits included_edges_count, so it is added
        # here from the emitted edges and both compact modes report it
        # (WI-zotam).
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

    _recompute_view_metrics(compact_map, selected, behavior_map)
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
        # is_excluded_kind, not plain ``EXCLUDED_KINDS`` membership: synthetic
        # ``method``/``function`` symbols carry their framework role on
        # ``meta["framework_role"]`` (ADR-0027 §"Phase 3" Wave 5; WI-jukav).
        eligible_symbols = [
            s for s in eligible_symbols
            if not is_excluded_kind(s.kind, s.meta, language=s.language)
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
        # No language stratification: sort all eligible symbols by centrality
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
    ``["edges"]`` to fit the token budget, which leaves the summary's ``included.count`` /
    ``included_edges_count`` and the whole ``omitted`` distribution describing arrays that
    were never written. This helper recomputes the summary block as a pure function of the
    *authoritative* post-shrink arrays (``len(view_map["nodes"])`` /
    ``len(view_map["edges"])``) plus the selection-time centrality, so the counts cannot
    disagree with the arrays — by construction.

    It re-derives the summary ONLY; it does not touch ``view_map["nodes"]`` / ``["edges"]``
    / ``["entrypoints"]`` (the shrink loop already produced the correct sets), so the
    emitted node/edge/entrypoint membership is provably unchanged — only the summary moves.

    The omitted universe (``population``) and ``centrality`` are caller-supplied rather than
    hardcoded so the tiered caller can pass its ``eligible_symbols`` (tests/examples/
    boundary nodes pre-filtered) without baking that filter into the helper — any other
    caller would pass its own universe. Iteration is over the
    ``population`` list, so the bag-of-words tie-order (``Counter.most_common``) and the
    whole summary are independent of ``PYTHONHASHSEED``.

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


def _stamp_tier_tokens_used(tiered_map: dict[str, Any]) -> int:
    """Record in the map the size it will be written at, and return it.

    The field is part of the file it measures, so the value is the fixed
    point of "size of the map holding this value". It is reached in a step
    or two: a longer number adds a character or two, the size never falls
    as the number grows, and the token count is a quarter of the characters.
    """
    tiered_map["tier_tokens_used"] = 0
    while True:
        used = estimate_written_json_tokens(tiered_map)
        if used == tiered_map["tier_tokens_used"]:
            return used
        tiered_map["tier_tokens_used"] = used


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
    # bridge nodes always get budget, mirroring the default (non-connectivity)
    # seed quota in format_compact_behavior_map.
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
    # has edges.  Centrality-only selection (select_by_tokens) picks
    # high-centrality nodes independently, which yields disconnected output
    # with no induced edges at small tiers.
    # Connectivity-aware selection starts from entrypoints (seeds) and
    # expands via the frontier, so selected nodes share edges by design.
    #
    # Estimate max_additional from the token budget.  A slim ``compact_node``
    # emits at ~80 tokens; reserve 50% of budget for edges, entrypoints, and
    # overhead.  The post-selection shrink loop (below) enforces the exact
    # budget, so over-estimating here is safe.
    _AVG_TOKENS_PER_NODE = 80
    node_budget_tokens = target_tokens // 2
    max_additional = max(1, node_budget_tokens // _AVG_TOKENS_PER_NODE)

    # Filter symbols the same way select_by_tokens does: exclude tests,
    # non-code, example paths, and boundary nodes so they don't pollute
    # the tiered view.  Boundary nodes (external_symbol, path=<external>)
    # are serialized in behavior_map["nodes"] for disk-load consumers
    # (slice / verify-claims / test-coverage) to reason about external
    # edges, but the tiered compact view hides them because they have no
    # source code.
    # is_excluded_kind: see the comment on the eligible_symbols filter in
    # select_by_tokens.
    eligible_symbols = [
        s for s in symbols
        if not is_excluded_kind(s.kind, s.meta, language=s.language)
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
    # Finalize sets it before tier generation (ADR-0043), so it must be stripped here.
    tiered_map = {
        k: v for k, v in behavior_map.items()
        if k not in _TIERED_STRIP_KEYS
    }
    tiered_map["view"] = "tiered"
    tiered_map["tier_tokens"] = target_tokens
    # Every section the node selection does not own takes its tier form
    # first (WI-lavum): they are measured as they will be written.
    project_fixed_sections(tiered_map, target_tokens)

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

    # Tier records (WI-lavum): every record is slimmed once, here. The
    # selection is searched over the SOURCE dicts, because the search reads
    # ``src`` / ``dst`` / ``symbol_id`` off them; what is measured and written
    # is each one's tier form.
    initial_symbols = included_symbols
    initial_edges = induced_edges
    initial_eps = filtered_eps
    node_records = {s.id: compact_node(s) for s in initial_symbols}
    edge_records = {id(e): compact_edge(e) for e in initial_edges}
    entry_records = {id(ep): compact_entrypoint(ep) for ep in initial_eps}
    all_features = behavior_map.get("features", [])

    # --- Budget enforcement (INV-nukuk, WI-lavum) ---
    # The budget describes the FILE: the map as it is written to disk, every
    # section included. If the whole selection does not fit, nodes are left
    # out, least valuable first, and the question is only HOW MANY.
    #
    # The order nodes leave in does not depend on the budget: each victim is
    # the node contributing least to the subgraph that is left (see
    # ``next_victim``). So the candidates are a fixed chain, the selection
    # with its first k victims removed for k = 0..n-1, each smaller than the
    # last, and the answer is the smallest k whose file fits: a bisection,
    # with every probe measuring the real written size.
    #
    # It used to be a loop that removed one node at a time against a running
    # size. That size held the features of the selection the loop STARTED
    # with, and features can be most of a tier and shrink with every node
    # removed, so the loop removed nodes the finished file had room for:
    # killbill's 16k tier kept one node with 2.2k tokens unused.
    victims: list[Symbol] = []
    remaining = list(initial_symbols)
    remaining_edges = list(initial_edges)
    victim_centrality: dict[str, float] = {}

    def next_victim() -> None:
        """Append to ``victims`` the node that contributes least to what is
        left, and take it out of what is left.

        Removal ordering considers LOCAL edge degree to preserve
        connectivity. Ordering by (force_include, global_centrality) alone
        would remove non-forced, low-centrality frontier nodes first even
        when they are the only ones providing edges, leaving disconnected
        force-included entrypoints and a tier with no edges.
        """
        nonlocal remaining, remaining_edges
        if not victim_centrality:
            # The shared helper applies rank_symbols' tuned
            # compute_centrality params (WI-tahum) so victim selection
            # reflects the same graph-structural dampening as the upstream
            # connectivity selection. Only tier weighting is applied here:
            # non-tier dampeners are excluded so selection-time signals don't
            # propagate into post-budget pruning.
            victim_centrality.update(compute_dampened_centrality(
                symbols, edges,
                exclude_dampeners=_VICTIM_REMOVAL_EXCLUDE_DAMPENERS,
            ))
        local_degree: dict[str, int] = {}
        for e in remaining_edges:
            src, dst = e.get("src"), e.get("dst")
            local_degree[src] = local_degree.get(src, 0) + 1
            local_degree[dst] = local_degree.get(dst, 0) + 1
        # Sort key (ascending = remove first):
        #   1. has_edges: False < True → remove 0-edge singletons first
        #   2. is_forced: False < True → remove non-forced first
        #   3. centrality: low centrality removed first
        victim = min(
            remaining,
            key=lambda s: (
                local_degree.get(s.id, 0) > 0,
                s.id in force_include_ids,
                victim_centrality.get(s.id, 0),
            ),
        )
        victims.append(victim)
        remaining = [s for s in remaining if s.id != victim.id]
        remaining_edges = [
            e for e in remaining_edges
            if e.get("src") != victim.id and e.get("dst") != victim.id
        ]

    def place(removed: int) -> list[Symbol]:
        """Put into the map the selection with its first ``removed`` victims
        left out, and return the Symbols kept: nodes, the edges and
        entrypoints among them, and the
        features re-projected onto them (INV-titid). The full ``features[]``
        is frequently the single largest field (a lone feature can reference
        thousands of nodes); filtering each feature's pointers to the
        retained sets, and dropping features whose entry nodes were all
        pruned, is what lets a small tier hold more than one symbol
        (WI-pohuf)."""
        while len(victims) < removed:
            next_victim()
        gone = {victim.id for victim in victims[:removed]}
        kept = [s for s in initial_symbols if s.id not in gone]
        kept_edges = [
            e for e in initial_edges
            if e.get("src") not in gone and e.get("dst") not in gone
        ]
        tiered_map["nodes"] = [node_records[s.id] for s in kept]
        tiered_map["edges"] = [edge_records[id(e)] for e in kept_edges]
        tiered_map["entrypoints"] = [
            entry_records[id(ep)] for ep in initial_eps
            if ep.get("symbol_id") not in gone
        ]
        tiered_map["features"] = [
            compact_feature(feature)
            for feature in _reproject_features(
                all_features,
                {s.id for s in kept},
                {e.get("id") for e in kept_edges},
            )
        ]
        return kept

    def probe(removed: int) -> int:
        """The written size of the map with ``removed`` victims left out.
        The summary, metrics and size stamp are the ones last written; they
        change by a few characters with the selection, which ``finish`` and
        the loop below account for."""
        place(removed)
        return estimate_written_json_tokens(tiered_map)

    def finish(removed: int) -> int:
        """Write the whole map for this selection and return its size."""
        kept = place(removed)
        tiered_map["features_summary"] = _array_projection_summary(
            len(all_features), len(tiered_map["features"])
        )
        # Re-derive nodes_summary from the FINAL arrays so its included.count /
        # included_edges_count and the omitted distribution can never disagree
        # with tiered_map["nodes"] / ["edges"] (INV-pazur). Population is
        # eligible_symbols (the same universe select_by_connectivity saw);
        # centrality is the selection-time dampened centrality computed once
        # above.
        recompute_view_summary(
            tiered_map, eligible_symbols, selection_centrality, emit_edge_count=True
        )
        _recompute_view_metrics(tiered_map, kept, behavior_map)
        return _stamp_tier_tokens_used(tiered_map)

    def smallest_fitting(above: int) -> int:
        """The smallest number of victims greater than ``above`` whose probe
        fits, given that ``above`` does not; the one-node floor if none."""
        low, high = above, len(initial_symbols) - 1
        while high - low > 1:
            middle = (low + high) // 2
            if probe(middle) <= target_tokens:
                high = middle
            else:
                low = middle
        return high

    # Scratch for the probes, until the first ``finish`` replaces them.
    tiered_map["nodes_summary"] = conn_result.to_dict()
    tiered_map["tier_tokens_used"] = target_tokens

    floor = len(initial_symbols) - 1
    removed = 0
    used = finish(0)
    while used > target_tokens and removed < floor:
        # Either the whole selection does not fit, or the sections ``finish``
        # re-derived came out larger than the probes assumed. Search again
        # from here, against what was just written.
        removed = smallest_fitting(removed)
        used = finish(removed)
    # A map still over budget here has one node left: the sections that do not
    # depend on the selection exceed the budget by themselves. It is written
    # as it is, and ``tier_tokens_used > tier_tokens`` says so.
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
