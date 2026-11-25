# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker: inherited-call resolution via ancestor walking.

Why a linker
------------
Resolving a call against a class's ancestors is the same walk in every
language; only the MRO order differs. Done per analyzer, it is duplicated
where it exists and silently missing everywhere else, so per ADR-3bbb it
lives here, in one Tier-2 Infrastructure linker at the same layer as
``inheritance.py``, serving every analyzer that stamps the hints below.
(history: INV-nilud, WI-puluf)

Three Sites
-----------
The linker dispatches per unresolved-call edge by which hint(s)
``make_unresolved_edge`` attached to ``Edge.meta``, in specificity order:

- **Site 3** (``inherited_field_receiver`` + ``enclosing_class``):
  ``field.method()`` where ``field`` is declared on an ancestor of
  ``enclosing_class``. The linker walks ``extends``/``implements``/
  ``inherits``/``includes`` edges from the enclosing class, consults each parent
  symbol's ``meta["fields"]`` (``{field: type_name}``, populated by the
  Java and Python analyzers) for a matching field name, looks up the
  field's type as a class symbol, and resolves the method on that type's
  MRO. Emits
  ``ast_call_inherited_field`` at confidence 0.80. For Python the parent
  walk follows the C3 linearization (WI-rarab), so a field name declared
  at two depths with divergent types takes the MRO-earlier declaration,
  as Python's attribute lookup does; other languages walk insertion-order
  BFS.

- **Site 2** (``receiver_type_hint``): ``var.method()`` where the
  analyzer inferred ``var``'s type. Resolution:
  (1) direct method on the type → ``ast_call_type_inferred`` at 0.85;
  (2) MRO walk on the type → ``ast_call_inherited_method`` at 0.70.
  A method on neither leaves the CALL unresolved; for Java
  (``_SITE2_TYPE_USE_LANGS``) step (3) adds a ``uses`` edge from the caller
  to the type (``ast_call_inherited_method`` at 0.70): the caller depends on
  the type, which is not the callee. It must not be a ``calls`` edge: such a
  call targets a method inherited from an external base (``getMessage``,
  ``getClass``, an ``InputStream`` subclass's ``read``) or an in-repo method
  the walk missed, and a ``calls`` edge to the type would hand
  ``method_call_recovery`` a class hint that binds ``...builder().build()``
  to an unrelated ``Builder.build``. (history: WI-jomik)

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
The dispatch table is hardcoded (YAML / decorator hooks would be ceremony
for static language semantics):

- ``_walk_insertion_order``: Ruby, Groovy, Go, Rust, C++. BFS through
  inheritance edges in declaration order. Per-source visited set guards
  against cycles.
- ``_walk_single_then_interfaces``: Java, PHP, JavaScript, TypeScript, C#,
  Objective-C. Single superclass (``extends``) walked before interfaces
  (``implements``/``includes``) via edge-type priority.
- ``_walk_c3`` (Python): true C3 linearization over the in-tree
  ``extends`` chain. Python's MRO is C3, not insertion-order BFS — the two
  diverge on uneven-depth diamonds, where BFS picks the wrong ancestor.
  Registered for ``python`` only; deliberately NOT added to
  ``_LEGACY_SITE2_LANGS`` (Python keeps the strict Site-2 collision guard).
- ``_walk_linearization`` (Scala): right-to-left BFS approximating Scala
  trait linearization (``extends B with T1 with T2`` → ``C, T2, T1, B``;
  rightmost mixin wins, superclass last).
- ``_walk_left_to_right`` (Swift): left-to-right pre-order DFS (single
  superclass chain, then protocol extensions in declaration order).

The Scala and Swift analyzers stamp ``receiver_type_hint``, so Site-2
resolves a method directly on the typed receiver for them; their walkers
add the ancestor-chain step. External-base shadowing (INV-guviv) is guarded
for Python only — at Site-1, Site-2 and Site-3; the other walkers see only
in-tree ancestors and share that limitation unguarded.

The analyzer must opt in by emitting the hint. Without a registered walker
for the source language, Site-2 still resolves a method directly on the
typed receiver (its step 1); only the ancestor-chain steps need a walker.

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

Same-name ambiguity guards and class-id hints (INV-fahub, WI-supat)
-------------------------------------------------------------------
Name hints under-determine the class when two same-language classes share a
short name. Site-2 refuses such a collision for every language outside
``_LEGACY_SITE2_LANGS`` (Java). At Site-1 and Site-3 the guard is limited to
``_SITE1_STRICT_LANGS`` (Python only): Python's module namespaces make
same-name classes distinct, while in Ruby they are one reopened class, and
guarding there would drop the Ruby ``X.new`` -> inherited ``#initialize``
resolution. Site-3 applies the guard twice, to the enclosing class and to the
field's type name.

To recover the recall the guard gives up, a producer can attach concrete
class ids next to the names: ``enclosing_class_id`` (an exact lexical
method-to-class map) and ``receiver_type_id`` (stamped only when the type's
short name is unique within the file) on the edge meta, and
``meta["field_type_ids"]`` (``{field: class_id}``) on the parent class
symbol for Site-3. The Python analyzer stamps all three. The Java analyzer
stamps ``enclosing_class_id`` only, found by the call's position, so a Java
typed receiver still goes by name. An id
that is a known class symbol in the call's language replaces the name lookup
and skips that guard; a missing, stale or foreign-language id falls back to
the name path and its guard.

Which type a name means (ADR-0064)
----------------------------------
A producer-threaded id is one way to know which class a name is. The other,
for an analyzer that hands over its files' import tables
(``LinkerContext.import_scopes``), is to ask: ``type_visibility.visible_types``
turns the name on the stub into the declarations it can mean from the
caller's place (the caller's enclosing scopes and the file's imports for a
language with declared names; the caller's own file and the files its imports
name for a language whose imports name files). One type: it is used exactly
as an id is (several declarations when the type is partial, told from a
generic namesake by ``meta["type_arity"]``). None, or two: the call stays
unresolved, whatever namesakes the repository holds. A file with no table is
untouched by any of this.

Five more things ride on the same loop, each read only for a file with a
table unless said otherwise:

* ``meta["qualifier"]`` (``Util.Helper()``: the call names its owner) goes
  through Site 2 and the bind is marked ``qualified``.
* A call fact for every file: ``meta["super_receiver"]`` makes Site 1 start
  at the enclosing type's parents (``base.M()``, ``super.m()``).
* Site 2, step 2b: where the type is settled and no method of the name is on
  it, a field or property of the name is bound (an invoked delegate).
* A function called by its imported name, in a language whose imports name
  files: an explicit binding names the function with the original name in the
  bound file; a qualifier that is a namespace binding names that file's
  function; a bare name with no binding names the function a wildcard import
  supplies. A file that only passes the name on (a barrel file, ``pub use``)
  is followed to the one that declares it. Marked ``import``. This is not one of the three Sites: there is
  no type in it.

