# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Airflow framework-dispatch linker (WI-nutav).

Validates that the linker creates ``dispatches_to`` edges from Airflow
subclasses (``BaseOperator`` / ``BaseHook`` / ``BaseSensor`` / ``BaseTrigger``)
to the framework-called override methods defined on those subclasses, so the
overrides are not misclassified as dead code by the dead-code analyzer.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.airflow_framework_dispatch import (
    AIRFLOW_BASE_METHODS,
    _build_method_index,
    _find_airflow_subclasses,
    _short_base_name,
    link_airflow_framework_dispatch,
)
from hypergumbo_core.linkers.registry import LinkerContext


def _ctx(
    symbols: list[Symbol], edges: list[Edge] | None = None,
) -> LinkerContext:
    return LinkerContext(
        repo_root=Path("/repo"),
        symbols=symbols,
        edges=list(edges or []),
    )


def _class_sym(
    name: str,
    path: str = "dags/my_operator.py",
    base_classes: list[str] | None = None,
    span: tuple[int, int] = (1, 20),
    kind: str = "class",
) -> Symbol:
    meta: dict[str, object] = {}
    if base_classes is not None:
        meta["base_classes"] = base_classes
    return Symbol(
        id=f"python:{path}:{span[0]}-{span[1]}:{name}:{kind}",
        name=name,
        kind=kind,
        language="python",
        path=path,
        span=Span(span[0], span[1], 0, 0),
        meta=meta or None,
    )


def _method_sym(
    qualified_name: str,
    path: str = "dags/my_operator.py",
    span: tuple[int, int] = (5, 10),
) -> Symbol:
    return Symbol(
        id=f"python:{path}:{span[0]}-{span[1]}:{qualified_name}:method",
        name=qualified_name,
        kind="method",
        language="python",
        path=path,
        span=Span(span[0], span[1], 0, 0),
        meta=None,
    )


class TestShortBaseName:
    def test_bare_name(self) -> None:
        assert _short_base_name("BaseOperator") == "BaseOperator"

    def test_dotted_qualified(self) -> None:
        assert _short_base_name("airflow.models.BaseOperator") == "BaseOperator"

    def test_generic_parameters_stripped(self) -> None:
        assert _short_base_name("BaseSensor<int>") == "BaseSensor"

    def test_dotted_then_generic(self) -> None:
        assert (
            _short_base_name("airflow.sensors.base.BaseSensor<Any>") == "BaseSensor"
        )

    def test_scoped_double_colon(self) -> None:
        assert _short_base_name("airflow::BaseTrigger") == "BaseTrigger"


class TestFindAirflowSubclasses:
    def test_detects_base_operator_subclass(self) -> None:
        c = _class_sym("MyOperator", base_classes=["BaseOperator"])
        result = _find_airflow_subclasses([c])
        assert len(result) == 1
        sym, methods, _is_fallback = result[0]
        assert sym.name == "MyOperator"
        assert methods == AIRFLOW_BASE_METHODS["BaseOperator"]

    def test_detects_qualified_base(self) -> None:
        c = _class_sym("MyHook", base_classes=["airflow.hooks.base.BaseHook"])
        result = _find_airflow_subclasses([c])
        assert len(result) == 1
        assert result[0][1] == AIRFLOW_BASE_METHODS["BaseHook"]
        assert result[0][2] is False

    def test_base_sensor_operator_alias(self) -> None:
        c = _class_sym("S", base_classes=["BaseSensorOperator"])
        result = _find_airflow_subclasses([c])
        assert len(result) == 1
        assert result[0][1] == AIRFLOW_BASE_METHODS["BaseSensorOperator"]

    def test_non_airflow_base_ignored(self) -> None:
        c = _class_sym("Widget", base_classes=["MyOwnBase", "object"])
        result = _find_airflow_subclasses([c])
        assert result == []

    def test_class_with_no_base_classes_ignored(self) -> None:
        c = _class_sym("Plain", base_classes=None)
        assert _find_airflow_subclasses([c]) == []

    def test_non_class_symbols_ignored(self) -> None:
        fn = Symbol(
            id="python:dags/x.py:1-5:top:function",
            name="top",
            kind="function",
            language="python",
            path="dags/x.py",
            span=Span(1, 5, 0, 0),
            meta={"base_classes": ["BaseOperator"]},
        )
        assert _find_airflow_subclasses([fn]) == []

    def test_multi_inherit_union_of_method_sets(self) -> None:
        # Rare but real: an internal plugin mixes BaseOperator and BaseHook
        # to share state; the methods from both bases are in scope.
        c = _class_sym("Hybrid", base_classes=["BaseOperator", "BaseHook"])
        result = _find_airflow_subclasses([c])
        assert len(result) == 1
        methods = result[0][1]
        assert "execute" in methods  # from BaseOperator
        assert "get_conn" in methods  # from BaseHook

    def test_struct_kind_accepted(self) -> None:
        # struct kind included for language parity with the inheritance linker.
        c = _class_sym(
            "Rec", base_classes=["BaseOperator"], kind="struct",
        )
        result = _find_airflow_subclasses([c])
        assert len(result) == 1


