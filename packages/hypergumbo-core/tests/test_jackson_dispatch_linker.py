# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Jackson / JavaBean serialization dispatch linker (WI-gupah).

Validates that the linker creates ``dispatches_to`` edges from serialization-
target classes to the bean-convention accessors (and method-level Jackson-
annotated methods) so the accessors are not misclassified as dead code by
the dead-code analyzer.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.jackson_dispatch import (
    BEAN_MARKER_BASE_CLASSES,
    CLASS_LEVEL_SERIALIZATION_ANNOTATIONS,
    METHOD_LEVEL_SERIALIZATION_ANNOTATIONS,
    _build_class_method_index,
    _class_has_serialization_hint,
    _decorator_names,
    _find_bean_target_classes,
    _get_signature,
    _is_bean_accessor_name,
    _method_is_serialization_annotated,
    _select_dispatch_targets,
    _short_annotation_name,
    _signature_arity,
    link_jackson_dispatch,
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
    path: str = "src/main/java/com/example/User.java",
    decorators: list[dict[str, object]] | None = None,
    base_classes: list[str] | None = None,
    span: tuple[int, int] = (1, 20),
    kind: str = "class",
    language: str = "java",
) -> Symbol:
    meta: dict[str, object] = {}
    if decorators is not None:
        meta["decorators"] = decorators
    if base_classes is not None:
        meta["base_classes"] = base_classes
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
    path: str = "src/main/java/com/example/User.java",
    span: tuple[int, int] = (5, 10),
    decorators: list[dict[str, object]] | None = None,
    signature: str | None = None,
    language: str = "java",
) -> Symbol:
    meta: dict[str, object] = {}
    if decorators is not None:
        meta["decorators"] = decorators
    return Symbol(
        id=f"{language}:{path}:{span[0]}-{span[1]}:{qualified_name}:method",
        name=qualified_name,
        kind="method",
        language=language,
        path=path,
        span=Span(span[0], span[1], 0, 0),
        signature=signature,
        meta=meta or None,
    )


class TestShortAnnotationName:
    def test_bare_name_unchanged(self) -> None:
        assert _short_annotation_name("JsonProperty") == "JsonProperty"

    def test_qualified_collapsed(self) -> None:
        assert _short_annotation_name(
            "com.fasterxml.jackson.annotation.JsonProperty",
        ) == "JsonProperty"

    def test_generic_parameters_stripped(self) -> None:
        assert _short_annotation_name("JsonSerialize<Foo>") == "JsonSerialize"


class TestDecoratorNames:
    def test_empty_meta(self) -> None:
        assert _decorator_names(None) == set()

    def test_non_dict_meta(self) -> None:
        assert _decorator_names("oops") == set()

    def test_decorators_key(self) -> None:
        meta = {"decorators": [{"name": "JsonProperty"}, {"name": "Deprecated"}]}
        assert _decorator_names(meta) == {"JsonProperty", "Deprecated"}

    def test_annotations_key_fallback(self) -> None:
        """Some analyzers use 'annotations' instead of 'decorators'."""
        meta = {"annotations": [{"name": "JsonGetter"}]}
        assert _decorator_names(meta) == {"JsonGetter"}

    def test_ignores_malformed_entries(self) -> None:
        meta = {"decorators": [{"name": "JsonProperty"}, "bogus", {}, {"name": ""}]}
        assert _decorator_names(meta) == {"JsonProperty"}

    def test_ignores_non_list_decorators(self) -> None:
        assert _decorator_names({"decorators": "not a list"}) == set()

    def test_short_name_applied(self) -> None:
        meta = {"decorators": [{"name": "com.fasterxml.jackson.annotation.JsonProperty"}]}
        assert _decorator_names(meta) == {"JsonProperty"}


