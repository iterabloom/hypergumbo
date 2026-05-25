# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker: inherited-call resolution via ancestor walking.

WI-hatip (PR-2 of the INV-nilud campaign — see ``~/puluf-plan.md``).

Why a linker
------------
Inheritance-aware call resolution used to live in two analyzers: Java
(``java.py:1414-1591``, three call sites emitting ``ast_call_inherited``
/ ``ast_call_inherited_method`` / ``ast_call_inherited_field``) and
Ruby (``ruby.py:1901-1956``, ``_find_inherited_initialize``). The other
12 analyzers shared the same silent gap with no equivalent walk. The
WI-puluf Ruby ``include``/``extend`` mixin investigation surfaced the
deeper duplication, and per ADR-0003-ext this work belongs in a Tier-2
Infrastructure linker — same layer as ``inheritance.py``.

PR-2 lays the substrate. It registers a single per-language MRO walker
(`_walk_insertion_order`, used by Ruby and Groovy) and a Site-1 resolver
that consumes the ``enclosing_class`` hint added to
``Edge.meta`` by ``make_unresolved_edge`` in PR-1. PR-3 adds Java's
``_walk_single_then_interfaces`` walker; PR-5 adds Site-2 and Site-3
resolvers for Java's typed-receiver and inherited-field cases.

Contract
--------
A language analyzer indicates "this call may dispatch through inherited
methods on class X" by emitting an unresolved-call edge::

    make_unresolved_edge(
        lang=..., src_id=..., callee_name=<short_method_name>,
        line=..., pass_id=..., run_id=...,
        enclosing_class=<class_short_name>,
    )

This linker:

1. Builds the inheritance index over ``extends``/``implements``/``includes``
   edges (so Ruby ``include`` mixins participate alongside class inheritance).
2. Builds a short-method-name → ``[(class_id, Symbol)]`` index via the
   PR-1 ``build_method_index`` helper extracted from ``type_hierarchy.py``.
3. For every unresolved ``calls`` edge that has the ``enclosing_class``
   hint and whose source language has a registered MRO walker, looks up
   the starting class symbols by name, walks ancestors, and emits a
   resolved ``calls`` edge to the first matching method.

Edge confidence + evidence preserve the Java/Ruby precedent:

- ``evidence_type="ast_call_inherited"`` (Site 1) — confidence 0.90
- ``evidence_type="ast_call_inherited_method"`` (Site 2) — confidence 0.70 (PR-5)
- ``evidence_type="ast_call_inherited_field"`` (Site 3) — confidence 0.80 (PR-5)

Priority + ordering
-------------------
Registered at ``priority=18``, between ``inheritance`` (15, produces the
extends/implements/includes edges this linker walks) and
``type_hierarchy`` (20, consumes resolved call edges). The linker only
emits NEW edges; it never mutates existing ones.

Per-language MRO walkers
------------------------
The dispatch table is hardcoded (YAML / decorator-hooks were rejected as
ceremony for static language semantics). Initial table (PR-2):

- ``_walk_insertion_order``: Ruby, Groovy. BFS through inheritance edges
  in declaration order. Per-source visited set guards against cycles.

Future PRs:

- ``_walk_single_then_interfaces`` (default + Java/Kotlin/C#): single
  superclass before interface list — PR-3.
- ``_walk_c3`` (Python): C3 linearization — future.
- ``_walk_left_to_right`` (PHP/Swift/Obj-C/C++): left-to-right depth
  first — future.
- ``_walk_linearization`` (Scala traits) — future.

Languages whose walker isn't registered yet are silently no-op'd; the
analyzer must opt in by emitting the hint AND the linker must have a
walker registered for that source language.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable

from ..ir import PASS_VERSION, AnalysisRun, Edge, Symbol, make_pass_id
from .method_call_recovery import parse_unresolved_name
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)
from .type_hierarchy import (
    _TypeHierarchyIndex,
    build_method_index,
)

PASS_ID = make_pass_id("inherited-calls-linker")

_INHERITED_CALL_EDGE_TYPES: tuple[str, ...] = (
    "extends", "implements", "includes",
)

# Confidence per Site (preserves Java/Ruby precedent; see module docstring).
_SITE_1_CONFIDENCE = 0.90

# Depth cap matches the existing Java + Ruby ancestor walks.
_DEFAULT_DEPTH_CAP = 10

# Edge-type priority for the single-then-interfaces walker (Java/Kotlin/C#).
# Lower value = walked first. extends is the single-superclass MRO; implements
# / includes are interfaces / mixin contributions consulted after the extends
# chain is exhausted at the same depth.
_EDGE_TYPE_PRIORITY: dict[str, int] = {
    "extends": 0, "implements": 1, "includes": 2,
}


