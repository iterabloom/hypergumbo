# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the shared transitive-base-name helper (WI-halat)."""
from __future__ import annotations

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers._transitive_bases import (
    build_inheritance_index,
    collect_transitive_base_names,
)


def _cls(
    name: str,
    *,
    path: str = "app.py",
    base_classes: list[str] | None = None,
    span: tuple[int, int] = (1, 5),
) -> Symbol:
    meta: dict[str, object] = {}
    if base_classes is not None:
        meta["base_classes"] = base_classes
    return Symbol(
        id=f"python:{path}:{span[0]}-{span[1]}:{name}:class",
        name=name,
        kind="class",
        language="python",
        path=path,
        span=Span(span[0], span[1], 0, 0),
        meta=meta or None,
    )


def _edge(src: Symbol, dst: Symbol, edge_type: str = "extends") -> Edge:
    return Edge.create(
        src=src.id, dst=dst.id, edge_type=edge_type, line=1,
    )


class TestBuildInheritanceIndex:
    def test_indexes_extends_edges(self) -> None:
        a, b = _cls("A"), _cls("B", span=(10, 15))
        index = build_inheritance_index([_edge(a, b)])
        assert index[a.id] == [b.id]

    def test_indexes_implements_edges(self) -> None:
        a, b = _cls("A"), _cls("B", span=(10, 15))
        index = build_inheritance_index([_edge(a, b, "implements")])
        assert index[a.id] == [b.id]

    def test_ignores_unrelated_edges(self) -> None:
        a, b = _cls("A"), _cls("B", span=(10, 15))
        index = build_inheritance_index([_edge(a, b, "calls")])
        assert a.id not in index

    def test_multiple_parents(self) -> None:
        a = _cls("A")
        b = _cls("B", span=(10, 15))
        c = _cls("C", span=(20, 25))
        index = build_inheritance_index([_edge(a, b), _edge(a, c)])
        assert sorted(index[a.id]) == sorted([b.id, c.id])


class TestCollectTransitiveBaseNames:
    def test_direct_base_names_from_self(self) -> None:
        a = _cls("A", base_classes=["BaseOperator"])
        names = collect_transitive_base_names(a, {a.id: a}, {})
        assert names == ["BaseOperator"]

    def test_walks_one_intermediate(self) -> None:
        # MyOp -> AlloyDBWriteBaseOperator -> (external) BaseOperator
        # Both MyOp and AlloyDBWriteBaseOperator are in-tree.
        intermediate = _cls(
            "AlloyDBWriteBaseOperator",
            span=(50, 80),
            base_classes=["BaseOperator"],
        )
        leaf = _cls(
            "MyOp", span=(100, 120),
            base_classes=["AlloyDBWriteBaseOperator"],
        )
        edges = [_edge(leaf, intermediate)]
        sym_by_id = {leaf.id: leaf, intermediate.id: intermediate}
        index = build_inheritance_index(edges)
        names = collect_transitive_base_names(leaf, sym_by_id, index)
        assert "AlloyDBWriteBaseOperator" in names
        assert "BaseOperator" in names

    def test_walks_two_intermediates(self) -> None:
        # Order -> LoggedModel -> AbstractModel -> (external) Model
        abstract = _cls(
            "AbstractModel", span=(10, 20),
            base_classes=["Model"],
        )
        logged = _cls(
            "LoggedModel", span=(30, 40),
            base_classes=["AbstractModel"],
        )
        order = _cls(
            "Order", span=(50, 60),
            base_classes=["LoggedModel"],
        )
        edges = [_edge(order, logged), _edge(logged, abstract)]
        sym_by_id = {s.id: s for s in (order, logged, abstract)}
        index = build_inheritance_index(edges)
        names = collect_transitive_base_names(order, sym_by_id, index)
        assert {"LoggedModel", "AbstractModel", "Model"} <= set(names)

    def test_diamond_no_double_visit(self) -> None:
        # D inherits B and C; both inherit A. A's bases must be
        # collected once even though D reaches A by two paths.
        a = _cls("A", span=(1, 5), base_classes=["External"])
        b = _cls("B", span=(10, 15), base_classes=["A"])
        c = _cls("C", span=(20, 25), base_classes=["A"])
        d = _cls("D", span=(30, 35), base_classes=["B", "C"])
        edges = [_edge(d, b), _edge(d, c), _edge(b, a), _edge(c, a)]
        sym_by_id = {s.id: s for s in (a, b, c, d)}
        index = build_inheritance_index(edges)
        names = collect_transitive_base_names(d, sym_by_id, index)
        # External appears once (A visited once).
        assert names.count("External") == 1

    def test_self_cycle_safe(self) -> None:
        # Pathological: a class transitively extends itself.
        a = _cls("A", base_classes=["A"])
        edges = [_edge(a, a)]
        sym_by_id = {a.id: a}
        index = build_inheritance_index(edges)
        # Should terminate and just yield the literal name from
        # meta.base_classes once.
        names = collect_transitive_base_names(a, sym_by_id, index)
        assert names == ["A"]

    def test_missing_parent_symbol_skipped(self) -> None:
        # extends edge points at a dst id we don't have a symbol for —
        # walk should not crash.
        a = _cls("A", base_classes=["Missing"])
        edges = [
            Edge.create(
                src=a.id, dst="python:nowhere:1-2:Missing:class",
                edge_type="extends", line=1,
            ),
        ]
        sym_by_id = {a.id: a}
        index = build_inheritance_index(edges)
        names = collect_transitive_base_names(a, sym_by_id, index)
        assert names == ["Missing"]

    def test_class_with_no_meta(self) -> None:
        a = Symbol(
            id="python:x.py:1-5:A:class",
            name="A",
            kind="class",
            language="python",
            path="x.py",
            span=Span(1, 5, 0, 0),
            meta=None,
        )
        names = collect_transitive_base_names(a, {a.id: a}, {})
        assert names == []

    def test_non_string_base_class_entries_ignored(self) -> None:
        # Defensive: a malformed base_classes list with non-string
        # entries should not break the walk.
        a = _cls("A", base_classes=["BaseOperator", "BaseHook"])
        a.meta["base_classes"] = ["BaseOperator", 42, None, "BaseHook"]
        names = collect_transitive_base_names(a, {a.id: a}, {})
        assert names == ["BaseOperator", "BaseHook"]