* A declaration named by a bare call or a ``new``, in a language of declared
  names (``_declaration_named``): once the enclosing type's own members have
  been tried, a bare ``helper()`` means the function of that name visible
  from the caller's file (its own package, an import, a wildcard), and
  ``new Conn()`` (an unresolved ``instantiates`` stub) the type of that name;
  in a language that writes a construction as a call (Kotlin ``Request(url)``)
  a bare call may name a type too. The mark is the rule that scoped it
  (``type_visibility.visible_declarations``): ``same_module``, ``import`` or
  ``qualified``. Not visible, or two candidates: unresolved.

The name of this module is narrower than what it now does; it stays until the
pilot of ADR-0064 has settled what belongs here.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable

from .. import resolved_by
from ..edge_types import INHERITANCE_EDGE_TYPES
from ..import_scope import MODULES_ARE_FILES, ImportScope
from ..ir import PASS_VERSION, AnalysisRun, Edge, Symbol, make_pass_id
from ..symbol_kinds import type_like_kind_names
from ..type_visibility import (
    call_language, declaring_files, dotted, visible_declarations, visible_types,
    without_type_arguments, written_arity,
)
from .method_call_recovery import parse_unresolved_name, short_name
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

# The registry's inheritance family plus ``includes`` for the Ruby mixin walk.
# Taking the family from the registry, not a hand-written tuple, keeps every
# member walked (``inherits`` carries Solidity ancestors). ``includes`` is
# unioned rather than registry-resident because most of its producers mean
# file inclusion; the MRO walkers below only ever see edges whose src is a
# type, so a Makefile ``include`` cannot reach them. (history: INV-nosoz)
_INHERITED_CALL_EDGE_TYPES: tuple[str, ...] = tuple(sorted(
    INHERITANCE_EDGE_TYPES | frozenset({"includes"})
))

# Confidence per Site (see module docstring).
_SITE_1_CONFIDENCE = 0.90
_SITE_2_DIRECT_CONFIDENCE = 0.85  # ast_call_type_inferred
_SITE_2_INHERITED_CONFIDENCE = 0.70  # ast_call_inherited_method
_SITE_3_CONFIDENCE = 0.80  # ast_call_inherited_field

# Maximum ancestor hops any walker takes.
_DEFAULT_DEPTH_CAP = 10

