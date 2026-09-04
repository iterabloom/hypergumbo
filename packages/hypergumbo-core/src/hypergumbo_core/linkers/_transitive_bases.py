# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared helper: collect transitive base-class names via inheritance edges.

Framework-dispatch linkers (``airflow_framework_dispatch``,
``django_orm_dispatch``, etc.) match a class against a per-base method
table by reading ``meta.base_classes`` directly. That works for direct
subclasses of a framework base but misses the dominant real-world case:
projects use intermediate base classes (``AlloyDBWriteBaseOperator``
extending ``BaseOperator``, ``LoggedModel`` extending ``models.Model``,
``HierarkeyForm`` extending ``Form``) that wrap the framework's actual
base. The intermediate is in-tree; ``MyClass.meta.base_classes`` lists
only ``[AlloyDBWriteBaseOperator]``, not the ultimate framework base.

This helper walks the registry's inheritance family — :data:`INHERITANCE_EDGE_TYPES`,
i.e. ``extends`` / ``implements`` / ``inherits``, not a local literal (INV-nosoz
removed the two-member literal that silently omitted ``inherits``) — from a class
up through every reachable in-tree ancestor, collecting the raw base-class name
strings that each ancestor's ``meta.base_classes`` records. Callers may pass a
different edge-type set (``includes``) and different ``meta_keys``
(``interfaces``, for the JVM languages). The caller is responsible for
normalization (``_short_base_name`` style).

The module also exports the INV-zuhub short-name disambiguation pair
:func:`build_short_name_collisions` / :func:`short_name_fallback`, and
:func:`build_inheritance_index`.

Why a shared helper instead of per-linker logic
-----------------------------------------------
The walk is identical across linkers: same edge types, same metadata
field, same termination condition. Factoring it once also localizes
the cycle-guard (a class can transitively re-reach itself in
pathological cases) and the "edge index" build cost.

Scope (WI-halat)
----------------
Per the WI-halat acceptance criteria, this fix originally targeted
framework-dispatch linkers that consult ``meta.base_classes`` literally;
the ``meta_keys`` parameter has since generalized it to any metadata key
naming ancestors. The
helper does not modify analyzer-emitted metadata; it produces a
read-only ancestor-name set that consumers can fold into the existing
matcher.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Iterable

from ..edge_types import INHERITANCE_EDGE_TYPES
from ..symbol_kinds import type_like_kind_names

if TYPE_CHECKING:
    from ..ir import Edge, Symbol


# INV-nosoz: the default is the registry family, not a local literal. It
# read ``("extends", "implements")`` and so omitted ``inherits``, making
# Solidity base chains invisible to every caller that took the default.
_INHERITANCE_EDGE_TYPES: tuple[str, ...] = tuple(sorted(INHERITANCE_EDGE_TYPES))


def build_short_name_collisions(
    symbols: list["Symbol"],
    keys: frozenset[str] | set[str],
    *,
    kinds: frozenset[str] | set[str] | None = None,
    languages: frozenset[str] | set[str] | None = None,
) -> frozenset[str]:
    """Return the subset of ``keys`` that collide with an in-tree symbol.

    A *collision* is the existence of any in-tree ``Symbol`` whose
    ``name`` is one of *keys*, ``kind`` is in *kinds*, and (if
    *languages* is provided) ``language`` is in *languages*. The
    returned frozenset is the keys for which a collision exists — i.e.
    the names that cannot be presumed external by elimination.

    Used by framework-dispatch linkers to flag short-name matches
    against a literal external-API set as simple-name fallbacks under
    INV-zuhub: a class extending an unqualified ``Transformer`` /
    ``Model`` / ``ConfigurationProperties`` could be referring to the
    external framework type *or* to an in-tree class of the same name,
    and the static analysis cannot disambiguate.
    """
    # ``kinds=None`` means "every nominal type declaration". Resolved from the
    # registry rather than defaulted to a literal: audit-findings 0018 measured
    # that the literal this replaced omitted ``protocol``, so every Swift
    # caller silently skipped collision detection.
    effective_kinds = type_like_kind_names() if kinds is None else kinds
    collisions: set[str] = set()
    for sym in symbols:
        if sym.name not in keys:
            continue
        if sym.kind not in effective_kinds:
            continue
        if languages is not None and sym.language not in languages:
            continue
        collisions.add(sym.name)
    return frozenset(collisions)


