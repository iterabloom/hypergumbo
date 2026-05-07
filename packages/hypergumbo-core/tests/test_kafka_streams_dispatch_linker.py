# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Kafka Streams topology-callback dispatch linker (WI-lisov).

Validates that the linker emits ``dispatches_to`` edges from concrete
callback impl classes to the framework-called methods declared by their
implemented interface(s), so the callbacks are not misclassified as dead
code by the dead-code analyzer.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.kafka_streams_dispatch import (
    KAFKA_STREAMS_CALLBACKS,
    _build_class_method_index,
    _callback_interfaces_on,
    _expected_method_names,
    _short_type_name,
    link_kafka_streams_dispatch,
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
    *,
    path: str = "src/main/java/com/example/UppercaseMapper.java",
    base_classes: list[str] | None = None,
    interfaces: list[str] | None = None,
    span: tuple[int, int] = (1, 20),
    kind: str = "class",
    language: str = "java",
) -> Symbol:
    meta: dict[str, object] = {}
    if base_classes is not None:
        meta["base_classes"] = base_classes
    if interfaces is not None:
        meta["interfaces"] = interfaces
    return Symbol(
        id=f"{language}:{path}:{span[0]}-{span[1]}:{name}:{kind}",
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=Span(span[0], span[1], 0, 0),
        meta=meta or None,
    )


def _method_sym(
    qualified_name: str,
    *,
    path: str = "src/main/java/com/example/UppercaseMapper.java",
    span: tuple[int, int] = (5, 10),
    language: str = "java",
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{span[0]}-{span[1]}:{qualified_name}:method",
        name=qualified_name,
        kind="method",
        language=language,
        path=path,
        span=Span(span[0], span[1], 0, 0),
    )


# ---------------------------------------------------------------------------
# Unit tests: _short_type_name
# ---------------------------------------------------------------------------


class TestShortTypeName:
    def test_bare_name_unchanged(self) -> None:
        assert _short_type_name("ValueMapper") == "ValueMapper"

    def test_qualified_collapsed(self) -> None:
        assert _short_type_name(
            "org.apache.kafka.streams.kstream.ValueMapper",
        ) == "ValueMapper"

    def test_generic_parameters_stripped(self) -> None:
        assert _short_type_name("ValueMapper<K, V, VR>") == "ValueMapper"

    def test_qualified_with_generics(self) -> None:
        assert _short_type_name(
            "org.apache.kafka.streams.kstream.ValueMapper<K, V, VR>",
        ) == "ValueMapper"


# ---------------------------------------------------------------------------
# Unit tests: _callback_interfaces_on
# ---------------------------------------------------------------------------


class TestCallbackInterfacesOn:
    def test_non_dict_meta_returns_empty(self) -> None:
        sym = Symbol(
            id="java:p:1-2:C:class", name="C", kind="class", language="java",
            path="p", span=Span(1, 2, 0, 0), meta=None,
        )
        assert _callback_interfaces_on(sym) == []

    def test_base_classes_match(self) -> None:
        sym = _class_sym("C", base_classes=["ValueMapper"])
        assert _callback_interfaces_on(sym) == ["ValueMapper"]

    def test_interfaces_match(self) -> None:
        sym = _class_sym("C", interfaces=["Predicate"])
        assert _callback_interfaces_on(sym) == ["Predicate"]

    def test_qualified_name_normalized(self) -> None:
        sym = _class_sym(
            "C",
            interfaces=["org.apache.kafka.streams.kstream.ValueMapper<K, V, VR>"],
        )
        assert _callback_interfaces_on(sym) == ["ValueMapper"]

    def test_unknown_interface_skipped(self) -> None:
        sym = _class_sym("C", interfaces=["Cloneable", "Serializable"])
        assert _callback_interfaces_on(sym) == []

    def test_dedup_across_keys(self) -> None:
        """A class listing the same interface under both keys only reports it once."""
        sym = _class_sym(
            "C",
            base_classes=["ValueMapper"],
            interfaces=["ValueMapper"],
        )
        assert _callback_interfaces_on(sym) == ["ValueMapper"]

    def test_non_list_entry_skipped(self) -> None:
        sym = Symbol(
            id="java:p:1-2:C:class", name="C", kind="class", language="java",
            path="p", span=Span(1, 2, 0, 0),
            meta={"base_classes": "not-a-list", "interfaces": ["Predicate"]},
        )
        assert _callback_interfaces_on(sym) == ["Predicate"]

    def test_non_string_entry_skipped(self) -> None:
        sym = Symbol(
            id="java:p:1-2:C:class", name="C", kind="class", language="java",
            path="p", span=Span(1, 2, 0, 0),
            meta={"interfaces": [42, None, "ValueMapper"]},
        )
        assert _callback_interfaces_on(sym) == ["ValueMapper"]

    def test_missing_keys(self) -> None:
        sym = _class_sym("C")
        assert _callback_interfaces_on(sym) == []

    def test_multiple_interfaces(self) -> None:
        sym = _class_sym("C", interfaces=["Transformer", "ProcessorSupplier"])
        # Order matches declaration order.
        assert _callback_interfaces_on(sym) == [
            "Transformer", "ProcessorSupplier",
        ]