# Edge-type priority for the single-then-interfaces walker (see _MRO_WALKERS).
# Lower value = walked first. extends is the single-superclass MRO; implements
# / includes are interfaces / mixin contributions consulted after the extends
# chain is exhausted at the same depth.
_EDGE_TYPE_PRIORITY: dict[str, int] = {
    "extends": 0, "inherits": 0, "implements": 1, "includes": 2,
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
        depth_cap: Maximum walk depth (``_DEFAULT_DEPTH_CAP``, 10 hops).

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
    """BFS prioritizing extends parents over interfaces (Java-like MRO).

    Java method dispatch walks the single superclass chain (``extends``)
    before considering interface default methods (``implements``). When a
    class has both an extends parent and implements interfaces and both
    define a method of the matching short name, the extends parent wins.
    When the extends chain is exhausted without a match, interface default
    methods are consulted (Java 8+).

    This is implemented as BFS where, at each node, parents are pushed in
    edge-type priority order (extends first, then implements / includes).
    The visited set ensures each class is examined once. Within one depth
    the extends parent is checked before the implements parent, because
    the queue order at each push respects edge-type priority. Across depths
    BFS order holds, so an interface at depth 1 is checked before the
    superclass's own superclass at depth 2 — an approximation of Java's
    rule, which exhausts the whole superclass chain first.

    Registered for Java, PHP, JavaScript, TypeScript, C# and Objective-C
    (``_MRO_WALKERS``).
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


def _walk_linearization(
    start_class_id: str,
    callee_short_name: str,
    inheritance_index: dict[str, list[tuple[str, str]]],
    method_index: _TypeHierarchyIndex,
    depth_cap: int = _DEFAULT_DEPTH_CAP,
) -> Symbol | None:
    """Right-to-left BFS approximating Scala trait linearization (WI-nazab).

    Scala linearizes ``class C extends B with T1 with T2`` as ``C, T2, T1,
    B`` — a later mixin overrides an earlier one, and the superclass
    resolves last. So for the "is this method defined on an ancestor, and
    which wins?" question, walking each node's parents in REVERSED
    declaration order (rightmost mixin first) reproduces the precedence
    without computing the full C3 merge — the same pragmatic approximation
    ``_walk_insertion_order`` makes for Ruby's declaration-order MRO. The
    ``inheritance_index`` preserves ``extends``/``with`` declaration order,
    which this walker reverses per level.
    """
    visited: set[str] = {start_class_id}
    queue: deque[tuple[str, int]] = deque([(start_class_id, 0)])
    methods_by_class: dict[str, Symbol] = dict(
        method_index.methods_by_short_name.get(callee_short_name, []),
    )
    while queue:
        class_id, depth = queue.popleft()
        candidate = methods_by_class.get(class_id)
        if candidate is not None:
            return candidate
        if depth >= depth_cap:
            continue
        for parent_id, _edge_type in reversed(
            inheritance_index.get(class_id, []),
        ):
            if parent_id in visited:
                continue
            visited.add(parent_id)
            queue.append((parent_id, depth + 1))
    return None


def _walk_left_to_right(
    start_class_id: str,
    callee_short_name: str,
    inheritance_index: dict[str, list[tuple[str, str]]],
    method_index: _TypeHierarchyIndex,
    depth_cap: int = _DEFAULT_DEPTH_CAP,
) -> Symbol | None:
    """Left-to-right depth-first walk (Swift MRO; WI-sojim).

    Swift resolves a call against the single superclass chain first, then
    protocol extensions in declaration order. Implemented with an
    explicit stack whose parents are pushed in REVERSED order so the
    leftmost parent's subtree is popped (explored) before its right
    siblings — a left-to-right pre-order DFS. Registered for Swift only;
    PHP and Obj-C use ``_walk_single_then_interfaces`` and C++ uses
    ``_walk_insertion_order`` (``_MRO_WALKERS``).
    """
    visited: set[str] = {start_class_id}
    methods_by_class: dict[str, Symbol] = dict(
        method_index.methods_by_short_name.get(callee_short_name, []),
    )
    stack: list[tuple[str, int]] = [(start_class_id, 0)]
    while stack:
        class_id, depth = stack.pop()
        candidate = methods_by_class.get(class_id)
        if candidate is not None:
            return candidate
        if depth >= depth_cap:
            continue
        for parent_id, _edge_type in reversed(
            inheritance_index.get(class_id, []),
        ):
            if parent_id in visited:
                continue
            visited.add(parent_id)
            stack.append((parent_id, depth + 1))
    return None


# Per-language MRO dispatch. Hardcoded clauses (no YAML / no decorator
# hooks) because the table is static language semantics (ADR-3bbb).
_MRO_WALKERS: dict[str, Callable[
    [str, str, dict[str, list[tuple[str, str]]],
     _TypeHierarchyIndex, int], Symbol | None,
]] = {
    "ruby": _walk_insertion_order,
    "groovy": _walk_insertion_order,
    "java": _walk_single_then_interfaces,
    "python": _walk_c3,
    "scala": _walk_linearization,  # WI-nazab
    "swift": _walk_left_to_right,  # WI-sojim
    # These walkers recover the inherited implicit-``this``/``self`` calls
    # the analyzers defer to this linker (INV-fahub). Each language's analyzer
    # emits ``base_classes`` (or, for Rust, ``implements``) metadata that the
    # language-agnostic ``inheritance`` linker turns into the extends/implements
    # edges these walkers traverse. Walker choice follows the language's MRO
    # shape: single-superclass-then-interfaces (Java-like) for php/js/ts/csharp/
    # objc; insertion-order BFS for go struct embedding, rust trait impls, and
    # C++ multiple inheritance. (lua/zig emit no inheritance model, so no
    # walker recovers anything for them — deliberately unregistered. dart
    # emits extends/implements/includes edges but has no walker: its member
    # lookup runs class, mixins right to left, then the superclass, and
    # ``implements`` contributes no implementation, which no walker here
    # models.)
    "go": _walk_insertion_order,
    "rust": _walk_insertion_order,
    "php": _walk_single_then_interfaces,
    # Kotlin: one superclass, then interfaces (which may carry default
    # implementations). Its analyzer has stamped ``enclosing_class`` on the
    # bare calls it withholds since INV-fahub and said this linker resolves
    # the inherited ones; until ADR-0064's batch 5a no walker was registered
    # for it, so nothing did.
    "kotlin": _walk_single_then_interfaces,
    "javascript": _walk_single_then_interfaces,
    "typescript": _walk_single_then_interfaces,
    "csharp": _walk_single_then_interfaces,
    "cpp": _walk_insertion_order,
    "objc": _walk_single_then_interfaces,
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
# must not be over-suppressed (a naive "any external base ahead → suppress"
# gate would suppress exactly that idiom). They remain a documented residual;
# extend the catalog to well-known ORM bases only if a bakeoff shows real hits.
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


# Languages whose Site-2 typed-receiver resolution is LEGACY-PERMISSIVE: a
# same-name-class collision resolves by first match (the first same-named
# class that has the method) rather than biasing to unresolved. Java only,
# on evidence rather than precedent: Java has no ``receiver_type_id``
# producer, and most Java first-match bindings agree with Java's own name
# rule (same file, single-type import, same package), so the guard would
# drop far more right edges than wrong ones (history: WI-jomik). Once the
# Java analyzer stamps an import-resolved ``receiver_type_id`` (the WI-supat
# channel, module docstring), Java can leave this set.
#
# Every OTHER language — Python (WI-noham) and any future emitter —
# gets the STRICT INV-fahub mode: a same-name-class collision is unresolved
# (an under-determined receiver must stay ambiguous/external). No language
# gets a ``calls→class`` fallback (module docstring, Site 2).
#
# DELIBERATELY DECOUPLED from ``_MRO_WALKERS``: this is NOT ``set(_MRO_WALKERS)``.
# Adding an MRO walker must NOT silently make a language permissive; a
# language opts in only by being listed HERE, consciously.
_LEGACY_SITE2_LANGS: frozenset[str] = frozenset({"java"})

# Languages whose Site-2 step 3 records a ``uses`` edge from the caller to the
# receiver's TYPE when the method is nowhere on its chain: ``repo.save(e)``
# on a Spring Data ``OwnerRepository`` (``save`` is ``JpaRepository``'s) is a
# controller -> repository dependency, and Java emits no other method -> type
# edge. It is ``uses``, not ``calls``: the type is not the callee, and a
# ``calls`` edge here would hand ``method_call_recovery`` a false class hint
# (module docstring, Site 2). Java only, the one language measured with it;
# another opts in after its own A/B.
_SITE2_TYPE_USE_LANGS: frozenset[str] = frozenset({"java"})


# ``_SITE1_STRICT_LANGS`` — languages whose Site-1 (``enclosing_class``)
# resolution applies the INV-fahub same-short-name ambiguity guard, as do the
# two Site-3 guards. This is DELIBERATELY NOT
# ``all langs except _LEGACY_SITE2_LANGS``: the
# guard's premise is that two in-tree classes sharing a short name are DISTINCT
# classes (true in Python, which has module namespaces — a bare ``enclosing_class``
# name genuinely under-determines which module's class the caller is in). It is
# FALSE in Ruby (and any single-global-namespace language): a *reopened* class
# (``class Worker`` opened in several files — ubiquitous in Rails) emits one class
# symbol per definition, all sharing the same short name, yet they are ONE logical
# class. Applying the guard there would suppress the Ruby
# ``X.new → inherited #initialize`` Site-1 resolution (INV-nilud). So the guard
# is opt-in per language, and only Python is a member; Ruby/Groovy keep their
# loop-all-first-match Site-1 behavior. A concrete class id threaded by the
# producer recovers Python's guard-sacrificed recall (module docstring; WI-supat).
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

    Used by Site-3 resolution. The Java and Python analyzers populate each
    class symbol's ``meta["fields"]`` with its ``{field_name: type_name}``
    map. This walker traverses the enclosing class's parents and returns
    the first field-type name it finds; the caller then looks up the
    method on that type.

    Args:
        start_class_id: Symbol ID of the enclosing class. The walk does
            NOT examine its own fields — by design: Site-3 is for a
            receiver that is not one of the enclosing class's own fields.
        field_short_name: The receiver-as-field name to look for.
        inheritance_index: Map ``child_id -> [(parent_id, edge_type), ...]``.
        class_symbols: Map ``class_id -> Symbol``. Used to read
            ``meta["fields"]`` from each parent.
        depth_cap: Maximum walk depth.

    Returns:
        On a HIT, a ``(type_name, type_id_or_None)`` 2-tuple — the field's type
        short name plus the concrete type id from the parent's parallel
        ``meta["field_type_ids"]`` (WI-supat), or ``None`` when the parent
        has no id for the field (Java stamps only the name-keyed
        ``meta["fields"]``). On a MISS (no ancestor within the depth cap
        declares the field, or a Python hierarchy cannot be linearized),
        bare ``None``.
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
    "inherited-calls-linker",
    priority=18,  # Between inheritance (15) and type_hierarchy (60).
    description=(
        "Resolves unresolved calls by walking the ancestor chain in each "
        "language's own method-resolution order: a bare call inside a class "
        "(enclosing_class hint), a call on a typed receiver "
        "(receiver_type_hint) and a call on an inherited field."
    ),
    activation=LinkerActivation(always=True),
    # CNF: the analyzers whose output can make this linker emit ANYTHING.
    # A hand-copied snapshot of ``_MRO_WALKERS`` goes stale as soon as a
    # walker is added (history: WI-rasal).
    #
    # Derived, not hand-copied, from the three emission paths:
    #   Site 1  — ``enclosing_class`` producers INTERSECT ``_MRO_WALKERS``
    #             (dart/lua/zig stamp the hint but have no walker)
    #   Site 2  — ``receiver_type_hint`` producers; these need NO walker,
    #             because step 1 resolves the method directly on the inferred
    #             type (this is what adds ``d`` and ``kotlin``)
    #   Site 3  — ``inherited_field_receiver`` producers
    # ``test_depends_on_producer_sets.py`` re-derives all three and fails when
    # this list drifts from the registry. Note ``_MRO_WALKERS`` is keyed on
    # ``Symbol.language``, a wider vocabulary than pass ids: its ``typescript``
    # key maps onto the ``javascript`` pass.
    #
    # There is no ``["inheritance-linker"]`` conjunct: it would be false, since
    # Site-2 step 1 emits ``ast_call_type_inferred`` with no inheritance edges
    # in the graph at all. Running after inheritance-linker, whose
    # extends/implements/includes edges the MRO walks traverse, is an
    # ordering, carried by ``priority=18`` against that linker's 15, which is
    # the axis for it.
    depends_on=[
        [
            "cpp", "csharp", "d", "go", "groovy", "java", "javascript",
            "kotlin", "objc", "php", "python", "ruby", "rust",
            "rust_analyzer",  # WI-juzig: the second producer of rust
            "scala", "scip_python",  # WI-nanom: the second producer of python
            "swift",
        ],
    ],
    # ADR-0057 §14.1: this pass's resolutions may demote the stub they were
    # derived from. Granted on audited evidence, not for being a linker: its
    # resolutions audit as correct and consistent with the declared
    # inheritance graph (docs/audits/0020). The sibling recovery pass is NOT
    # granted it: a material share of its edges are wrong and no shipped
    # signal separates them.
    supersedes_consumed_stub=True,
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

    # Type name → [class_id] map and class_id → Symbol map. The method index
    # treats these uniformly; the walker looks up methods by class_id.
    # What counts as a type is the symbol-kind registry's answer (a record, an
    # enum, an actor and a contract hold methods like a class does), plus the
    # Ruby ``module``, which is a mixin and not type-like in the registry's
    # sense but is a method owner here. The linker's own list had left
    # ``record`` out.
    type_kinds = type_like_kind_names() | {"module"}
    class_ids_by_name: dict[str, list[str]] = {}
    class_symbols: dict[str, Symbol] = {}
    for s in ctx.symbols:
        if s.kind in type_kinds:
            class_ids_by_name.setdefault(s.name, []).append(s.id)
            class_symbols[s.id] = s
    # C3 needs left-to-right base order, but a qualified in-tree base's
    # extends edge arrives out of order (recovered late by the
    # inheritance-linker). Restore each Python class's source base order from
    # meta['base_classes'] before the C3 walker consumes the index (WI-hiziz).
    _reorder_python_bases_by_source(inheritance_index, class_symbols)
    method_index = build_method_index(
        ctx.symbols, class_ids_by_name, class_symbols,
    )
    # Members that are not methods and can still be what a call invokes: a
    # field or property holding a delegate (``Clock.Sleep(t)`` where ``Sleep``
    # is a ``Func<...>`` field). Looked at only after the methods, and only
    # where the type's identity is settled.
    member_index = build_method_index(
        ctx.symbols, class_ids_by_name, class_symbols, kinds=_INVOKED_MEMBER_KINDS,
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

    # Functions by (file, name), for a call to a function by its imported
    # name. Built only when some file's table names files.
    functions_in_file: dict[tuple[str, str], list[Symbol]] = {}
    if any(s.module_kind == MODULES_ARE_FILES for s in ctx.import_scopes.values()):
        for s in ctx.symbols:
            if s.kind == "function":
                functions_in_file.setdefault((s.path, s.name), []).append(s)

    # Functions by short name, and the constructions already bound, for a
    # declaration named by a bare call or a ``new`` (``_declaration_named``).
    # Built only when some file's table names namespaces.
    functions_by_name: dict[str, list[Symbol]] = {}
    existing_constructions: set[tuple[str, str]] = set()
    if any(s.module_kind != MODULES_ARE_FILES for s in ctx.import_scopes.values()):
        for s in ctx.symbols:
            if s.kind == "function":
                functions_by_name.setdefault(short_name(s.name), []).append(s)
        existing_constructions = {
            (e.src, e.dst) for e in ctx.edges
            if e.edge_type == "instantiates" and e.is_resolved
        }

    def named_declaration_edge(stub: Edge, caller: Symbol, table: ImportScope) -> Edge | None:
        """The stub bound to the declaration its bare name means from the
        caller's file, or ``None`` (``_declaration_named``)."""
        constructs = stub.edge_type == "instantiates"
        language = caller.language or ""
        answer = _declaration_named(
            str((stub.meta or {}).get("callee_name") or parse_unresolved_name(stub.dst) or ""),
            caller, table, ctx.import_scopes, functions_by_name,
            class_ids_by_name, class_symbols,
            functions=not constructs,
            types=constructs or language in _BARE_CALL_MAY_NAME_A_TYPE,
        )
        if answer is None:
            return None
        target, scoped = answer
        pairs = existing_constructions if constructs else existing_call_pairs
        if (stub.src, target.id) in pairs:
            return None
        pairs.add((stub.src, target.id))
        return Edge.create(
            src=stub.src, dst=target.id, edge_type=stub.edge_type,
            line=stub.line, origin=PASS_ID, origin_run_id=run.execution_id,
            evidence_type="ast_new" if constructs else "ast_call",
            is_resolved=True, derived_from=[stub.id, target.id],
            resolved_by=scoped,
        )

    new_edges: list[Edge] = []
    for edge in ctx.edges:
        if edge.is_resolved or edge.edge_type not in ("calls", "instantiates"):
            continue
        meta = edge.meta or {}
        if edge.edge_type == "instantiates":
            # ``new T()``: the one thing this linker does with a construction
            # is to say which type the written name means (ADR-0064).
            constructor = symbol_by_id.get(edge.src)
            table = ctx.import_scopes.get(constructor.path) if constructor else None
            if constructor is not None and table is not None and (
                table.module_kind != MODULES_ARE_FILES
            ):
                bound = named_declaration_edge(edge, constructor, table)
                if bound is not None:
                    new_edges.append(bound)
            continue

        # Dispatch by hint kind, in specificity order:
        # 1. Site 3 (inherited-field-receiver + enclosing_class) — most
        #    constrained call shape (receiver is a field on a parent).
        # 2. Site 2 (receiver_type_hint) — typed local variable receiver.
        # 3. Site 1 (enclosing_class only) — bare / ``this`` call.
        # Each edge produces at most one resolved edge from this linker.
        receiver_type_hint = meta.get("receiver_type_hint")
        inherited_field_receiver = meta.get("inherited_field_receiver")
        enclosing_class = meta.get("enclosing_class")
        # The concrete class ids the producer threaded alongside the name
        # hints: an AUTHORITATIVE enclosing-class id, and a
        # within-file-uniqueness-gated receiver-type id. Consumed by the Site
        # resolvers to skip the same-short-name ambiguity guard precisely. The
        # Site-3 field-TYPE id rides the parent class symbol's meta, not here.
        # (history: WI-supat)
        receiver_type_id = meta.get("receiver_type_id")
        enclosing_class_id = meta.get("enclosing_class_id")
        # The owner the call site names (``Util`` in ``Util.Helper()``): a
        # call fact of ADR-0064, read only where the file handed over a table.
        qualifier = meta.get("qualifier")

        src_sym = symbol_by_id.get(edge.src)
        src_lang = src_sym.language if src_sym else None
        if src_sym is None or src_lang is None:
            continue

        # A function called by its imported name (ADR-0064). The table of a
        # language whose imports name files says which file's function the
        # name, or the qualifier, stands for. It comes first: a qualifier that
        # is a namespace binding of the file names a module, not a type.
        scope = ctx.import_scopes.get(src_sym.path)
        if (
            scope is not None and scope.module_kind == MODULES_ARE_FILES
            and not (receiver_type_hint or inherited_field_receiver)
            # A call written on a receiver (``x.merge()``) is not a call of
            # the function a same-named import binds; a qualifier is read as
            # the namespace binding it may be.
            and (qualifier or (edge.meta or {}).get("call_construct") != "method")
        ):
            imported = _imported_function(
                parse_unresolved_name(edge.dst) or "", qualifier, scope,
                functions_in_file, src_lang, ctx.import_scopes,
            )
            if imported is not None:
                if (edge.src, imported.id) not in existing_call_pairs:
                    new_edges.append(Edge.create(
                        src=edge.src, dst=imported.id, edge_type="calls",
                        line=edge.line, origin=PASS_ID, origin_run_id=run.execution_id,
                        evidence_type="ast_call_direct", is_resolved=True,
                        derived_from=[edge.id, imported.id],
                        resolved_by=resolved_by.IMPORT,
                    ))
                    existing_call_pairs.add((edge.src, imported.id))
                continue

        # A bare call in a language of declared names: once the enclosing
        # type's own members have been tried (Site 1, below), the name means
        # a function or a type visible from the caller's file.
        bare_name = (
            scope is not None and scope.module_kind != MODULES_ARE_FILES
            and not (receiver_type_hint or inherited_field_receiver or qualifier)
            and meta.get("call_construct") != "method"
            and not meta.get("super_receiver")
        )
        if not (
            receiver_type_hint or inherited_field_receiver
            or enclosing_class or qualifier
        ):
            if bare_name and scope is not None:
                bound = named_declaration_edge(edge, src_sym, scope)
                if bound is not None:
                    new_edges.append(bound)
            continue

        # WHICH type the name means (ADR-0064). The hint is a name, and a name
        # looked up among every class of the language binds a namesake when
        # the file means a library's type, and is refused when two types share
        # it. Where the caller's file handed over its import table, the table
        # and the caller's enclosing scopes say which declaration is meant;
        # that declaration is then used exactly as a producer-threaded
        # ``receiver_type_id`` is. No table: the name lookup, as before.
        scoped_by = resolved_by.RECEIVER_TYPE
        settled_type_ids: list[str] | None = None
        if scope is not None and not inherited_field_receiver:
            named_type = receiver_type_hint
            if not named_type and qualifier:
                named_type, scoped_by = qualifier, resolved_by.QUALIFIED
            if named_type and receiver_type_id is None:
                meant = _type_meant(
                    named_type, src_sym, scope, class_ids_by_name, class_symbols,
                    ctx.import_scopes,
                )
                if meant is _NOT_BOUND:
                    continue
                if meant is not None:
                    receiver_type_hint, settled_type_ids = named_type, meant
            elif not named_type and enclosing_class and enclosing_class_id is None:
                # A bare call starts at the type the caller is declared in,
                # which the caller's own qualified name identifies.
                settled_type_ids = _enclosing_type_ids(
                    src_sym, enclosing_class, class_ids_by_name, class_symbols,
                    one_file=scope.module_kind == MODULES_ARE_FILES,
                )
        if not (receiver_type_hint or inherited_field_receiver or enclosing_class):
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
            scoped_by=scoped_by,
            settled_type_ids=settled_type_ids,
            super_receiver=bool(meta.get("super_receiver")),
            member_index=member_index,
        )
        if emitted is None and bare_name and scope is not None:
            emitted = named_declaration_edge(edge, src_sym, scope)
            if emitted is not None:
                new_edges.append(emitted)
            continue
        if emitted is not None:
            new_edges.append(emitted)
            existing_call_pairs.add((emitted.src, emitted.dst))

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=new_edges, run=run)


