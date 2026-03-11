"""Symbol and file ranking utilities for hypergumbo output.

This module provides reusable ranking functions that determine which symbols
and files are most important in a codebase. These utilities power the
thoughtful ordering in sketch output and can be used by slice, run, and
other modes.

How It Works
------------
Ranking uses multiple signals combined:

1. **Centrality**: Bidirectional centrality uses in-degree as the base
   signal (how many other symbols reference this one), boosted by a
   log-scaled out-degree factor.  This rewards *connectors* — symbols
   that are both depended-on and depend-on-others (e.g., QuerySet, Model)
   — over pure *sinks* (e.g., exception classes, utility decorators) that
   have high in-degree but near-zero out-degree.  Pure sinks (out=0) get
   a 0.5x multiplier instead of 1.0x, penalizing popular leaf utilities
   (e.g., Elm ``map`` with in=71, out=0) that are plumbing.  Extreme
   in-degree is
   saturated via ``hub_threshold`` to prevent infrastructure utilities
   (error sentinels, loggers) from dominating rankings.  Cross-file edges
   count as 1.0 while within-file edges are dampened (default 0.3x) to
   prevent local variables referenced many times within a single file
   (e.g., ``pSpec`` with 96 in-degree) from outranking architecturally
   important symbols referenced across many files.

2. **Supply Chain Tier Weighting**: First-party code (tier 1) gets a 2x
   boost, internal dependencies (tier 2) get 1.5x, external dependencies
   (tier 3) get 1x, and derived/generated code (tier 4) gets 0x. This
   ensures your code ranks higher than bundled dependencies.

3. **File Density Scoring**: Files are scored by the sum of their top-K
   symbol scores, not just the maximum. This rewards files with multiple
   important symbols rather than one outlier.

4. **Utility Symbol Dampening**: Symbols with infrastructure-utility names
   (Logger, Clock, Metrics, empty, size, toString, __repr__) get 90%
   centrality reduction. This supplements hub saturation (which only caps
   in-degree) with name-based detection of symbols that are plumbing, not
   architecture. Addresses INV-mahap: DefaultClock.Now with in-degree 474
   dominated rankings because hub saturation at 100 only reduced effective_in
   to ~106.

5. **Trivial Sink Dampening**: Symbols with out_degree <= 1 AND
   lines_of_code <= 5 get 90% centrality reduction.  These are trivial
   accessors/stubs that accumulate high in-degree through ubiquitous use
   but have no architectural significance.  Addresses bakeoff cohorts 16-17
   findings: Timer.Duration (in=98, LoC=3), noopMetric.Inc (in=109, LoC=1),
   CheckError (in=195, LoC=3), syncTask.name (in=109, LoC=2) all dominated
   rankings despite being plumbing.

6. **Sibling Implementation Dampening**: When many methods share the same
   name (e.g., 19 ``Notifier.Notify`` variants — one per notification
   channel in alertmanager), they flood top rankings even after common
   method name dampening.  Within each name group of 6+ methods, the top 3
   by score keep full weight; the rest get a 0.15x multiplier.  This
   ensures users see 2-3 representative implementations, not 19 variants
   of the same interface method.

7. **Edge Confidence Filtering**: Low-confidence edges (e.g., inferred
   method calls with confidence <0.5) are excluded from centrality
   computation. This prevents method name collisions from inflating
   in-degree: DirLocker.Lock gets 255 false in-degree from unrelated
   .Lock() calls, MemoryCache.get gets 228 from unrelated .get() calls.
   Filtering at confidence 0.5 eliminates these artifacts.

Why These Heuristics
--------------------
- **Centrality** captures structural importance: core abstractions and
  integration points (connectors) naturally score higher than pure sinks
  (exception classes, utility decorators imported everywhere).

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
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .ir import Symbol, Edge
from .paths import is_test_file, is_test_node
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
    hub_threshold: int | None = None,
    within_file_weight: float = 1.0,
    max_per_file_in: int | None = None,
) -> Dict[str, float]:
    """Compute symbol importance using bidirectional centrality.

    Uses in-degree as the base signal (symbols called by many others are
    important), boosted by a log-scaled out-degree factor.  This rewards
    *connectors* — symbols that are both depended-on and depend-on-others —
    over pure *sinks* (exception classes, utility decorators) that have high
    in-degree but near-zero out-degree.

    The formula is::

        out_multiplier = (1 + ln(1 + out_degree)) if out_degree > 0 else 0.5
        raw_score = effective_in_degree * out_multiplier

    Pure sinks (out_degree=0) get a 0.5x multiplier instead of the standard
    1.0x floor.  This ensures popular leaf utilities (e.g., ``map`` with
    in=71, out=0) rank below architectural connectors with the same
    in-degree.

    When ``hub_threshold`` is set, in-degree is saturated for symbols above
    the threshold to prevent infrastructure utilities (error sentinels,
    loggers, DB accessors) from dominating rankings.  The saturation formula
    is::

        effective_in = threshold + ln(1 + in_degree - threshold)

    This is nearly a hard cap: a symbol with 200 in-edges and threshold 100
    gets effective_in ≈ 104.6 instead of 200.  The log term preserves
    ordering among hubs without letting raw degree dominate.  Saturation
    happens *before* normalization, which is critical — post-normalization
    dampening cannot flip relative rankings.

    When ``within_file_weight`` is less than 1.0, edges where source and
    destination are in the same file contribute less to in-degree.  This
    prevents local variables and helpers referenced many times within a
    single file from outranking architecturally important symbols that are
    referenced across many files.  Cross-file edges always contribute 1.0.

    When ``max_per_file_in`` is set, each unique source file can contribute
    at most this many edges worth of in-degree to any target symbol.  This
    prevents a utility function called 50 times from 2 files from outranking
    an architecturally important function called 15 times from 15 files.

    Scores are normalized to 0-1 after computation.

    Args:
        symbols: List of symbols to rank.
        edges: List of edges (calls, imports) between symbols.
        hub_threshold: In-degree above which saturation applies. None
            disables saturation.  Default None (off for backward compat).
            ``rank_symbols`` passes 100.
        within_file_weight: Weight for within-file edges (0.0-1.0).
            Cross-file edges always count as 1.0.  Default 1.0
            (all edges equal, backward compatible).  ``rank_symbols``
            passes 0.3 to dampen local-variable inflation.
        max_per_file_in: Maximum in-degree contribution from any single
            source file. None disables capping. ``rank_symbols`` passes 5.

    Returns:
        Dictionary mapping symbol ID to centrality score (0-1 normalized).
    """
    symbol_ids = {s.id for s in symbols}
    in_degree: Dict[str, float] = dict.fromkeys(symbol_ids, 0.0)
    out_degree: Dict[str, int] = dict.fromkeys(symbol_ids, 0)

    # Build symbol-to-path lookup for cross-file detection and per-file capping
    use_file_weighting = within_file_weight < 1.0 or max_per_file_in is not None
    if use_file_weighting:
        symbol_path: Dict[str, str] = {s.id: s.path for s in symbols}

    if max_per_file_in is not None:
        # Track per-(target, source_file) edge counts for capping
        per_file_counts: Dict[str, Dict[str, int]] = {}

    for edge in edges:
        target = edge.dst
        if target and target in in_degree:
            if use_file_weighting:
                src_path = symbol_path.get(edge.src, "")
                dst_path = symbol_path.get(target, "")
                weight = within_file_weight if (src_path and src_path == dst_path) else 1.0

                # Per-file in-degree capping
                if max_per_file_in is not None and src_path:
                    file_counts = per_file_counts.setdefault(target, {})
                    count = file_counts.get(src_path, 0)
                    if count >= max_per_file_in:
                        weight = 0.0  # Capped — don't count this edge
                    else:
                        file_counts[src_path] = count + 1

                in_degree[target] += weight
            else:
                in_degree[target] += 1
        source = edge.src
        if source and source in out_degree:
            out_degree[source] += 1

    # Bidirectional score: in-degree boosted by log-scaled out-degree
    scores: Dict[str, float] = {}
    for sid in symbol_ids:
        ind = in_degree[sid]
        outd = out_degree[sid]

        # Saturate extreme in-degree to prevent infrastructure hubs from
        # dominating.  The log term preserves ordering among hubs.
        if hub_threshold is not None and ind > hub_threshold:
            effective_in = hub_threshold + math.log(1 + ind - hub_threshold)
        else:
            effective_in = ind

        # Pure sinks (out_degree=0) get a dampened multiplier (0.5) vs
        # the normal floor of 1.0.  This penalizes popular leaf utilities
        # (e.g., Elm map with in=71, out=0) that accumulate high in-degree
        # through ubiquitous use but have no outgoing dependencies — they're
        # plumbing, not connectors.  Symbols with out_degree >= 1 get the
        # standard (1 + ln(1 + out_degree)) boost.
        out_multiplier = (1 + math.log(1 + outd)) if outd > 0 else 0.5
        scores[sid] = effective_in * out_multiplier

    # Normalize to 0-1 range
    max_score = max(scores.values()) if scores else 1.0
    if max_score == 0:
        max_score = 1.0

    return {sid: score / max_score for sid, score in scores.items()}


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


# Path patterns for code that is structurally connected but operationally
# irrelevant to runtime architecture (e.g., database migrations).
_NOISE_PATH_SEGMENTS = (
    "/db/migrate/",
    "/migrations/",
)


def _is_noise_path(path: str) -> bool:
    """Check if path represents noise code like database migrations.

    Migrations are structurally connected (Migration#up dispatches to all
    subclass#up methods) but run once and are irrelevant to understanding
    runtime architecture. De-weighting them prevents inflation of centrality
    rankings.
    """
    path_lower = path.lower()
    # Check for segments (handles both absolute and relative paths)
    for seg in _NOISE_PATH_SEGMENTS:
        if seg in path_lower:
            return True
    # Also check path starts (for relative paths like "db/migrate/...")
    if path_lower.startswith("db/migrate/") or path_lower.startswith("migrations/"):
        return True
    return False


def apply_noise_weights(
    centrality: Dict[str, float],
    symbols: List[Symbol],
    noise_weight: float = 0.1,
) -> Dict[str, float]:
    """Apply noise path weighting to centrality scores.

    Symbols in noise paths (database migrations, etc.) get their centrality
    reduced by noise_weight. This prevents migration classes from inflating
    centrality rankings despite being operationally irrelevant to runtime
    architecture.

    Args:
        centrality: Centrality scores (possibly already tier-weighted).
        symbols: List of symbols (used to look up paths).
        noise_weight: Multiplier for noise path nodes (default 0.1).

    Returns:
        Dictionary mapping symbol ID to noise-weighted centrality score.
    """
    symbol_paths = {s.id: s.path for s in symbols}
    weighted = {}
    for sid, score in centrality.items():
        path = symbol_paths.get(sid, "")
        if _is_noise_path(path):
            weighted[sid] = score * noise_weight
        else:
            weighted[sid] = score
    return weighted


# Patterns for detecting infrastructure utility symbols by name.
# These symbols have high in-degree but low domain relevance — they're
# plumbing, not architecture.  Matching is case-insensitive.
#
# Categories:
#   - Logging classes: Logger, getLogger, log_message
#   - Logging methods: log, warn, error, debug, info, trace, fatal (+ Go fmt variants)
#   - Timing: Clock, DefaultClock, SystemClock
#   - Metrics: Metrics, MetricsCollector, metricsRecorder
#   - Error sentinels: ErrNotFound, ErrTimeout (Go convention)
#   - Exception/error classes: IOException, ValueError, GuacamoleServerException
#   - STL accessors: empty, size, begin, end, length
#   - Boilerplate: toString, hashCode, equals, __repr__, __str__, __hash__, __eq__
_UTILITY_SYMBOL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)logger$"),          # Logger, AppLogger, getLogger
    re.compile(r"(?i)^get_?logger$"),    # getLogger, get_logger
    re.compile(r"(?i)^log_"),            # log_message, log_error
    # Logging method names — exact match with optional format suffix (f/ln).
    # Catches: log, warn, error, debug, info, trace, fatal, Errorf, Warnln, Printf.
    # Avoids false positives: login, logout, dialog, catalog, handleError, UserInfo,
    # information, debugger, traceback, warningLevel, fatalError, ErrorHandler.
    re.compile(r"(?i)^(?:log|warn|error|debug|info|trace|fatal|print)(?:f|ln)?$"),
    re.compile(r"(?i)clock$"),           # Clock, DefaultClock, SystemClock
    re.compile(r"^now$"),                 # now() time accessor (e.g., Log.now)
    re.compile(r"(?i)^metrics"),         # Metrics, MetricsCollector
    re.compile(r"(?i)^err[A-Z_]"),       # ErrNotFound, ErrTimeout, err_invalid
    # Exception/error classes: thrown/caught infrastructure, not domain logic.
    # Catches: IOException, GuacamoleServerException, ValueError, TypeError.
    # Avoids: ExceptionHandler, ErrorHandler, ErrorResponse, ErrorBoundary.
    re.compile(r"(?i)Exception$"),       # Java/C# exception classes
    re.compile(r"^[A-Z].*Error$"),       # PascalCase error classes (ValueError, TypeError)
    re.compile(r"^(?:empty|size|begin|end|length|capacity)$"),  # STL accessors
    re.compile(r"^(?:insert|erase|push_back|pop_back|front|back|clear|swap|reserve|resize|at)$"),
    re.compile(r"^(?:toString|hashCode|equals|compareTo|clone|finalize)$"),  # Java boilerplate
    re.compile(r"^__(?:repr|str|hash|eq|ne|lt|le|gt|ge|len|bool|init|del)__$"),  # Python dunder
    # Rust derive-generated methods: Clone, Default, Debug, Hash, PartialEq, Into/From.
    # These have high in-degree from derive macros but low architectural relevance.
    re.compile(r"^(?:fmt|default|into|from|as_ref|deref|drop|unwrap)$"),
    # Functional programming primitives: generic collection operations with
    # high fan-in but no architectural relevance.  Elm's map/filter/reduce
    # and JS Array methods dominate rankings in polyglot repos.
    re.compile(r"^(?:map|filter|reduce|forEach|flatMap|fold|foldl|foldr|zip|concat|apply)$"),
    # Test fixtures and doubles: mock/fake/stub/dummy classes used in tests.
    # These have high in-degree from test code but zero production relevance.
    # Catches: DummyNetworkAdapter, MockClient, FakeServer, StubRepository.
    # Case-insensitive prefix, but suffix must be uppercase (to exclude "Mockingbird").
    re.compile(r"^(?:[Dd]ummy|[Mm]ock|[Ff]ake|[Ss]tub|[Ss]py)[A-Z_]"),
]


def is_utility_symbol(name: str) -> bool:
    """Check if a symbol name looks like infrastructure utility.

    Infrastructure symbols (loggers, clocks, metrics, error sentinels, STL
    accessors, boilerplate methods) are plumbing — high in-degree but low
    domain relevance. This supplements is_utility_file (path-based) with
    name-based detection.

    Checks both the full name and the unqualified part (after the last
    separator: `::`, `.`, `#`, `\\`). This catches derive-generated methods
    like `AllocatedNum::clone` where only the unqualified `clone` matches.

    Args:
        name: Symbol name to check.

    Returns:
        True if the name matches a known utility pattern.
    """
    if any(p.search(name) for p in _UTILITY_SYMBOL_PATTERNS):
        return True
    # Check unqualified name for qualified symbols (Rust ::, Java/C# ., Ruby #)
    for sep in ("::", ".", "#", "\\"):
        if sep in name:
            unqualified = name.rsplit(sep, 1)[-1]
            if any(p.search(unqualified) for p in _UTILITY_SYMBOL_PATTERNS):
                return True
    return False


def _is_helper_file(path: str) -> bool:
    """Check if the file path or basename suggests a helper/utility module.

    Files named ``*_helpers.*``, ``*_utils.*``, ``*_util.*``, ``helpers.*``,
    ``utils.*``, ``util.*``, or located in ``utils/`` or ``helpers/``
    directories are infrastructure plumbing whose symbols should be dampened
    in rankings.  E.g. ``openapi_helpers.go`` whose ``createYAMLNode``,
    ``responsesWithErrorExamples`` dominate rankings in prometheus, or Elm
    ``Utils/Api.elm`` whose ``map`` dominated rankings in alertmanager.
    """
    if not path:
        return False
    path_lower = path.lower()
    if "/utils/" in path_lower or "/helpers/" in path_lower:
        return True
    basename = path_lower.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return (
        basename.endswith("_helpers")
        or basename.endswith("_utils")
        or basename.endswith("_util")
        or basename in ("helpers", "utils", "util")
        # Test fixture files: conftest, fixtures, factories, test_helpers
        or basename in ("conftest", "fixtures", "factories")
        or basename.startswith("test_helper")
    )


# Concepts from framework YAML patterns that indicate infrastructure logging.
# Symbols enriched with these concepts by logging-conventions.yaml get dampened.
_LOGGING_CONCEPTS: frozenset[str] = frozenset({"logging", "logger"})


def _has_logging_concept(symbol: "Symbol | None") -> bool:
    """Check if a symbol has a logging-related framework concept."""
    if symbol is None or not symbol.meta:
        return False
    for concept in symbol.meta.get("concepts", []):
        if isinstance(concept, dict) and concept.get("concept") in _LOGGING_CONCEPTS:
            return True
    return False


def apply_utility_symbol_weights(
    centrality: Dict[str, float],
    symbols: List[Symbol],
    utility_weight: float = 0.1,
) -> Dict[str, float]:
    """Apply utility symbol weighting to centrality scores.

    Symbols whose names match infrastructure utility patterns (loggers,
    clocks, metrics, STL accessors) OR whose framework concepts indicate
    logging infrastructure get their centrality reduced by utility_weight.
    This prevents infrastructure hubs from dominating rankings even after
    hub saturation dampening.

    Two detection mechanisms (INV-mosag):
    1. Name-based: regex patterns in _UTILITY_SYMBOL_PATTERNS
    2. Concept-based: symbols enriched with "logging" concept by
       logging-conventions.yaml (catches custom logger wrappers,
       log bridges, logger factory classes)

    Args:
        centrality: Centrality scores (possibly already tier-weighted).
        symbols: List of symbols (used to look up names).
        utility_weight: Multiplier for utility symbol nodes (default 0.1).

    Returns:
        Dictionary mapping symbol ID to utility-weighted centrality score.
    """
    symbol_lookup = {s.id: s for s in symbols}
    weighted = {}
    for sid, score in centrality.items():
        sym = symbol_lookup.get(sid)
        name = sym.name if sym else ""
        path = sym.path if sym else ""
        if (
            is_utility_symbol(name)
            or _has_logging_concept(sym)
            or _is_helper_file(path)
        ):
            weighted[sid] = score * utility_weight
        else:
            weighted[sid] = score
    return weighted


def apply_trivial_sink_weights(
    centrality: Dict[str, float],
    symbols: List[Symbol],
    edges: List[Edge],
    sink_weight: float = 0.1,
    max_out_degree: int = 1,
    max_loc: int = 5,
    pure_sink_max_loc: int = 20,
) -> Dict[str, float]:
    """Dampen centrality for trivial sinks — short-bodied symbols with near-zero out-degree.

    Bakeoff cohorts 16-17 showed that trivial accessors/stubs dominate
    rankings purely through raw in-degree even after hub saturation and
    bidirectional scoring.  The root cause: when the highest in-degree
    symbols in a codebase are ALL pure sinks, there are no connectors
    with enough in-degree to overcome the sink's base score.

    Examples dampened by this function:
      - Timer.Duration (in=98, out=0, LoC=3) — 1-line accessor
      - noopMetric.Inc (in=109, out=0, LoC=1) — null object stub
      - CheckError (in=195, out=1, LoC=3) — error-handling wrapper
      - syncTask.name (in=109, out=0, LoC=2) — trivial accessor
      - node_text (in=496, out=0, LoC=11) — tree-sitter helper with docstring
      - find_child_by_type (in=291, out=0, LoC=16) — utility with docstring

    Two tiers of detection:
      1. Near-sinks (out_degree <= 1, loc <= max_loc=5): strict, catches
         accessors/stubs regardless of whether they have 0 or 1 edge out.
      2. Pure sinks (out_degree == 0, loc <= pure_sink_max_loc=20): relaxed
         LOC for truly zero-out-degree functions.  These call nothing — even
         with docstrings making them 10-20 lines, they're leaf helpers, not
         architectural functions.

    Args:
        centrality: Centrality scores to weight.
        symbols: Symbol list (used for lines_of_code).
        edges: Edge list (used to compute out-degree).
        sink_weight: Multiplier for trivial sinks (default 0.1).
        max_out_degree: Maximum out-degree to qualify as sink (default 1).
        max_loc: Maximum lines of code for near-sinks (default 5).
        pure_sink_max_loc: Maximum lines of code for pure sinks with
            out_degree == 0 (default 20).  Higher than max_loc because
            pure sinks are definitively non-architectural — they call
            nothing.  The higher threshold catches utility helpers whose
            spans include docstrings (e.g., node_text at 11 lines).

    Returns:
        Dictionary mapping symbol ID to dampened centrality score.
    """
    # Compute out-degree from edges
    out_degree: Dict[str, int] = {}
    for edge in edges:
        if edge.src:
            out_degree[edge.src] = out_degree.get(edge.src, 0) + 1

    # Build lines-of-code lookup
    symbol_loc: Dict[str, int] = {}
    for s in symbols:
        loc = s.lines_of_code
        if loc is None and s.span is not None:
            loc = s.span.end_line - s.span.start_line + 1
        symbol_loc[s.id] = loc if loc is not None else 0

    weighted = {}
    for sid, score in centrality.items():
        outd = out_degree.get(sid, 0)
        loc = symbol_loc.get(sid, 0)
        if outd <= max_out_degree and loc <= max_loc:
            weighted[sid] = score * sink_weight
        elif outd == 0 and loc <= pure_sink_max_loc:
            weighted[sid] = score * sink_weight
        else:
            weighted[sid] = score
    return weighted


def apply_common_method_name_weights(
    centrality: Dict[str, float],
    symbols: List[Symbol],
    name_threshold: int = 10,
    floor: float = 0.1,
) -> Dict[str, float]:
    """Dampen centrality for methods whose name appears on many distinct symbols.

    When a method name (e.g., 'execute', 'call', 'perform') is defined on
    many distinct classes/modules, receiver_call edges with confidence 0.75
    create massive false positive in-degree because callers of ANY method
    with that name get attributed to the same target.  This function applies
    a dampening factor proportional to the number of symbols sharing the name.

    Bakeoff finding WI-luvaj: PartialToViewsMappings#execute (in-degree 2894)
    dominated rankings because 'execute' appears on 50+ classes and every
    unresolved `.execute()` call was attributed to it.

    Detection:
    - Count distinct symbols with each method/function name
    - Only counts function and method kinds (not class, module, etc.)
    - If count > name_threshold (default 10), apply dampening:
      factor = max(floor, name_threshold / count)
    - e.g., 'execute' on 50 symbols → 10/50 = 0.2x
    - e.g., 'process' on 15 symbols → 10/15 = 0.67x
    - e.g., 'analyze_data' on 1 symbol → no dampening

    Args:
        centrality: Centrality scores to weight.
        symbols: Symbol list (used for name counts).
        name_threshold: Minimum symbol count before dampening applies (default 10).
        floor: Minimum dampening factor (default 0.1).

    Returns:
        Dictionary mapping symbol ID to dampened centrality score.
    """
    # Count how many distinct symbols define each method name
    # Only count callable kinds (function, method) — class names are
    # expected to repeat across modules
    _CALLABLE_KINDS = {"function", "method"}

    name_counts: Dict[str, int] = {}
    symbol_lookup: Dict[str, Symbol] = {}
    for s in symbols:
        symbol_lookup[s.id] = s
        if s.kind in _CALLABLE_KINDS:
            name_counts[s.name] = name_counts.get(s.name, 0) + 1

    weighted = {}
    for sid, score in centrality.items():
        sym = symbol_lookup.get(sid)
        if sym is None or sym.kind not in _CALLABLE_KINDS:
            weighted[sid] = score
            continue

        count = name_counts.get(sym.name, 1)
        if count > name_threshold:
            factor = max(floor, name_threshold / count)
            weighted[sid] = score * factor
        else:
            weighted[sid] = score

    return weighted


def apply_sibling_impl_weights(
    centrality: Dict[str, float],
    symbols: List[Symbol],
    top_k: int = 3,
    min_group_size: int = 6,
    tail_factor: float = 0.15,
) -> Dict[str, float]:
    """Dampen centrality for excess interface implementation siblings.

    When many methods share the same name (e.g., 19 Notifier.Notify variants
    in alertmanager — one per notification channel), they flood top rankings
    even after common-method-name dampening.  This function groups same-name
    methods, keeps the top K by score at full weight, and steeply dampens
    the rest.

    Bakeoff finding: alertmanager cohort-008 iter-003 had 19 Notifier.Notify
    implementations occupying positions 1-20.  After common-method-name
    dampening (0.53x), they still dominated because all had similar raw
    scores (in=18 from shared interface callers).

    Detection:
    - Group callable symbols (function/method) by name
    - Only apply to groups with >= min_group_size members
    - Sort each group by centrality score (descending)
    - Top K keep their score; remainder get tail_factor multiplier

    Args:
        centrality: Centrality scores to weight.
        symbols: Symbol list (used for name grouping).
        top_k: Number of highest-scored members to keep at full weight
            within each name group. Default 3.
        min_group_size: Minimum group size to trigger dampening. Default 6.
        tail_factor: Multiplier for symbols beyond top K. Default 0.15.

    Returns:
        Dictionary mapping symbol ID to dampened centrality score.
    """
    _CALLABLE_KINDS = {"function", "method"}

    # Group callable symbols by name
    name_groups: Dict[str, list[str]] = {}
    symbol_lookup: Dict[str, Symbol] = {}
    for s in symbols:
        symbol_lookup[s.id] = s
        if s.kind in _CALLABLE_KINDS:
            name_groups.setdefault(s.name, []).append(s.id)

    # Identify which symbol IDs need dampening
    dampened_ids: set[str] = set()
    for _name, sids in name_groups.items():
        if len(sids) < min_group_size:
            continue
        # Sort by centrality descending, dampen beyond top_k
        ranked = sorted(sids, key=lambda sid: -centrality.get(sid, 0))
        for sid in ranked[top_k:]:
            dampened_ids.add(sid)

    weighted = {}
    for sid, score in centrality.items():
        if sid in dampened_ids:
            weighted[sid] = score * tail_factor
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


def filter_edges_for_ranking(
    edges: List[Edge],
    symbols: List[Symbol],
    exclude_test_edges: bool = True,
    exclude_import_edges: bool = True,
    min_edge_confidence: float = 0.0,
) -> List[Edge]:
    """Filter edges for ranking, removing test, import, and low-confidence edges.

    Structural edges (extends, implements) are always preserved because they
    reflect architectural importance of the target (base class / interface),
    regardless of whether the source lives in a test file.

    Args:
        edges: List of edges to filter.
        symbols: List of symbols (used to look up source paths for test filtering).
        exclude_test_edges: If True, ignore edges originating from test
            files. Default True.
        exclude_import_edges: If True, ignore import/imports_module edges.
            Import edges represent file-level visibility, not actual call
            relationships. Default True.
        min_edge_confidence: Minimum edge confidence to include. Edges below
            this threshold are excluded. Default 0.0 (include all).

    Returns:
        Filtered list of edges.
    """
    _STRUCTURAL_EDGE_TYPES = {"extends", "implements"}
    _IMPORT_EDGE_TYPES = {"imports", "imports_module"}

    if exclude_test_edges:
        symbol_by_id = {s.id: s for s in symbols}
        filtered = [
            e for e in edges
            if e.edge_type in _STRUCTURAL_EDGE_TYPES
            or not is_test_node(
                (symbol_by_id[e.src].path if e.src in symbol_by_id else ''),
                (symbol_by_id[e.src].meta if e.src in symbol_by_id else None),
            )
        ]
    else:
        filtered = list(edges)

    if exclude_import_edges:
        filtered = [
            e for e in filtered
            if e.edge_type not in _IMPORT_EDGE_TYPES
        ]

    if min_edge_confidence > 0:
        filtered = [
            e for e in filtered
            if e.confidence >= min_edge_confidence
        ]

    return filtered


def rank_symbols(
    symbols: List[Symbol],
    edges: List[Edge],
    first_party_priority: bool = True,
    exclude_test_edges: bool = True,
    exclude_import_edges: bool = True,
    min_edge_confidence: float = 0.0,
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
        exclude_import_edges: If True, ignore import/imports_module edges
            when computing centrality. Import edges represent file-level
            visibility, not actual call relationships. Default True.
        min_edge_confidence: Minimum edge confidence to include in
            centrality computation. Edges below this threshold are
            excluded. Default 0.0 (include all). Set to 0.5 to exclude
            low-confidence inferred edges (ast_method_inferred) that
            inflate in-degree for common method names like .Lock(),
            .get(), .setValue().

    Returns:
        List of RankedSymbol objects sorted by importance (highest first).
    """
    if not symbols:
        return []

    filtered_edges = filter_edges_for_ranking(
        edges, symbols,
        exclude_test_edges=exclude_test_edges,
        exclude_import_edges=exclude_import_edges,
        min_edge_confidence=min_edge_confidence,
    )

    # Compute centrality with hub saturation (threshold=100 dampens
    # infrastructure utilities like error sentinels and loggers).
    # within_file_weight=0.3 dampens local-variable inflation: symbols
    # referenced many times within one file rank lower than symbols
    # referenced across many files.
    # max_per_file_in=5 caps per-source-file in-degree contribution,
    # preventing utility functions called many times from few files
    # (e.g., createYAMLNode with 46 calls from 1 file) from outranking
    # architecturally important symbols called from many files.
    raw_centrality = compute_centrality(
        symbols, filtered_edges, hub_threshold=100,
        within_file_weight=0.3, max_per_file_in=5,
    )

    # Apply tier weighting if enabled
    if first_party_priority:
        weighted_centrality = apply_tier_weights(raw_centrality, symbols)
    else:
        weighted_centrality = raw_centrality

    # De-weight noise paths (database migrations, etc.)
    weighted_centrality = apply_noise_weights(weighted_centrality, symbols)

    # De-weight utility symbols (loggers, clocks, STL accessors, etc.)
    weighted_centrality = apply_utility_symbol_weights(weighted_centrality, symbols)

    # De-weight common method names (execute, call, perform, etc.)
    weighted_centrality = apply_common_method_name_weights(
        weighted_centrality, symbols,
    )

    # De-weight excess interface implementation siblings (e.g., 19
    # Notifier.Notify variants — keep top 3, dampen the rest)
    weighted_centrality = apply_sibling_impl_weights(
        weighted_centrality, symbols,
    )

    # De-weight trivial sinks (short-bodied pure sinks like accessors/stubs)
    weighted_centrality = apply_trivial_sink_weights(
        weighted_centrality, symbols, filtered_edges,
    )

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
    exclude_test_edges: bool = True,
    exclude_import_edges: bool = True,
    min_edge_confidence: float = 0.0,
) -> List[RankedFile]:
    """Rank files by importance using symbol density scoring.

    Uses the same pipeline as rank_symbols(): edge filtering, hub saturation,
    tier weighting, and full dampening (noise, utility, common method name,
    trivial sink) before computing file scores.

    Args:
        symbols: List of symbols to analyze.
        edges: List of edges between symbols.
        first_party_priority: If True, apply tier weighting. Default True.
        top_k: Number of top symbols to sum for file score. Default 3.
        exclude_test_edges: If True, ignore edges from test files. Default True.
        exclude_import_edges: If True, ignore import edges. Default True.
        min_edge_confidence: Minimum edge confidence to include. Default 0.0.

    Returns:
        List of RankedFile objects sorted by importance (highest first).
    """
    if not symbols:
        return []

    # Filter edges (same as rank_symbols)
    filtered_edges = filter_edges_for_ranking(
        edges, symbols,
        exclude_test_edges=exclude_test_edges,
        exclude_import_edges=exclude_import_edges,
        min_edge_confidence=min_edge_confidence,
    )

    # Compute centrality with hub saturation (same as rank_symbols)
    raw_centrality = compute_centrality(
        symbols, filtered_edges, hub_threshold=100
    )

    if first_party_priority:
        centrality = apply_tier_weights(raw_centrality, symbols)
    else:
        centrality = raw_centrality

    # Apply full dampening pipeline (same as rank_symbols)
    centrality = apply_noise_weights(centrality, symbols)
    centrality = apply_utility_symbol_weights(centrality, symbols)
    centrality = apply_common_method_name_weights(centrality, symbols)
    centrality = apply_trivial_sink_weights(centrality, symbols, filtered_edges)

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
        # Resolve absolute path for LOC computation
        path_obj = Path(file_path)
        if not path_obj.is_absolute():
            abs_path = repo_root / file_path
            rel_path = file_path
        else:
            abs_path = path_obj
            # Normalize to relative path for consistent key format
            try:
                rel_path = str(abs_path.relative_to(repo_root))
            except ValueError:  # pragma: no cover
                rel_path = file_path  # Fallback if not under repo_root

        loc = compute_file_loc(abs_path)

        if loc < min_loc:
            density_scores[rel_path] = 0.0
            continue

        # Sum raw in-degree of all symbols in file
        total_in_degree = sum(in_degree.get(s.id, 0) for s in symbols)
        density_scores[rel_path] = total_in_degree / loc

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

    Note: ripgrep was tried here but removed due to complexity around regex
    escaping for symbol names containing special characters. The parallelized
    Python regex approach is sufficient for practical repo sizes.
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


