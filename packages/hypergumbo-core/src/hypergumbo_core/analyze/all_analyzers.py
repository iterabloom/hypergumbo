"""Consolidated analyzer registry with plugin discovery for cli.py.

This module provides a single import point for all language analyzers,
supporting both static registration and dynamic plugin discovery via entry_points.

How It Works
------------
- Analyzers can be registered statically in ANALYZERS list
- Additional analyzers are discovered via entry_points group 'hypergumbo.analyzers'
- run_all_analyzers() iterates over all discovered analyzers

Why This Design
---------------
- Single import point: `from hypergumbo_core.analyze.all_analyzers import run_all_analyzers`
- Plugin support: Language packages register via entry_points in pyproject.toml
- Backwards compatible: Static ANALYZERS list still works for core analyzers
- Lazy loading: Analyzer functions loaded at call time for test patchability
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any, Callable, NamedTuple

from ..ir import Edge, Symbol, UsageContext
from ..limits import Limits


class AnalyzerSpec(NamedTuple):
    """Specification for an analyzer in the registry.

    Attributes:
        name: Unique identifier for this analyzer (for logging/debugging)
        module_path: Module path containing the analyzer function
        func_name: Name of the analyzer function in the module
        supports_max_files: Whether this analyzer accepts max_files parameter
        capture_symbols_as: If set, store symbols in a separate variable for linkers
    """

    name: str
    module_path: str
    func_name: str
    supports_max_files: bool = False
    capture_symbols_as: str | None = None

    def get_func(self) -> Callable[..., Any]:
        """Get the analyzer function via dynamic import.

        This enables patching to work correctly in tests, since
        we look up the function at call time rather than import time.
        """
        import importlib
        module = importlib.import_module(self.module_path)
        return getattr(module, self.func_name)


def discover_analyzers() -> list[AnalyzerSpec]:
    """Discover all registered analyzers from entry_points and static list.

    This function combines:
    1. Core analyzers from the CORE_ANALYZERS list (no dependencies)
    2. Analyzers from language packages via 'hypergumbo.analyzers' entry_points

    Returns:
        Sorted list of AnalyzerSpec, with core analyzers first.
    """
    all_specs: list[AnalyzerSpec] = list(CORE_ANALYZERS)
    seen_names: set[str] = {spec.name for spec in CORE_ANALYZERS}

    # Discover analyzers from entry_points
    try:
        eps = importlib.metadata.entry_points(group="hypergumbo.analyzers")
        for ep in eps:
            try:
                # Entry point loads a list of AnalyzerSpec
                specs_list = ep.load()
                if isinstance(specs_list, list):
                    for spec in specs_list:
                        if spec.name not in seen_names:
                            all_specs.append(spec)
                            seen_names.add(spec.name)
            except Exception:  # pragma: no cover
                # Skip broken plugins
                pass
    except Exception:  # pragma: no cover
        # Fall back to core analyzers if entry_points unavailable
        pass

    return all_specs


# Core analyzers (no optional dependencies beyond tree-sitter core)
# These are always available and don't require language-specific packages
CORE_ANALYZERS: list[AnalyzerSpec] = []

# Cache for discovered analyzers
_cached_analyzers: list[AnalyzerSpec] | None = None


def get_analyzers() -> list[AnalyzerSpec]:
    """Get all registered analyzers (cached).

    Returns cached list of analyzers, discovering on first call.
    """
    global _cached_analyzers
    if _cached_analyzers is None:
        _cached_analyzers = discover_analyzers()
    return _cached_analyzers


def clear_analyzer_cache() -> None:  # pragma: no cover
    """Clear the analyzer cache (useful for testing)."""
    global _cached_analyzers
    _cached_analyzers = None


# Legacy alias for backwards compatibility
# Note: This is now dynamically populated on first access
ANALYZERS: list[AnalyzerSpec] = []


def collect_analyzer_result(
    result: Any,
    analysis_runs: list[dict],
    all_symbols: list[Symbol],
    all_edges: list[Edge],
    all_usage_contexts: list[UsageContext],
    limits: Limits,
) -> None:
    """Collect results from an analyzer into the aggregated lists.

    This replaces 50+ repetitive code blocks in run_behavior_map().
    Each block had the same pattern; this function captures that pattern once.

    Args:
        result: The analyzer result (any XxxAnalysisResult type)
        analysis_runs: List to append run metadata to
        all_symbols: List to append symbols to
        all_edges: List to append edges to
        all_usage_contexts: List to append usage contexts to
        limits: Limits object to track skipped passes
    """
    # Handle results without run (shouldn't happen but be defensive)
    if result.run is None:  # pragma: no cover
        all_symbols.extend(result.symbols)
        all_edges.extend(result.edges)
        all_usage_contexts.extend(getattr(result, "usage_contexts", []))
        return

    # Check if analyzer was skipped (optional deps missing)
    # Some analyzers (Python, HTML) don't have skipped attribute
    is_skipped = getattr(result, "skipped", False)
    skip_reason = getattr(result, "skip_reason", "")

    if is_skipped:
        limits.skipped_passes.append({
            "pass": result.run.pass_id,
            "reason": skip_reason,
        })
    else:
        analysis_runs.append(result.run.to_dict())
        all_symbols.extend(result.symbols)
        all_edges.extend(result.edges)
        all_usage_contexts.extend(getattr(result, "usage_contexts", []))


def run_all_analyzers(
    repo_root: Path,
    max_files: int | None = None,
) -> tuple[
    list[dict],  # analysis_runs
    list[Symbol],  # all_symbols
    list[Edge],  # all_edges
    list[UsageContext],  # all_usage_contexts
    Limits,  # limits
    dict[str, list[Symbol]],  # captured_symbols (for linkers)
]:
    """Run all registered analyzers and collect their results.

    This replaces ~800 lines of repetitive analyzer invocation code
    in run_behavior_map() with a clean loop.

    Args:
        repo_root: Repository root path
        max_files: Optional max files per analyzer

    Returns:
        Tuple of (analysis_runs, all_symbols, all_edges, all_usage_contexts,
        limits, captured_symbols) where captured_symbols is a dict mapping
        capture names to symbol lists (e.g., {"c": [...], "java": [...]}
        for the JNI linker).
    """
    analysis_runs: list[dict] = []
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    all_usage_contexts: list[UsageContext] = []
    limits = Limits()
    limits.max_files_per_analyzer = max_files
    captured_symbols: dict[str, list[Symbol]] = {}

    for spec in get_analyzers():
        # Build kwargs based on analyzer capabilities
        kwargs: dict[str, Any] = {}
        if spec.supports_max_files and max_files is not None:  # pragma: no cover
            kwargs["max_files"] = max_files

        # Run the analyzer (get_func enables test patching via lazy import)
        func = spec.get_func()
        result = func(repo_root, **kwargs)

        # Collect results
        collect_analyzer_result(
            result, analysis_runs, all_symbols, all_edges, all_usage_contexts, limits
        )

        # Capture symbols for linkers if needed (e.g., JNI needs c_symbols and java_symbols)
        if spec.capture_symbols_as and not result.skipped:
            captured_symbols[spec.capture_symbols_as] = list(result.symbols)

    # Deduplicate edges by ID (some analyzers may produce duplicate edges)
    seen_edge_ids: set[str] = set()
    deduped_edges: list[Edge] = []
    for edge in all_edges:
        if edge.id not in seen_edge_ids:
            seen_edge_ids.add(edge.id)
            deduped_edges.append(edge)
    all_edges = deduped_edges

    return analysis_runs, all_symbols, all_edges, all_usage_contexts, limits, captured_symbols