# ---------------------------------------------------------------------------
# Per-Site resolvers.
# ---------------------------------------------------------------------------


#: Languages that write a construction as a call (``Request(url)``): a bare
#: call there may name a TYPE. Elsewhere a type is constructed with ``new``,
#: reported as an ``instantiates`` stub, and a bare call names a function only.
_BARE_CALL_MAY_NAME_A_TYPE = frozenset({"kotlin"})


def _declaration_named(
    written: str,
    caller: Symbol,
    scope: ImportScope,
    scopes: dict[str, ImportScope],
    functions_by_name: dict[str, list[Symbol]],
    class_ids_by_name: dict[str, list[str]],
    class_symbols: dict[str, Symbol],
    *,
    functions: bool,
    types: bool,
) -> tuple[Symbol, str] | None:
    """The function or type a bare name means from the caller's file, in a
    language of declared names, and the rule that scoped it (the edge's
    ``resolved_by``): ``type_visibility.visible_declarations`` over the
    functions and / or the types of that name in the caller's language.

    ONE declaration, or ``None``: nothing of that name is visible (a
    library's), two packages supply it, or a function and a type share the
    name in the scope that answers. Several declarations with one qualified
    name and one kind of thing are one: the overloads of a function (the
    first in its file is bound; overload selection is not made, WI-tosut) and
    the parts of a type. Where several FILES declare it, the caller's own
    file's declaration is the one meant: two scripts that each define a
    function of one name are not loaded together.
    """
    language = caller.language or ""

    def candidates(short: str) -> list[Symbol]:
        found: list[Symbol] = []
        if functions:
            found += [
                f for f in functions_by_name.get(short, ())
                if call_language(f.language or "") == call_language(language)
            ]
        if types:
            found += [
                class_symbols[class_id] for class_id in _same_language_class_ids(
                    short, language, class_ids_by_name, class_symbols,
                )
            ]
        return found

    seen, scoped = visible_declarations(
        written, caller.qualified_name, scope, candidates,
        caller_path=caller.path, scopes=scopes,
    )
    if not seen or scoped is None:
        return None
    one_name = {dotted(s.qualified_name or "") for s in seen}
    one_kind = {s.kind == "function" for s in seen}
    if len(one_name) != 1 or len(one_kind) != 1:
        return None
    first = min(seen, key=lambda s: (
        s.path != caller.path, s.path, s.span.start_line if s.span else 0, s.id,
    ))
    return first, scoped


