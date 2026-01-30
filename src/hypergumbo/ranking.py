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

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .ir import Symbol, Edge
from .paths import is_test_file
from .selection.filters import is_test_path

logger = logging.getLogger(__name__)

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


def apply_test_weights(
    centrality: Dict[str, float],
    symbols: List[Symbol],
    test_weight: float = 0.5,
) -> Dict[str, float]:
    """Apply test file weighting to centrality scores.

    Symbols in test files get their centrality reduced by test_weight.
    This causes production code to rank higher than test code while still
    including test code in the results.

    This is useful for reverse slicing where test callers are still relevant
    but production callers should be prioritized.

    Args:
        centrality: Centrality scores (possibly already tier-weighted).
        symbols: List of symbols (used to look up paths).
        test_weight: Multiplier for test file nodes (default 0.5).

    Returns:
        Dictionary mapping symbol ID to test-weighted centrality score.
    """
    symbol_paths = {s.id: s.path for s in symbols}
    weighted = {}
    for sid, score in centrality.items():
        path = symbol_paths.get(sid, "")
        if is_test_file(path):
            weighted[sid] = score * test_weight
        else:
            weighted[sid] = score
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


def compute_transitive_test_coverage(
    test_ids: set[str],
    target_ids: set[str],
    call_edges: List[tuple[str, str]],
) -> Dict[str, set[str]]:
    """Compute which tests transitively reach each target using BFS.

    This is the core test coverage algorithm shared between sketch.py and
    cmd_test_coverage. Uses BFS from each test symbol to find all transitively
    reachable production symbols.

    If test_foo() calls helper() which calls core(), both helper and core
    are considered "tested" by test_foo.

    Args:
        test_ids: Set of symbol IDs that are test functions/methods.
        target_ids: Set of symbol IDs that are production functions/methods.
        call_edges: List of (src, dst) tuples representing call relationships.

    Returns:
        Dictionary mapping target_id to set of test_ids that reach it.
    """
    from collections import deque

    # Build call graph (src → list of dst)
    call_graph: Dict[str, List[str]] = {}
    for src, dst in call_edges:
        if src and dst:
            if src not in call_graph:
                call_graph[src] = []
            call_graph[src].append(dst)

    # For each test symbol, BFS to find all transitively reachable targets
    tests_per_target: Dict[str, set[str]] = {tid: set() for tid in target_ids}

    for test_id in test_ids:
        # BFS from this test symbol
        visited: set[str] = set()
        queue: deque[str] = deque([test_id])
        visited.add(test_id)

        while queue:
            current = queue.popleft()
            for neighbor in call_graph.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        # All targets reachable from this test are "tested" by it
        for target_id in visited & target_ids:
            tests_per_target[target_id].add(test_id)

    return tests_per_target


def compute_raw_in_degree(
    symbols: List[Symbol],
    edges: List[Edge],
) -> Dict[str, int]:
    """Compute raw in-degree counts for each symbol.

    Unlike compute_centrality() which normalizes to 0-1, this returns
    raw counts (number of incoming edges). Useful when you need absolute
    thresholds like "at least 2 callers".

    Args:
        symbols: List of symbols to count in-degree for.
        edges: List of edges (calls, imports) between symbols.

    Returns:
        Dictionary mapping symbol ID to raw in-degree count.
    """
    symbol_ids = {s.id for s in symbols}
    in_degree: Dict[str, int] = dict.fromkeys(symbol_ids, 0)

    for edge in edges:
        target = edge.dst
        if target and target in in_degree:
            in_degree[target] += 1

    return in_degree


