# SPDX-License-Identifier: AGPL-3.0-or-later
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

from ..ir import PASS_VERSION, AnalysisRun, Edge, Symbol, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    pass

PASS_ID = make_pass_id("inheritance-linker")


def _build_symbol_maps(
    symbols: list[Symbol],
) -> tuple[dict[str, list[Symbol]], dict[str, list[Symbol]]]:
    """Build multi-value lookup maps for classes and interfaces.

    Uses list values to handle name collisions (e.g., multiple classes named
    'Model' across different files). Resolution is done by
    ``_resolve_target_symbol`` using same-file preference and deterministic
    fallback.

    Returns:
        Tuple of (class_by_name, interface_by_name) dicts mapping name to
        list of Symbol candidates.
    """
    class_by_name: dict[str, list[Symbol]] = {}
    interface_by_name: dict[str, list[Symbol]] = {}

    for sym in symbols:
        if sym.kind in ("class", "struct"):
            if sym.name not in class_by_name:
                class_by_name[sym.name] = []
            class_by_name[sym.name].append(sym)
        elif sym.kind in ("interface", "trait"):
            # Traits (Scala, Groovy, Rust) are semantically like interfaces
            # with default implementations — index alongside interfaces.
            if sym.name not in interface_by_name:
                interface_by_name[sym.name] = []
            interface_by_name[sym.name].append(sym)

    return class_by_name, interface_by_name


def _resolve_target_symbol(
    name: str,
    child_sym: Symbol,
    candidates_by_name: dict[str, list[Symbol]],
) -> Symbol | None:
    """Resolve a base class/interface name to a specific Symbol.

    When multiple symbols share the same name (e.g., test stubs named 'Model'),
    uses a priority cascade:

    1. Same-file match: prefer the candidate defined in the same file as the child
    2. Deterministic fallback: first by sorted symbol ID

    The centralized linker does not have per-file import context (unlike
    per-analyzer resolvers), so import-based disambiguation is not available.

    Args:
        name: The base class/interface name to resolve
        child_sym: The child symbol (for file context)
        candidates_by_name: Multi-value lookup: name -> list of candidates

    Returns:
        The resolved Symbol, or None if no match found.
    """
    candidates = candidates_by_name.get(name)
    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]

    child_path = child_sym.path or ""

    # 1. Same-file match: prefer candidate in the same file
    same_file = [c for c in candidates if c.path == child_path]
    if len(same_file) == 1:
        return same_file[0]

    # 2. Deterministic fallback: first by symbol ID (sorted for stability)
    candidates_sorted = sorted(candidates, key=lambda c: c.id)
    return candidates_sorted[0]


def _create_inheritance_edges(
    symbols: list[Symbol],
    class_by_name: dict[str, list[Symbol]],
    interface_by_name: dict[str, list[Symbol]],
    existing_edges: list[Edge],
    run: AnalysisRun,
) -> list[Edge]:
    """Create extends/implements edges from base_classes metadata.

    For each symbol with base_classes metadata:
    - If base is an interface in our codebase -> implements edge
    - If base is a class in our codebase -> extends edge
    - If base is not found (external) -> no edge
    - If edge already exists (from analyzer) -> skip to avoid duplicates

    Uses ``_resolve_target_symbol`` to disambiguate when multiple classes or
    interfaces share the same name.

    Args:
        symbols: All symbols to process
        class_by_name: Multi-value map of class name -> list of Symbol candidates
        interface_by_name: Multi-value map of interface name -> list of candidates
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
        if sym.kind not in ("class", "interface", "struct", "trait"):
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
                resolved = _resolve_target_symbol(
                    lookup_name, sym, interface_by_name,
                )
                if resolved is not None:
                    target_sym = resolved
                    edge_type = "implements"
                    break
                resolved = _resolve_target_symbol(
                    lookup_name, sym, class_by_name,
                )
                if resolved is not None:
                    target_sym = resolved
                    edge_type = "extends"
                    break

            if target_sym is None:
                continue  # External base class, no edge

            # Skip self-inheritance
            if target_sym.id == sym.id:
                continue

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

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Build multi-value lookup maps (INV-015: handles name collisions)
    class_by_name, interface_by_name = _build_symbol_maps(ctx.symbols)

    # Create edges (skipping any that already exist from analyzers)
    edges = _create_inheritance_edges(
        ctx.symbols, class_by_name, interface_by_name, ctx.edges, run
    )

    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(
        symbols=[],  # No new symbols
        edges=edges,
        run=run,
    )