def _imported_function(
    callee: str,
    qualifier: str | None,
    scope: ImportScope,
    functions_in_file: dict[tuple[str, str], list[Symbol]],
    language: str,
    scopes: dict[str, ImportScope],
) -> Symbol | None:
    """The function a call names through the caller's imports, in a language
    whose imports name files.

    ``callee`` an explicit binding (``import {helper as h}``; ``h()``): the
    function with the ORIGINAL name in the bound file. ``qualifier`` a
    namespace binding (``import * as util``; ``util.helper()``): the function
    ``callee`` in that file. A bare ``callee`` with no binding of its own:
    the function of that name in a file the caller's file imports wholesale
    (``use crate::schema::*;``). A file that only passes the name on (a
    barrel file, ``pub use``) is followed to the one that declares it
    (``type_visibility.declaring_files``). One function of the caller's
    language, or ``None``: no binding, a name declared nowhere the imports
    lead, or two declarations of it.
    """
    if qualifier:
        module = scope.namespace(dotted(qualifier))
        asked = [(module, callee)] if module is not None else []
    else:
        bound = scope.named(callee)
        asked = (
            [(bound.module, bound.name)] if bound is not None
            else [(module, callee) for module in scope.wildcard_modules]
        )
    found: list[Symbol] = []
    for module, name in asked:
        for key in declaring_files(
            module, name, lambda f, n: (f, n) in functions_in_file, scopes,
        ):
            found += [
                f for f in functions_in_file[key]
                if call_language(f.language or "") == call_language(language)
                and f not in found
            ]
    return found[0] if len(found) == 1 else None


