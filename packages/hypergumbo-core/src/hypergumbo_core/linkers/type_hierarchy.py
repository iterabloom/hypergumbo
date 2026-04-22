# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: type hierarchy for polymorphic dispatch resolution.

Creates `dispatches_to` edges from interface/abstract class methods
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

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, Symbol, make_pass_id
from ..paths import is_test_file
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)

if TYPE_CHECKING:
    pass

PASS_ID = make_pass_id("type-hierarchy")


# WI-sukav A1: per-language gate for concrete-extends → virtual dispatch.
#
# Some languages model concrete inheritance/composition with an `extends`
# edge but do NOT use virtual dispatch through it.  For these languages,
# the type-hierarchy linker must NOT create dispatches_to edges from a
# parent's method to a child's same-named method via an `extends` edge,
# because there is no code path through which the parent's static type
# could land in the child's method.
#
# Languages WITHOUT virtual dispatch through `extends` (filtered):
#   - go     — struct embedding is composition; calling a method on the
#              embedded type always lands in the embedded type's method,
#              never the embedder's same-named shadow method.
#   - cpp    — only methods marked `virtual` dispatch polymorphically;
#              non-virtual methods are statically resolved at the static
#              type.  Pessimistic until per-method virtual tracking lands.
#   - rust   — no inheritance; struct/trait are separate concepts.  Trait
#              dispatch flows through `implements` edges, which are
#              unaffected by this gate.
#   - csharp — methods are non-virtual unless marked `virtual`/`override`.
#              Pessimistic until per-method tracking lands.
#
# Languages WITH virtual dispatch through `extends` (default behavior):
# Java, Kotlin, Python, Ruby, Scala, Swift, and any analyzer not in the
# deny set.  Defaulting to allow keeps the linker conservative for
# analyzers we have not yet investigated.
#
# `implements` edges are unaffected — interface satisfaction is virtual
# dispatch in every language that has the concept.
NO_VIRTUAL_EXTENDS_LANGUAGES: frozenset[str] = frozenset({
    "go", "cpp", "rust", "csharp",
})


def _extends_admits_dispatch(language: str | None) -> bool:
    """Return True if `extends` edges in this language imply virtual dispatch.

    A None or empty language is treated as default-allow (the conservative
    choice for unknown analyzers).
    """
    if not language:
        return True
    return language not in NO_VIRTUAL_EXTENDS_LANGUAGES