class TestBuildMethodIndex:
    def test_indexes_methods_by_path_and_qualified_name(self) -> None:
        m = _method_sym("MyOperator.execute")
        index = _build_method_index([m])
        assert index[("dags/my_operator.py", "MyOperator.execute")] is m

    def test_ignores_functions_and_classes(self) -> None:
        fn = Symbol(
            id="python:x.py:1-3:top:function",
            name="top",
            kind="function",
            language="python",
            path="x.py",
            span=Span(1, 3, 0, 0),
        )
        c = _class_sym("K")
        assert _build_method_index([fn, c]) == {}

    def test_ignores_unqualified_methods(self) -> None:
        # Methods from languages that don't emit ClassName.method notation
        # shouldn't appear in the index — the linker is Python-shaped.
        m = Symbol(
            id="python:x.py:1-3:plain:method",
            name="plain",
            kind="method",
            language="python",
            path="x.py",
            span=Span(1, 3, 0, 0),
        )
        assert _build_method_index([m]) == {}

    def test_first_occurrence_wins_on_collision(self) -> None:
        m1 = _method_sym("C.execute", span=(5, 10))
        m2 = _method_sym("C.execute", span=(20, 25))
        index = _build_method_index([m1, m2])
        assert index[("dags/my_operator.py", "C.execute")] is m1


