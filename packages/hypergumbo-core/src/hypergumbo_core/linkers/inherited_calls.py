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
per ADR-3bbb this work belongs in a Tier-2 Infrastructure linker
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
- ``_walk_single_then_interfaces`` (Java; PR-3 / WI-dukog): single
  superclass (``extends``) walked before interfaces
  (``implements``/``includes``) via edge-type priority. Default /
  Kotlin / C# extension still future.

- ``_walk_c3`` (Python; WI-hiziz / D1): true C3 linearization over the
  in-tree ``extends`` chain. Python's MRO is C3, not insertion-order BFS —
  the two diverge on uneven-depth diamonds, where BFS picks the wrong
  ancestor. Registered for ``python`` only; deliberately NOT added to
  ``_LEGACY_SITE2_LANGS`` (Python keeps strict Site-2, no Step-3 fallback).

Future PRs:

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

PASS_ID = make_pass_id("inherited-calls")

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


def _reorder_python_bases_by_source(
    inheritance_index: dict[str, list[tuple[str, str]]],
    class_symbols: dict[str, Symbol],
) -> None:
    """Reorder each Python class's parent list to match SOURCE base order.

    C3 correctness needs the left-to-right base order (``class D(B, C)`` →
    ``[B, C]``). ``_build_typed_inheritance_index`` preserves edge-ARRIVAL
    order, which diverges for a QUALIFIED in-tree base: py.py's short-name base
    resolver misses ``class D(mod.Bar, Mixin)``'s ``mod.Bar``, so that
    ``extends`` edge is recovered LATE by the inheritance-linker and arrives
    after ``Mixin`` — reversing the base order and (on a method-name collision)
    resolving to the wrong ancestor. Each Python child's authoritative source
    order lives in ``meta['base_classes']`` (``node.bases`` order, dotted names
    intact); parents are sorted by their short name's position there, with
    parents whose short name isn't found kept in a stable trailing block. In
    place; only Python multi-base children are touched, so ruby / groovy / java
    walkers (which rely on edge-arrival / edge-type order) are unaffected.
    """
    for child in class_symbols.values():
        if child.language != "python":
            continue
        parents = inheritance_index.get(child.id)
        if not parents or len(parents) < 2:
            continue
        base_names = [
            str(b).split(".")[-1]
            for b in ((child.meta or {}).get("base_classes") or [])
        ]
        if not base_names:
            continue
        pos = {name: i for i, name in enumerate(base_names)}
        fallback = len(base_names)
        decorated: list[tuple[int, int, tuple[str, str]]] = []
        for arrival, parent_entry in enumerate(parents):
            psym = class_symbols.get(parent_entry[0])
            if psym is None:  # pragma: no cover - an extends dst is always an in-tree class
                short = ""
            else:
                short = psym.name.split(".")[-1]
            decorated.append((pos.get(short, fallback), arrival, parent_entry))
        decorated.sort()
        inheritance_index[child.id] = [entry for _, _, entry in decorated]


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


def _c3_merge(sequences: list[list[str]]) -> list[str]:
    """C3 merge: combine linearizations preserving monotonicity + local order.

    Repeatedly takes a *good head* — a class that is the head of some sequence
    and appears in NO sequence's tail — appends it, and removes it from every
    sequence. A mathematically-inconsistent hierarchy (real Python raises
    ``TypeError``) has no good head; a static walker must stay total, so we
    DEGRADE by taking the first available head instead of raising. The result
    has no duplicates (each pick is removed from all sequences).
    """
    seqs = [list(s) for s in sequences if s]
    result: list[str] = []
    while seqs:
        head: str | None = None
        for seq in seqs:
            candidate = seq[0]
            if not any(candidate in s[1:] for s in seqs):
                head = candidate
                break
        if head is None:  # inconsistent hierarchy — degrade, never raise
            head = seqs[0][0]
        result.append(head)
        seqs = [[c for c in s if c != head] for s in seqs]
        seqs = [s for s in seqs if s]
    return result


