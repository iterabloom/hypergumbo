"""Facade for analyzer dispatch — delegates to the decorator-based registry.

This module provides the stable import points used by cli.py and
partial_install_warnings.py. Internally it delegates to the canonical
registry in analyze/registry.py (ADR-0012 Step 1).

Import points:
    - cli.py: ``from .analyze.all_analyzers import run_all_analyzers``
    - partial_install_warnings.py: ``from .analyze.all_analyzers import get_analyzers``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..discovery import set_global_on_file_skipped
from ..ir import Edge, Symbol, UsageContext
from ..limits import Limits
from ..paths import normalize_path
from .registry import (
    RegisteredAnalyzer,
    clear_registry,
    ensure_discovered,
    get_all_analyzers as _registry_get_all,
)


def get_analyzers() -> list[RegisteredAnalyzer]:
    """Get all registered analyzers (triggers discovery on first call).

    Returns list of RegisteredAnalyzer objects from the canonical registry.
    """
    ensure_discovered()
    return list(_registry_get_all())


def clear_analyzer_cache() -> None:
    """Clear the analyzer cache and registry. For testing only."""
    clear_registry()


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

    Triggers entry-point discovery, then iterates all registered analyzers
    in priority order. Handles supports_max_files, capture_symbols_as,
    result collection, and edge deduplication.

    Args:
        repo_root: Repository root path
        max_files: Optional max files per analyzer

    Returns:
        Tuple of (analysis_runs, all_symbols, all_edges, all_usage_contexts,
        limits, captured_symbols) where captured_symbols is a dict mapping
        capture names to symbol lists (e.g., {"c": [...], "java": [...]}
        for the JNI linker).
    """
    ensure_discovered()

    analysis_runs: list[dict] = []
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    all_usage_contexts: list[UsageContext] = []
    limits = Limits()
    limits.max_files_per_analyzer = max_files
    captured_symbols: dict[str, list[Symbol]] = {}

    # Wire global callback so find_files() reports skipped files to limits
    def _on_file_skipped(path: Path, size_bytes: int, reason: str) -> None:
        limits.add_truncated_file(str(path), size_bytes, reason)

    set_global_on_file_skipped(_on_file_skipped)

    for analyzer in _registry_get_all():
        # Build kwargs based on analyzer capabilities
        kwargs: dict[str, Any] = {}
        if analyzer.supports_max_files and max_files is not None:  # pragma: no cover
            kwargs["max_files"] = max_files

        # Run the analyzer (using get_func() for test patchability)
        result = analyzer.get_func()(repo_root, **kwargs)

        # Collect results
        collect_analyzer_result(
            result, analysis_runs, all_symbols, all_edges, all_usage_contexts, limits
        )

        # Capture symbols for linkers if needed (e.g., JNI needs c_symbols and java_symbols)
        if analyzer.capture_symbols_as and not result.skipped:
            captured_symbols[analyzer.capture_symbols_as] = list(result.symbols)

    # Deduplicate edges by ID (some analyzers may produce duplicate edges)
    seen_edge_ids: set[str] = set()
    deduped_edges: list[Edge] = []
    for edge in all_edges:
        if edge.id not in seen_edge_ids:
            seen_edge_ids.add(edge.id)
            deduped_edges.append(edge)
    all_edges = deduped_edges

    # Normalize paths: some analyzers produce absolute paths instead of
    # paths relative to repo_root.  Stripping the repo_root prefix ensures
    # consistent tier classification, test-file detection, and
    # machine-independent output.
    root_prefix = normalize_path(str(repo_root)).rstrip("/") + "/"
    for sym in all_symbols:
        normed = normalize_path(sym.path)
        if normed.startswith(root_prefix):
            sym.path = normed[len(root_prefix):]
    for uc in all_usage_contexts:
        normed = normalize_path(uc.path)
        if normed.startswith(root_prefix):
            uc.path = normed[len(root_prefix):]

    # Clear global callback to avoid leaking state
    set_global_on_file_skipped(None)

    return analysis_runs, all_symbols, all_edges, all_usage_contexts, limits, captured_symbols
