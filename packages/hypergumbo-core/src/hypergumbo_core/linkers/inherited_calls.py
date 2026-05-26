# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker: inherited-call resolution via ancestor walking.

INV-nilud campaign META — see ``~/puluf-plan.md``. PR-2 (WI-hatip) lays
the substrate + Ruby `include`/`extend` mixin coverage; PR-3 (WI-dukog)
adds Java Site-1; PR-4 (WI-sivuk) threads Site-2/3 hints; PR-5
(WI-puvil) lifts Java Sites 2 and 3 fully — this module.

Why a linker
------------
Inheritance-aware call resolution used to live in two analyzers: Java
(``java.py:1414-1622``, three call sites emitting ``ast_call_inherited``
/ ``ast_call_inherited_method`` / ``ast_call_inherited_field``) and
Ruby (``ruby.py:1901-1956``, ``_find_inherited_initialize``). The other
12 analyzers shared the same silent gap with no equivalent walk. The
WI-puluf Ruby mixin investigation surfaced the deeper duplication, and
per ADR-0003-ext this work belongs in a Tier-2 Infrastructure linker
— same layer as ``inheritance.py``.

Three Sites
-----------
The linker dispatches per unresolved-call edge by which hint(s)
``make_unresolved_edge`` attached to ``Edge.meta``, in specificity order:

- **Site 3** (``inherited_field_receiver`` + ``enclosing_class``):
  ``field.method()`` where ``field`` is declared on an ancestor of
  ``enclosing_class``. The linker walks ``extends``/``implements``/
  ``includes`` edges from the enclosing class, consults each parent
  symbol's ``meta["fields"]`` (populated by the Java analyzer in PR-5)
  for a matching field name, looks up the field's type as a class
  symbol, and resolves the method on that type's MRO. Emits
  ``ast_call_inherited_field`` at confidence 0.80.

- **Site 2** (``receiver_type_hint``): ``var.method()`` where the
  analyzer inferred ``var``'s type. Three-step resolution:
  (1) direct method on the type → ``ast_call_type_inferred`` at 0.85;
  (2) MRO walk on the type → ``ast_call_inherited_method`` at 0.70;
  (3) fallback to the type symbol itself → ``ast_call_inherited_method``
  at 0.70. Step (2) is a forward improvement over the pre-PR-5
  in-analyzer behavior, which never walked the chain for Site 2.

- **Site 1** (``enclosing_class`` only): bare or ``this.method()`` call.
  Walks the enclosing class's MRO via the registered per-language
  walker. Emits ``ast_call_inherited`` at 0.90.

Each unresolved edge produces at most one resolved edge from this
linker. Site 3 takes priority when both ``enclosing_class`` and
``inherited_field_receiver`` are present; Site 3 finding nothing does
NOT fall through to Site 1 (the call shape is structurally
``field.method``, not bare).

Priority + ordering
-------------------
Registered at ``priority=18``, between ``inheritance`` (15, produces the
extends/implements/includes edges this linker walks) and
``type_hierarchy`` (60, the next caller of ``build_method_index``). The
linker only emits NEW edges; it never mutates existing ones.

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
_SITE_2_DIRECT_CONFIDENCE = 0.85  # ast_call_type_inferred
_SITE_2_INHERITED_CONFIDENCE = 0.70  # ast_call_inherited_method
_SITE_3_CONFIDENCE = 0.80  # ast_call_inherited_field

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
# Site-3 helpers (inherited-field walk).
# ---------------------------------------------------------------------------


