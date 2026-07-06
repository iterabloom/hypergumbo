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

Language scoping (INV-milud)
----------------------------
``class_ids_by_name`` is a language-AGNOSTIC short-name index — a Python
``Handler`` and a Java ``Handler`` collide on the same key. But MRO /
typed-receiver dispatch never crosses a language boundary (cross-language
edges are the FFI/bridge linkers' job), so every place a Site turns a
receiver / enclosing-class / field-type NAME into candidate class ids goes
through ``_same_language_class_ids``, which restricts the candidates to the
call's own ``src_lang``. This is one chokepoint (not a per-Site sweep) and it
closes two failures at once: a confidently-wrong cross-language ``calls`` edge
(the Java legacy-permissive Site-2 skips the same-name ambiguity guard and
would otherwise bind to a foreign namesake), and a false-negative where a
foreign namesake inflates the strict ``len(...) > 1`` ambiguity count and
suppresses a legitimate same-language resolution.
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

    Scope caveat (INV-guviv): the linearization spans only in-tree bases. An
    EXTERNAL base (``dict`` / a 3rd-party class) produces no ``extends`` edge, so
    it is invisible to the walk — and if such a base sits ahead of the in-tree
    ancestor in the real MRO *and* defines the same method name, the walk would
    resolve to the wrong (in-tree) method. The Site resolvers guard the BUILTIN
    subset of this via ``_python_stdlib_base_shadows`` (the ``_STDLIB_BASE_METHODS``
    catalog): a cataloged builtin base declared ahead of the in-tree ancestor and
    defining the method biases the resolution to unresolved. A generic 3rd-party
    external base (not cataloged — the catalog is deliberately not open-ended, to
    avoid over-suppressing the external-mixin-first idiom) remains a documented
    residual, shared with every intra-repo-only walker.
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


# INV-guviv: hardcoded stdlib-base-method catalog for the Python C3 walker's
# external-base shadow guard. Static language semantics — hardcoded over YAML,
# consistent with ``_MRO_WALKERS`` (ADR-0029) and the sibling framework-base
# tables (``DJANGO_BASE_METHODS`` in django_orm_dispatch, ``THIRD_PARTY_BASE_METHODS``
# in _third_party_bases — "the table lives with its consumer"). Maps a builtin
# container base's short name → the named methods it defines. Scoped to the
# small, STABLE builtin collision types: a class inheriting one of these plus an
# in-tree mixin, where the builtin is declared first, has the builtin's method
# win Python's real MRO — invisible to the in-tree-only C3 walk. Non-stdlib
# external bases (a 3rd-party ORM base, an auth mixin) are DELIBERATELY not
# cataloged: that list is open-ended, and the common external-mixin-first idiom
# must not be over-suppressed (the naive "any external ahead → suppress" gate,
# rejected in the INV-guviv design workflow, regressed exactly that idiom). They
# remain a documented residual (fast-follow: extend to well-known ORM bases only
# if a bakeoff shows real hits).
_STDLIB_BASE_METHODS: dict[str, frozenset[str]] = {
    "dict": frozenset({
        "keys", "values", "items", "get", "pop", "popitem",
        "setdefault", "update", "clear", "copy", "fromkeys",
    }),
    "list": frozenset({
        "append", "extend", "insert", "remove", "pop", "clear",
        "index", "count", "sort", "reverse", "copy",
    }),
    "set": frozenset({
        "add", "remove", "discard", "pop", "clear", "copy",
        "union", "intersection", "difference", "symmetric_difference",
        "update", "intersection_update", "difference_update",
        "symmetric_difference_update", "issubset", "issuperset", "isdisjoint",
    }),
    "frozenset": frozenset({
        "copy", "union", "intersection", "difference",
        "symmetric_difference", "issubset", "issuperset", "isdisjoint",
    }),
    "tuple": frozenset({"index", "count"}),
    "str": frozenset({
        "capitalize", "casefold", "center", "count", "encode", "endswith",
        "expandtabs", "find", "format", "format_map", "index", "isalnum",
        "isalpha", "isascii", "isdecimal", "isdigit", "isidentifier",
        "islower", "isnumeric", "isprintable", "isspace", "istitle",
        "isupper", "join", "ljust", "lower", "lstrip", "maketrans",
        "partition", "removeprefix", "removesuffix", "replace", "rfind",
        "rindex", "rjust", "rpartition", "rsplit", "rstrip", "split",
        "splitlines", "startswith", "strip", "swapcase", "title",
        "translate", "upper", "zfill",
    }),
    "bytes": frozenset({
        "decode", "hex", "count", "find", "index", "rfind", "rindex",
        "split", "rsplit", "splitlines", "join", "replace", "startswith",
        "endswith", "strip", "lstrip", "rstrip", "partition", "rpartition",
        "translate", "center", "ljust", "rjust", "zfill",
    }),
    "bytearray": frozenset({
        "append", "extend", "insert", "remove", "pop", "clear", "reverse",
        "copy", "decode", "hex", "count", "find", "index", "split", "join",
        "replace", "startswith", "endswith", "strip",
    }),
}


def _stdlib_short_base(raw: str) -> str:
    """Strip generics + dotted qualifier so a declared base name → its short form.

    Mirrors the sibling linkers' ``_short_base_name``. Only the bare builtin
    names (``dict``/``list``/...) match the catalog; a dotted form takes the
    last segment.
    """
    name = raw.split("[")[0].split("<")[0].strip()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name


def _python_stdlib_base_shadows(
    start_class_id: str,
    callee_short: str,
    inheritance_index: dict[str, list[tuple[str, str]]],
    class_symbols: dict[str, Symbol],
    method_index: _TypeHierarchyIndex,
) -> bool:
    """True when a resolved Python inherited method is shadowed by a builtin base.

    INV-guviv: ``_walk_c3`` spans only in-tree ``extends`` edges, so an external
    builtin base is invisible. If such a base is declared BEFORE the first
    in-tree base of ``start_class_id`` AND it defines ``callee_short``, Python's
    real MRO dispatches to the (unseen) builtin method — the walk's in-tree
    binding is confidently wrong, so bias to unresolved.

    Sound by construction (never over-suppresses the external-mixin-first idiom):
    fires only when a CATALOGED builtin that DEFINES the method precedes ANY
    in-tree base. A non-stdlib external mixin is not cataloged; a builtin that
    does not define the method is skipped; a class defining the method itself is
    exempt (its own method is first in the MRO). Scoped to the start class's own
    declared bases — a builtin declared in an in-tree ANCESTOR is a documented
    residual. The scan stops at the FIRST in-tree base, so a builtin declared
    AFTER an in-tree base that lacks the method but BEFORE a LATER in-tree base
    that has it is a documented UNDER-suppression residual (safe: it leaves the
    pre-existing edge, never over-suppresses a correct one).
    """
    start_sym = class_symbols.get(start_class_id)
    declared = (
        ((start_sym.meta or {}).get("base_classes") if start_sym else None) or []
    )
    if not declared:
        return False
    # The class's own method is first in the MRO — never shadowed.
    if any(
        cid == start_class_id
        for cid, _ in method_index.methods_by_short_name.get(callee_short, [])
    ):
        return False
    # ``str(raw)`` coercion mirrors the sibling ``_reorder_python_bases_by_source``
    # defense (the producer is typed ``-> str``, so this is belt-and-suspenders).
    declared_short = [_stdlib_short_base(str(raw)) for raw in declared]
    in_tree_short = {
        _stdlib_short_base(class_symbols[pid].name)
        for pid, _ in inheritance_index.get(start_class_id, ())
        if pid in class_symbols
    }
    # Soundness guard: the shadow reasoning aligns declared base ORDER with the
    # in-tree parents. If an in-tree parent's name is ABSENT from the declared
    # bases (e.g. an in-tree class import-aliased to a builtin's exact name, or
    # any name divergence), the alignment is unreliable — bias to no-shadow
    # rather than risk over-suppressing a correct resolution.
    if not in_tree_short.issubset(declared_short):
        return False
    for short in declared_short:
        if short in in_tree_short:
            # Reached the in-tree resolution path before any shadowing builtin.
            return False
        methods = _STDLIB_BASE_METHODS.get(short)
        if methods is not None and callee_short in methods:
            return True
    return False  # pragma: no cover - the subset guard guarantees an in-tree hit


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
    src_lang: str | None = None,
) -> tuple[str, str | None] | None:
    """Walk the parent chain looking for ``meta["fields"][field_short_name]``.

    WI-rarab: for Python (``src_lang == "python"``) the walk follows the C3
    linearization, not insertion-order BFS — on an uneven-depth diamond where
    the same field name is declared at different depths with DIVERGENT types,
    BFS returns a direct base's field while C3 returns the MRO-earlier
    ancestor's (the one Python's real attribute lookup would use). An
    un-linearizable (cyclic / too-deep) Python hierarchy biases to unresolved
    (``None``), matching ``_walk_c3``. Other languages keep the insertion-order
    BFS below.

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
        On a HIT, a ``(type_name, type_id_or_None)`` 2-tuple — the field's type
        short name plus the concrete type id from the parent's parallel
        ``meta["field_type_ids"]`` (WI-supat PR-B), or ``None`` when the parent
        carries only the legacy name-keyed ``meta["fields"]`` (java / pre-PR-B).
        On a MISS (no ancestor declares the field), bare ``None`` — preserving
        the depth-cap / cycle negatives that assert ``result is None``.
    """
    if src_lang == "python":
        # WI-rarab: C3-ordered field walk. Linearize the enclosing class and
        # take the first ancestor (excluding the class itself — own fields are
        # not examined) that declares the field.
        linearization = _linearize_c3(start_class_id, inheritance_index, depth_cap)
        if linearization is None:
            return None
        for class_id in linearization[1:]:
            sym = class_symbols.get(class_id)
            if sym is not None and sym.meta:
                fields = sym.meta.get("fields") or {}
                if field_short_name in fields:
                    type_ids = sym.meta.get("field_type_ids") or {}
                    return fields[field_short_name], type_ids.get(field_short_name)
        return None

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
                    type_ids = parent_sym.meta.get("field_type_ids") or {}
                    return fields[field_short_name], type_ids.get(field_short_name)
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
        # WI-supat (D3): the concrete class ids the producer threaded alongside
        # the name hints (an AUTHORITATIVE enclosing-class id, and a
        # within-file-uniqueness-gated receiver-type id). Consumed by the Site
        # resolvers to skip the same-short-name ambiguity guard precisely. The
        # Site-3 field-TYPE id rides the parent class symbol's meta, not here.
        receiver_type_id = meta.get("receiver_type_id")
        enclosing_class_id = meta.get("enclosing_class_id")

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
            receiver_type_id=receiver_type_id,
            enclosing_class_id=enclosing_class_id,
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