class TestLinkAirflowFrameworkDispatch:
    def test_empty_corpus(self) -> None:
        ctx = _ctx([], edges=[])
        result = link_airflow_framework_dispatch(ctx)
        assert result.edges == []
        assert result.symbols == []

    def test_no_airflow_classes_no_edges(self) -> None:
        ctx = _ctx([_class_sym("Plain", base_classes=["object"])])
        assert link_airflow_framework_dispatch(ctx).edges == []

    def test_emits_edge_for_overridden_execute(self) -> None:
        cls = _class_sym("MyOperator", base_classes=["BaseOperator"])
        method = _method_sym("MyOperator.execute")
        ctx = _ctx([cls, method], edges=[])
        result = link_airflow_framework_dispatch(ctx)
        assert len(result.edges) == 1
        e = result.edges[0]
        assert e.src == cls.id
        assert e.dst == method.id
        assert e.edge_type == "dispatches_to"
        assert e.confidence == 0.90
        assert e.evidence_type == "ast_call_direct"
        assert (e.meta or {}).get("framework_dispatch") == "airflow"

    def test_emits_edges_for_all_overridden_methods(self) -> None:
        cls = _class_sym("MyOperator", base_classes=["BaseOperator"])
        m_exec = _method_sym("MyOperator.execute", span=(5, 9))
        m_pre = _method_sym("MyOperator.pre_execute", span=(11, 14))
        m_post = _method_sym("MyOperator.post_execute", span=(16, 19))
        ctx = _ctx([cls, m_exec, m_pre, m_post], edges=[])
        result = link_airflow_framework_dispatch(ctx)
        dsts = {e.dst for e in result.edges}
        assert dsts == {m_exec.id, m_pre.id, m_post.id}

    def test_does_not_emit_for_non_framework_methods(self) -> None:
        cls = _class_sym("MyOperator", base_classes=["BaseOperator"])
        m_helper = _method_sym("MyOperator.my_helper")
        ctx = _ctx([cls, m_helper], edges=[])
        assert link_airflow_framework_dispatch(ctx).edges == []

    def test_does_not_cross_file_boundaries(self) -> None:
        # Two classes named 'MyOperator' in different files — each gets its
        # own methods, not the other's.
        cls_a = _class_sym(
            "MyOperator", path="dags/a.py", base_classes=["BaseOperator"],
        )
        cls_b = _class_sym(
            "MyOperator", path="dags/b.py", base_classes=["BaseOperator"],
        )
        m_a = _method_sym("MyOperator.execute", path="dags/a.py")
        m_b = _method_sym("MyOperator.execute", path="dags/b.py")
        ctx = _ctx([cls_a, cls_b, m_a, m_b], edges=[])
        result = link_airflow_framework_dispatch(ctx)
        pairs = {(e.src, e.dst) for e in result.edges}
        assert pairs == {(cls_a.id, m_a.id), (cls_b.id, m_b.id)}

    def test_hook_base_triggers_get_conn_edge(self) -> None:
        cls = _class_sym(
            "MyHook", path="hooks/x.py", base_classes=["BaseHook"],
        )
        m_conn = _method_sym("MyHook.get_conn", path="hooks/x.py")
        ctx = _ctx([cls, m_conn], edges=[])
        result = link_airflow_framework_dispatch(ctx)
        assert {e.dst for e in result.edges} == {m_conn.id}

    def test_trigger_base_triggers_run_edge(self) -> None:
        cls = _class_sym(
            "MyTrigger", path="triggers/x.py", base_classes=["BaseTrigger"],
        )
        m_run = _method_sym("MyTrigger.run", path="triggers/x.py")
        ctx = _ctx([cls, m_run], edges=[])
        assert {e.dst for e in link_airflow_framework_dispatch(ctx).edges} == {
            m_run.id,
        }

    def test_skips_edges_that_already_exist(self) -> None:
        cls = _class_sym("MyOperator", base_classes=["BaseOperator"])
        m = _method_sym("MyOperator.execute")
        prior = Edge.create(
            src=cls.id, dst=m.id, edge_type="dispatches_to", line=1,
        )
        ctx = _ctx([cls, m], edges=[prior])
        assert link_airflow_framework_dispatch(ctx).edges == []

    def test_transitive_subclass_via_extends_edge(self) -> None:
        # WI-halat: MyOp -> AlloyDBWriteBaseOperator -> BaseOperator.
        # AlloyDBWriteBaseOperator is in-tree (an intermediate base);
        # BaseOperator is external. The linker must walk the extends
        # edge and recognise MyOp as an Airflow subclass even though
        # MyOp.meta.base_classes lists only "AlloyDBWriteBaseOperator".
        intermediate = _class_sym(
            "AlloyDBWriteBaseOperator",
            path="airflow_plugins/base.py",
            base_classes=["BaseOperator"],
            span=(50, 80),
        )
        leaf = _class_sym(
            "MyOp",
            path="dags/alloydb.py",
            base_classes=["AlloyDBWriteBaseOperator"],
            span=(100, 120),
        )
        method = _method_sym(
            "MyOp.execute", path="dags/alloydb.py", span=(105, 110),
        )
        prior = Edge.create(
            src=leaf.id, dst=intermediate.id,
            edge_type="extends", line=100,
        )
        ctx = _ctx([leaf, intermediate, method], edges=[prior])
        result = link_airflow_framework_dispatch(ctx)
        assert {(e.src, e.dst) for e in result.edges} == {(leaf.id, method.id)}

    def test_transitive_two_intermediates(self) -> None:
        # MyOp -> Mid1 -> Mid2 -> BaseSensor (external).
        mid2 = _class_sym(
            "Mid2", path="bases/m2.py",
            base_classes=["BaseSensor"], span=(1, 5),
        )
        mid1 = _class_sym(
            "Mid1", path="bases/m1.py",
            base_classes=["Mid2"], span=(1, 5),
        )
        leaf = _class_sym(
            "MyOp", path="dags/x.py",
            base_classes=["Mid1"], span=(1, 5),
        )
        method = _method_sym(
            "MyOp.poke", path="dags/x.py", span=(2, 4),
        )
        edges = [
            Edge.create(
                src=leaf.id, dst=mid1.id, edge_type="extends", line=1,
            ),
            Edge.create(
                src=mid1.id, dst=mid2.id, edge_type="extends", line=1,
            ),
        ]
        ctx = _ctx([leaf, mid1, mid2, method], edges=edges)
        result = link_airflow_framework_dispatch(ctx)
        assert {e.dst for e in result.edges} == {method.id}

    def test_deduplicates_within_run(self) -> None:
        # Two classes, both named MyOperator in the same file — method index
        # only keeps the first, so both classes dispatch to the same method.
        # The dedupe set must prevent emitting the same edge twice.
        cls1 = _class_sym("MyOperator", base_classes=["BaseOperator"], span=(1, 10))
        cls2 = _class_sym("MyOperator", base_classes=["BaseOperator"], span=(30, 40))
        m = _method_sym("MyOperator.execute", span=(3, 8))
        ctx = _ctx([cls1, cls2, m], edges=[])
        result = link_airflow_framework_dispatch(ctx)
        # The class IDs differ (different spans), so two distinct edges to
        # the same method should both appear. But re-running must not grow.
        pairs = {(e.src, e.dst) for e in result.edges}
        assert (cls1.id, m.id) in pairs
        assert (cls2.id, m.id) in pairs
        assert len(result.edges) == 2