#: Member kinds, other than a method, that a call can invoke through the
#: value they hold.
_INVOKED_MEMBER_KINDS = ("field", "property")

#: ``_type_meant``'s answer when the name means no single type from the
#: caller's place: the call stays unresolved.
_NOT_BOUND: list[str] = ["<not bound>"]


def _type_arity(symbol: Symbol) -> int | None:
    """How many type parameters the declaration takes, when its producer says
    (``meta["type_arity"]``); ``None`` when it does not."""
    arity = (symbol.meta or {}).get("type_arity")
    return arity if isinstance(arity, int) else None


def _of_arity(found: list[Symbol], arity: int | None) -> list[Symbol]:
    """``found`` narrowed to the declarations that take ``arity`` type
    parameters. Unchanged when ``arity`` is not known or any declaration does
    not record its own: an absent count is not a zero."""
    if arity is None or any(_type_arity(s) is None for s in found):
        return found
    return [s for s in found if _type_arity(s) == arity]


def _one_type(found: list[Symbol]) -> list[str] | None:
    """The ids of ``found`` when they are declarations of ONE type (a type
    declared in several files, a C# partial class, has several declarations
    and one qualified name); ``None`` when they are different types. The
    caller has already narrowed ``found`` to one arity (``_of_arity``), which
    is what keeps ``Policy`` and ``Policy<TResult>`` apart: they share the
    qualified name."""
    if len({dotted(s.qualified_name or "") for s in found}) != 1:
        return None
    return [s.id for s in found]


def _type_meant(
    written: str,
    caller: Symbol,
    scope: ImportScope,
    class_ids_by_name: dict[str, list[str]],
    class_symbols: dict[str, Symbol],
    scopes: dict[str, ImportScope],
) -> list[str] | None:
    """The class ids ``written`` means for ``caller``, whose file's imports
    are ``scope`` (``type_visibility.visible_types``); ``scopes`` is every
    file's table, for a name that reaches the caller through a re-export.

    The ids of one type when the name is settled. ``_NOT_BOUND`` when no
    declaration of that name is visible from the caller (a library's type,
    however many namesakes the repository holds) or when two types are (a
    tie). ``None`` when visibility cannot judge (a candidate with no
    qualified name): the caller falls back to the name lookup.
    """
    arity = written_arity(written)
    written_short = dotted(without_type_arguments(written)).rpartition(".")[2]

    def types_named(short: str) -> list[Symbol]:
        found = [
            class_symbols[class_id] for class_id in _same_language_class_ids(
                short, caller.language or "", class_ids_by_name, class_symbols,
            )
        ]
        # Name and arity are looked up TOGETHER, before any scope is walked:
        # a non-generic ``Registry`` in a nearer scope does not hide the
        # ``Registry<TKey>`` the caller wrote. An alias is looked up by the
        # aliased name, which the written arity does not describe.
        return _of_arity(found, arity) if short == written_short else found

    seen = visible_types(
        written, caller.qualified_name, scope, types_named,
        caller_path=caller.path, scopes=scopes,
    )
    if seen is None:
        return None
    return (_one_type(seen) if seen else None) or _NOT_BOUND


