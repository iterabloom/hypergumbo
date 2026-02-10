"""Containment linker for creating class-to-method `contains` edges.

This linker connects class/interface symbols to their method symbols
based on naming conventions, creating `contains` edges. Without these
edges, methods are orphaned in the graph — disconnected from their
parent classes — which inflates orphan rates and hides the class
structure from slice traversal.

How It Works
------------
1. Builds a map of class/interface names to their symbols
2. For each method/getter/setter symbol, extracts the parent name
   using language-specific separators (`.`, `#`, `::`)
3. Looks up the parent symbol and creates a `contains` edge
4. Handles nested classes: Outer.Inner.method → Inner contains method

Naming Conventions by Language
------------------------------
- Most languages use `.`: ClassName.methodName (Python, Java, JS/TS, etc.)
- Ruby instance methods use `#`: ClassName#method_name
- Rust uses `::`: ImplTarget::method_name

Why a Linker Instead of Per-Analyzer Logic
-----------------------------------------
Same rationale as the inheritance linker: containment is a structural
relationship that works identically across all languages. Analyzers
only need to set qualified names; edge creation is language-agnostic.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..ir import AnalysisRun, Edge
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

PASS_ID = "containment-linker-v1"

# Symbol kinds that can be "contained" by a class/interface
CONTAINABLE_KINDS = frozenset({"method", "getter", "setter"})

# Symbol kinds that can "contain" other symbols.
# Includes struct/trait/enum for Rust (and Go/C/Zig structs).
CONTAINER_KINDS = frozenset({"class", "interface", "struct", "trait", "enum"})

# Separators used in method names, ordered by specificity
# Ruby `#` and Rust `::` are checked before `.` to avoid
# mis-splitting on languages that use both
_SEPARATORS = ("#", "::", ".")


def _extract_parent_name(method_name: str) -> str | None:
    """Extract the parent class/type name from a qualified method name.

    Uses language-specific separators to split the name:
    - "User.save" → "User"
    - "User#save" → "User"
    - "User::new" → "User"
    - "com.example.UserService.getUsers" → "com.example.UserService"
    - "Outer.Inner.do_thing" → "Outer.Inner"
    - "save" → None (no parent)

    Args:
        method_name: The qualified method name.

    Returns:
        The parent name, or None if there is no parent.
    """
    for sep in _SEPARATORS:
        if sep in method_name:
            # rsplit with maxsplit=1 to get the immediate parent
            parent, _method = method_name.rsplit(sep, 1)
            if parent:
                return parent
    return None


@register_linker(
    "containment",
    priority=12,  # After analyzers (0), before inheritance (15)
    description="Creates contains edges from classes/interfaces to their methods",
)
def link_containment(ctx: LinkerContext) -> LinkerResult:
    """Create contains edges from class/interface symbols to their methods.

    For each method symbol with a qualified name (e.g., User.save),
    finds the corresponding class symbol (User) and creates a
    `contains` edge from class → method.

    Args:
        ctx: LinkerContext with all symbols and existing edges.

    Returns:
        LinkerResult with new `contains` edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version="hypergumbo-0.1.0")

    # Build multimap of class/interface names to symbols.
    # Multiple classes can share the same name (e.g., Django has 238 classes
    # named "Model" — 1 real + 237 test stubs). When linking methods, we
    # prefer the class in the same file as the method.
    container_by_name: dict[str, list[Symbol]] = {}
    for sym in ctx.symbols:
        if sym.kind in CONTAINER_KINDS:
            container_by_name.setdefault(sym.name, []).append(sym)

    # Build set of existing contains edge keys for deduplication
    existing_contains: set[tuple[str, str]] = {
        (e.src, e.dst) for e in ctx.edges if e.edge_type == "contains"
    }

    edges: list[Edge] = []

    for sym in ctx.symbols:
        # Process methods, getters, setters, and nested classes
        if sym.kind not in CONTAINABLE_KINDS and sym.kind not in CONTAINER_KINDS:
            continue

        # Extract parent name from qualified name (e.g., "User.save" → "User")
        # For top-level classes with no separator, parent_name is None → skip
        parent_name = _extract_parent_name(sym.name)
        if parent_name is None:
            continue

        # Look up the parent container, preferring same-file match
        candidates = container_by_name.get(parent_name)
        if not candidates:
            continue
        parent_sym: Symbol | None = None
        if len(candidates) == 1:
            parent_sym = candidates[0]
        else:
            # Prefer the class in the same file as the method
            for c in candidates:
                if c.path == sym.path:
                    parent_sym = c
                    break
            if parent_sym is None:
                parent_sym = candidates[0]

        # Skip self-containment
        if parent_sym.id == sym.id:
            continue  # pragma: no cover - defensive

        # Skip duplicates
        pair = (parent_sym.id, sym.id)
        if pair in existing_contains:
            continue

        edges.append(Edge.create(
            src=parent_sym.id,
            dst=sym.id,
            edge_type="contains",
            line=sym.span.start_line if sym.span else 0,
            confidence=1.0,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="naming_convention",
        ))
        # Track to avoid duplicates within this run
        existing_contains.add(pair)

    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(
        symbols=[],
        edges=edges,
        run=run,
    )