# ---------------------------------------------------------------------------
# INV-zuhub property tests: airflow framework-dispatch fallback (WI-bojok PR3)
# ---------------------------------------------------------------------------


class TestInvZuhubAirflowFallback:
    """INV-zuhub item 1, dispatches_to family — airflow framework-base shape.

    The short-base-name match against ``AIRFLOW_BASE_METHODS`` cannot
    tell airflow's external framework type from an in-tree Python class
    of the same name. Three property tests mirror the canonical
    inheritance.py / kafka shape from earlier PRs:

    1. ``test_dispatches_to_prefers_fqn_over_in_tree_collision``: FQN-
       qualified raw entries (``airflow.models.BaseOperator``) are
       precision matches even when the short name has an in-tree class
       collision.
    2. ``test_dispatches_to_no_in_tree_collision_keeps_high_confidence``:
       short-name matches with no in-tree collision are precision by
       elimination.
    3. ``test_dispatches_to_deterministic_fallback_when_ambiguous``:
       unqualified short-name matches that collide with an in-tree
       Python class downgrade to ``confidence <= 0.5`` and
       ``meta["disambiguation_fallback"] = True``.
    """

    def test_dispatches_to_prefers_fqn_over_in_tree_collision(self) -> None:
        in_tree = _class_sym(
            "BaseOperator", path="dags/internal/BaseOperator.py",
        )
        user_cls = _class_sym(
            "MyOperator", path="dags/my_operator.py",
            base_classes=["airflow.models.BaseOperator"],
            span=(1, 30),
        )
        execute_m = _method_sym(
            "MyOperator.execute", path="dags/my_operator.py", span=(10, 15),
        )
        ctx = _ctx([in_tree, user_cls, execute_m])
        result = link_airflow_framework_dispatch(ctx)
        edges = [e for e in result.edges if e.src == user_cls.id]
        assert len(edges) == 1
        edge = edges[0]
        assert edge.confidence == 0.90
        assert (edge.meta or {}).get("disambiguation_fallback") is not True

    def test_dispatches_to_no_in_tree_collision_keeps_high_confidence(
        self,
    ) -> None:
        # No in-tree ``BaseOperator`` exists, so the unqualified base
        # can only mean airflow's framework class.
        user_cls = _class_sym(
            "MyOperator", path="dags/my_operator.py",
            base_classes=["BaseOperator"], span=(1, 30),
        )
        execute_m = _method_sym(
            "MyOperator.execute", path="dags/my_operator.py", span=(10, 15),
        )
        ctx = _ctx([user_cls, execute_m])
        result = link_airflow_framework_dispatch(ctx)
        edges = [e for e in result.edges if e.src == user_cls.id]
        assert len(edges) == 1
        assert edges[0].confidence == 0.90
        assert (edges[0].meta or {}).get("disambiguation_fallback") is not True

    def test_dispatches_to_deterministic_fallback_when_ambiguous(self) -> None:
        # An in-tree class named ``BaseOperator`` collides with airflow's
        # framework class. A subclass declaring an unqualified
        # ``BaseOperator`` base is ambiguous.
        in_tree = _class_sym(
            "BaseOperator", path="dags/internal/BaseOperator.py",
        )
        user_cls = _class_sym(
            "MyOperator", path="dags/my_operator.py",
            base_classes=["BaseOperator"], span=(1, 30),
        )
        execute_m = _method_sym(
            "MyOperator.execute", path="dags/my_operator.py", span=(10, 15),
        )
        ctx = _ctx([in_tree, user_cls, execute_m])
        result = link_airflow_framework_dispatch(ctx)
        edges = [e for e in result.edges if e.src == user_cls.id]
        assert len(edges) == 1
        edge = edges[0]
        assert edge.confidence <= 0.5
        assert edge.meta is not None
        assert edge.meta.get("disambiguation_fallback") is True
        assert edge.meta.get("framework_dispatch") == "airflow"