def compute_truncation_elbow(
    symbols: List[Symbol],
    centrality: Dict[str, float],
    file_path: str,
    chars_per_line: int = 80,
    chars_per_token: int = 4,
    default_tokens: int = 500,
) -> int:
    """Determine the optimal truncation point for a file based on symbol centrality.

    Uses the cumulative centrality curve of symbols ordered by their end_line
    to find the "elbow" — the point of diminishing returns where most of
    the file's important symbols have been covered. The elbow is computed
    using the max-distance-from-chord method (point maximizing perpendicular
    distance from the line connecting the first and last points on the curve).

    This provides a semantically-informed minimum truncation target: truncate
    the file at the point where you've captured most of its important content.

    Args:
        symbols: All symbols in the repository.
        centrality: Weighted centrality scores (symbol ID → score).
        file_path: The file path to compute elbow for (matched against symbol.path).
        chars_per_line: Average characters per line. Default 80.
        chars_per_token: Average characters per token. Default 4.
        default_tokens: Returned when elbow cannot be determined. Default 500.

    Returns:
        Token count representing the optimal truncation point.
    """
    # Filter to symbols in this file that have spans and positive centrality
    file_symbols = [
        s for s in symbols
        if s.path == file_path
        and s.span.end_line > 0
        and centrality.get(s.id, 0) > 0
    ]

    if len(file_symbols) < 3:
        return default_tokens

    # Sort by end_line
    file_symbols.sort(key=lambda s: s.span.end_line)

    # Build cumulative centrality curve
    total = sum(centrality.get(s.id, 0) for s in file_symbols)
    if total <= 0:  # pragma: no cover - filtered to positive centrality above
        return default_tokens

    cumulative = 0.0
    points: list[tuple[int, float]] = []
    for s in file_symbols:
        cumulative += centrality.get(s.id, 0)
        points.append((s.span.end_line, cumulative / total))

    # Need at least 3 points for meaningful elbow detection
    if len(points) < 3:  # pragma: no cover - same as len(file_symbols) check above
        return default_tokens

    # Max-distance-from-chord method
    # Chord from first to last point
    x1, y1 = float(points[0][0]), points[0][1]
    x2, y2 = float(points[-1][0]), points[-1][1]

    dx = x2 - x1
    dy = y2 - y1
    chord_len = math.sqrt(dx * dx + dy * dy)

    if chord_len == 0:  # pragma: no cover - unreachable: y_last=1.0, y_first<1.0
        return default_tokens

    # Find point with maximum perpendicular distance from chord
    max_dist = 0.0
    elbow_line = points[0][0]
    for px, py in points[1:-1]:  # Exclude endpoints
        # Perpendicular distance from point to line
        dist = abs(dy * float(px) - dx * py + x2 * y1 - y2 * x1) / chord_len
        if dist > max_dist:
            max_dist = dist
            elbow_line = px

    # Convert elbow line number to tokens
    return elbow_line * chars_per_line // chars_per_token