def short_name_fallback(
    raw_entry: str,
    short_name: str,
    in_tree_collisions: frozenset[str],
    fqn_prefixes: Iterable[str],
) -> bool:
    """Whether a ``(raw_entry → short_name)`` match is a simple-name fallback.

    A match is precise (returns ``False``) when either:

    - The raw entry is FQN-qualified — it starts with any string in
      *fqn_prefixes* (e.g. ``"org.apache.kafka."``, ``"django."``,
      ``"airflow."``). FQN qualifiers unambiguously name the external
      framework type even when an in-tree collision exists.
    - There is no in-tree symbol with the matched short name —
      ``short_name not in in_tree_collisions``. The unqualified raw
      entry cannot mean anything but the external framework type.

    A match is fallback (returns ``True``) when an unqualified raw
    entry's short name has an in-tree collision: the static analysis
    cannot tell whether the user meant the framework type or the
    in-tree one. Per INV-zuhub, edges built on such matches must carry
    ``confidence <= 0.5`` and ``meta["disambiguation_fallback"] = True``.
    """
    for prefix in fqn_prefixes:
        if raw_entry.startswith(prefix):
            return False
    return short_name in in_tree_collisions


def build_inheritance_index(
    edges: list["Edge"],
    edge_types: tuple[str, ...] = _INHERITANCE_EDGE_TYPES,
) -> dict[str, list[str]]:
    """Build a src-id -> list-of-dst-ids map from inheritance-style edges.

    The returned map keys on the child class's symbol id and yields the
    parent symbol ids reachable in one step. Edge types other than the
    tuple passed in ``edge_types`` are ignored.

    Args:
        edges: All edges to consider.
        edge_types: Tuple of edge_type strings to treat as inheritance.
            Defaults to the registry family
            (:data:`~hypergumbo_core.edge_types.INHERITANCE_EDGE_TYPES`).
            Ruby ``include``/``extend`` ancestor walks additionally pass
            ``"includes"`` (WI-hatip / PR-2 of INV-nilud) so the mixin edge
            participates in transitive base-name collection. Callers pass a
            type tuple rather than the edge predicate because this index is
            built from types alone; a caller that needs to tell a Ruby mixin
            from a Makefile ``include`` should filter with
            :func:`~hypergumbo_core.edge_types.is_inheritance_edge` first.
    """
    index: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.edge_type in edge_types:
            index[edge.src].append(edge.dst)
    return index


def collect_transitive_base_names(
    class_sym: "Symbol",
    symbol_by_id: dict[str, "Symbol"],
    inheritance_index: dict[str, list[str]],
    meta_keys: tuple[str, ...] = ("base_classes",),
) -> list[str]:
    """Return the raw base-class name strings reachable from ``class_sym``.

    Walks ``extends``/``implements`` edges from ``class_sym`` to every
    transitively-reachable in-tree ancestor (BFS), collecting strings
    from each visited symbol's ``meta`` keys listed in ``meta_keys``
    (default ``("base_classes",)``) — including ``class_sym``'s own
    entries. The class itself's name is NOT included, only the names
    listed in its (and its ancestors') metadata.

    The ``meta_keys`` parameter exists because Java/Kotlin/Scala
    analyzers split ancestors into ``base_classes`` (extends) and
    ``interfaces`` (implements) under separate metadata keys. Linkers
    that match against interface names (kafka_streams_dispatch) pass
    ``meta_keys=("base_classes", "interfaces")`` so the root class's
    interface declarations are captured alongside its extends targets.
    Default keeps WI-halat callers (airflow, django) backward-compatible.

    Names are returned in their raw, un-normalized form: callers should
    apply their own ``_short_base_name``-style stripping before lookup
    against framework base tables.

    Cycle protection: visited-set prevents infinite loops if the
    inheritance graph is malformed (e.g., a class transitively extends
    itself, which can happen when name-collision resolution in the
    inheritance linker accidentally points an edge back at the source).

    The returned value is a list (not a set) so callers can preserve
    declaration order if they care; duplicates within the list are
    expected and fine.
    """
    collected: list[str] = []
    visited: set[str] = {class_sym.id}
    queue: list["Symbol"] = [class_sym]

    while queue:
        current = queue.pop(0)
        meta = current.meta or {}
        for key in meta_keys:
            entries = meta.get(key, []) or []
            if not isinstance(entries, list):
                continue
            for raw in entries:
                if isinstance(raw, str):
                    collected.append(raw)

        for parent_id in inheritance_index.get(current.id, ()):
            if parent_id in visited:
                continue
            visited.add(parent_id)
            parent_sym = symbol_by_id.get(parent_id)
            if parent_sym is None:
                continue
            queue.append(parent_sym)

    return collected