# ---------------------------------------------------------------------------
# Unit tests: _expected_method_names
# ---------------------------------------------------------------------------


class TestExpectedMethodNames:
    def test_single_interface(self) -> None:
        assert _expected_method_names(["ValueMapper"]) == frozenset({"apply"})

    def test_transformer_lifecycle(self) -> None:
        assert _expected_method_names(["Transformer"]) == frozenset(
            {"transform", "init", "close"},
        )

    def test_multi_interface_union(self) -> None:
        """Combining Transformer and Predicate unions the method sets."""
        assert _expected_method_names(["Transformer", "Predicate"]) == frozenset(
            {"transform", "init", "close", "test"},
        )

    def test_empty_list(self) -> None:
        assert _expected_method_names([]) == frozenset()


# ---------------------------------------------------------------------------
# Unit tests: _build_class_method_index
# ---------------------------------------------------------------------------


class TestBuildClassMethodIndex:
    def test_groups_by_class(self) -> None:
        methods = [
            _method_sym("Foo.apply"),
            _method_sym("Foo.helper"),
            _method_sym("Bar.apply"),
        ]
        index = _build_class_method_index(methods)
        assert set(index.keys()) == {
            ("src/main/java/com/example/UppercaseMapper.java", "Foo"),
            ("src/main/java/com/example/UppercaseMapper.java", "Bar"),
        }
        assert len(index[(
            "src/main/java/com/example/UppercaseMapper.java", "Foo",
        )]) == 2

    def test_skips_non_method(self) -> None:
        syms = [
            _class_sym("Foo"),
            _method_sym("Foo.apply"),
        ]
        index = _build_class_method_index(syms)
        assert list(index) == [(
            "src/main/java/com/example/UppercaseMapper.java", "Foo",
        )]

    def test_skips_non_jvm_language(self) -> None:
        syms = [_method_sym("Foo.apply", language="python")]
        assert _build_class_method_index(syms) == {}

    def test_skips_top_level_function(self) -> None:
        """Methods without a dotted name aren't class-owned."""
        syms = [_method_sym("topLevelFn")]
        assert _build_class_method_index(syms) == {}


# ---------------------------------------------------------------------------
# Integration tests: link_kafka_streams_dispatch
# ---------------------------------------------------------------------------