def build_inheritance_maps(
    symbols: list[Symbol],
    edges: list[Edge],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build inheritance maps from extends/implements edges.

    Per WI-sukav A1, `extends` edges from no-virtual-dispatch languages
    (Go, C++, Rust, C#) are filtered out of `parent_to_children` so the
    type-hierarchy linker does not create spurious dispatches_to edges
    from a parent's method to a child's shadow method.  `implements`
    edges are never filtered — interface dispatch is virtual in every
    language.

    Args:
        symbols: All symbols (for src-symbol language lookup)
        edges: All edges (to find extends/implements)

    Returns:
        Tuple of:
        - parent_to_children: class_id -> [child_class_ids] (from extends)
        - interface_to_impls: interface_id -> [implementing_class_ids] (from implements)
    """
    symbol_by_id = {s.id: s for s in symbols}
    parent_to_children: dict[str, list[str]] = defaultdict(list)
    interface_to_impls: dict[str, list[str]] = defaultdict(list)

    for edge in edges:
        if edge.edge_type == "extends":
            # edge: child --extends--> parent
            child_sym = symbol_by_id.get(edge.src)
            child_lang = child_sym.language if child_sym else None
            if not _extends_admits_dispatch(child_lang):
                continue
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


def _resolve_method_class_id(
    method_sym: Symbol,
    class_ids_by_name: dict[str, list[str]],
    class_symbols: dict[str, Symbol],
) -> str | None:
    """Resolve a method symbol to its actual containing class ID.

    When multiple classes share the same short name (common in Go where
    each package can define a type named ``Notifier``), this picks the
    class in the same file as the method.  For languages where methods
    and their class are defined in the same file (Python, Java, Ruby,
    Go — always same package, usually same file), same-file matching
    is the correct disambiguation.

    Falls back to the first candidate if no same-file class exists, to
    preserve behavior for languages that allow methods in different
    files than their class.
    """
    class_name = _get_class_name_from_method(method_sym)
    if not class_name:
        return None
    candidate_ids = class_ids_by_name.get(class_name, [])
    if not candidate_ids:
        return None
    # Prefer a class in the same file as the method
    for cid in candidate_ids:
        csym = class_symbols.get(cid)
        if csym and csym.path == method_sym.path:
            return cid
    # Fallback: first candidate (historical behavior for cross-file cases)
    return candidate_ids[0]


@dataclass
class _TypeHierarchyIndex:
    """Pre-built indexes for fast type hierarchy resolution.

    Built once in link_type_hierarchy and shared across all
    find_implementing_methods calls. Avoids rebuilding symbol_by_id
    and scanning all_symbols on every invocation.

    Methods are indexed by their resolved class ID (not name), so that
    multiple classes sharing a short name do not collide.
    """

    symbol_by_id: dict[str, Symbol]
    # short_method_name -> [(class_id, Symbol)]
    methods_by_short_name: dict[str, list[tuple[str, Symbol]]]

    @staticmethod
    def build(
        all_symbols: list[Symbol],
        class_ids_by_name: dict[str, list[str]],
        class_symbols: dict[str, Symbol],
    ) -> _TypeHierarchyIndex:
        """Build indexes from the full symbol list."""
        symbol_by_id = {s.id: s for s in all_symbols}
        methods_by_short_name: dict[str, list[tuple[str, Symbol]]] = {}
        for sym in all_symbols:
            if sym.kind != "method":
                continue
            short_name = _get_method_short_name(sym.name)
            class_id = _resolve_method_class_id(
                sym, class_ids_by_name, class_symbols,
            )
            if class_id is not None:
                methods_by_short_name.setdefault(short_name, []).append(
                    (class_id, sym)
                )
        return _TypeHierarchyIndex(
            symbol_by_id=symbol_by_id,
            methods_by_short_name=methods_by_short_name,
        )


def find_implementing_methods(
    parent_method: Symbol,
    parent_class: Symbol,
    parent_to_children: dict[str, list[str]],
    all_symbols: list[Symbol],
    index: _TypeHierarchyIndex | None = None,
) -> list[Symbol]:
    """Find methods in child classes that override a parent method.

    Args:
        parent_method: Method to find overrides for
        parent_class: Class containing the method
        parent_to_children: Map of class_id -> [child_class_ids]
        all_symbols: All symbols to search through
        index: Pre-built index for fast resolution. When provided,
            avoids O(N) linear scans and per-call dict rebuilds.

    Returns:
        List of method symbols that override the parent method
    """
    # Get short method name
    method_short_name = _get_method_short_name(parent_method.name)

    # Get child class IDs
    child_class_ids = parent_to_children.get(parent_class.id, [])
    if not child_class_ids:
        return []

    # Use pre-built index when available
    if index is not None:
        return _find_implementing_methods_indexed(
            method_short_name, child_class_ids, index
        )

    # Legacy fallback for direct callers without index
    return _find_implementing_methods_scan(  # pragma: no cover
        method_short_name, child_class_ids, all_symbols
    )


def _find_implementing_methods_indexed(
    method_short_name: str,
    child_class_ids: list[str],
    index: _TypeHierarchyIndex,
) -> list[Symbol]:
    """Find overriding methods using pre-built indexes.

    Filters by class *ID*, not class name.  This prevents false positives
    when multiple classes share the same short name — e.g., Go packages
    each defining a struct named ``Notifier`` must not cross-dispatch.
    """
    child_id_set = set(child_class_ids)
    candidates = index.methods_by_short_name.get(method_short_name, [])
    return [sym for class_id, sym in candidates
            if class_id in child_id_set]


def _find_implementing_methods_scan(
    method_short_name: str,
    child_class_ids: list[str],
    all_symbols: list[Symbol],
) -> list[Symbol]:  # pragma: no cover
    """Legacy linear-scan fallback for finding implementing methods."""
    symbol_by_id = {s.id: s for s in all_symbols}

    child_class_names = set()
    for child_id in child_class_ids:
        child_sym = symbol_by_id.get(child_id)
        if child_sym:
            child_class_names.add(child_sym.name)

    overrides = []
    for sym in all_symbols:
        if sym.kind != "method":
            continue
        sym_short_name = _get_method_short_name(sym.name)
        if sym_short_name != method_short_name:
            continue
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
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

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

    # Build index of class/interface/struct/trait symbols by ID and
    # multi-valued by name.  Name collisions are common in Go (multiple
    # packages defining the same short type name like ``Notifier``), so we
    # track all candidates and disambiguate methods by file path.
    # Struct and trait are included to match the inheritance linker's
    # broader definition of "type with methods".
    class_symbols = {
        s.id: s for s in ctx.symbols
        if s.kind in ("class", "interface", "struct", "trait")
    }
    class_ids_by_name: dict[str, list[str]] = defaultdict(list)
    for cid, csym in class_symbols.items():
        class_ids_by_name[csym.name].append(cid)

    # Build index of methods by their actual containing class ID.
    # Each method is assigned to the class in the same file (preferred),
    # falling back to the first candidate with a matching name.
    methods_by_class: dict[str, list[Symbol]] = defaultdict(list)
    for sym in ctx.symbols:
        if sym.kind != "method":
            continue
        class_id = _resolve_method_class_id(
            sym, class_ids_by_name, class_symbols,
        )
        if class_id is not None:
            methods_by_class[class_id].append(sym)

    # Build shared index once for find_implementing_methods
    hierarchy_index = _TypeHierarchyIndex.build(
        ctx.symbols, class_ids_by_name, class_symbols,
    )

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
                index=hierarchy_index,
            )

            # Scale confidence by 1/sqrt(N) for fan-out dampening
            # (WI-kabom).  Interfaces with many implementors (e.g., 19
            # Notifier impls) create N edges per method; without scaling,
            # these dominate ranking and pollute reverse slices.  Matches
            # the precedent in symbol_resolution.py for ambiguous lookups.
            num_overrides = len(overrides)
            base_confidence = 0.85 / math.sqrt(max(1, num_overrides))

            for override in overrides:
                # Skip self-dispatch: a method cannot dispatch to itself.
                # The class-ID filter in _find_implementing_methods_indexed
                # already prevents this in normal flow; this guard only
                # fires for malformed data (e.g., a class with an extends
                # edge to itself), so it is not exercised by tests.
                if override.id == parent_method.id:  # pragma: no cover
                    continue
                # Avoid duplicate edges (defensive - find_implementing_methods
                # uses a set, so duplicates are rare)
                pair = (parent_method.id, override.id)
                if pair in seen_pairs:  # pragma: no cover - defensive for edge cases
                    continue
                seen_pairs.add(pair)

                # Apply confidence penalty for test-file overrides (WI-supok).
                # Test overrides (e.g., TestImpl.method → base.method) inflate
                # centrality and pollute reverse slices.  Penalty matches the
                # precedent in ranking.py for test-tier downweighting.
                confidence = base_confidence
                if override.path and is_test_file(override.path):
                    confidence = 0.30

                edge = Edge.create(
                    src=parent_method.id,
                    dst=override.id,
                    edge_type="dispatches_to",
                    line=parent_method.span.start_line if parent_method.span else 0,
                    confidence=confidence,
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
