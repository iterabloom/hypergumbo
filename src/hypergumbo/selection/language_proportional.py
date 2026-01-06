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
    from hypergumbo.selection.language_proportional import (
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
    from hypergumbo.ir import Symbol


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