def _walk_parents_for_field(
    start_class_id: str,
    field_short_name: str,
    inheritance_index: dict[str, list[tuple[str, str]]],
    class_symbols: dict[str, Symbol],
    depth_cap: int = _DEFAULT_DEPTH_CAP,
) -> str | None:
    """BFS the parent chain looking for ``meta["fields"][field_short_name]``.

    Used by Site-3 resolution (WI-puvil / PR-5). The Java analyzer
    populates each class symbol's ``meta["fields"]`` with the
    ``{field_name: type_name}`` map it would otherwise have used in the
    in-analyzer ``class_fields`` walk. This walker traverses the
    enclosing class's parents and returns the first field-type name it
    finds; the caller then looks up the method on that type.

    Args:
        start_class_id: Symbol ID of the enclosing class. The walk does
            NOT examine its own fields — this is by design, matching the
            analyzer's pre-PR-5 behavior, which only fires Site-3 when the
            receiver isn't in the enclosing class's own field set.
        field_short_name: The receiver-as-field name to look for.
        inheritance_index: Map ``child_id -> [(parent_id, edge_type), ...]``.
        class_symbols: Map ``class_id -> Symbol``. Used to read
            ``meta["fields"]`` from each parent.
        depth_cap: Maximum walk depth.

    Returns:
        First matching field type name (e.g. ``"Logger"``), or ``None``.
    """
    visited: set[str] = {start_class_id}
    queue: deque[tuple[str, int]] = deque([(start_class_id, 0)])
    while queue:
        class_id, depth = queue.popleft()
        if depth >= depth_cap:
            continue
        for parent_id, _edge_type in inheritance_index.get(class_id, ()):
            if parent_id in visited:
                continue
            visited.add(parent_id)
            parent_sym = class_symbols.get(parent_id)
            if parent_sym is not None and parent_sym.meta:
                fields = parent_sym.meta.get("fields") or {}
                if field_short_name in fields:
                    return fields[field_short_name]
            queue.append((parent_id, depth + 1))
    return None


def _extract_method_short_name(callee_name: str) -> str:
    """Pull the method short name from a Site-2/3 unresolved callee.

    Site-1 edges emit just the method name (``"foo"``). Site-2/3 emit
    ``"receiver.method"`` (e.g., ``"owners.save"``, ``"log.info"``). We
    want just ``"method"`` so we can index into ``method_index``.
    Returns the input unchanged when no dot is present (Site-1 shape).
    """
    if "." in callee_name:
        return callee_name.rsplit(".", 1)[-1]
    return callee_name


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
    # CNF: registered walkers exist for Java, Ruby, Groovy (per
    # ``_MRO_WALKERS``). Future PRs extend the set to Python/Kotlin/etc.
    # This linker also depends on the inheritance-linker pass producing
    # extends/implements/includes edges first.
    depends_on=[
        ["inheritance-linker"],
        ["java", "ruby", "groovy"],
    ],
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

        # Dispatch by hint kind, in specificity order:
        # 1. Site 3 (inherited-field-receiver + enclosing_class) — most
        #    constrained call shape (receiver is a field on a parent).
        # 2. Site 2 (receiver_type_hint) — typed local variable receiver.
        # 3. Site 1 (enclosing_class only) — bare / ``this`` call.
        # Each edge produces at most one resolved edge from this linker.
        receiver_type_hint = meta.get("receiver_type_hint")
        inherited_field_receiver = meta.get("inherited_field_receiver")
        enclosing_class = meta.get("enclosing_class")

        if not (
            receiver_type_hint or inherited_field_receiver
            or enclosing_class
        ):
            continue

        src_sym = symbol_by_id.get(edge.src)
        src_lang = src_sym.language if src_sym else None
        if src_lang is None:
            continue

        callee_short = parse_unresolved_name(edge.dst)
        if not callee_short:
            continue

        emitted = _try_resolve(
            edge=edge, callee_short=callee_short,
            src_lang=src_lang,
            receiver_type_hint=receiver_type_hint,
            inherited_field_receiver=inherited_field_receiver,
            enclosing_class=enclosing_class,
            class_ids_by_name=class_ids_by_name,
            class_symbols=class_symbols,
            method_index=method_index,
            inheritance_index=inheritance_index,
            existing_call_pairs=existing_call_pairs,
            run=run,
        )
        if emitted is not None:
            new_edges.append(emitted)
            existing_call_pairs.add((emitted.src, emitted.dst))

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=new_edges, run=run)


# ---------------------------------------------------------------------------
# Per-Site resolvers.
# ---------------------------------------------------------------------------