def _linearize_c3(
    class_id: str,
    inheritance_index: dict[str, list[tuple[str, str]]],
    depth_cap: int = _DEFAULT_DEPTH_CAP,
    _memo: dict[str, list[str] | None] | None = None,
    _in_progress: frozenset[str] = frozenset(),
) -> list[str] | None:
    """Compute the C3 linearization (Python MRO order) of ``class_id``.

    ``L[C] = [C] + merge(L[B1], ..., L[Bn], [B1, ..., Bn])`` over the in-tree
    ancestor subgraph, using the ORDERED base list from ``inheritance_index``
    (left-to-right ``extends`` order, which C3's local-precedence rule needs).
    Results are memoized per class.

    Returns ``None`` — a "cannot linearize reliably, bias to unresolved" signal
    — rather than a best-effort order in two cases that would otherwise emit a
    CONFIDENTLY-WRONG resolution: an inheritance **cycle** (``_in_progress``;
    malformed, can't be valid Python) and **depth exhaustion** (``depth_cap``;
    a truncated branch would silently drop a precedence edge and reorder two
    real ancestors). ``None`` propagates: if any base branch is unreliable, the
    whole linearization is. A final dedup pass keeps the result robust to
    repeated classes. Never raises.
    """
    if _memo is None:
        _memo = {}
    if class_id in _memo:
        return _memo[class_id]
    if class_id in _in_progress or depth_cap <= 0:
        return None
    child_in_progress = _in_progress | {class_id}
    parents = [parent_id for parent_id, _edge_type in inheritance_index.get(class_id, ())]
    seqs: list[list[str]] = []
    for parent_id in parents:
        parent_lin = _linearize_c3(
            parent_id, inheritance_index, depth_cap - 1, _memo, child_in_progress,
        )
        if parent_lin is None:
            return None
        seqs.append(parent_lin)
    if parents:
        seqs.append(list(parents))
    merged = [class_id] + _c3_merge(seqs)
    seen: set[str] = set()
    deduped = [c for c in merged if not (c in seen or seen.add(c))]
    _memo[class_id] = deduped
    return deduped


def _walk_c3(
    start_class_id: str,
    callee_short_name: str,
    inheritance_index: dict[str, list[tuple[str, str]]],
    method_index: _TypeHierarchyIndex,
    depth_cap: int = _DEFAULT_DEPTH_CAP,
) -> Symbol | None:
    """C3-linearization MRO walk (Python).

    Python's MRO is C3 linearization, NOT the insertion-order BFS the Ruby /
    Groovy walker uses. They agree on single inheritance and even diamonds but
    diverge on uneven-depth diamonds, where insertion-order picks the *wrong*
    ancestor (a confidently-wrong ``calls`` edge). This walker computes the C3
    order and returns the first class in it that defines ``callee_short_name``;
    an un-linearizable hierarchy (cycle / too deep) biases to unresolved.

    Scope caveat: the linearization spans only in-tree bases. An EXTERNAL base
    (``dict`` / ``Enum`` / a 3rd-party class) produces no ``extends`` edge, so
    it is invisible to the walk — and if such a base sits ahead of the in-tree
    ancestor in the real MRO *and* defines the same method name, the walk can
    resolve to the wrong (in-tree) method. This is a confidently-wrong, not
    merely missing, edge; it is shared with every intra-repo-only walker and
    tracked as a fast-follow (gate on a fully-in-tree ancestry).
    """
    candidates_by_short = method_index.methods_by_short_name.get(
        callee_short_name, [],
    )
    methods_by_class: dict[str, Symbol] = dict(candidates_by_short)
    if not methods_by_class:
        return None
    linearization = _linearize_c3(start_class_id, inheritance_index, depth_cap)
    if linearization is None:
        return None
    for class_id in linearization:
        candidate = methods_by_class.get(class_id)
        if candidate is not None:
            return candidate
    return None


# Per-language MRO dispatch. Hardcoded clauses (no YAML / no decorator
# hooks) because the table is static language semantics — see ADR-3bbb
# and the WI-hatip plan discussion at ~/puluf-plan.md.
_MRO_WALKERS: dict[str, Callable[
    [str, str, dict[str, list[tuple[str, str]]],
     _TypeHierarchyIndex, int], Symbol | None,
]] = {
    "ruby": _walk_insertion_order,
    "groovy": _walk_insertion_order,
    "java": _walk_single_then_interfaces,
    "python": _walk_c3,
}


