# SPDX-License-Identifier: AGPL-3.0-or-later
"""Language-proportional symbol selection utilities.

This module provides functions for language-stratified symbol selection,
ensuring multi-language projects get representation from each language
proportional to its symbol count.

How It Works
------------
Symbol selection in multi-language projects can be dominated by verbose
languages (e.g., Java) that produce many more symbols than concise ones
(e.g., Python). Language-proportional selection addresses this by:

1. Grouping symbols by their source language
2. Allocating symbol budget proportionally by language
3. Selecting within each language up to its budget

This ensures that a Python+Java project with 100 Python symbols and
1000 Java symbols doesn't have its output dominated by Java. Instead,
both languages get representation proportional to their contribution.

Why This Design
---------------
- Uses actual symbol counts, not LOC, for accurate proportions
- Floor guarantee (min_per_language) ensures minority languages appear
- Remainder redistribution gives extra slots to largest languages
- Works with any selection strategy (coverage, centrality, etc.)

Usage
-----
    from hypergumbo_core.selection.language_proportional import (
        group_symbols_by_language,
        allocate_language_budget,
        select_proportionally,
    )

    # Group symbols by language
    lang_groups = group_symbols_by_language(symbols)

    # Allocate budget (e.g., 100 slots total)
    budgets = allocate_language_budget(lang_groups, max_symbols=100)

    # Or use the convenience function for proportional selection
    selected = select_proportionally(
        symbols, centrality, max_symbols=100
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hypergumbo_core.ir import Edge, Symbol


def group_symbols_by_language(
    symbols: list[Symbol],
) -> dict[str, list[Symbol]]:
    """Group symbols by their source language.

    Args:
        symbols: List of symbols to group.

    Returns:
        Dict mapping language name to list of symbols.
        Languages with no symbols are excluded.
    """
    lang_groups: dict[str, list[Symbol]] = {}
    for sym in symbols:
        lang = sym.language
        if lang not in lang_groups:
            lang_groups[lang] = []
        lang_groups[lang].append(sym)
    return lang_groups


def group_files_by_language(
    by_file: dict[str, list[Symbol]],
) -> dict[str, dict[str, list[Symbol]]]:
    """Group files by the dominant language of their symbols.

    Each file is assigned to a single language based on its first symbol's
    language field. This works well because source files are typically
    monolingual (a .py file only contains Python symbols).

    This function is used for language-proportional symbol selection,
    ensuring that multi-language projects have representation from each
    language proportional to its symbol count.

    Args:
        by_file: Symbols grouped by file path.

    Returns:
        Dict mapping language -> {file_path -> [symbols]}.
        Empty files (no symbols) are excluded from the result.
    """
    lang_groups: dict[str, dict[str, list[Symbol]]] = {}
    for file_path, symbols in by_file.items():
        if not symbols:
            continue
        # Use first symbol's language (files are typically monolingual)
        lang = symbols[0].language
        if lang not in lang_groups:
            lang_groups[lang] = {}
        lang_groups[lang][file_path] = symbols
    return lang_groups


def allocate_language_budget(
    lang_groups: dict[str, list[Symbol]] | dict[str, dict[str, list[Symbol]]],
    max_symbols: int,
    min_per_language: int = 1,
) -> dict[str, int]:
    """Allocate symbol budget proportionally by language symbol count.

    Computes proportions from actual filtered symbols, not raw profile LOC.
    This naturally handles languages like JSON/YAML that have LOC but produce
    no analyzable symbols.

    Each language receives a proportional share of the budget with a floor
    guarantee (min_per_language). Any remainder after proportional allocation
    is redistributed to the largest languages.

    Args:
        lang_groups: Either:
            - Dict mapping language -> [symbols] (flat grouping)
            - Dict mapping language -> {file_path -> [symbols]} (file-based)
        max_symbols: Total symbol budget to allocate.
        min_per_language: Minimum slots per language (floor guarantee).

    Returns:
        Dict mapping language to allocated symbol budget.
        Empty if lang_groups is empty.
    """
    # Count symbols per language
    lang_symbol_count: dict[str, int] = {}
    for lang, group in lang_groups.items():
        if isinstance(group, dict):
            # File-based grouping: {file_path -> [symbols]}
            lang_symbol_count[lang] = sum(len(syms) for syms in group.values())
        else:
            # Flat grouping: [symbols]
            lang_symbol_count[lang] = len(group)

    total_symbols = sum(lang_symbol_count.values())
    if total_symbols == 0:
        return {}

    budgets: dict[str, int] = {}
    allocated = 0

    # Proportional allocation with floor
    for lang, count in lang_symbol_count.items():
        proportion = count / total_symbols
        budget = max(min_per_language, int(max_symbols * proportion))
        budgets[lang] = budget
        allocated += budget

    # Redistribute remainder to largest languages
    remaining = max_symbols - allocated
    if remaining > 0:
        sorted_langs = sorted(
            lang_symbol_count.keys(),
            key=lambda lang: -lang_symbol_count[lang]
        )
        for lang in sorted_langs:
            if remaining <= 0:
                break
            budgets[lang] += 1
            remaining -= 1

    return budgets


def select_proportionally(
    symbols: list[Symbol],
    centrality: dict[str, float],
    max_symbols: int,
    min_per_language: int = 1,
) -> list[Symbol]:
    """Select symbols proportionally by language, ranked by centrality.

    This is a convenience function that combines grouping, budget allocation,
    and selection into a single call.

    Args:
        symbols: List of symbols to select from.
        centrality: Centrality scores for each symbol ID.
        max_symbols: Maximum total symbols to select.
        min_per_language: Minimum slots per language (floor guarantee).

    Returns:
        List of selected symbols, up to max_symbols.
        Includes top symbols from each language proportionally.
    """
    if not symbols:
        return []

    # Group by language
    lang_groups = group_symbols_by_language(symbols)

    # Allocate budget
    budgets = allocate_language_budget(
        lang_groups, max_symbols, min_per_language
    )

    # Select from each language
    selected: list[Symbol] = []
    for lang, budget in budgets.items():
        lang_symbols = lang_groups.get(lang, [])
        # Sort by centrality (highest first)
        sorted_symbols = sorted(
            lang_symbols,
            key=lambda s: (-centrality.get(s.id, 0), s.name)
        )
        # Take up to budget
        selected.extend(sorted_symbols[:budget])

    # Final sort by centrality for consistent ordering
    selected.sort(key=lambda s: (-centrality.get(s.id, 0), s.name))

    return selected[:max_symbols]


def find_underrepresented_language_seeds(
    symbols: list[Symbol],
    edges: list[Edge],
    current_seeds: set[str],
    edge_share_threshold: float = 0.10,
) -> set[str]:
    """Find seed node IDs for languages unreachable from the BFS frontier.

    When entrypoints for a dominant language have no outgoing edges (e.g.,
    C main() dispatching via function pointer tables invisible to tree-sitter),
    the BFS frontier built from current_seeds has zero nodes in that language.
    The greedy selection loop in select_by_connectivity then fills the budget
    entirely with languages that DO have frontier access.

    This function identifies languages with significant edge mass but no
    frontier representation, and returns the highest-degree node from each
    as an additional seed.  Once injected into the seed set, BFS can expand
    into that language's subgraph.

    Args:
        symbols: Eligible symbols (already filtered for test/example exclusion).
        edges: All edges.
        current_seeds: Current seed node IDs (entrypoint IDs).
        edge_share_threshold: Minimum fraction of total edges for a language
            to be considered significant.  Default 0.10 (10%).

    Returns:
        Set of additional seed IDs to inject.  Empty if all significant
        languages are already reachable from current seeds.
    """
    eligible_ids = {s.id for s in symbols}
    symbol_lang = {s.id: s.language for s in symbols}

    # Build adjacency (only edges touching eligible symbols)
    adj_out: dict[str, set[str]] = {}
    adj_in: dict[str, set[str]] = {}
    for e in edges:
        if e.src in eligible_ids and e.dst in eligible_ids and e.src != e.dst:
            adj_out.setdefault(e.src, set()).add(e.dst)
            adj_in.setdefault(e.dst, set()).add(e.src)

    # Count edges per language (edge counts toward a language if at least one
    # endpoint is in that language)
    lang_edge_count: dict[str, int] = {}
    for e in edges:
        src_lang = symbol_lang.get(e.src)
        dst_lang = symbol_lang.get(e.dst)
        if src_lang:
            lang_edge_count[src_lang] = lang_edge_count.get(src_lang, 0) + 1
        if dst_lang and dst_lang != src_lang:
            lang_edge_count[dst_lang] = lang_edge_count.get(dst_lang, 0) + 1
    total_edge_mentions = sum(lang_edge_count.values()) or 1

    # Determine which languages are reachable from current seeds' frontier
    frontier_langs: set[str] = set()
    for sid in current_seeds:
        for dst in adj_out.get(sid, set()):
            if dst not in current_seeds:
                lang = symbol_lang.get(dst)
                if lang:
                    frontier_langs.add(lang)
        for src in adj_in.get(sid, set()):
            if src not in current_seeds:
                lang = symbol_lang.get(src)
                if lang:
                    frontier_langs.add(lang)

    # For each underrepresented language, pick the highest-degree node
    extra_seeds: set[str] = set()
    for lang, count in lang_edge_count.items():
        share = count / total_edge_mentions
        if share >= edge_share_threshold and lang not in frontier_langs:
            # Find the node in this language with highest total degree
            candidates = [
                s for s in symbols
                if s.language == lang and s.id not in current_seeds
            ]
            if candidates:
                best = max(
                    candidates,
                    key=lambda s: (
                        len(adj_out.get(s.id, set()))
                        + len(adj_in.get(s.id, set()))
                    ),
                )
                extra_seeds.add(best.id)

    return extra_seeds
