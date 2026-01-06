"""Symbol and file ranking utilities for hypergumbo output.

This module provides reusable ranking functions that determine which symbols
and files are most important in a codebase. These utilities power the
thoughtful ordering in sketch output and can be used by slice, run, and
other modes.

How It Works
------------
Ranking uses multiple signals combined:

1. **Centrality**: In-degree centrality measures how many other symbols
   reference a given symbol. Symbols called by many others are considered
   more important ("authority" in the codebase).

2. **Supply Chain Tier Weighting**: First-party code (tier 1) gets a 2x
   boost, internal dependencies (tier 2) get 1.5x, external dependencies
   (tier 3) get 1x, and derived/generated code (tier 4) gets 0x. This
   ensures your code ranks higher than bundled dependencies.

3. **File Density Scoring**: Files are scored by the sum of their top-K
   symbol scores, not just the maximum. This rewards files with multiple
   important symbols rather than one outlier.

Why These Heuristics
--------------------
- **Centrality** captures structural importance: heavily-called utilities,
  core abstractions, and integration points naturally score high.

- **Tier weighting** reflects user intent: when exploring a codebase, you
  usually care more about the project's own code than vendored libraries.

- **Sum-of-top-K** (vs max) provides stability: a file with 3 moderately
  important functions ranks higher than one with 1 important + 10 trivial.

Usage
-----
For symbol ranking:
    centrality = compute_centrality(symbols, edges)
    weighted = apply_tier_weights(centrality, symbols)
    ranked_symbols = sorted(symbols, key=lambda s: -weighted.get(s.id, 0))

For file ranking:
    by_file = group_symbols_by_file(symbols)
    file_scores = compute_file_scores(by_file, centrality)
    ranked_files = sorted(by_file.keys(), key=lambda f: -file_scores.get(f, 0))

For combined ranking with all heuristics:
    ranked = rank_symbols(symbols, edges, first_party_priority=True)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .ir import Symbol, Edge
from .selection.filters import is_test_path

# Backwards compatibility alias - external code imports _is_test_path from here
_is_test_path = is_test_path


# Tier weights for supply chain ranking (first-party prioritized)
# Tier 4 (derived) gets 0 weight since those files shouldn't be analyzed
TIER_WEIGHTS: Dict[int, float] = {1: 2.0, 2: 1.5, 3: 1.0, 4: 0.0}


@dataclass
class RankedSymbol:
    """A symbol with its computed ranking scores.

    Attributes:
        symbol: The original Symbol object.
        raw_centrality: In-degree centrality score (0-1 normalized).
        weighted_centrality: Centrality after tier weighting.
        rank: Position in the ranked list (0 = highest).
    """

    symbol: Symbol
    raw_centrality: float
    weighted_centrality: float
    rank: int


@dataclass
class RankedFile:
    """A file with its computed ranking scores.

    Attributes:
        path: File path relative to repo root.
        density_score: Sum of top-K symbol scores in this file.
        symbol_count: Number of symbols in this file.
        top_symbols: The top-K symbols contributing to the score.
        rank: Position in the ranked list (0 = highest).
    """

    path: str
    density_score: float
    symbol_count: int
    top_symbols: List[Symbol]
    rank: int


def compute_centrality(
    symbols: List[Symbol],
    edges: List[Edge],
) -> Dict[str, float]:
    """Compute symbol importance using in-degree centrality.

    Symbols called by many others are considered more important.
    This uses in-degree as a simple proxy for "authority" in the codebase.

    Args:
        symbols: List of symbols to rank.
        edges: List of edges (calls, imports) between symbols.

    Returns:
        Dictionary mapping symbol ID to centrality score (0-1 normalized).
    """
    symbol_ids = {s.id for s in symbols}
    in_degree: Dict[str, int] = dict.fromkeys(symbol_ids, 0)

    for edge in edges:
        # Edge uses 'dst' for target in IR
        target = edge.dst
        if target and target in in_degree:
            in_degree[target] += 1

    # Normalize to 0-1 range
    max_degree = max(in_degree.values()) if in_degree else 1
    if max_degree == 0:
        max_degree = 1

    return {sid: count / max_degree for sid, count in in_degree.items()}


def apply_tier_weights(
    centrality: Dict[str, float],
    symbols: List[Symbol],
    tier_weights: Dict[int, float] | None = None,
) -> Dict[str, float]:
    """Apply tier-based weighting to centrality scores.

    First-party symbols (tier 1) get a 2x boost, internal deps (tier 2) get 1.5x,
    external deps (tier 3) get 1x, and derived (tier 4) gets 0x.

    This ensures first-party code ranks higher than bundled dependencies
    even when dependencies have higher raw centrality.

    Args:
        centrality: Raw centrality scores from compute_centrality().
        symbols: List of symbols (must have supply_chain_tier set).
        tier_weights: Optional custom tier weights. Defaults to TIER_WEIGHTS.

    Returns:
        Dictionary mapping symbol ID to weighted centrality score.
    """
    if tier_weights is None:
        tier_weights = TIER_WEIGHTS

    symbol_tiers = {s.id: s.supply_chain_tier for s in symbols}
    weighted = {}
    for sid, score in centrality.items():
        tier = symbol_tiers.get(sid, 1)
        weight = tier_weights.get(tier, 1.0)
        weighted[sid] = score * weight
    return weighted


def group_symbols_by_file(symbols: List[Symbol]) -> Dict[str, List[Symbol]]:
    """Group symbols by their file path.

    Args:
        symbols: List of symbols to group.

    Returns:
        Dictionary mapping file path to list of symbols in that file.
    """
    by_file: Dict[str, List[Symbol]] = {}
    for s in symbols:
        by_file.setdefault(s.path, []).append(s)
    return by_file


def compute_file_scores(
    by_file: Dict[str, List[Symbol]],
    centrality: Dict[str, float],
    top_k: int = 3,
) -> Dict[str, float]:
    """Compute file importance scores using sum of top-K symbol scores.

    This provides a more robust file ranking than single-max centrality,
    as it rewards files with multiple important symbols ("density").

    Args:
        by_file: Symbols grouped by file path.
        centrality: Centrality scores for each symbol ID.
        top_k: Number of top symbols to sum for file score.

    Returns:
        Dictionary mapping file paths to importance scores.
    """
    file_scores: Dict[str, float] = {}
    for file_path, symbols in by_file.items():
        # Get top-K centrality scores for this file
        scores = sorted(
            [centrality.get(s.id, 0) for s in symbols],
            reverse=True
        )[:top_k]
        file_scores[file_path] = sum(scores)
    return file_scores


def rank_symbols(
    symbols: List[Symbol],
    edges: List[Edge],
    first_party_priority: bool = True,
    exclude_test_edges: bool = True,
) -> List[RankedSymbol]:
    """Rank symbols by importance using centrality and tier weighting.

    This is the main entry point for symbol ranking, combining all
    heuristics into a single ranked list.

    Args:
        symbols: List of symbols to rank.
        edges: List of edges between symbols.
        first_party_priority: If True, apply tier weighting to boost
            first-party code. Default True.
        exclude_test_edges: If True, ignore edges originating from test
            files when computing centrality. Default True.

    Returns:
        List of RankedSymbol objects sorted by importance (highest first).
    """
    if not symbols:
        return []

    # Build lookup for filtering edges
    symbol_path_by_id = {s.id: s.path for s in symbols}

    # Filter edges if requested
    if exclude_test_edges:
        filtered_edges = [
            e for e in edges
            if not _is_test_path(symbol_path_by_id.get(e.src, ''))
        ]
    else:
        filtered_edges = list(edges)

    # Compute centrality
    raw_centrality = compute_centrality(symbols, filtered_edges)

    # Apply tier weighting if enabled
    if first_party_priority:
        weighted_centrality = apply_tier_weights(raw_centrality, symbols)
    else:
        weighted_centrality = raw_centrality

    # Sort by weighted centrality (highest first), then by name for stability
    sorted_symbols = sorted(
        symbols,
        key=lambda s: (-weighted_centrality.get(s.id, 0), s.name)
    )

    # Build ranked results
    return [
        RankedSymbol(
            symbol=s,
            raw_centrality=raw_centrality.get(s.id, 0),
            weighted_centrality=weighted_centrality.get(s.id, 0),
            rank=i,
        )
        for i, s in enumerate(sorted_symbols)
    ]


def rank_files(
    symbols: List[Symbol],
    edges: List[Edge],
    first_party_priority: bool = True,
    top_k: int = 3,
) -> List[RankedFile]:
    """Rank files by importance using symbol density scoring.

    Args:
        symbols: List of symbols to analyze.
        edges: List of edges between symbols.
        first_party_priority: If True, apply tier weighting. Default True.
        top_k: Number of top symbols to sum for file score. Default 3.

    Returns:
        List of RankedFile objects sorted by importance (highest first).
    """
    if not symbols:
        return []

    # Compute symbol centrality
    raw_centrality = compute_centrality(symbols, edges)

    if first_party_priority:
        centrality = apply_tier_weights(raw_centrality, symbols)
    else:
        centrality = raw_centrality

    # Group by file
    by_file = group_symbols_by_file(symbols)

    # Compute file scores
    file_scores = compute_file_scores(by_file, centrality, top_k=top_k)

    # Sort files by score
    sorted_files = sorted(
        by_file.keys(),
        key=lambda f: (-file_scores.get(f, 0), f)
    )

    # Build ranked results
    results = []
    for rank, file_path in enumerate(sorted_files):
        file_symbols = by_file[file_path]
        # Sort symbols by centrality to get top ones
        sorted_syms = sorted(
            file_symbols,
            key=lambda s: -centrality.get(s.id, 0)
        )
        results.append(
            RankedFile(
                path=file_path,
                density_score=file_scores.get(file_path, 0),
                symbol_count=len(file_symbols),
                top_symbols=sorted_syms[:top_k],
                rank=rank,
            )
        )

    return results


def get_importance_threshold(
    centrality: Dict[str, float],
    percentile: float = 0.5,
) -> float:
    """Get the centrality score at a given percentile.

    Useful for marking "important" symbols (e.g., starred in output).

    Args:
        centrality: Centrality scores for symbols.
        percentile: Percentile threshold (0.0 to 1.0). Default 0.5 (median).

    Returns:
        The centrality score at the given percentile.
    """
    if not centrality:
        return 0.0

    scores = sorted(centrality.values(), reverse=True)
    index = int(len(scores) * (1 - percentile))
    index = max(0, min(index, len(scores) - 1))
    return scores[index]