# Languages that get the LEGACY-PERMISSIVE Site-2 typed-receiver resolution:
# the Step-3 type-symbol fallback fires, and a same-name-class collision
# resolves by first-match rather than biasing to unresolved. This preserves
# the Java analyzer's pre-PR-5 behavior and the INV-nilud-validated Java
# Site-2 edges. Only ``java`` currently emits ``receiver_type_hint`` (Ruby /
# Groovy emit Site-1/Site-3 hints only), so ``java`` is the sole member.
#
# Every OTHER (newly-onboarded) language — Python (WI-noham Part A) and any
# future emitter — gets the STRICT INV-fahub mode: resolve ONLY when the
# method is directly on the concretely-named, unambiguous type (linker Step 1
# / Step 2 MRO), NEVER the Step-3 type-symbol fallback (which would mint a
# ``calls→class`` edge — a new runtime_coherence partition — and bind an
# under-determined receiver to a class), and NEVER a same-name-class first
# match (INV-fahub: an under-determined receiver must stay ambiguous/external).
#
# DELIBERATELY DECOUPLED from ``_MRO_WALKERS``: this is NOT ``set(_MRO_WALKERS)``.
# Adding a Python MRO walker (deferred D1) must NOT silently re-enable Python's
# permissive Step-3 fallback; a language opts into permissiveness only by being
# listed HERE, consciously, after allowlisting the ``calls→class`` partition.
_LEGACY_SITE2_LANGS: frozenset[str] = frozenset({"java"})


