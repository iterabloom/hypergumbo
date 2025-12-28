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


# Symbol kinds to exclude from tiered output
# These have high centrality but don't represent useful code
EXCLUDED_KINDS = frozenset({
    "dependency",       # package.json, pyproject.toml dependencies
    "devDependency",    # package.json dev dependencies
    "file",             # file-level nodes (import targets)
    "target",           # Makefile targets
    "special_target",   # .PHONY and other special targets
    "project",          # project-level nodes
    "package",          # package.json package name
    "script",           # package.json scripts
    "event_subscriber", # CSS/JS event handlers (less useful in isolation)
    "class_selector",   # CSS class selectors
    "id_selector",      # CSS id selectors
})

# Path patterns indicating test files
TEST_PATH_PATTERNS = (
    "/tests/",
    "/test/",
    "/__tests__/",
    "_test.go",
    "_test.py",
    ".test.ts",
    ".test.js",
    ".test.tsx",
    ".test.jsx",
    ".spec.ts",
    ".spec.js",
    ".spec.tsx",
    ".spec.jsx",
    ".test-d.ts",
    ".test-d.tsx",
    "test_",           # Python test files: test_foo.py
    "/testfixtures/",  # Gradle test fixtures (case-insensitive match)
    "/inttest/",       # Gradle integration test source set
    "/integrationtest/",  # Alternative integration test naming
    "tests.java",      # Java test files: FooTests.java
    "test.java",       # Java test files: FooTest.java (but not TestFoo.java utilities)
)

# Path patterns indicating example/demo code
# Include both /examples/ and examples/ to handle absolute and relative paths
EXAMPLE_PATH_PATTERNS = (
    "/examples/",
    "/example/",
    "/demos/",
    "/demo/",
    "/samples/",
    "/sample/",
    "/playground/",
    "/tutorial/",
    "/tutorials/",
)


def _is_example_path(path: str) -> bool:
    """Check if a path represents example/demo code.

    Args:
        path: File path to check.

    Returns:
        True if the path appears to be example code.
    """
    path_lower = path.lower()
    # Check standard patterns (with leading slash)
    if any(pattern in path_lower for pattern in EXAMPLE_PATH_PATTERNS):
        return True
    # Also check if path starts with example directory (relative paths)
    return path_lower.startswith(("examples/", "example/", "demos/", "demo/",
                                   "samples/", "sample/", "playground/",
                                   "tutorial/", "tutorials/"))


def _is_test_path(path: str) -> bool:
    """Check if a path represents a test file.

    Args:
        path: File path to check.

    Returns:
        True if the path appears to be a test file.
    """
    path_lower = path.lower()
    return any(pattern in path_lower for pattern in TEST_PATH_PATTERNS)


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
    """

    target_coverage: float = 0.8
    max_symbols: int = 100
    min_symbols: int = 10
    top_words_count: int = 10
    top_paths_count: int = 5
    first_party_priority: bool = True


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

    # Sort by centrality (highest first)
    sorted_symbols = sorted(
        symbols,
        key=lambda s: (-centrality.get(s.id, 0), s.name)
    )

    # Compute total centrality
    total_centrality = sum(centrality.values())
    if total_centrality == 0:
        total_centrality = 1.0  # Avoid division by zero

    # Select by coverage
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


# Token estimation constants
# ~4 chars per token is a reasonable approximation for JSON with code
CHARS_PER_TOKEN = 4

# Overhead per node (JSON structure, keys, formatting)
# Estimated from typical node: {"id": "...", "name": "...", "kind": "...", ...}
TOKENS_PER_NODE_OVERHEAD = 50

# Overhead for behavior map shell (schema_version, view, metrics, etc.)
TOKENS_BEHAVIOR_MAP_OVERHEAD = 200

# Default tiers in tokens (k = 1000 tokens)
DEFAULT_TIERS = ["4k", "16k", "64k"]


def parse_tier_spec(spec: str) -> int:
    """Parse a tier specification into target tokens.

    Args:
        spec: Tier spec like "4k", "16k", "64000", etc.

    Returns:
        Target token count.

    Raises:
        ValueError: If spec cannot be parsed.
    """
    spec = spec.lower().strip()
    if spec.endswith("k"):
        return int(float(spec[:-1]) * 1000)
    return int(spec)


def estimate_node_tokens(node_dict: dict) -> int:
    """Estimate tokens for a serialized node.

    Args:
        node_dict: Node dictionary from Symbol.to_dict().

    Returns:
        Estimated token count.
    """
    # Rough estimate based on JSON serialization
    import json
    json_str = json.dumps(node_dict)
    return len(json_str) // CHARS_PER_TOKEN


def estimate_behavior_map_tokens(behavior_map: dict) -> int:
    """Estimate total tokens for a behavior map.

    Args:
        behavior_map: Full behavior map dictionary.

    Returns:
        Estimated token count.
    """
    import json
    json_str = json.dumps(behavior_map)
    return len(json_str) // CHARS_PER_TOKEN


def select_by_tokens(
    symbols: List[Symbol],
    edges: List[Edge],
    target_tokens: int,
    first_party_priority: bool = True,
    exclude_tests: bool = True,
    exclude_non_code: bool = True,
    deduplicate_names: bool = True,
    exclude_examples: bool = True,
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

    # Sort eligible symbols by centrality (highest first)
    sorted_symbols = sorted(
        eligible_symbols,
        key=lambda s: (-centrality.get(s.id, 0), s.name)
    )

    # Compute total centrality for coverage calculation
    total_centrality = sum(centrality.values())
    if total_centrality == 0:
        total_centrality = 1.0

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
