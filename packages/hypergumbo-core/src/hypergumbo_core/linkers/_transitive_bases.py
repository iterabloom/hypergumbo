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

This helper walks ``extends``/``implements`` edges from a class up
through every reachable in-tree ancestor, collecting the raw base-class
name strings that each ancestor's ``meta.base_classes`` records. The
caller is responsible for normalization (``_short_base_name`` style).

Why a shared helper instead of per-linker logic
-----------------------------------------------
The walk is identical across linkers: same edge types, same metadata
field, same termination condition. Factoring it once also localizes
the cycle-guard (a class can transitively re-reach itself in
pathological cases) and the "edge index" build cost.

Scope (WI-halat)
----------------
Per the WI-halat acceptance criteria, this fix targets framework-
dispatch linkers that consult ``meta.base_classes`` literally. The
helper does not modify analyzer-emitted metadata; it produces a
read-only ancestor-name set that consumers can fold into the existing
matcher.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..ir import Edge, Symbol


_INHERITANCE_EDGE_TYPES = ("extends", "implements")


def build_inheritance_index(
    edges: list["Edge"],
) -> dict[str, list[str]]:
    """Build a src-id -> list-of-dst-ids map from extends/implements edges.

    The returned map keys on the child class's symbol id and yields the
    parent symbol ids reachable in one step. Edge types other than
    ``extends`` / ``implements`` are ignored.
    """
    index: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.edge_type in _INHERITANCE_EDGE_TYPES:
            index[edge.src].append(edge.dst)
    return index


def collect_transitive_base_names(
    class_sym: "Symbol",
    symbol_by_id: dict[str, "Symbol"],
    inheritance_index: dict[str, list[str]],
) -> list[str]:
    """Return the raw base-class name strings reachable from ``class_sym``.

    Walks ``extends``/``implements`` edges from ``class_sym`` to every
    transitively-reachable in-tree ancestor (BFS), collecting each
    visited symbol's ``meta.base_classes`` strings — including
    ``class_sym``'s own list. The class itself's name is NOT included,
    only the names listed in its (and its ancestors') ``base_classes``
    metadata.

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
        bases = meta.get("base_classes", []) or []
        for raw in bases:
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