class TestLinkKafkaStreamsDispatch:
    def test_value_mapper_impl_emits_apply_edge(self) -> None:
        cls = _class_sym("UppercaseMapper", interfaces=["ValueMapper"])
        apply_method = _method_sym("UppercaseMapper.apply")
        helper = _method_sym("UppercaseMapper.privateHelper", span=(30, 32))
        ctx = _ctx([cls, apply_method, helper])
        result = link_kafka_streams_dispatch(ctx)
        assert len(result.edges) == 1
        e = result.edges[0]
        assert e.src == cls.id
        assert e.dst == apply_method.id
        assert e.edge_type == "dispatches_to"
        assert e.evidence_type == "ast_call_direct"
        assert (e.meta or {}).get("framework_dispatch") == "kafka_streams"
        assert e.confidence == 0.90

    def test_transformer_emits_three_lifecycle_edges(self) -> None:
        cls = _class_sym("UppercaseTransformer", interfaces=["Transformer"])
        methods = [
            _method_sym("UppercaseTransformer.init", span=(10, 12)),
            _method_sym("UppercaseTransformer.transform", span=(15, 25)),
            _method_sym("UppercaseTransformer.close", span=(30, 32)),
            _method_sym("UppercaseTransformer.other", span=(40, 42)),
        ]
        ctx = _ctx([cls, *methods])
        result = link_kafka_streams_dispatch(ctx)
        dst_ids = sorted(e.dst for e in result.edges)
        expected_dst = sorted(m.id for m in methods[:3])  # init, transform, close
        assert dst_ids == expected_dst

    def test_qualified_interface_name_matched(self) -> None:
        cls = _class_sym(
            "UppercaseMapper",
            interfaces=["org.apache.kafka.streams.kstream.ValueMapper<K, V, VR>"],
        )
        apply_method = _method_sym("UppercaseMapper.apply")
        result = link_kafka_streams_dispatch(_ctx([cls, apply_method]))
        assert len(result.edges) == 1

    def test_non_callback_class_ignored(self) -> None:
        cls = _class_sym("Plain", interfaces=["Cloneable"])
        apply_method = _method_sym("Plain.apply")
        assert link_kafka_streams_dispatch(_ctx([cls, apply_method])).edges == []

    def test_non_jvm_class_ignored(self) -> None:
        cls = _class_sym(
            "Plain", interfaces=["ValueMapper"], language="python",
        )
        apply_method = _method_sym("Plain.apply", language="python")
        assert link_kafka_streams_dispatch(_ctx([cls, apply_method])).edges == []

    def test_no_classes(self) -> None:
        """Early-return when the corpus has no Kafka Streams impls."""
        method = _method_sym("Lone.apply")
        assert link_kafka_streams_dispatch(_ctx([method])).edges == []

    def test_kotlin_language_supported(self) -> None:
        cls = _class_sym(
            "KotlinMapper", interfaces=["ValueMapper"], language="kotlin",
        )
        apply_method = _method_sym("KotlinMapper.apply", language="kotlin")
        assert len(link_kafka_streams_dispatch(_ctx([cls, apply_method])).edges) == 1

    def test_scala_language_supported(self) -> None:
        cls = _class_sym(
            "ScalaReducer", interfaces=["Reducer"], language="scala",
        )
        apply_method = _method_sym("ScalaReducer.apply", language="scala")
        assert len(link_kafka_streams_dispatch(_ctx([cls, apply_method])).edges) == 1

    def test_interface_declared_on_interface_kind(self) -> None:
        """A user-declared interface extending ValueMapper still receives edges."""
        cls = _class_sym(
            "MapperIface", interfaces=["ValueMapper"], kind="interface",
        )
        apply_method = _method_sym("MapperIface.apply")
        assert len(link_kafka_streams_dispatch(_ctx([cls, apply_method])).edges) == 1

    def test_existing_edge_not_duplicated(self) -> None:
        cls = _class_sym("UppercaseMapper", interfaces=["ValueMapper"])
        apply_method = _method_sym("UppercaseMapper.apply")
        existing = Edge.create(
            src=cls.id, dst=apply_method.id, edge_type="dispatches_to",
            line=1, confidence=0.90, origin="other", origin_run_id="rx",
        )
        result = link_kafka_streams_dispatch(_ctx([cls, apply_method], [existing]))
        assert result.edges == []

    def test_supplier_get_edge(self) -> None:
        cls = _class_sym(
            "MyProcessorSupplier", interfaces=["ProcessorSupplier"],
        )
        get_method = _method_sym("MyProcessorSupplier.get")
        result = link_kafka_streams_dispatch(_ctx([cls, get_method]))
        assert len(result.edges) == 1
        assert result.edges[0].dst == get_method.id

    def test_multi_interface_union_edges(self) -> None:
        """A class implementing both Transformer and Predicate emits edges for both sets."""
        cls = _class_sym("Hybrid", interfaces=["Transformer", "Predicate"])
        methods = [
            _method_sym("Hybrid.transform", span=(10, 12)),
            _method_sym("Hybrid.init", span=(15, 17)),
            _method_sym("Hybrid.close", span=(20, 22)),
            _method_sym("Hybrid.test", span=(25, 27)),
        ]
        result = link_kafka_streams_dispatch(_ctx([cls, *methods]))
        assert len(result.edges) == 4

    def test_class_without_span_emits_line_zero(self) -> None:
        cls = Symbol(
            id="java:p:0-0:C:class", name="C", kind="class", language="java",
            path="p", span=None,
            meta={"interfaces": ["ValueMapper"]},
        )
        apply_method = _method_sym("C.apply", path="p")
        edges = link_kafka_streams_dispatch(_ctx([cls, apply_method])).edges
        assert len(edges) == 1
        assert edges[0].line == 0


class TestCallbackTable:
    def test_all_interfaces_have_method_sets(self) -> None:
        assert all(
            isinstance(v, frozenset) and v
            for v in KAFKA_STREAMS_CALLBACKS.values()
        )

    def test_supplier_interfaces_dispatch_to_get(self) -> None:
        for key in KAFKA_STREAMS_CALLBACKS:
            if key.endswith("Supplier"):
                assert KAFKA_STREAMS_CALLBACKS[key] == frozenset({"get"})