def _same_language_class_ids(
    name: str,
    src_lang: str,
    class_ids_by_name: dict[str, list[str]],
    class_symbols: dict[str, Symbol],
) -> list[str]:
    """Short-name class-id candidates RESTRICTED to ``src_lang`` (INV-milud).

    ``class_ids_by_name`` is built language-agnostically: a class named
    ``Handler`` in Python and a ``Handler`` in Java collide on the same short
    name. But an inherited-/typed-receiver call in a ``src_lang`` source file
    can only bind to a ``src_lang`` class — MRO and typed-receiver dispatch
    never cross a language boundary (cross-language edges are the FFI/bridge
    linkers' job). Applying this filter at the SINGLE point where every Site
    turns a receiver / enclosing-class / field-type NAME into candidate class
    ids keeps the name path from two distinct failures:

    * **Confidently-wrong cross-language edge** — the concrete leak was the
      Java legacy-permissive Site-2 (``_LEGACY_SITE2_LANGS``), which skips the
      same-name ambiguity guard and so would bind a Java receiver to a Python
      namesake's method (Step 1) or type symbol (Step 3 fallback).
    * **False-negative from count pollution** — a foreign-language namesake
      inflates the strict ``len(...) > 1`` ambiguity guard, suppressing a
      legitimate same-language resolution that was never actually ambiguous.

    Every id in ``class_ids_by_name`` is a key in ``class_symbols`` (both are
    built in the same pass over ``ctx.symbols``), so the subscript is total.
    """
    return [
        cid
        for cid in class_ids_by_name.get(name, [])
        if class_symbols[cid].language == src_lang
    ]


