"""Compact output mode with coverage-based truncation and residual summarization.

This module provides LLM-friendly output formatting that:
1. Selects symbols by centrality coverage (not arbitrary count)
2. Summarizes omitted items with semantic flavor (not just counts)
3. Uses bag-of-words analysis on symbol names for cheap extractive summarization

How It Works
------------
Traditional JSON output assumes unlimited consumer memory. LLMs have context
limits and need bounded, prioritized input with lossy summaries.

Coverage-based truncation selects the *fewest* symbols needed to capture a
target percentage of total centrality mass. This is more semantic than "top N"
because it adapts to the codebase's centrality distribution:
- Concentrated codebases (few important symbols): fewer items needed
- Flat codebases (importance spread out): more items needed

Residual summarization extracts "flavor" from omitted items using:
- Word frequency on symbol names (bag-of-words)
- File path pattern analysis
- Kind distribution (functions, classes, methods)

Why Bag-of-Words
----------------
Symbol names are information-dense. Words like "test", "handler", "parse",
"config" reveal what categories of code are being omitted. This gives LLMs
enough context to decide whether to request expansion.

Example output:
    {
      "included": {"count": 47, "coverage": 0.82},
      "omitted": {
        "count": 1200,
        "centrality_sum": 0.18,
        "top_words": ["test", "mock", "fixture", "assert"],
        "top_paths": ["tests/", "vendor/"],
        "kinds": {"function": 900, "class": 200, "method": 100}
      }
    }

An LLM seeing this knows: "The omitted stuff is mostly test code and vendor
dependencies. I can probably ignore it for production code questions."
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .ir import Symbol, Edge
from .ranking import compute_centrality, apply_tier_weights
from .selection.filters import (
    EXAMPLE_PATH_PATTERNS,  # re-export for backwards compatibility
    EXCLUDED_KINDS,
    is_test_path as _is_test_path,
    is_example_path as _is_example_path,
)
from .selection.language_proportional import (
    allocate_language_budget,
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

    def to_dict(self) -> dict:
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

    def to_dict(self) -> dict:
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

    def to_dict(self) -> dict:
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

    def to_dict(self) -> dict:
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

    def __init__(self, elements: list | None = None):
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


def compute_word_frequencies(symbols: List[Symbol]) -> Counter:
    """Compute word frequencies across symbol names.

    Args:
        symbols: List of symbols to analyze.

    Returns:
        Counter of word frequencies.
    """
    counter: Counter = Counter()
    for sym in symbols:
        tokens = tokenize_name(sym.name)
        counter.update(tokens)
    return counter


def compute_path_frequencies(symbols: List[Symbol]) -> Counter:
    """Compute path pattern frequencies.

    Args:
        symbols: List of symbols to analyze.

    Returns:
        Counter of path pattern frequencies.
    """
    counter: Counter = Counter()
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
    counter: Counter = Counter()
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
    counter: Counter = Counter()
    for sym in symbols:
        tier = getattr(sym, 'supply_chain_tier', 1)
        counter[tier] += 1
    return dict(counter)


def _build_adjacency_list(
    edges: List[Edge],
) -> Tuple[Dict[str, set], Dict[str, set]]:
    """Build bidirectional adjacency lists from edges.

    Args:
        edges: List of edges.

    Returns:
        Tuple of (outgoing adjacency, incoming adjacency).
        outgoing[src] = set of dst nodes
        incoming[dst] = set of src nodes
    """
    outgoing: Dict[str, set] = {}
    incoming: Dict[str, set] = {}

    for edge in edges:
        if edge.src not in outgoing:
            outgoing[edge.src] = set()
        outgoing[edge.src].add(edge.dst)

        if edge.dst not in incoming:
            incoming[edge.dst] = set()
        incoming[edge.dst].add(edge.src)

    return outgoing, incoming


def _compute_connectivity_score(
    node_id: str,
    selected_ids: set,
    uf: UnionFind,
    outgoing: Dict[str, set],
    incoming: Dict[str, set],
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
    seed_ids: set,
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
    edge_set = {(e.src, e.dst): e for e in edges}

    # Build adjacency lists
    outgoing, incoming = _build_adjacency_list(edges)

    # Compute centrality if not provided
    if centrality is None:
        from .ranking import compute_centrality as _compute_centrality
        centrality = _compute_centrality(symbols, edges)

    # Initialize selected set with seeds
    selected_ids: set = set()
    selected_symbols: List[Symbol] = []

    for sid in seed_ids:
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
    frontier: set = set()
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

        for node_id in frontier:
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

    # Compute induced subgraph edges
    included_edges: List[Edge] = []
    for (src, dst), edge in edge_set.items():
        if src in selected_ids and dst in selected_ids:
            included_edges.append(edge)

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
    )


def select_by_coverage(
    symbols: List[Symbol],
    edges: List[Edge],
    config: CompactConfig,
    force_include_ids: set | None = None,
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
        )

    # Compute centrality
    raw_centrality = compute_centrality(symbols, edges)

    if config.first_party_priority:
        centrality = apply_tier_weights(raw_centrality, symbols)
    else:
        centrality = raw_centrality

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
    included_ids: set = set()

    # First, force-include any must-include symbols (e.g., entrypoints)
    # These are semantically important and should always be included
    if force_include_ids:
        symbol_by_id = {s.id: s for s in symbols}
        for sid in force_include_ids:
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
    )


def format_compact_behavior_map(
    behavior_map: dict,
    symbols: List[Symbol],
    edges: List[Edge],
    config: CompactConfig,
    force_include_entrypoints: bool = True,
    connectivity_aware: bool = False,
) -> dict:
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
    force_include_ids: set = set()
    if force_include_entrypoints:
        symbol_ids = {s.id for s in symbols}
        entrypoints_with_ids = []
        for ep in behavior_map.get("entrypoints", []):
            sid = ep.get("symbol_id")
            if sid and sid in symbol_ids:
                entrypoints_with_ids.append(ep)

        # Cap entrypoints to leave room for bridge nodes in connectivity mode
        # Without this cap, repos with many entrypoints (e.g., 158 main() functions)
        # leave no room for nodes that connect them, resulting in 0 edges.
        # Use at least 1 to handle edge case where max_symbols is very small.
        max_forced = max(1, config.max_symbols // 2)
        if len(entrypoints_with_ids) > max_forced:
            # Sort by confidence (descending) and take top entries
            sorted_eps = sorted(
                entrypoints_with_ids,
                key=lambda ep: (-ep.get("confidence", 0), ep.get("symbol_id", ""))
            )
            entrypoints_with_ids = sorted_eps[:max_forced]

        force_include_ids = {ep.get("symbol_id") for ep in entrypoints_with_ids}

    if connectivity_aware:
        # Use connectivity-aware selection
        # Budget is remaining slots after entrypoints
        max_additional = max(0, config.max_symbols - len(force_include_ids))
        conn_result = select_by_connectivity(
            symbols, edges, force_include_ids, max_additional
        )

        # Create compact output
        compact_map = dict(behavior_map)
        compact_map["view"] = "compact"
        compact_map["nodes"] = [s.to_dict() for s in conn_result.included.symbols]
        compact_map["nodes_summary"] = conn_result.to_dict()

        # Use the induced edges from connectivity selection
        compact_map["edges"] = [e.to_dict() for e in conn_result.included_edges]

        # Filter entrypoints to only those whose symbol_id exists in included nodes
        included_ids = {s.id for s in conn_result.included.symbols}
        compact_map["entrypoints"] = [
            ep for ep in behavior_map.get("entrypoints", [])
            if ep.get("symbol_id") in included_ids
        ]
    else:
        # Use original coverage-based selection
        result = select_by_coverage(symbols, edges, config, force_include_ids)

        # Create compact output
        compact_map = dict(behavior_map)
        compact_map["view"] = "compact"
        compact_map["nodes"] = [s.to_dict() for s in result.included.symbols]
        compact_map["nodes_summary"] = result.to_dict()

        # Keep only edges where BOTH endpoints exist in the included set
        # Using AND (not OR) ensures the induced subgraph has valid connectivity
        included_ids = {s.id for s in result.included.symbols}
        compact_map["edges"] = [
            e for e in behavior_map.get("edges", [])
            if e.get("src") in included_ids and e.get("dst") in included_ids
        ]

        # Filter entrypoints to only those whose symbol_id exists in included nodes
        compact_map["entrypoints"] = [
            ep for ep in behavior_map.get("entrypoints", [])
            if ep.get("symbol_id") in included_ids
        ]

    return compact_map


# Backwards compatibility aliases for functions that were moved
def estimate_node_tokens(node_dict: dict) -> int:
    """Estimate tokens for a serialized node. Alias for estimate_json_tokens."""
    return estimate_json_tokens(node_dict)


def estimate_behavior_map_tokens(behavior_map: dict) -> int:
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
    force_include_ids: set | None = None,
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
        eligible_symbols = [s for s in eligible_symbols if s.kind not in EXCLUDED_KINDS]
    if exclude_tests:
        eligible_symbols = [s for s in eligible_symbols if not _is_test_path(s.path)]
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
    included_ids: set = set()

    # First, force-include any must-include symbols (e.g., entrypoints)
    # These are semantically important and should always be included
    if force_include_ids:
        symbol_by_id = {s.id: s for s in symbols}
        for sid in force_include_ids:
            if sid in symbol_by_id and sid not in included_ids:
                sym = symbol_by_id[sid]
                node_dict = sym.to_dict()
                node_tokens = estimate_node_tokens(node_dict)
                included.append(sym)
                included_centrality += centrality.get(sym.id, 0)
                tokens_used += node_tokens
                seen_names.add(sym.name)
                included_ids.add(sid)

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


def format_tiered_behavior_map(
    behavior_map: dict,
    symbols: List[Symbol],
    edges: List[Edge],
    target_tokens: int,
    force_include_entrypoints: bool = True,
) -> dict:
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
    # Extract entrypoint symbol_ids to force-include them
    force_include_ids: set = set()
    if force_include_entrypoints:
        symbol_ids = {s.id for s in symbols}
        for ep in behavior_map.get("entrypoints", []):
            sid = ep.get("symbol_id")
            if sid and sid in symbol_ids:
                force_include_ids.add(sid)

    result = select_by_tokens(
        symbols, edges, target_tokens, force_include_ids=force_include_ids
    )

    # Create tiered output
    tiered_map = dict(behavior_map)
    tiered_map["view"] = "tiered"
    tiered_map["tier_tokens"] = target_tokens
    tiered_map["nodes"] = [s.to_dict() for s in result.included.symbols]
    tiered_map["nodes_summary"] = result.to_dict()

    # Keep only edges where BOTH endpoints exist in the included set
    # Using AND (not OR) ensures the induced subgraph has valid connectivity
    included_ids = {s.id for s in result.included.symbols}
    tiered_map["edges"] = [
        e for e in behavior_map.get("edges", [])
        if e.get("src") in included_ids and e.get("dst") in included_ids
    ]

    # Filter entrypoints to only those whose symbol_id exists in included nodes
    tiered_map["entrypoints"] = [
        ep for ep in behavior_map.get("entrypoints", [])
        if ep.get("symbol_id") in included_ids
    ]

    return tiered_map


def generate_tier_filename(base_path: str, tier_spec: str) -> str:
    """Generate filename for a tier output file.

    Args:
        base_path: Base output path like "hypergumbo.results.json"
        tier_spec: Tier spec like "4k", "16k"

    Returns:
        Tier-specific filename like "hypergumbo.results.4k.json"
    """
    import os
    base, ext = os.path.splitext(base_path)
    return f"{base}.{tier_spec}{ext}"