class TestTransitiveCallbackInterface:
    """WI-vigih: callback-interface match walks transitive ancestors.

    A class implementing a Kotlin / Scala SAM-style wrapper that itself
    extends a Kafka Streams callback interface must be detected the
    same as a direct implementation.
    """

    def _link(
        self, symbols: list[Symbol], edges: list[Edge] | None = None,
    ) -> list[Edge]:
        return link_kafka_streams_dispatch(_ctx(symbols, edges)).edges

    def test_one_intermediate_chain(self) -> None:
        """LeafImpl extends BaseImpl which implements Processor."""
        base = _class_sym(
            "BaseImpl", path="src/main/java/com/example/Base.java",
            span=(1, 30), interfaces=["Processor"],
        )
        leaf = _class_sym(
            "LeafImpl", path="src/main/java/com/example/Leaf.java",
            span=(1, 30), base_classes=["BaseImpl"],
        )
        proc = _method_sym(
            "LeafImpl.process",
            path="src/main/java/com/example/Leaf.java", span=(10, 12),
        )
        edges = [Edge.create(src=leaf.id, dst=base.id, edge_type="extends", line=1)]
        result_edges = self._link([leaf, base, proc], edges=edges)
        assert proc.id in {e.dst for e in result_edges}

    def test_two_intermediate_chain(self) -> None:
        """Leaf -> Mid -> Base implements ValueMapper (two intermediates)."""
        base = _class_sym(
            "BaseMapper", path="src/main/java/com/example/Base.java",
            span=(1, 30), interfaces=["ValueMapper"],
        )
        mid = _class_sym(
            "MidMapper", path="src/main/java/com/example/Mid.java",
            span=(1, 30), base_classes=["BaseMapper"],
        )
        leaf = _class_sym(
            "LeafMapper", path="src/main/java/com/example/Leaf.java",
            span=(1, 30), base_classes=["MidMapper"],
        )
        apply = _method_sym(
            "LeafMapper.apply",
            path="src/main/java/com/example/Leaf.java", span=(10, 12),
        )
        edges = [
            Edge.create(src=leaf.id, dst=mid.id, edge_type="extends", line=1),
            Edge.create(src=mid.id, dst=base.id, edge_type="extends", line=1),
        ]
        result_edges = self._link([leaf, mid, base, apply], edges=edges)
        assert apply.id in {e.dst for e in result_edges}

    def test_diamond_inheritance_cycle_guarded(self) -> None:
        """Diamond inheritance must terminate without double-emit."""
        base = _class_sym(
            "BaseImpl", path="src/main/java/com/example/Base.java",
            span=(1, 10), interfaces=["Predicate"],
        )
        left = _class_sym(
            "LeftImpl", path="src/main/java/com/example/Left.java",
            span=(1, 10), base_classes=["BaseImpl"],
        )
        right = _class_sym(
            "RightImpl", path="src/main/java/com/example/Right.java",
            span=(1, 10), base_classes=["BaseImpl"],
        )
        leaf = _class_sym(
            "LeafImpl", path="src/main/java/com/example/Leaf.java",
            span=(1, 10), base_classes=["LeftImpl", "RightImpl"],
        )
        test_method = _method_sym(
            "LeafImpl.test",
            path="src/main/java/com/example/Leaf.java", span=(5, 6),
        )
        edges = [
            Edge.create(src=leaf.id, dst=left.id, edge_type="extends", line=1),
            Edge.create(src=leaf.id, dst=right.id, edge_type="extends", line=1),
            Edge.create(src=left.id, dst=base.id, edge_type="extends", line=1),
            Edge.create(src=right.id, dst=base.id, edge_type="extends", line=1),
        ]
        result_edges = self._link([leaf, left, right, base, test_method], edges=edges)
        edges_to_test = [e for e in result_edges if e.dst == test_method.id]
        assert len(edges_to_test) == 1

    def test_direct_subclass_regression_unaffected(self) -> None:
        """Direct interface implementation continues to work."""
        leaf = _class_sym(
            "DirectImpl", path="src/main/java/com/example/Direct.java",
            span=(1, 30), interfaces=["Reducer"],
        )
        apply = _method_sym(
            "DirectImpl.apply",
            path="src/main/java/com/example/Direct.java", span=(10, 12),
        )
        result_edges = self._link([leaf, apply])
        assert apply.id in {e.dst for e in result_edges}