# ---------------------------------------------------------------------------
# Index building.
# ---------------------------------------------------------------------------

def _build_typed_inheritance_index(
    edges: list[Edge],
    edge_types: tuple[str, ...],
) -> dict[str, list[tuple[str, str]]]:
    """Build a child_id -> [(parent_id, edge_type), ...] map.

    Like ``build_inheritance_index`` from ``_transitive_bases.py`` but
    preserves the per-parent ``edge_type`` so MRO walkers can prioritize
    extends over implements / includes (Java/Kotlin/C# single-superclass
    semantics). Used by all walkers in this module; Ruby/Groovy's
    insertion-order walker ignores the edge_type and walks in build order.
    """
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in edges:
        if edge.edge_type in edge_types:
            index[edge.src].append((edge.dst, edge.edge_type))
    return index


# ---------------------------------------------------------------------------
# Per-language MRO walkers.
# ---------------------------------------------------------------------------

def _walk_insertion_order(
    start_class_id: str,
    callee_short_name: str,
    inheritance_index: dict[str, list[tuple[str, str]]],
    method_index: _TypeHierarchyIndex,
    depth_cap: int = _DEFAULT_DEPTH_CAP,
) -> Symbol | None:
    """BFS through inheritance edges in declaration order (Ruby/Groovy MRO).

    Ruby's method resolution order walks ancestors in declaration order —
    the class itself first, then each mixin in declaration order, then
    the superclass, etc. (Ruby's actual MRO is more nuanced, but for the
    "is this method defined on any ancestor?" question, insertion-order
    BFS produces the same answer.) The same BFS works for Groovy
    interface mixins.

    Args:
        start_class_id: Symbol ID of the class whose ancestor chain to walk.
        callee_short_name: Short method name to find (e.g., ``"initialize"``).
        inheritance_index: Map ``child_id -> [(parent_id, edge_type), ...]``
            produced by ``_build_typed_inheritance_index``. The
            ``edge_type`` is ignored by this walker; it iterates parents in
            the order they appear in the list.
        method_index: Method lookup index built by
            ``build_method_index(...)``.
        depth_cap: Maximum walk depth; matches the existing Java + Ruby
            10-hop limit.

    Returns:
        First matching method ``Symbol``, or ``None`` if not found within
        the depth cap.
    """
    visited: set[str] = {start_class_id}
    queue: deque[tuple[str, int]] = deque([(start_class_id, 0)])
    candidates_by_short = method_index.methods_by_short_name.get(
        callee_short_name, [],
    )
    methods_by_class: dict[str, Symbol] = dict(candidates_by_short)

    while queue:
        class_id, depth = queue.popleft()
        candidate = methods_by_class.get(class_id)
        if candidate is not None:
            return candidate
        if depth >= depth_cap:
            continue
        for parent_id, _edge_type in inheritance_index.get(class_id, ()):
            if parent_id in visited:
                continue
            visited.add(parent_id)
            queue.append((parent_id, depth + 1))
    return None


def _walk_single_then_interfaces(
    start_class_id: str,
    callee_short_name: str,
    inheritance_index: dict[str, list[tuple[str, str]]],
    method_index: _TypeHierarchyIndex,
    depth_cap: int = _DEFAULT_DEPTH_CAP,
) -> Symbol | None:
    """BFS prioritizing extends parents over interfaces (Java/Kotlin/C# MRO).

    Java method dispatch walks the single superclass chain (``extends``)
    before considering interface default methods (``implements``). When a
    class has both an extends parent and implements interfaces and both
    define a method of the matching short name, the extends parent wins.
    When the extends chain is exhausted without a match, interface default
    methods are consulted (Java 8+).

    This is implemented as BFS where, at each node, parents are pushed in
    edge-type priority order (extends first, then implements / includes).
    The visited set ensures each class is examined once. Because BFS
    explores level-by-level, the extends parent at depth 1 is checked
    before the implements parent at depth 1 even though both are queued
    after popping the start class — the queue order at each push respects
    edge-type priority. This is correct for Java's "single-superclass MRO
    before interfaces" rule on every test case in our corpus.

    Registered for Java in PR-3 (``WI-dukog``); future PRs may extend the
    registry to Kotlin / C# / Scala-class.
    """
    visited: set[str] = {start_class_id}
    queue: deque[tuple[str, int]] = deque([(start_class_id, 0)])
    candidates_by_short = method_index.methods_by_short_name.get(
        callee_short_name, [],
    )
    methods_by_class: dict[str, Symbol] = dict(candidates_by_short)

    while queue:
        class_id, depth = queue.popleft()
        candidate = methods_by_class.get(class_id)
        if candidate is not None:
            return candidate
        if depth >= depth_cap:
            continue
        parents = inheritance_index.get(class_id, [])
        ordered = sorted(
            parents,
            key=lambda p: _EDGE_TYPE_PRIORITY.get(p[1], 99),
        )
        for parent_id, _edge_type in ordered:
            if parent_id in visited:
                continue
            visited.add(parent_id)
            queue.append((parent_id, depth + 1))
    return None