def _try_resolve(
    *,
    edge: Edge,
    callee_short: str,
    src_lang: str,
    receiver_type_hint: str | None,
    inherited_field_receiver: str | None,
    enclosing_class: str | None,
    class_ids_by_name: dict[str, list[str]],
    class_symbols: dict[str, Symbol],
    method_index: _TypeHierarchyIndex,
    inheritance_index: dict[str, list[tuple[str, str]]],
    existing_call_pairs: set[tuple[str, str]],
    run: AnalysisRun,
) -> Edge | None:
    """Dispatch an unresolved edge to the right Site resolver.

    Returns the resolved edge (or ``None`` if no Site matches). Caller
    appends to ``new_edges`` and updates the dedupe set.
    """
    method_short = _extract_method_short_name(callee_short)

    # Site 3 (most specific): inherited-field-receiver + enclosing_class.
    if inherited_field_receiver and enclosing_class:
        emitted = _resolve_site3(
            edge=edge, method_short=method_short,
            src_lang=src_lang,
            enclosing_class=enclosing_class,
            inherited_field_receiver=inherited_field_receiver,
            class_ids_by_name=class_ids_by_name,
            class_symbols=class_symbols,
            method_index=method_index,
            inheritance_index=inheritance_index,
            existing_call_pairs=existing_call_pairs,
            run=run,
        )
        if emitted is not None:
            return emitted
        # If Site 3 finds nothing, do NOT fall through to Site 1 — the
        # edge's call shape is "field.method", not bare-call.
        return None

    # Site 2: typed receiver.
    if receiver_type_hint:
        return _resolve_site2(
            edge=edge, method_short=method_short,
            receiver_type_hint=receiver_type_hint,
            src_lang=src_lang,
            class_ids_by_name=class_ids_by_name,
            class_symbols=class_symbols,
            method_index=method_index,
            inheritance_index=inheritance_index,
            existing_call_pairs=existing_call_pairs,
            run=run,
        )

    # Site 1: bare/this call (existing PR-2/PR-3 path).
    if enclosing_class:
        return _resolve_site1(
            edge=edge, callee_short=callee_short,
            src_lang=src_lang,
            enclosing_class=enclosing_class,
            class_ids_by_name=class_ids_by_name,
            method_index=method_index,
            inheritance_index=inheritance_index,
            existing_call_pairs=existing_call_pairs,
            run=run,
        )
    return None  # pragma: no cover  # unreachable: caller gated on any-hint


def _resolve_site1(
    *,
    edge: Edge,
    callee_short: str,
    src_lang: str,
    enclosing_class: str,
    class_ids_by_name: dict[str, list[str]],
    method_index: _TypeHierarchyIndex,
    inheritance_index: dict[str, list[tuple[str, str]]],
    existing_call_pairs: set[tuple[str, str]],
    run: AnalysisRun,
) -> Edge | None:
    """Site 1: bare / ``this`` call → walk enclosing class's MRO."""
    walker = _MRO_WALKERS.get(src_lang)
    if walker is None:
        return None
    start_class_ids = class_ids_by_name.get(enclosing_class, [])
    if not start_class_ids:
        return None
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
        return None
    if (edge.src, resolved_target.id) in existing_call_pairs:
        return None
    return Edge.create(
        src=edge.src, dst=resolved_target.id, edge_type="calls",
        line=edge.line, confidence=_SITE_1_CONFIDENCE,
        origin=PASS_ID, origin_run_id=run.execution_id,
        evidence_type="ast_call_inherited",
        is_resolved=True,
    )


