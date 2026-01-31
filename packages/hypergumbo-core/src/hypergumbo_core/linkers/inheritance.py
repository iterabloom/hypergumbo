"""Inheritance linker for creating extends/implements edges.

This linker creates graph edges from base_classes metadata, providing a single
implementation that works across ALL languages instead of duplicating edge
creation logic in each analyzer.

How It Works
------------
1. Finds all class/interface symbols with base_classes metadata
2. For each base class name, looks up the target symbol
3. Creates extends (for classes) or implements (for interfaces) edges
4. Runs BEFORE type_hierarchy linker (which needs these edges)

Why a Linker Instead of Per-Analyzer Logic
------------------------------------------
- Analyzers only need to extract base_classes metadata (language-specific)
- Edge creation is identical across languages (language-agnostic)
- Single point of logic for META-001 compliance
- New language support only requires metadata extraction, not edge creation
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..ir import AnalysisRun, Edge, Symbol
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    pass

PASS_ID = "inheritance-linker-v1"


def _build_symbol_maps(
    symbols: list[Symbol],
) -> tuple[dict[str, Symbol], dict[str, Symbol]]:
    """Build lookup maps for classes and interfaces.

    Returns:
        Tuple of (class_symbols, interface_symbols) dicts mapping name to Symbol.
    """
    class_symbols: dict[str, Symbol] = {}
    interface_symbols: dict[str, Symbol] = {}

    for sym in symbols:
        if sym.kind == "class":
            class_symbols[sym.name] = sym
        elif sym.kind == "interface":
            interface_symbols[sym.name] = sym

    return class_symbols, interface_symbols


def _create_inheritance_edges(
    symbols: list[Symbol],
    class_symbols: dict[str, Symbol],
    interface_symbols: dict[str, Symbol],
    existing_edges: list[Edge],
    run: AnalysisRun,
) -> list[Edge]:
    """Create extends/implements edges from base_classes metadata.

    For each symbol with base_classes metadata:
    - If base is an interface in our codebase -> implements edge
    - If base is a class in our codebase -> extends edge
    - If base is not found (external) -> no edge
    - If edge already exists (from analyzer) -> skip to avoid duplicates

    Args:
        symbols: All symbols to process
        class_symbols: Map of class name -> Symbol
        interface_symbols: Map of interface name -> Symbol
        existing_edges: Edges already created by analyzers
        run: Analysis run for provenance

    Returns:
        List of NEW extends/implements edges (not duplicates)
    """
    # Build set of existing edge keys for deduplication
    existing_edge_keys: set[tuple[str, str, str]] = {
        (e.src, e.dst, e.edge_type)
        for e in existing_edges
        if e.edge_type in ("extends", "implements")
    }

    edges: list[Edge] = []

    for sym in symbols:
        if sym.kind not in ("class", "interface"):
            continue

        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue

        for base_class_name in base_classes:
            # Handle various naming patterns:
            # - Generic: List<int> -> List
            # - Qualified: Foo.Bar -> try Bar first, then Foo.Bar
            # - Scoped: Foo::Bar -> try Bar first
            base_name = base_class_name

            # Strip generic parameters
            if "<" in base_name:
                base_name = base_name.split("<")[0]

            # Try qualified name lookup first
            lookup_names = [base_name]

            # Add last segment for qualified names
            if "." in base_name:
                lookup_names.append(base_name.split(".")[-1])
            if "::" in base_name:
                lookup_names.append(base_name.split("::")[-1])

            # Try to find the target symbol
            target_sym = None
            edge_type = None

            for lookup_name in lookup_names:
                if lookup_name in interface_symbols:
                    target_sym = interface_symbols[lookup_name]
                    edge_type = "implements"
                    break
                elif lookup_name in class_symbols:
                    target_sym = class_symbols[lookup_name]
                    edge_type = "extends"
                    break

            if target_sym is None:
                continue  # External base class, no edge

            # Skip if edge already exists (from analyzer)
            edge_key = (sym.id, target_sym.id, edge_type)
            if edge_key in existing_edge_keys:
                continue

            edge = Edge.create(
                src=sym.id,
                dst=target_sym.id,
                edge_type=edge_type,
                line=sym.span.start_line if sym.span else 0,
                confidence=0.95,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type=f"ast_{edge_type}",
            )
            edges.append(edge)

    return edges


@register_linker("inheritance", priority=15)  # Before type_hierarchy (priority 20)
def link_inheritance(ctx: LinkerContext) -> LinkerResult:
    """Create extends/implements edges from base_classes metadata.

    This linker operates on ALL symbols across all languages, creating
    inheritance edges for any symbol that has base_classes metadata.
    It runs before the type_hierarchy linker which depends on these edges.

    Args:
        ctx: Linker context with symbols and run info

    Returns:
        LinkerResult with new extends/implements edges
    """
    start_time = time.time()

    run = AnalysisRun.create(pass_id=PASS_ID, version="hypergumbo-0.1.0")

    # Build lookup maps
    class_symbols, interface_symbols = _build_symbol_maps(ctx.symbols)

    # Create edges (skipping any that already exist from analyzers)
    edges = _create_inheritance_edges(
        ctx.symbols, class_symbols, interface_symbols, ctx.edges, run
    )

    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(
        symbols=[],  # No new symbols
        edges=edges,
        run=run,
    )
