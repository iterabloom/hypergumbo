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


def select_by_coverage(
    symbols: List[Symbol],
    edges: List[Edge],
    config: CompactConfig,
) -> CompactResult:
    """Select symbols by centrality coverage with residual summarization.

    Selects the fewest symbols needed to capture target_coverage of total
    centrality mass, respecting min/max bounds. Summarizes omitted symbols
    with bag-of-words analysis for semantic flavor.

    Args:
        symbols: All symbols to consider.
        edges: Edges for centrality computation.
        config: Compact configuration.

    Returns:
        CompactResult with included symbols and omitted summary.
    """
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

    for sym in sorted_symbols:
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

    # Compute omitted symbols
    included_ids = {s.id for s in included}
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
) -> dict:
    """Format a behavior map in compact mode.

    Replaces the full nodes list with a compact selection plus summary.

    Args:
        behavior_map: Original behavior map dictionary.
        symbols: Symbol objects (for analysis).
        edges: Edge objects (for centrality).
        config: Compact configuration.

    Returns:
        Modified behavior map with compact output.
    """
    result = select_by_coverage(symbols, edges, config)

    # Create compact output
    compact_map = dict(behavior_map)
    compact_map["view"] = "compact"
    compact_map["nodes"] = [s.to_dict() for s in result.included.symbols]
    compact_map["nodes_summary"] = result.to_dict()

    # Keep edges that connect included nodes
    included_ids = {s.id for s in result.included.symbols}
    compact_map["edges"] = [
        e for e in behavior_map.get("edges", [])
        if e.get("src") in included_ids or e.get("dst") in included_ids
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

    Returns:
        CompactResult with symbols fitting the budget.
    """
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

    for sym in sorted_symbols:
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

    # Compute omitted symbols
    included_ids = {s.id for s in included}
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
) -> dict:
    """Format a behavior map for a specific token tier.

    Args:
        behavior_map: Original full behavior map.
        symbols: Symbol objects.
        edges: Edge objects.
        target_tokens: Target token budget.

    Returns:
        Behavior map formatted for the token tier.
    """
    result = select_by_tokens(symbols, edges, target_tokens)

    # Create tiered output
    tiered_map = dict(behavior_map)
    tiered_map["view"] = "tiered"
    tiered_map["tier_tokens"] = target_tokens
    tiered_map["nodes"] = [s.to_dict() for s in result.included.symbols]
    tiered_map["nodes_summary"] = result.to_dict()

    # Keep edges that connect included nodes
    included_ids = {s.id for s in result.included.symbols}
    tiered_map["edges"] = [
        e for e in behavior_map.get("edges", [])
        if e.get("src") in included_ids or e.get("dst") in included_ids
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
