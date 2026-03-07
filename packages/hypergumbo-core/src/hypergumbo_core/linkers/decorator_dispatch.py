"""Decorator dispatch linker for registry-based dynamic call resolution.

Creates ``dispatches_to`` edges from registry iteration call sites to all
functions registered via decorator patterns like ``@register_analyzer("go")``.

How It Works
------------
1. Scan all symbols for ``meta.decorators`` entries matching known registry
   patterns (e.g., ``register_analyzer``, ``register_linker``).
2. Find dispatch site symbols — functions that iterate the registry (e.g.,
   ``run_all_analyzers``, ``run_all_linkers``).
3. For each dispatch site, create ``dispatches_to`` edges to every handler
   registered with the matching decorator, grouped by registry family.

Why This Matters
----------------
Without these edges, forward slices from ``__main__`` stop at the dispatch
site and cannot reach the actual handler functions in other packages. For
monorepos like hypergumbo (100+ lang analyzer packages), this means slices
from the CLI entry point miss all language-specific code.

The ``dispatches_to`` edge type models runtime polymorphism: the dispatch
site calls *one of* the registered handlers at runtime, but statically we
connect to *all* of them to enable complete slice traversal.

Configuration
-------------
``DISPATCH_DECORATOR_PATTERNS`` maps decorator names to their dispatch site
function names. This is extensible: add new patterns for Flask, Click, or
any other registry-based decorator dispatch.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

logger = logging.getLogger(__name__)

PASS_ID = make_pass_id("decorator-dispatch-linker")

# Maps decorator name → list of dispatch site function names.
# Each entry defines a "registry family": handlers registered with the
# decorator are connected to the dispatch site functions.
DISPATCH_DECORATOR_PATTERNS: dict[str, list[str]] = {
    "register_analyzer": ["run_all_analyzers"],
    "register_linker": ["run_all_linkers"],
}


def _find_decorated_symbols(
    symbols: list[Symbol],
    patterns: dict[str, list[str]],
) -> list[tuple[Symbol, str]]:
    """Find symbols decorated with known registry decorators.

    Returns:
        List of (symbol, decorator_name) tuples for matched symbols.
    """
    results: list[tuple[Symbol, str]] = []
    for sym in symbols:
        if not sym.meta or "decorators" not in sym.meta:
            continue
        for dec in sym.meta["decorators"]:
            dec_name = dec.get("name", "")
            if dec_name in patterns:
                results.append((sym, dec_name))
                break  # One match per symbol is enough
    return results


def _find_dispatch_sites(
    symbols: list[Symbol],
    patterns: dict[str, list[str]],
) -> list[tuple[Symbol, str]]:
    """Find dispatch site functions that iterate a registry.

    Returns:
        List of (symbol, decorator_name) tuples. The decorator_name
        indicates which registry family this dispatch site serves.
    """
    # Build reverse map: dispatch_function_name → decorator_name
    dispatch_to_decorator: dict[str, str] = {}
    for dec_name, dispatch_names in patterns.items():
        for fn_name in dispatch_names:
            dispatch_to_decorator[fn_name] = dec_name

    results: list[tuple[Symbol, str]] = []
    for sym in symbols:
        if sym.name in dispatch_to_decorator:
            results.append((sym, dispatch_to_decorator[sym.name]))
    return results


@register_linker(
    "decorator-dispatch",
    priority=15,
    description="Resolve decorator-based registry dispatch patterns",
)
def link_decorator_dispatch(ctx: LinkerContext) -> LinkerResult:
    """Create dispatches_to edges from registry dispatch sites to registered handlers."""
    # Find all decorated handlers grouped by registry family
    decorated = _find_decorated_symbols(ctx.symbols, DISPATCH_DECORATOR_PATTERNS)
    handlers_by_family: dict[str, list[Symbol]] = {}
    for sym, dec_name in decorated:
        handlers_by_family.setdefault(dec_name, []).append(sym)

    # Find dispatch sites
    dispatch_sites = _find_dispatch_sites(ctx.symbols, DISPATCH_DECORATOR_PATTERNS)

    # Create edges: dispatch_site → each handler in the same family
    edges: list[Edge] = []
    for site_sym, dec_name in dispatch_sites:
        handlers = handlers_by_family.get(dec_name, [])
        for handler in handlers:
            edge = Edge.create(
                src=site_sym.id,
                dst=handler.id,
                edge_type="dispatches_to",
                line=site_sym.span.start_line if site_sym.span else 0,
                origin=PASS_ID,
                evidence_type="registry_dispatch",
                confidence=0.70,
            )
            edges.append(edge)

    run = AnalysisRun.create(
        pass_id=PASS_ID,
        version=PASS_VERSION,
    )

    if edges:
        logger.info(
            "decorator-dispatch: %d edges (%d dispatch sites, %d handlers)",
            len(edges),
            len(dispatch_sites),
            sum(len(h) for h in handlers_by_family.values()),
        )

    return LinkerResult(symbols=[], edges=edges, run=run)