def _enclosing_type_ids(
    caller: Symbol,
    enclosing_class: str,
    class_ids_by_name: dict[str, list[str]],
    class_symbols: dict[str, Symbol],
    one_file: bool = False,
) -> list[str] | None:
    """The declarations of the type ``caller`` is a member of: the classes
    named ``enclosing_class`` whose qualified name is the caller's own minus
    its last segment. ``one_file``: in a language whose imports name files a
    type is one file's declaration, so a namesake class of another file is
    another type however equal its qualified name (in a language of declared
    names it is a part of the same type: a C# partial class). ``None`` when the caller or a candidate records no
    qualified name, or none matches: the name walk is then all there is."""
    owner = dotted(caller.qualified_name or "").rpartition(".")[0]
    candidates = [
        class_symbols[class_id] for class_id in _same_language_class_ids(
            enclosing_class, caller.language or "", class_ids_by_name, class_symbols,
        )
    ]
    if not owner or any(not c.qualified_name for c in candidates):
        return None
    parts = [
        c for c in candidates
        if dotted(c.qualified_name or "") == owner
        and (not one_file or c.path == caller.path)
    ]
    # A generic type and its non-generic namesake share the qualified name.
    # The caller is written inside one of them: that declaration's arity says
    # which type the others must be parts of.
    line = caller.span.start_line if caller.span else 0
    around = [
        c for c in parts
        if c.path == caller.path and c.span is not None
        and c.span.start_line <= line <= c.span.end_line
    ]
    if len(around) == 1:
        parts = _of_arity(parts, _type_arity(around[0]))
    return [c.id for c in parts] or None


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

    * **Confidently-wrong cross-language edge** — exposed at the Java
      legacy-permissive Site-2 (``_LEGACY_SITE2_LANGS``), which skips the
      same-name ambiguity guard and so would bind a Java receiver to a Python
      namesake's method (Step 1).
    * **False-negative from count pollution** — a foreign-language namesake
      inflates the strict ``len(...) > 1`` ambiguity guard, suppressing a
      legitimate same-language resolution that was never actually ambiguous.

    Every id in ``class_ids_by_name`` is a key in ``class_symbols`` (both are
    built in the same pass over ``ctx.symbols``), so the subscript is total.
    """
    return [
        cid
        for cid in class_ids_by_name.get(name, [])
        if call_language(class_symbols[cid].language or "") == call_language(src_lang)
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
    scoped_by: str = resolved_by.RECEIVER_TYPE,
    settled_type_ids: list[str] | None = None,
    super_receiver: bool = False,
    member_index: _TypeHierarchyIndex | None = None,
) -> Edge | None:
    """Dispatch an unresolved edge to the right Site resolver.

    ``settled_type_ids``: the declarations of the ONE type the caller's file
    means (ADR-0064), for Site 2 the receiver's type and for Site 1 the type
    the caller is declared in; a Site resolver uses them in place of the
    by-name candidates and skips the same-name guard, as it does for a
    producer-threaded id.

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
            scoped_by=scoped_by,
            settled_type_ids=settled_type_ids,
            member_index=member_index,
        )

    # Site 1: bare/this call.
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
            settled_type_ids=settled_type_ids,
            super_receiver=super_receiver,
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
    settled_type_ids: list[str] | None = None,
    super_receiver: bool = False,
) -> Edge | None:
    """Site 1: bare / ``this`` call → walk enclosing class's MRO.

    ``super_receiver``: the call is written on the parent (``super.m()``,
    ``base.M()``). The walk still follows the enclosing type's MRO, but the
    enclosing type's own methods are hidden from it, so the first hit is the
    nearest ancestor's.
    """
    walker = _MRO_WALKERS.get(src_lang)
    if walker is None:
        return None
    # Prefer the AUTHORITATIVE concrete enclosing-class id the producer
    # threaded (an exact lexical method→class map, immune to the
    # bare-name last-write-wins clobber; WI-supat). A valid, same-language id names exactly
    # one class, so the receiver is NOT under-determined — resolve it precisely
    # and SKIP the ambiguity guard, recovering the recall the guard sacrifices
    # (both same-short-name and cross-language namesakes, since ``class_symbols``
    # is language-agnostic). ``id in class_symbols`` guards staleness; the
    # language match guards a foreign-language id keying a same-id class (belt-
    # and-suspenders: Symbol.id is language-prefixed, so this is always True for a
    # producer-stamped id — but a future shared stamp helper must not leak one).
    if settled_type_ids is not None:
        start_class_ids = settled_type_ids
    elif (
        enclosing_class_id is not None
        and enclosing_class_id in class_symbols
        and call_language(class_symbols[enclosing_class_id].language or "") == call_language(src_lang)
    ):
        start_class_ids = [enclosing_class_id]
    else:
        start_class_ids = _same_language_class_ids(
            enclosing_class, src_lang, class_ids_by_name, class_symbols,
        )
        if not start_class_ids:
            return None
        # INV-fahub ambiguity guard for Site-1. ``enclosing_class``
        # is a NAME only; in a language with module namespaces
        # (``_SITE1_STRICT_LANGS`` — Python) two in-tree classes sharing that short
        # name are DISTINCT classes, so the enclosing receiver is under-determined
        # — bias to unresolved rather than binding whichever namesake the walk hits
        # first. This is scoped to ``_SITE1_STRICT_LANGS`` (NOT "all but Java"): in
        # Ruby the same short name is a class REOPENING (one logical class), so
        # applying the guard would suppress its Site-1 resolution (INV-nilud).
        # The concrete-id path above recovers Python's guard-sacrificed
        # recall when the producer could stamp an authoritative id.
        if src_lang in _SITE1_STRICT_LANGS and len(start_class_ids) > 1:
            return None
    if super_receiver:
        own = set(start_class_ids)
        method_index = _TypeHierarchyIndex(
            symbol_by_id=method_index.symbol_by_id,
            methods_by_short_name={callee_short: [
                (class_id, method)
                for class_id, method in method_index.methods_by_short_name.get(callee_short, [])
                if class_id not in own
            ]},
        )
    resolved_target: Symbol | None = None
    resolved_start_id: str = ""
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
        derived_from=[edge.id, resolved_start_id],
        resolved_by=resolved_by.OWN_TYPE,
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
    scoped_by: str = resolved_by.RECEIVER_TYPE,
    settled_type_ids: list[str] | None = None,
    member_index: _TypeHierarchyIndex | None = None,
) -> Edge | None:
    """Site 2: ``var.method()`` typed receiver, or ``Type.method()`` where the
    call site names the type (``scoped_by`` is then ``qualified``).

    Resolution:

    1. **Direct lookup** — method defined on the type itself →
       ``ast_call_type_inferred`` at 0.85.
    2. **MRO walk** — method on an ancestor of the type →
       ``ast_call_inherited_method`` at 0.70.

    3. **Type use** — method on neither: the call stays unresolved, and a
       language in ``_SITE2_TYPE_USE_LANGS`` (Java) gets a ``uses`` edge to
       the type symbol at 0.70, never a ``calls`` edge (module docstring,
       Site 2). It names the caller, not the call stub, in
       ``derived_from``, so the still-unresolved stub is not demoted.

    Step 2 requires the language to have an MRO walker; step 1 works
    without one.

    Two ``src_lang``-gated modes for a same-name-class collision
    (``len(type_class_ids) > 1``, WI-noham):

    * **Legacy-permissive** (``src_lang in _LEGACY_SITE2_LANGS`` — Java only):
      the first same-name class that has the method wins.
    * **Strict INV-fahub** (every other language, e.g. Python): the collision
      biases to unresolved. Only a method on the single, concretely-named
      type (step 1 / step 2 MRO) resolves.
    """
    # Prefer the concrete receiver-type id the producer threaded (WI-supat).
    # It is stamped only when the type's short name is UNIQUE within the file (so
    # the bare-name inference that produced it could not have hit the wrong
    # same-file twin) and is validated here as a real, same-language
    # ``class_symbols`` key. A valid id names exactly one type, so the receiver is
    # NOT under-determined — resolve it precisely (Step 1/2) and SKIP the
    # ambiguity guard, recovering the recall the guard
    # sacrifices. Absent / stale / foreign-language → the name path below.
    if settled_type_ids is not None:
        type_class_ids = settled_type_ids
    elif (
        receiver_type_id is not None
        and receiver_type_id in class_symbols
        and call_language(class_symbols[receiver_type_id].language or "") == call_language(src_lang)
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

    def declared_on_the_type(member: Symbol, type_class_id: str) -> Edge:
        """The call bound to a member the type itself declares (steps 1, 2b)."""
        return Edge.create(
            src=edge.src, dst=member.id, edge_type="calls",
            line=edge.line, confidence=_SITE_2_DIRECT_CONFIDENCE,
            origin=PASS_ID, origin_run_id=run.execution_id,
            evidence_type="ast_call_type_inferred",
            is_resolved=True,
            derived_from=[edge.id, type_class_id],
            resolved_by=scoped_by,
        )

    # Step 1: direct method on the type.
    for type_class_id in type_class_ids:
        direct = methods_by_class.get(type_class_id)
        if direct is None:
            continue
        if (edge.src, direct.id) in existing_call_pairs:  # pragma: no cover
            return None
        return declared_on_the_type(direct, type_class_id)

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
                    derived_from=[edge.id, type_class_id],
                    resolved_by=scoped_by,
                )

    # Step 2b: no method of that name on the type or its ancestors. Where the
    # type's identity is settled (ADR-0064), a field or property of that name
    # declared on the type is what the call invokes: the delegate it holds.
    # A type found by name alone is not evidence enough for that.
    if settled_type_ids is not None and member_index is not None:
        members = dict(member_index.methods_by_short_name.get(method_short, []))
        for type_class_id in type_class_ids:
            member = members.get(type_class_id)
            if member is not None and (edge.src, member.id) not in existing_call_pairs:
                return declared_on_the_type(member, type_class_id)

    # Step 3: the method is nowhere on the type's chain (an external base's,
    # or one the walk cannot see). The call stays unresolved; a language in
    # _SITE2_TYPE_USE_LANGS records that the caller USES the type.
    if src_lang not in _SITE2_TYPE_USE_LANGS:
        return None
    type_class_id = type_class_ids[0]
    type_sym = class_symbols.get(type_class_id)
    if type_sym is None:  # pragma: no cover
        return None
    # derived_from names the caller, not the call stub: ADR-0057 §14.1 reads
    # a stub id there as "this resolution consumed it", and this edge leaves
    # the call unresolved, so naming the stub would demote a live stub.
    # (history: WI-gupak)
    return Edge.create(
        src=edge.src, dst=type_sym.id, edge_type="uses",
        line=edge.line, confidence=_SITE_2_INHERITED_CONFIDENCE,
        origin=PASS_ID, origin_run_id=run.execution_id,
        evidence_type="ast_call_inherited_method",
        is_resolved=True,
        derived_from=[edge.src, type_class_id],
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
    # Prefer the authoritative concrete enclosing-class id (same contract as
    # Site-1; WI-supat). A valid, same-language id starts the parent-walk from
    # exactly the caller's lexical class, so the enclosing receiver is not
    # under-determined — skip the enclosing ambiguity guard. The field-TYPE
    # disambiguation below is INDEPENDENT (its own id, its own guard).
    if (
        enclosing_class_id is not None
        and enclosing_class_id in class_symbols
        and call_language(class_symbols[enclosing_class_id].language or "") == call_language(src_lang)
    ):
        encl_class_ids = [enclosing_class_id]
    else:
        encl_class_ids = _same_language_class_ids(
            enclosing_class, src_lang, class_ids_by_name, class_symbols,
        )
        if not encl_class_ids:  # pragma: no cover
            return None
        # INV-fahub ambiguity guard on the ENCLOSING class,
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

    # Prefer the concrete field-TYPE id the producer threaded on the parent's
    # meta['field_type_ids'] (validated, same-language; WI-supat). It names
    # exactly one type, so the field's type is not under-determined — skip the
    # field-type ambiguity guard. Absent (Java stamps none) / stale /
    # foreign-language falls back to the re-globalized name path + guard below.
    if (
        field_type_id is not None
        and field_type_id in class_symbols
        and call_language(class_symbols[field_type_id].language or "") == call_language(src_lang)
    ):
        field_type_class_ids = [field_type_id]
    else:
        # Look up the field's type as a same-language class symbol.
        field_type_class_ids = _same_language_class_ids(
            field_type, src_lang, class_ids_by_name, class_symbols,
        )
        if not field_type_class_ids:
            return None
        # The SECOND INV-fahub guard — the field's TYPE name is
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
    resolved_field_type_id: str = ""
    for ftid in field_type_class_ids:
        direct = methods_by_class.get(ftid)
        if direct is not None:
            resolved_target, resolved_field_type_id = direct, ftid
            break
        if walker is not None:
            via = walker(
                ftid, method_short, inheritance_index,
                method_index, _DEFAULT_DEPTH_CAP,
            )
            # INV-guviv: the field's TYPE is the walk's start class here, so a
            # builtin base declared ahead of its in-tree ancestor shadows the
            # walk's binding exactly as at Site-1 / Site-2 -- skip it.
            if via is not None and not (
                src_lang == "python"
                and _python_stdlib_base_shadows(
                    ftid, method_short, inheritance_index,
                    class_symbols, method_index,
                )
            ):
                resolved_target, resolved_field_type_id = via, ftid
                break

    if resolved_target is None:
        return None
    # Dedup: the analyzer may already have a resolved calls edge to this target
    # (the Python producer can emit one).
    if (edge.src, resolved_target.id) in existing_call_pairs:
        return None
    return Edge.create(
        src=edge.src, dst=resolved_target.id, edge_type="calls",
        line=edge.line, confidence=_SITE_3_CONFIDENCE,
        origin=PASS_ID, origin_run_id=run.execution_id,
        evidence_type="ast_call_inherited_field",
        is_resolved=True,
        derived_from=[edge.id, resolved_field_type_id],
        resolved_by=resolved_by.RECEIVER_TYPE,
    )