class TestClassHasSerializationHint:
    def test_class_level_jackson_annotation(self) -> None:
        c = _class_sym("User", decorators=[{"name": "JsonSerialize"}])
        assert _class_has_serialization_hint(c) is True

    def test_qualified_jackson_annotation(self) -> None:
        c = _class_sym(
            "User",
            decorators=[{"name": "com.fasterxml.jackson.annotation.JsonRootName"}],
        )
        assert _class_has_serialization_hint(c) is True

    def test_xml_annotation(self) -> None:
        c = _class_sym("User", decorators=[{"name": "XmlRootElement"}])
        assert _class_has_serialization_hint(c) is True

    def test_spring_configuration_properties(self) -> None:
        c = _class_sym("User", decorators=[{"name": "ConfigurationProperties"}])
        assert _class_has_serialization_hint(c) is True

    def test_unrelated_annotation_rejected(self) -> None:
        c = _class_sym("User", decorators=[{"name": "Deprecated"}])
        assert _class_has_serialization_hint(c) is False

    def test_bean_marker_base_class(self) -> None:
        c = _class_sym(
            "KafkaClient",
            base_classes=["org.springframework.boot.context.properties.ConfigurationProperties"],
        )
        assert _class_has_serialization_hint(c) is True

    def test_no_meta(self) -> None:
        c = _class_sym("User")
        assert _class_has_serialization_hint(c) is False

    def test_non_list_base_classes(self) -> None:
        c = Symbol(
            id="java:a.java:1-5:User:class",
            name="User",
            kind="class",
            language="java",
            path="a.java",
            span=Span(1, 5, 0, 0),
            meta={"base_classes": "oops"},
        )
        assert _class_has_serialization_hint(c) is False

    def test_empty_base_class_entries_ignored(self) -> None:
        c = _class_sym("User", base_classes=[None, 123, "Foo"])  # type: ignore[list-item]
        assert _class_has_serialization_hint(c) is False


class TestMethodIsSerializationAnnotated:
    def test_jackson_method_annotation(self) -> None:
        m = _method_sym("User.getId", decorators=[{"name": "JsonGetter"}])
        assert _method_is_serialization_annotated(m) is True

    def test_class_level_annotation_not_method_level(self) -> None:
        m = _method_sym("User.getId", decorators=[{"name": "JsonSerialize"}])
        # JsonSerialize is class-level, not method-level
        assert _method_is_serialization_annotated(m) is False

    def test_no_decorators(self) -> None:
        m = _method_sym("User.getId")
        assert _method_is_serialization_annotated(m) is False


class TestIsBeanAccessorName:
    def test_getter_no_signature(self) -> None:
        assert _is_bean_accessor_name("getUser", None) is True

    def test_is_getter_no_signature(self) -> None:
        assert _is_bean_accessor_name("isActive", None) is True

    def test_setter_no_signature(self) -> None:
        assert _is_bean_accessor_name("setUser", None) is True

    def test_bare_get_rejected(self) -> None:
        assert _is_bean_accessor_name("get", None) is False

    def test_getter_lowercase_next_rejected(self) -> None:
        assert _is_bean_accessor_name("getter", None) is False

    def test_is_rejected_without_property(self) -> None:
        assert _is_bean_accessor_name("is", None) is False

    def test_getter_with_args_rejected(self) -> None:
        assert _is_bean_accessor_name("getUser", "(String id)") is False

    def test_setter_with_zero_args_rejected(self) -> None:
        assert _is_bean_accessor_name("setUser", "()") is False

    def test_getter_with_zero_args_accepted(self) -> None:
        assert _is_bean_accessor_name("getUser", "()") is True

    def test_setter_with_one_arg_accepted(self) -> None:
        assert _is_bean_accessor_name("setUser", "(User u)") is True

    def test_unrelated_method_rejected(self) -> None:
        assert _is_bean_accessor_name("compute", "(int x)") is False

    def test_is_getter_with_signature(self) -> None:
        assert _is_bean_accessor_name("isActive", "()") is True


class TestSignatureArity:
    def test_zero_args(self) -> None:
        assert _signature_arity("()", default=99) == 0

    def test_one_arg(self) -> None:
        assert _signature_arity("(String s)", default=99) == 1

    def test_multiple_args(self) -> None:
        assert _signature_arity("(int a, String b, Foo c)", default=99) == 3

    def test_nested_parens_preserve_count(self) -> None:
        assert _signature_arity("(Map<String,Integer> m, List<Foo> l)", default=99) == 2

    def test_missing_paren_returns_default(self) -> None:
        assert _signature_arity("no-paren", default=7) == 7

    def test_empty_signature_returns_default(self) -> None:
        assert _signature_arity(None, default=3) == 3

    def test_unclosed_paren_returns_default(self) -> None:
        assert _signature_arity("(String s", default=4) == 4


class TestGetSignature:
    def test_symbol_signature_preferred(self) -> None:
        m = _method_sym("User.getId", signature="()")
        assert _get_signature(m) == "()"

    def test_meta_signature_fallback(self) -> None:
        m = Symbol(
            id="java:a.java:1-5:User.getId:method",
            name="User.getId",
            kind="method",
            language="java",
            path="a.java",
            span=Span(1, 5, 0, 0),
            meta={"signature": "(String s)"},
        )
        assert _get_signature(m) == "(String s)"

    def test_no_signature(self) -> None:
        m = _method_sym("User.getId")
        assert _get_signature(m) is None