def _try_resolve(
    *,
    edge: Edge,
    callee_short: str,
    src_lang: str,
    receiver_type_hint: str | None,
    inherited_field_receiver: str | None,
    enclosing_class: str | None,
    receiver_type_id: str | None,
    enclosing_class_id: str | None,
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

    ``receiver_type_id`` / ``enclosing_class_id`` (WI-supat) are the concrete
    class ids the producer threaded alongside the name hints; a Site resolver
    prefers a valid, same-language id over the re-globalized name (skipping the
    ambiguity guard) to recover the recall the guard sacrifices.
    """
    method_short = _extract_method_short_name(callee_short)

    # Site 3 (most specific): inherited-field-receiver + enclosing_class.
    if inherited_field_receiver and enclosing_class:
        emitted = _resolve_site3(
            edge=edge, method_short=method_short,
            src_lang=src_lang,
            enclosing_class=enclosing_class,
            enclosing_class_id=enclosing_class_id,
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
            receiver_type_id=receiver_type_id,
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
            enclosing_class_id=enclosing_class_id,
            class_ids_by_name=class_ids_by_name,
            class_symbols=class_symbols,
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
    enclosing_class_id: str | None,
    class_ids_by_name: dict[str, list[str]],
    class_symbols: dict[str, Symbol],
    method_index: _TypeHierarchyIndex,
    inheritance_index: dict[str, list[tuple[str, str]]],
    existing_call_pairs: set[tuple[str, str]],
    run: AnalysisRun,
) -> Edge | None:
    """Site 1: bare / ``this`` call → walk enclosing class's MRO."""
    walker = _MRO_WALKERS.get(src_lang)
    if walker is None:
        return None
    # WI-supat (D3): prefer the AUTHORITATIVE concrete enclosing-class id the
    # producer threaded (an exact lexical method→class map, immune to the
    # bare-name last-write-wins clobber). A valid, same-language id names exactly
    # one class, so the receiver is NOT under-determined — resolve it precisely
    # and SKIP the ambiguity guard, recovering the recall the guard sacrifices
    # (both same-short-name and cross-language namesakes, since ``class_symbols``
    # is language-agnostic). ``id in class_symbols`` guards staleness; the
    # language match guards a foreign-language id keying a same-id class (belt-
    # and-suspenders: Symbol.id is language-prefixed, so this is always True for a
    # producer-stamped id — but a future shared stamp helper must not leak one).
    if (
        enclosing_class_id is not None
        and enclosing_class_id in class_symbols
        and class_symbols[enclosing_class_id].language == src_lang
    ):
        start_class_ids = [enclosing_class_id]
    else:
        start_class_ids = _same_language_class_ids(
            enclosing_class, src_lang, class_ids_by_name, class_symbols,
        )
        if not start_class_ids:
            return None
        # WI-hiziz PR-2: INV-fahub ambiguity guard for Site-1. ``enclosing_class``
        # is a NAME only; in a language with module namespaces
        # (``_SITE1_STRICT_LANGS`` — Python) two in-tree classes sharing that short
        # name are DISTINCT classes, so the enclosing receiver is under-determined
        # — bias to unresolved rather than binding whichever namesake the walk hits
        # first. This is scoped to ``_SITE1_STRICT_LANGS`` (NOT "all but Java"): in
        # Ruby the same short name is a class REOPENING (one logical class), so
        # applying the guard would suppress its INV-nilud-validated Site-1
        # resolution. The concrete-id path above recovers Python's guard-sacrificed
        # recall when the producer could stamp an authoritative id.
        if src_lang in _SITE1_STRICT_LANGS and len(start_class_ids) > 1:
            return None
    resolved_target: Symbol | None = None
    resolved_start_id: str | None = None
    for start_id in start_class_ids:
        candidate = walker(
            start_id, callee_short, inheritance_index,
            method_index, _DEFAULT_DEPTH_CAP,
        )
        if candidate is not None:
            resolved_target = candidate
            resolved_start_id = start_id
            break
    if resolved_target is None:
        return None
    # INV-guviv: an external builtin base ahead of the in-tree ancestor shadows
    # the walk's resolution in Python's real MRO — bias to unresolved.
    if (
        src_lang == "python"
        and resolved_start_id is not None
        and _python_stdlib_base_shadows(
            resolved_start_id, callee_short, inheritance_index,
            class_symbols, method_index,
        )
    ):
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
    receiver_type_id: str | None,
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
    # WI-supat (D3): prefer the concrete receiver-type id the producer threaded.
    # It is stamped only when the type's short name is UNIQUE within the file (so
    # the bare-name inference that produced it could not have hit the wrong
    # same-file twin) and is validated here as a real, same-language
    # ``class_symbols`` key. A valid id names exactly one type, so the receiver is
    # NOT under-determined — resolve it precisely (Step 1/2 only; Step 3 stays
    # gated below) and SKIP the ambiguity guard, recovering the recall the guard
    # sacrifices. Absent / stale / foreign-language → the original name path.
    if (
        receiver_type_id is not None
        and receiver_type_id in class_symbols
        and class_symbols[receiver_type_id].language == src_lang
    ):
        type_class_ids = [receiver_type_id]
    else:
        type_class_ids = _same_language_class_ids(
            receiver_type_hint, src_lang, class_ids_by_name, class_symbols,
        )
        if not type_class_ids:
            return None

        # Strict INV-fahub ambiguity guard: the hint carries only a class NAME.
        # When two in-repo classes share it, the receiver is under-determined —
        # bias to unresolved instead of binding to whichever same-named class
        # happens to define the method (the concrete-id path above recovers this
        # recall precisely when the producer could stamp an authoritative id).
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
                # INV-guviv: skip when a builtin base shadows the resolution.
                if src_lang == "python" and _python_stdlib_base_shadows(
                    type_class_id, method_short, inheritance_index,
                    class_symbols, method_index,
                ):
                    continue
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
    enclosing_class_id: str | None,
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
    # WI-supat (D3): prefer the authoritative concrete enclosing-class id (same
    # contract as Site-1). A valid, same-language id starts the parent-walk from
    # exactly the caller's lexical class, so the enclosing receiver is not
    # under-determined — skip the enclosing ambiguity guard. The field-TYPE
    # disambiguation below is INDEPENDENT (recovered in PR-B).
    if (
        enclosing_class_id is not None
        and enclosing_class_id in class_symbols
        and class_symbols[enclosing_class_id].language == src_lang
    ):
        encl_class_ids = [enclosing_class_id]
    else:
        encl_class_ids = _same_language_class_ids(
            enclosing_class, src_lang, class_ids_by_name, class_symbols,
        )
        if not encl_class_ids:  # pragma: no cover
            return None
        # WI-hiziz PR-3: INV-fahub ambiguity guard on the ENCLOSING class,
        # mirroring Site-1/Site-2, scoped to _SITE1_STRICT_LANGS (Python — module
        # namespaces make same-short-name classes distinct). Two same-name
        # enclosing classes would each expose different parent fields →
        # under-determined → bias to unresolved. Java/Ruby stay permissive
        # (reopening / legacy first-match).
        if src_lang in _SITE1_STRICT_LANGS and len(encl_class_ids) > 1:
            return None

    # Find the field's type (name + optional concrete id) by walking the
    # enclosing class's parents.
    field_type: str | None = None
    field_type_id: str | None = None
    for encl_id in encl_class_ids:
        walked = _walk_parents_for_field(
            encl_id, inherited_field_receiver, inheritance_index,
            class_symbols, _DEFAULT_DEPTH_CAP, src_lang=src_lang,
        )
        if walked is not None:
            field_type, field_type_id = walked
            break
    if field_type is None:
        return None

    # WI-supat (D3) PR-B: prefer the concrete field-TYPE id the producer threaded
    # on the parent's meta['field_type_ids'] (validated, same-language). It names
    # exactly one type, so the field's type is not under-determined — skip the
    # field-type ambiguity guard. Absent (java/legacy) / stale / foreign-language
    # falls back to the re-globalized name path + guard below.
    if (
        field_type_id is not None
        and field_type_id in class_symbols
        and class_symbols[field_type_id].language == src_lang
    ):
        field_type_class_ids = [field_type_id]
    else:
        # Look up the field's type as a same-language class symbol.
        field_type_class_ids = _same_language_class_ids(
            field_type, src_lang, class_ids_by_name, class_symbols,
        )
        if not field_type_class_ids:
            return None
        # WI-hiziz PR-3: the SECOND INV-fahub guard — the field's TYPE name is
        # also re-globalized; two same-short-name types are under-determined for
        # a strict language. Bias to unresolved rather than resolving the method
        # on an arbitrary namesake type (the concrete-id path above recovers this
        # recall when the producer could stamp an authoritative id).
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