def compute_file_loc(file_path: Path) -> int:
    """Count lines of code in a file.

    Args:
        file_path: Path to the file.

    Returns:
        Line count, or 0 if file cannot be read.
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def compute_symbol_importance_density(
    by_file: Dict[str, List[Symbol]],
    in_degree: Dict[str, int],
    repo_root: Path,
    min_loc: int = 5,
) -> Dict[str, float]:
    """Compute symbol importance density for each file.

    Density = sum(raw_in_degree of symbols in file) / LOC

    Files with very few lines (below min_loc threshold) get a score of 0
    to avoid small files with one important symbol from dominating.

    Args:
        by_file: Symbols grouped by file path.
        in_degree: Raw in-degree counts for each symbol (from compute_raw_in_degree).
        repo_root: Repository root path for resolving file paths.
        min_loc: Minimum lines of code threshold. Files below this get 0 score.

    Returns:
        Dictionary mapping file paths to importance density scores.
    """
    density_scores: Dict[str, float] = {}

    for file_path, symbols in by_file.items():
        # Resolve absolute path
        path_obj = Path(file_path)
        if not path_obj.is_absolute():
            abs_path = repo_root / file_path
        else:
            abs_path = path_obj

        loc = compute_file_loc(abs_path)

        if loc < min_loc:
            density_scores[file_path] = 0.0
            continue

        # Sum raw in-degree of all symbols in file
        total_in_degree = sum(in_degree.get(s.id, 0) for s in symbols)
        density_scores[file_path] = total_in_degree / loc

    return density_scores


@dataclass
class CentralityResult:
    """Result from symbol mention centrality computation.

    Attributes:
        normalized_scores: Dict mapping file paths to normalized centrality scores
            (in-degree sum / file size). Used for ranking files.
        symbols_per_file: Dict mapping file paths to sets of symbol names mentioned
            in that file. Used for accurate representativeness (unique symbols).
        name_to_in_degree: Dict mapping symbol names to their in-degree values.
            Used to compute in-degree sum for unique symbols.
    """
    normalized_scores: Dict[Path, float]
    symbols_per_file: Dict[Path, set[str]]
    name_to_in_degree: Dict[str, int]


def compute_symbol_mention_centrality_batch(
    files: List[Path],
    symbols: List[Symbol],
    in_degree: Dict[str, int],
    min_in_degree: int = 2,
    max_file_size: int = 100 * 1024,
    progress_callback: "callable | None" = None,
) -> CentralityResult:
    """Compute symbol mention centrality for multiple files efficiently.

    Uses parallelized Python regex with a combined alternation pattern for
    O(files) complexity instead of O(files * symbols).

    Args:
        files: List of file paths to scan.
        symbols: List of symbols to search for.
        in_degree: Raw in-degree counts for each symbol.
        min_in_degree: Only match symbols with at least this many callers.
        max_file_size: Skip files larger than this (bytes). Default 100KB.
        progress_callback: Optional callback(current, total) for progress.

    Returns:
        CentralityResult containing:
        - normalized_scores: For ranking (in-degree/filesize)
        - symbols_per_file: Per-file sets of mentioned symbol names
        - name_to_in_degree: Symbol name to in-degree mapping
    """
    # Filter symbols by in-degree threshold and dedupe names
    eligible_symbols = [
        s for s in symbols
        if in_degree.get(s.id, 0) >= min_in_degree
    ]

    # Build name -> total in-degree map (sum in-degrees for all symbols with same name)
    # When a doc mentions a name, it documents all symbols with that name,
    # so we sum their in-degrees (analogous to how Source Files counts each symbol).
    name_to_in_degree: Dict[str, int] = {}
    for s in eligible_symbols:
        s_in_degree = in_degree.get(s.id, 0)
        name_to_in_degree[s.name] = name_to_in_degree.get(s.name, 0) + s_in_degree

    if not files:
        return CentralityResult(
            normalized_scores={},
            symbols_per_file={},
            name_to_in_degree=name_to_in_degree,
        )

    if not name_to_in_degree:
        # No eligible symbols, return zeros
        return CentralityResult(
            normalized_scores=dict.fromkeys(files, 0.0),
            symbols_per_file={f: set() for f in files},
            name_to_in_degree=name_to_in_degree,
        )

    logger.debug(
        "centrality: processing %d files with %d patterns",
        len(files),
        len(name_to_in_degree),
    )
    return _compute_centrality_with_python(
        files, name_to_in_degree, max_file_size, progress_callback
    )


def _compute_centrality_with_python(
    files: List[Path],
    name_to_in_degree: Dict[str, int],
    max_file_size: int,
    progress_callback: "callable | None",
) -> CentralityResult:
    """Compute centrality using Python regex (fallback path).

    Uses a single combined regex pattern for efficiency: O(files) instead of
    O(files * symbols). The pattern matches all symbol names in one pass.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Build combined pattern ONCE: \b(name1|name2|...)\b
    # This makes each file search O(1) instead of O(symbols)
    escaped_names = [re.escape(name) for name in name_to_in_degree.keys()]
    if escaped_names:
        combined_pattern = re.compile(r'\b(' + '|'.join(escaped_names) + r')\b')
    else:  # pragma: no cover - caller already handles empty symbols
        combined_pattern = None

    def _compute_one(f: Path) -> tuple[Path, float, set[str]]:
        """Returns (path, normalized_score, matched_names)."""
        try:
            file_size = f.stat().st_size
            if file_size > max_file_size:
                return (f, 0.0, set())
            content = f.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return (f, 0.0, set())

        if not content:  # pragma: no cover - empty file
            return (f, 0.0, set())

        if combined_pattern is None:  # pragma: no cover - no symbols
            return (f, 0.0, set())

        # Find all matches in one pass (O(1) regex operation)
        matched_names = set(combined_pattern.findall(content))

        # Sum in-degrees of matched symbols
        total_in_degree = sum(
            name_to_in_degree.get(name, 0) for name in matched_names
        )

        score = total_in_degree / len(content) if content else 0.0
        return (f, score, matched_names)

    normalized_scores: Dict[Path, float] = {}
    symbols_per_file: Dict[Path, set[str]] = {}

    if progress_callback:
        progress_callback(0, len(files))

    max_workers = min(8, len(files)) if files else 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_compute_one, f): f for f in files}
        for i, future in enumerate(as_completed(futures)):
            path, score, matched = future.result()
            normalized_scores[path] = score
            symbols_per_file[path] = matched
            if progress_callback:
                progress_callback(i + 1, len(files))

    return CentralityResult(
        normalized_scores=normalized_scores,
        symbols_per_file=symbols_per_file,
        name_to_in_degree=name_to_in_degree,
    )