class TestBuildClassMethodIndex:
    def test_groups_by_path_and_class(self) -> None:
        methods = [
            _method_sym("User.getId", path="a.java"),
            _method_sym("User.setId", path="a.java"),
            _method_sym("Order.total", path="b.java"),
        ]
        idx = _build_class_method_index(methods)
        assert {m.name for m in idx[("a.java", "User")]} == {"User.getId", "User.setId"}
        assert [m.name for m in idx[("b.java", "Order")]] == ["Order.total"]

    def test_skips_non_methods_and_non_jvm(self) -> None:
        methods = [
            _method_sym("User.getId"),
            _class_sym("User"),
            _method_sym("Py.get_id", language="python"),
            _method_sym("no_dot_method"),
        ]
        idx = _build_class_method_index(methods)
        assert list(idx.keys()) == [("src/main/java/com/example/User.java", "User")]

    def test_kotlin_and_scala_included(self) -> None:
        methods = [
            _method_sym("User.getId", language="kotlin", path="a.kt"),
            _method_sym("User.getId", language="scala", path="a.scala"),
        ]
        idx = _build_class_method_index(methods)
        assert ("a.kt", "User") in idx
        assert ("a.scala", "User") in idx


class TestFindBeanTargetClasses:
    def test_class_level_annotation_marks_target(self) -> None:
        c = _class_sym("User", decorators=[{"name": "JsonSerialize"}])
        idx = _build_class_method_index([])
        assert _find_bean_target_classes([c], idx) == [c]

    def test_method_annotation_marks_class(self) -> None:
        c = _class_sym("User")
        m = _method_sym("User.getId", decorators=[{"name": "JsonProperty"}])
        idx = _build_class_method_index([m])
        assert _find_bean_target_classes([c, m], idx) == [c]

    def test_unmarked_class_skipped(self) -> None:
        c = _class_sym("User")
        m = _method_sym("User.getId")
        idx = _build_class_method_index([m])
        assert _find_bean_target_classes([c, m], idx) == []

    def test_non_jvm_class_skipped(self) -> None:
        c = _class_sym(
            "User", language="python", path="user.py",
            decorators=[{"name": "JsonSerialize"}],
        )
        idx = _build_class_method_index([])
        assert _find_bean_target_classes([c], idx) == []

    def test_non_class_kind_skipped(self) -> None:
        c = _class_sym(
            "User", kind="function",
            decorators=[{"name": "JsonSerialize"}],
        )
        idx = _build_class_method_index([])
        assert _find_bean_target_classes([c], idx) == []


class TestSelectDispatchTargets:
    def test_bean_accessor_selected(self) -> None:
        m = _method_sym("User.getId", signature="()")
        assert _select_dispatch_targets([m]) == [m]

    def test_annotated_non_accessor_selected(self) -> None:
        m = _method_sym(
            "User.userId", decorators=[{"name": "JsonProperty"}], signature="()",
        )
        assert _select_dispatch_targets([m]) == [m]

    def test_plain_method_rejected(self) -> None:
        m = _method_sym("User.compute", signature="(int x)")
        assert _select_dispatch_targets([m]) == []