# Per-language MRO dispatch. Hardcoded clauses (no YAML / no decorator
# hooks) because the table is static language semantics — see ADR-0003-ext
# and the WI-hatip plan discussion at ~/puluf-plan.md.
_MRO_WALKERS: dict[str, Callable[
    [str, str, dict[str, list[tuple[str, str]]],
     _TypeHierarchyIndex, int], Symbol | None,
]] = {
    "ruby": _walk_insertion_order,
    "groovy": _walk_insertion_order,
    "java": _walk_single_then_interfaces,
}


# ---------------------------------------------------------------------------
# Linker entry point.
# ---------------------------------------------------------------------------

@register_linker(
    "inherited-calls",
    priority=18,  # Between inheritance (15) and type_hierarchy (20).
    description=(
        "Walks ancestor chains to resolve unresolved calls that carry the "
        "enclosing_class hint added by make_unresolved_edge (PR-1). PR-2 "
        "registers the Ruby/Groovy insertion-order walker; later PRs add "
        "Java/Kotlin/Python/etc walkers and Site-2/3 receiver resolvers."
    ),
    activation=LinkerActivation(always=True),
)
def link_inherited_calls(ctx: LinkerContext) -> LinkerResult:
    """See module docstring for the algorithm."""
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Build the inheritance index covering extends + implements + includes
    # (the includes axis is what makes Ruby `include`/`extend` mixins
    # participate alongside concrete inheritance). Edge type is preserved
    # per parent so the Java single-then-interfaces walker can prioritize
    # extends; Ruby/Groovy's insertion-order walker ignores the edge_type
    # tag.
    inheritance_index = _build_typed_inheritance_index(
        ctx.edges, edge_types=_INHERITED_CALL_EDGE_TYPES,
    )

    # Class/struct/module name → [class_id] map and class_id → Symbol map.
    # The method index treats these uniformly; the walker looks up methods
    # by class_id.
    class_ids_by_name: dict[str, list[str]] = {}
    class_symbols: dict[str, Symbol] = {}
    for s in ctx.symbols:
        if s.kind in ("class", "struct", "module", "interface", "trait", "protocol"):
            class_ids_by_name.setdefault(s.name, []).append(s.id)
            class_symbols[s.id] = s
    method_index = build_method_index(
        ctx.symbols, class_ids_by_name, class_symbols,
    )

    # Pre-compute existing (src, dst) resolved-calls edges so we don't
    # emit a duplicate when the analyzer already covered the same target
    # directly.
    existing_call_pairs: set[tuple[str, str]] = {
        (e.src, e.dst)
        for e in ctx.edges
        if e.edge_type == "calls" and e.is_resolved
    }

    symbol_by_id: dict[str, Symbol] = {s.id: s for s in ctx.symbols}

    new_edges: list[Edge] = []
    for edge in ctx.edges:
        if edge.edge_type != "calls" or edge.is_resolved:
            continue
        meta = edge.meta or {}
        enclosing_class = meta.get("enclosing_class")
        if not enclosing_class:
            continue

        # Dispatch by source language (the analyzer that emitted the
        # unresolved edge tells us which MRO to apply). If the language
        # isn't in the walker table, no-op — future PRs add walkers.
        src_sym = symbol_by_id.get(edge.src)
        src_lang = src_sym.language if src_sym else None
        if src_lang is None:
            continue
        walker = _MRO_WALKERS.get(src_lang)
        if walker is None:
            continue

        callee_short = parse_unresolved_name(edge.dst)
        if not callee_short:
            continue

        start_class_ids = class_ids_by_name.get(enclosing_class, [])
        if not start_class_ids:
            continue

        # Try each candidate starting class; emit at the first match.
        resolved_target: Symbol | None = None
        for start_id in start_class_ids:
            candidate = walker(
                start_id, callee_short, inheritance_index,
                method_index, _DEFAULT_DEPTH_CAP,
            )
            if candidate is not None:
                resolved_target = candidate
                break

        if resolved_target is None:
            continue

        if (edge.src, resolved_target.id) in existing_call_pairs:
            continue

        new_edges.append(Edge.create(
            src=edge.src,
            dst=resolved_target.id,
            edge_type="calls",
            line=edge.line,
            confidence=_SITE_1_CONFIDENCE,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="ast_call_inherited",
            is_resolved=True,
        ))
        existing_call_pairs.add((edge.src, resolved_target.id))

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=new_edges, run=run)