def compute_symbol_mention_centrality(
    file_path: Path,
    symbols: List[Symbol],
    in_degree: Dict[str, int],
    min_in_degree: int = 2,
    max_file_size: int = 100 * 1024,
) -> float:
    """Compute symbol mention centrality score for a file.

    Scans file content for symbol name mentions (with word boundaries)
    and sums the in-degree of matched symbols, normalized by character count.

    This helps rank non-source files (docs, configs, templates) by how
    much they reference important code symbols.

    Args:
        file_path: Path to the file to scan.
        symbols: List of symbols to search for.
        in_degree: Raw in-degree counts for each symbol.
        min_in_degree: Only match symbols with at least this many callers.
        max_file_size: Skip files larger than this (bytes). Default 100KB.

    Returns:
        Symbol mention centrality score (higher = more references to important symbols).
    """
    try:
        file_size = file_path.stat().st_size
        if file_size > max_file_size:
            return 0.0
        content = file_path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return 0.0

    if not content:
        return 0.0

    # Filter symbols by raw in-degree threshold
    eligible_symbols = [
        s for s in symbols
        if in_degree.get(s.id, 0) >= min_in_degree
    ]

    # Match symbol names with word boundaries
    total_in_degree = 0
    matched_names: set[str] = set()

    for sym in eligible_symbols:
        if sym.name not in matched_names:
            # Word-boundary match
            pattern = r'\b' + re.escape(sym.name) + r'\b'
            if re.search(pattern, content):
                total_in_degree += in_degree.get(sym.id, 0)
                matched_names.add(sym.name)

    return total_in_degree / len(content) if content else 0.0