class TestLinkJacksonDispatch:
    def test_emits_dispatches_to_for_annotated_class(self) -> None:
        c = _class_sym("User", decorators=[{"name": "JsonSerialize"}])
        get_id = _method_sym("User.getId", span=(5, 7), signature="()")
        set_id = _method_sym("User.setId", span=(9, 11), signature="(int id)")
        unrelated = _method_sym("User.compute", span=(13, 15), signature="(int x)")
        result = link_jackson_dispatch(_ctx([c, get_id, set_id, unrelated]))
        dsts = {e.dst for e in result.edges}
        assert dsts == {get_id.id, set_id.id}
        for e in result.edges:
            assert e.src == c.id
            assert e.edge_type == "dispatches_to"
            assert e.evidence_type == "jackson_bean_dispatch"
            assert e.confidence == 0.90

    def test_method_annotation_triggers_class_dispatch(self) -> None:
        c = _class_sym("Order")
        annotated = _method_sym(
            "Order.orderId", decorators=[{"name": "JsonProperty"}], signature="()",
        )
        result = link_jackson_dispatch(_ctx([c, annotated]))
        assert [e.dst for e in result.edges] == [annotated.id]
        assert result.edges[0].src == c.id

    def test_no_edges_when_no_targets(self) -> None:
        c = _class_sym("Plain")
        m = _method_sym("Plain.getId", signature="()")
        assert link_jackson_dispatch(_ctx([c, m])).edges == []

    def test_dedupes_against_existing_dispatches_to_edges(self) -> None:
        c = _class_sym("User", decorators=[{"name": "JsonSerialize"}])
        get_id = _method_sym("User.getId", signature="()")
        existing = Edge.create(
            src=c.id,
            dst=get_id.id,
            edge_type="dispatches_to",
            line=1,
            origin="prior-linker",
            origin_run_id="r1",
        )
        result = link_jackson_dispatch(_ctx([c, get_id], edges=[existing]))
        assert result.edges == []

    def test_dedupes_within_run(self) -> None:
        """Same target appearing via multiple paths emits a single edge."""
        c = _class_sym("User", decorators=[{"name": "JsonSerialize"}])
        get_id = _method_sym(
            "User.getId", decorators=[{"name": "JsonProperty"}], signature="()",
        )
        # The method matches BOTH the bean-convention name AND the annotation
        # selector, but only one edge should be produced.
        result = link_jackson_dispatch(_ctx([c, get_id]))
        assert len(result.edges) == 1

    def test_different_classes_in_same_file_isolated(self) -> None:
        user = _class_sym(
            "User", path="a.java", span=(1, 10),
            decorators=[{"name": "JsonSerialize"}],
        )
        order = _class_sym("Order", path="a.java", span=(11, 20))
        user_get = _method_sym("User.getName", path="a.java", span=(5, 6), signature="()")
        order_get = _method_sym("Order.getItems", path="a.java", span=(15, 16), signature="()")
        result = link_jackson_dispatch(_ctx([user, order, user_get, order_get]))
        # Only User is a target; Order is unmarked.
        assert {e.src for e in result.edges} == {user.id}
        assert {e.dst for e in result.edges} == {user_get.id}

    def test_kotlin_class_covered(self) -> None:
        c = _class_sym(
            "User", path="a.kt", language="kotlin",
            decorators=[{"name": "JsonSerialize"}],
        )
        get_id = _method_sym(
            "User.getId", path="a.kt", language="kotlin", signature="()",
        )
        result = link_jackson_dispatch(_ctx([c, get_id]))
        assert [e.dst for e in result.edges] == [get_id.id]

    def test_scala_class_covered(self) -> None:
        c = _class_sym(
            "User", path="a.scala", language="scala",
            decorators=[{"name": "XmlRootElement"}],
        )
        get_id = _method_sym(
            "User.getId", path="a.scala", language="scala", signature="()",
        )
        result = link_jackson_dispatch(_ctx([c, get_id]))
        assert [e.dst for e in result.edges] == [get_id.id]

    def test_run_duration_populated(self) -> None:
        c = _class_sym("User", decorators=[{"name": "JsonSerialize"}])
        m = _method_sym("User.getId", signature="()")
        result = link_jackson_dispatch(_ctx([c, m]))
        assert result.run.duration_ms >= 0

    def test_empty_input(self) -> None:
        result = link_jackson_dispatch(_ctx([]))
        assert result.edges == []
        assert result.symbols == []


class TestConstants:
    def test_class_level_set_non_empty(self) -> None:
        assert "JsonSerialize" in CLASS_LEVEL_SERIALIZATION_ANNOTATIONS
        assert "XmlRootElement" in CLASS_LEVEL_SERIALIZATION_ANNOTATIONS
        assert "ConfigurationProperties" in CLASS_LEVEL_SERIALIZATION_ANNOTATIONS

    def test_method_level_set_non_empty(self) -> None:
        assert "JsonProperty" in METHOD_LEVEL_SERIALIZATION_ANNOTATIONS
        assert "JsonGetter" in METHOD_LEVEL_SERIALIZATION_ANNOTATIONS

    def test_bean_marker_base_classes_non_empty(self) -> None:
        assert "ConfigurationProperties" in BEAN_MARKER_BASE_CLASSES

    def test_class_and_method_sets_disjoint(self) -> None:
        """Class-level and method-level annotations are distinct markers."""
        assert (
            CLASS_LEVEL_SERIALIZATION_ANNOTATIONS
            & METHOD_LEVEL_SERIALIZATION_ANNOTATIONS
        ) == set()
