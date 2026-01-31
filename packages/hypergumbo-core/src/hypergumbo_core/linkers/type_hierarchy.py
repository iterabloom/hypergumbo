"""Type hierarchy linker for polymorphic dispatch resolution.

This linker creates `dispatches_to` edges from interface/abstract class methods
to their concrete implementations, enabling polymorphic call resolution.

How It Works
------------
1. Build inheritance maps from `extends` and `implements` edges
2. For each class/interface with subclasses or implementors:
   - Find methods on that class/interface
   - Find matching methods (same short name) in child classes
   - Create `dispatches_to` edges from parent method to child methods

Use Case
--------
- Interface `UserService` with method `findUser()`
- Class `UserServiceImpl implements UserService` with `findUser()`
- When code calls `service.findUser()` (typed as UserService), we currently
  resolve to `UserService.findUser`. The `dispatches_to` edge shows that
  this call may actually execute `UserServiceImpl.findUser`.

Benefits
--------
- Helps navigate from interface to implementations
- Shows polymorphic targets for method calls
- Particularly valuable for DI-heavy codebases (Spring, ASP.NET, Angular)

Limitations
-----------
- Currently only works for languages with explicit `extends`/`implements` edges
- Java: Full support
- Other languages: Need `extends` edge creation for this linker to help
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from ..ir import AnalysisRun, Edge, Symbol
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)

if TYPE_CHECKING:
    pass

PASS_ID = "type-hierarchy-v1"


def build_inheritance_maps(
    symbols: list[Symbol],
    edges: list[Edge],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build inheritance maps from extends/implements edges.

    Args:
        symbols: All symbols (for reference)
        edges: All edges (to find extends/implements)

    Returns:
        Tuple of:
        - parent_to_children: class_id -> [child_class_ids] (from extends)
        - interface_to_impls: interface_id -> [implementing_class_ids] (from implements)
    """
    parent_to_children: dict[str, list[str]] = defaultdict(list)
    interface_to_impls: dict[str, list[str]] = defaultdict(list)

    for edge in edges:
        if edge.edge_type == "extends":
            # edge: child --extends--> parent
            parent_id = edge.dst
            child_id = edge.src
            parent_to_children[parent_id].append(child_id)
        elif edge.edge_type == "implements":
            # edge: impl --implements--> interface
            interface_id = edge.dst
            impl_id = edge.src
            interface_to_impls[interface_id].append(impl_id)

    return dict(parent_to_children), dict(interface_to_impls)


def _get_method_short_name(method_name: str) -> str:
    """Extract short method name from qualified name.

    Examples:
        "Animal.speak" -> "speak"
        "com.example.UserService.findUser" -> "findUser"
        "UserController#index" -> "index" (Ruby style)

    Args:
        method_name: Qualified method name

    Returns:
        Short method name (last component)
    """
    # Handle Ruby-style Class#method
    if "#" in method_name:
        return method_name.split("#")[-1]
    # Handle dot-separated qualified names
    if "." in method_name:
        return method_name.split(".")[-1]
    return method_name


def _get_class_name_from_method(method_symbol: Symbol) -> str | None:
    """Extract class name from a method symbol.

    Looks for:
    1. meta.class field
    2. Qualified name before the method name

    Args:
        method_symbol: A method symbol

    Returns:
        Class name, or None if not determinable
    """
    # Check meta.class first
    if method_symbol.meta and "class" in method_symbol.meta:
        return method_symbol.meta["class"]

    # Extract from qualified name
    name = method_symbol.name
    # Ruby style: Class#method
    if "#" in name:
        return name.split("#")[0]
    # Dot style: Class.method
    if "." in name:
        parts = name.rsplit(".", 1)
        if len(parts) == 2:
            return parts[0]

    return None