# ``_SITE1_STRICT_LANGS`` — languages whose Site-1 (``enclosing_class``)
# resolution applies the INV-fahub same-short-name ambiguity guard (WI-hiziz
# PR-2). This is DELIBERATELY NOT ``all langs except _LEGACY_SITE2_LANGS``: the
# guard's premise is that two in-tree classes sharing a short name are DISTINCT
# classes (true in Python, which has module namespaces — a bare ``enclosing_class``
# name genuinely under-determines which module's class the caller is in). It is
# FALSE in Ruby (and any single-global-namespace language): a *reopened* class
# (``class Worker`` opened in several files — ubiquitous in Rails) emits one class
# symbol per definition, all sharing the same short name, yet they are ONE logical
# class. Applying the guard there would suppress the INV-nilud-validated
# ``X.new → inherited #initialize`` Site-1 resolution this linker was built to
# preserve. So the guard is opt-in per language, and only Python (whose Site-1
# WI-hiziz PR-2 onboards) is a member; Ruby/Groovy keep their loop-all-first-match
# Site-1 behavior. WI-supat (D3) threads a concrete class id to recover Python's
# guard-sacrificed recall.
_SITE1_STRICT_LANGS: frozenset[str] = frozenset({"python"})


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
    # WI-hiziz (D1): C3 needs left-to-right base order, but a qualified in-tree
    # base's extends edge arrives out of order (recovered late by the
    # inheritance-linker). Restore each Python class's source base order from
    # meta['base_classes'] before the C3 walker consumes the index.
    _reorder_python_bases_by_source(inheritance_index, class_symbols)
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
    # WI-hiziz PR-2: INV-fahub ambiguity guard for Site-1. ``enclosing_class``
    # is a NAME only; in a language with module namespaces (``_SITE1_STRICT_LANGS``
    # — Python) two in-tree classes sharing that short name are DISTINCT classes,
    # so the enclosing receiver is under-determined — bias to unresolved rather
    # than binding whichever namesake the walk hits first. This is scoped to
    # ``_SITE1_STRICT_LANGS`` (NOT "all but Java"): in Ruby the same short name is
    # a class REOPENING (one logical class), so applying the guard would suppress
    # its INV-nilud-validated Site-1 resolution. WI-supat (D3) threads a concrete
    # enclosing-class id to recover Python's guard-sacrificed recall (incl. the
    # cross-language-namesake over-suppression, since ``class_ids_by_name`` is
    # language-agnostic).
    if src_lang in _SITE1_STRICT_LANGS and len(start_class_ids) > 1:
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
        derived_from=[edge.src, resolved_target.id],
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

    Three-step resolution preserving the Java analyzer's pre-PR-5 behavior:

    1. **Direct lookup** — method defined on the type itself →
       ``ast_call_type_inferred`` at 0.85 (matches analyzer's Case 3 if).
    2. **MRO walk** — method on an ancestor of the type →
       ``ast_call_inherited_method`` at 0.70 (forward improvement: the
       analyzer never walked the chain for Site 2).
    3. **Type-symbol fallback** — method nowhere on the chain →
       ``ast_call_inherited_method`` at 0.70 pointing to the type
       symbol itself (matches analyzer's Case 3 else fallback).

    Step 2 requires the language to have an MRO walker; step 1 works
    without one.

    Two ``src_lang``-gated modes (WI-noham Part A):

    * **Legacy-permissive** (``src_lang in _LEGACY_SITE2_LANGS`` — Java only):
      steps 1-3 as above, first same-name class wins. Preserves the
      INV-nilud-validated Java edges.
    * **Strict INV-fahub** (every other language, e.g. Python): step 3 is
      DISABLED (no ``calls→class`` under-determined bind / new ratchet
      partition), and a same-name-class collision (``len(type_class_ids) > 1``)
      biases to unresolved rather than binding by first match. Only a method
      DIRECTLY on the single, concretely-named type (step 1 / step 2 MRO)
      resolves.
    """
    type_class_ids = class_ids_by_name.get(receiver_type_hint, [])
    if not type_class_ids:
        return None

    # Strict INV-fahub ambiguity guard: the hint carries only a class NAME.
    # When two in-repo classes share it, the receiver is under-determined —
    # bias to unresolved instead of binding to whichever same-named class
    # happens to define the method (deferred D3 threads the concrete class id
    # to recover this recall precisely).
    if src_lang not in _LEGACY_SITE2_LANGS and len(type_class_ids) > 1:
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
            derived_from=[edge.src, direct.id],
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
                    derived_from=[edge.src, via_mro.id],
                )

    # Step 3: fallback to the type symbol itself.
    # Strict INV-fahub gate: only legacy-permissive languages fall back. For
    # every newly-onboarded language the method was nowhere on the (single)
    # type's chain, so we leave the call unresolved rather than mint a
    # low-confidence ``calls→class`` edge (a new runtime_coherence partition
    # and an under-determined bind).
    if src_lang not in _LEGACY_SITE2_LANGS:
        return None
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
        derived_from=[edge.src, type_sym.id],
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
    # WI-hiziz PR-3: INV-fahub ambiguity guard on the ENCLOSING class, mirroring
    # Site-1/Site-2, scoped to _SITE1_STRICT_LANGS (Python — module namespaces
    # make same-short-name classes distinct). Two same-name enclosing classes
    # would each expose different parent fields → under-determined → bias to
    # unresolved. Java/Ruby stay permissive (reopening / legacy first-match).
    if src_lang in _SITE1_STRICT_LANGS and len(encl_class_ids) > 1:
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
    # WI-hiziz PR-3: the SECOND INV-fahub guard — the field's TYPE name is also
    # re-globalized; two same-short-name types are under-determined for a strict
    # language. Bias to unresolved rather than resolving the method on an
    # arbitrary namesake type.
    if src_lang in _SITE1_STRICT_LANGS and len(field_type_class_ids) > 1:
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
    # Dedup: the analyzer may already have a resolved calls edge to this target
    # (Python producer now exercises this — WI-hiziz PR-3 dedup test).
    if (edge.src, resolved_target.id) in existing_call_pairs:
        return None
    return Edge.create(
        src=edge.src, dst=resolved_target.id, edge_type="calls",
        line=edge.line, confidence=_SITE_3_CONFIDENCE,
        origin=PASS_ID, origin_run_id=run.execution_id,
        evidence_type="ast_call_inherited_field",
        is_resolved=True,
        derived_from=[edge.src, resolved_target.id],
    )