def _resolve_site2(
    *,
    edge: Edge,
    method_short: str,
    receiver_type_hint: str,
    src_lang: str,
    class_ids_by_name: dict[str, list[str]],
    class_symbols: dict[str, Symbol],
    method_index: _TypeHierarchyIndex,
    inheritance_index: dict[str, list[tuple[str, str]]],
    existing_call_pairs: set[tuple[str, str]],
    run: AnalysisRun,
) -> Edge | None:
    """Site 2: ``var.method()`` typed receiver.

    Three-step resolution preserving Java analyzer's pre-PR-5 behavior:

    1. **Direct lookup** — method defined on the type itself →
       ``ast_call_type_inferred`` at 0.85 (matches analyzer's Case 3 if).
    2. **MRO walk** — method on an ancestor of the type →
       ``ast_call_inherited_method`` at 0.70 (forward improvement: the
       analyzer never walked the chain for Site 2).
    3. **Type-symbol fallback** — method nowhere on the chain →
       ``ast_call_inherited_method`` at 0.70 pointing to the type
       symbol itself (matches analyzer's Case 3 else fallback).

    Steps 2 + 3 require the language to have an MRO walker; steps 1 + 3
    work without one (used by analyzers whose MRO isn't registered yet).
    """
    type_class_ids = class_ids_by_name.get(receiver_type_hint, [])
    if not type_class_ids:
        return None

    walker = _MRO_WALKERS.get(src_lang)
    candidates_by_short = method_index.methods_by_short_name.get(
        method_short, [],
    )
    methods_by_class: dict[str, Symbol] = dict(candidates_by_short)

    # Step 1: direct method on the type.
    for type_class_id in type_class_ids:
        direct = methods_by_class.get(type_class_id)
        if direct is None:
            continue
        if (edge.src, direct.id) in existing_call_pairs:  # pragma: no cover
            return None
        return Edge.create(
            src=edge.src, dst=direct.id, edge_type="calls",
            line=edge.line, confidence=_SITE_2_DIRECT_CONFIDENCE,
            origin=PASS_ID, origin_run_id=run.execution_id,
            evidence_type="ast_call_type_inferred",
            is_resolved=True,
        )

    # Step 2: MRO walk (requires a registered walker).
    if walker is not None:
        for type_class_id in type_class_ids:
            via_mro = walker(
                type_class_id, method_short, inheritance_index,
                method_index, _DEFAULT_DEPTH_CAP,
            )
            if via_mro is not None:
                if (edge.src, via_mro.id) in existing_call_pairs:  # pragma: no cover
                    return None
                return Edge.create(
                    src=edge.src, dst=via_mro.id, edge_type="calls",
                    line=edge.line,
                    confidence=_SITE_2_INHERITED_CONFIDENCE,
                    origin=PASS_ID, origin_run_id=run.execution_id,
                    evidence_type="ast_call_inherited_method",
                    is_resolved=True,
                )

    # Step 3: fallback to the type symbol itself.
    type_class_id = type_class_ids[0]
    type_sym = class_symbols.get(type_class_id)
    if type_sym is None:  # pragma: no cover
        return None
    if (edge.src, type_sym.id) in existing_call_pairs:  # pragma: no cover
        return None
    return Edge.create(
        src=edge.src, dst=type_sym.id, edge_type="calls",
        line=edge.line, confidence=_SITE_2_INHERITED_CONFIDENCE,
        origin=PASS_ID, origin_run_id=run.execution_id,
        evidence_type="ast_call_inherited_method",
        is_resolved=True,
    )


def _resolve_site3(
    *,
    edge: Edge,
    method_short: str,
    src_lang: str,
    enclosing_class: str,
    inherited_field_receiver: str,
    class_ids_by_name: dict[str, list[str]],
    class_symbols: dict[str, Symbol],
    method_index: _TypeHierarchyIndex,
    inheritance_index: dict[str, list[tuple[str, str]]],
    existing_call_pairs: set[tuple[str, str]],
    run: AnalysisRun,
) -> Edge | None:
    """Site 3: ``field.method()`` where ``field`` is declared on an
    ancestor of ``enclosing_class``.

    Walks the parent chain via ``_walk_parents_for_field`` to find the
    field's type, then looks up the method on that type (direct or via
    MRO walk if one is registered). Emits ``ast_call_inherited_field``
    at confidence 0.80.
    """
    encl_class_ids = class_ids_by_name.get(enclosing_class, [])
    if not encl_class_ids:  # pragma: no cover
        return None

    # Find the field's type by walking the enclosing class's parents.
    field_type: str | None = None
    for encl_id in encl_class_ids:
        ft = _walk_parents_for_field(
            encl_id, inherited_field_receiver, inheritance_index,
            class_symbols, _DEFAULT_DEPTH_CAP,
        )
        if ft is not None:
            field_type = ft
            break
    if field_type is None:
        return None

    # Look up the field's type as a class symbol.
    field_type_class_ids = class_ids_by_name.get(field_type, [])
    if not field_type_class_ids:
        return None

    # Resolve the method on the field's type (direct or MRO walk).
    walker = _MRO_WALKERS.get(src_lang)
    candidates_by_short = method_index.methods_by_short_name.get(
        method_short, [],
    )
    methods_by_class: dict[str, Symbol] = dict(candidates_by_short)

    resolved_target: Symbol | None = None
    for ftid in field_type_class_ids:
        direct = methods_by_class.get(ftid)
        if direct is not None:
            resolved_target = direct
            break
        if walker is not None:
            via = walker(
                ftid, method_short, inheritance_index,
                method_index, _DEFAULT_DEPTH_CAP,
            )
            if via is not None:
                resolved_target = via
                break

    if resolved_target is None:
        return None
    if (edge.src, resolved_target.id) in existing_call_pairs:  # pragma: no cover
        return None
    return Edge.create(
        src=edge.src, dst=resolved_target.id, edge_type="calls",
        line=edge.line, confidence=_SITE_3_CONFIDENCE,
        origin=PASS_ID, origin_run_id=run.execution_id,
        evidence_type="ast_call_inherited_field",
        is_resolved=True,
    )