def find_implementing_methods(
    parent_method: Symbol,
    parent_class: Symbol,
    parent_to_children: dict[str, list[str]],
    all_symbols: list[Symbol],
) -> list[Symbol]:
    """Find methods in child classes that override a parent method.

    Args:
        parent_method: Method to find overrides for
        parent_class: Class containing the method
        parent_to_children: Map of class_id -> [child_class_ids]
        all_symbols: All symbols to search through

    Returns:
        List of method symbols that override the parent method
    """
    # Get short method name
    method_short_name = _get_method_short_name(parent_method.name)

    # Get child class IDs
    child_class_ids = parent_to_children.get(parent_class.id, [])
    if not child_class_ids:
        return []

    # Build index of symbols by ID for quick lookup
    symbol_by_id = {s.id: s for s in all_symbols}

    # Find child class names
    child_class_names = set()
    for child_id in child_class_ids:
        child_sym = symbol_by_id.get(child_id)
        if child_sym:
            child_class_names.add(child_sym.name)

    # Find methods in child classes with matching short name
    overrides = []
    for sym in all_symbols:
        if sym.kind != "method":
            continue

        sym_short_name = _get_method_short_name(sym.name)
        if sym_short_name != method_short_name:
            continue

        # Check if this method belongs to a child class
        sym_class_name = _get_class_name_from_method(sym)
        if sym_class_name and sym_class_name in child_class_names:
            overrides.append(sym)

    return overrides


def link_type_hierarchy(ctx: LinkerContext) -> LinkerResult:
    """Create dispatches_to edges for polymorphic method dispatch.

    This linker:
    1. Builds inheritance maps from extends/implements edges
    2. For each method on a class/interface that has children:
       - Finds matching methods in child classes
       - Creates dispatches_to edges from parent to child methods

    Args:
        ctx: LinkerContext with symbols and edges

    Returns:
        LinkerResult with new dispatches_to edges
    """
    run = AnalysisRun.create(pass_id=PASS_ID, version="hypergumbo-0.1.0")

    # Build inheritance maps
    parent_to_children, interface_to_impls = build_inheritance_maps(
        ctx.symbols, ctx.edges
    )

    # Combine both maps - we treat extends and implements the same way
    all_parents_to_children: dict[str, list[str]] = {}
    all_parents_to_children.update(parent_to_children)
    all_parents_to_children.update(interface_to_impls)

    if not all_parents_to_children:
        # No inheritance relationships, nothing to do
        return LinkerResult(run=run)

    # Build index of class/interface symbols by ID
    class_symbols = {
        s.id: s for s in ctx.symbols
        if s.kind in ("class", "interface")
    }

    # Build index of methods by their containing class
    methods_by_class: dict[str, list[Symbol]] = defaultdict(list)
    for sym in ctx.symbols:
        if sym.kind != "method":
            continue
        class_name = _get_class_name_from_method(sym)
        if class_name:
            # Try to find the class symbol
            for class_id, class_sym in class_symbols.items():
                if class_sym.name == class_name:
                    methods_by_class[class_id].append(sym)
                    break

    # Create dispatches_to edges
    new_edges: list[Edge] = []
    seen_pairs: set[tuple[str, str]] = set()

    for parent_id, _child_ids in all_parents_to_children.items():
        parent_class = class_symbols.get(parent_id)
        if not parent_class:  # pragma: no cover - defensive for malformed inheritance
            continue

        # Get methods on this parent class
        parent_methods = methods_by_class.get(parent_id, [])

        for parent_method in parent_methods:
            overrides = find_implementing_methods(
                parent_method,
                parent_class,
                all_parents_to_children,
                ctx.symbols,
            )

            for override in overrides:
                # Avoid duplicate edges (defensive - find_implementing_methods
                # uses a set, so duplicates are rare)
                pair = (parent_method.id, override.id)
                if pair in seen_pairs:  # pragma: no cover - defensive for edge cases
                    continue
                seen_pairs.add(pair)

                edge = Edge.create(
                    src=parent_method.id,
                    dst=override.id,
                    edge_type="dispatches_to",
                    line=parent_method.span.start_line if parent_method.span else 0,
                    confidence=0.85,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="type_hierarchy",
                )
                new_edges.append(edge)

    return LinkerResult(edges=new_edges, run=run)


# Register the linker
@register_linker(
    "type_hierarchy",
    priority=60,  # Run after analyzers, before final cleanup
    description="Creates dispatches_to edges for polymorphic method dispatch",
    activation=LinkerActivation(always=True),  # Run on all codebases
)
def _link_type_hierarchy_entry(ctx: LinkerContext) -> LinkerResult:
    """Entry point for type hierarchy linker."""
    return link_type_hierarchy(ctx)