def compute_harmonic_shares(n: int, total_budget: int) -> list[int]:
    """Compute harmonic budget shares for N ranked items.

    File at rank i (1-indexed) gets ``total_budget * (1/i) / H_n`` tokens,
    where ``H_n = sum(1/k for k=1..n)`` is the n-th harmonic number.  This
    gives a depth-over-breadth distribution: the highest-ranked file gets
    the most tokens, with each subsequent file receiving proportionally less.

    Allocations are rounded down to integers.  Any leftover tokens from
    rounding are distributed one each to the highest-ranked items so the
    sum equals *exactly* ``total_budget``.

    Args:
        n: Number of items to allocate budget across.  Must be >= 0.
        total_budget: Total token budget to distribute.  Must be >= 0.

    Returns:
        A list of ``n`` integer allocations summing to ``total_budget``,
        monotonically non-increasing.
    """
    if n <= 0 or total_budget <= 0:
        return [] if n <= 0 else [0] * n

    # Compute H_n (n-th harmonic number)
    h_n = sum(1.0 / k for k in range(1, n + 1))

    # Compute raw shares and floor them
    shares = [int(total_budget * (1.0 / i) / h_n) for i in range(1, n + 1)]

    # Distribute remainder to top-ranked items
    remainder = total_budget - sum(shares)
    for i in range(remainder):
        shares[i] += 1

    return shares
