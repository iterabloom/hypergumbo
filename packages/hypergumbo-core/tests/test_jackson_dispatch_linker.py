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

    def test_jpa_entity_annotation(self) -> None:
        """JPA @Entity classes are routinely Jackson-serialized as REST
        response bodies (Spring Data JPA + Spring MVC). WI-sokaz BUG-02:
        spring-petclinic's 6 @Entity JPA classes (Owner, Pet, Visit, Vet,
        Specialty, PetType) dominate the Jackson serialization surface.
        """
        c = _class_sym("Owner", decorators=[{"name": "Entity"}])
        assert _class_has_serialization_hint(c) is True

    def test_jpa_entity_qualified_annotation(self) -> None:
        c = _class_sym(
            "Owner",
            decorators=[{"name": "jakarta.persistence.Entity"}],
        )
        assert _class_has_serialization_hint(c) is True

    def test_jpa_javax_entity_annotation(self) -> None:
        c = _class_sym(
            "Owner",
            decorators=[{"name": "javax.persistence.Entity"}],
        )
        assert _class_has_serialization_hint(c) is True

    def test_jpa_mapped_superclass_annotation(self) -> None:
        """JPA @MappedSuperclass marks an abstract base whose accessors are
        inherited by every @Entity subclass — Jackson reflects over those too.
        """
        c = _class_sym(
            "BaseEntity",
            decorators=[{"name": "MappedSuperclass"}],
        )
        assert _class_has_serialization_hint(c) is True

    def test_jpa_embeddable_annotation(self) -> None:
        """JPA @Embeddable value types (e.g. Address embedded in Owner) are
        flattened into the parent's serialized form; Jackson reflects over
        them just like @Entity classes.
        """
        c = _class_sym(
            "Address",
            decorators=[{"name": "Embeddable"}],
        )
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
        assert _find_bean_target_classes([c], idx) == [(c, False)]

    def test_method_annotation_marks_class(self) -> None:
        c = _class_sym("User")
        m = _method_sym("User.getId", decorators=[{"name": "JsonProperty"}])
        idx = _build_class_method_index([m])
        assert _find_bean_target_classes([c, m], idx) == [(c, False)]

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
            assert e.evidence_type == "ast_decorator"
            assert (e.meta or {}).get("framework_dispatch") == "jackson_bean"
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

    def test_jpa_entity_emits_dispatches_to_for_accessors(self) -> None:
        """JPA @Entity classes (the spring-petclinic Owner/Pet/Vet/... shape)
        produce dispatches_to edges to their bean accessors. WI-sokaz BUG-02.
        """
        owner = _class_sym(
            "Owner",
            path="src/main/java/com/example/Owner.java",
            decorators=[{"name": "Entity"}],
            span=(1, 50),
        )
        get_first_name = _method_sym(
            "Owner.getFirstName",
            path="src/main/java/com/example/Owner.java",
            span=(10, 12),
            signature="()",
        )
        set_first_name = _method_sym(
            "Owner.setFirstName",
            path="src/main/java/com/example/Owner.java",
            span=(14, 16),
            signature="(String firstName)",
        )
        result = link_jackson_dispatch(_ctx([owner, get_first_name, set_first_name]))
        dsts = {e.dst for e in result.edges}
        assert dsts == {get_first_name.id, set_first_name.id}
        for e in result.edges:
            assert e.src == owner.id
            assert e.edge_type == "dispatches_to"

    def test_jpa_mapped_superclass_emits_dispatches_to_for_accessors(self) -> None:
        """JPA @MappedSuperclass abstract bases also receive dispatches_to
        edges — Jackson reflects over inherited accessors on every concrete
        @Entity subclass.
        """
        base = _class_sym(
            "BaseEntity",
            path="src/main/java/com/example/BaseEntity.java",
            decorators=[{"name": "MappedSuperclass"}],
            span=(1, 30),
        )
        get_id = _method_sym(
            "BaseEntity.getId",
            path="src/main/java/com/example/BaseEntity.java",
            span=(10, 12),
            signature="()",
        )
        result = link_jackson_dispatch(_ctx([base, get_id]))
        assert [e.dst for e in result.edges] == [get_id.id]


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


class TestTransitiveBeanMarkerBase:
    """WI-vigih property tests: bean-marker base traversal walks transitive ancestors.

    A class that extends an in-tree intermediate which itself extends
    ``ConfigurationProperties`` (or any other ``BEAN_MARKER_BASE_CLASSES``
    entry) is a serialization target the same way a direct subclass is.
    """

    def _link(
        self, symbols: list[Symbol], edges: list[Edge] | None = None,
    ) -> list[Edge]:
        return link_jackson_dispatch(_ctx(symbols, edges)).edges

    def test_one_intermediate_chain(self) -> None:
        """LeafConfig -> ProjectBaseConfig -> ConfigurationProperties."""
        base = _class_sym(
            "ProjectBaseConfig",
            path="src/main/java/com/example/Base.java", span=(1, 30),
            base_classes=["ConfigurationProperties"],
        )
        leaf = _class_sym(
            "LeafConfig", path="src/main/java/com/example/Leaf.java",
            span=(1, 40), base_classes=["ProjectBaseConfig"],
        )
        get_url = _method_sym(
            "LeafConfig.getUrl",
            path="src/main/java/com/example/Leaf.java",
            span=(10, 12), signature="()",
        )
        edges = [Edge.create(src=leaf.id, dst=base.id, edge_type="extends", line=1)]
        result_edges = self._link([leaf, base, get_url], edges=edges)
        assert get_url.id in {e.dst for e in result_edges}

    def test_two_intermediate_chain(self) -> None:
        """Leaf -> Mid -> Base -> ConfigurationProperties."""
        base = _class_sym(
            "ProjectBase", path="src/main/java/com/example/Base.java",
            span=(1, 30), base_classes=["ConfigurationProperties"],
        )
        mid = _class_sym(
            "ProjectMid", path="src/main/java/com/example/Mid.java",
            span=(1, 30), base_classes=["ProjectBase"],
        )
        leaf = _class_sym(
            "LeafConfig", path="src/main/java/com/example/Leaf.java",
            span=(1, 30), base_classes=["ProjectMid"],
        )
        get_x = _method_sym(
            "LeafConfig.getX",
            path="src/main/java/com/example/Leaf.java",
            span=(10, 12), signature="()",
        )
        edges = [
            Edge.create(src=leaf.id, dst=mid.id, edge_type="extends", line=1),
            Edge.create(src=mid.id, dst=base.id, edge_type="extends", line=1),
        ]
        result_edges = self._link([leaf, mid, base, get_x], edges=edges)
        assert get_x.id in {e.dst for e in result_edges}

    def test_diamond_inheritance_cycle_guarded(self) -> None:
        """Diamond inheritance must not double-count or loop."""
        base = _class_sym(
            "BaseConfig", path="src/main/java/com/example/Base.java",
            span=(1, 10), base_classes=["ConfigurationProperties"],
        )
        left = _class_sym(
            "LeftConfig", path="src/main/java/com/example/Left.java",
            span=(1, 10), base_classes=["BaseConfig"],
        )
        right = _class_sym(
            "RightConfig", path="src/main/java/com/example/Right.java",
            span=(1, 10), base_classes=["BaseConfig"],
        )
        leaf = _class_sym(
            "LeafConfig", path="src/main/java/com/example/Leaf.java",
            span=(1, 10), base_classes=["LeftConfig", "RightConfig"],
        )
        get_x = _method_sym(
            "LeafConfig.getX",
            path="src/main/java/com/example/Leaf.java",
            span=(5, 6), signature="()",
        )
        edges = [
            Edge.create(src=leaf.id, dst=left.id, edge_type="extends", line=1),
            Edge.create(src=leaf.id, dst=right.id, edge_type="extends", line=1),
            Edge.create(src=left.id, dst=base.id, edge_type="extends", line=1),
            Edge.create(src=right.id, dst=base.id, edge_type="extends", line=1),
        ]
        result_edges = self._link([leaf, left, right, base, get_x], edges=edges)
        # Exactly one edge to LeafConfig.getX (no double-emit through diamond).
        edges_to_get_x = [e for e in result_edges if e.dst == get_x.id]
        assert len(edges_to_get_x) == 1

    def test_direct_subclass_regression_unaffected(self) -> None:
        """Direct subclass of bean-marker base still produces dispatches."""
        leaf = _class_sym(
            "DirectConfig", path="src/main/java/com/example/Direct.java",
            span=(1, 30), base_classes=["ConfigurationProperties"],
        )
        get_url = _method_sym(
            "DirectConfig.getUrl",
            path="src/main/java/com/example/Direct.java",
            span=(10, 12), signature="()",
        )
        result_edges = self._link([leaf, get_url])
        assert get_url.id in {e.dst for e in result_edges}


# ---------------------------------------------------------------------------
# INV-zuhub property tests: jackson bean-marker fallback (WI-bojok PR4)
# ---------------------------------------------------------------------------


class TestInvZuhubJacksonFallback:
    """INV-zuhub item 1, dispatches_to family — jackson bean-marker shape.

    The class-level / method-level Jackson annotation paths are
    precision: the Jackson / JPA / Spring annotation namespaces are the
    canonical disambiguators. The bean-marker base path
    (``ConfigurationProperties`` short-name match) can collide with an
    in-tree class of the same name. Per INV-zuhub, dispatch edges in
    that case downgrade to ``confidence <= 0.5`` with the fallback flag.
    """

    def test_dispatches_to_class_level_annotation_keeps_high_confidence(
        self,
    ) -> None:
        """Annotation-bearing class is precision regardless of an in-tree
        bean-marker collision.
        """
        in_tree = _class_sym(
            "ConfigurationProperties",
            path="src/main/java/com/example/local/ConfigurationProperties.java",
            span=(1, 80),
        )
        user_cls = _class_sym(
            "User",
            path="src/main/java/com/example/User.java",
            decorators=[{"name": "JsonSerialize"}],
            span=(1, 50),
        )
        getter = _method_sym(
            "User.getId",
            path="src/main/java/com/example/User.java",
            span=(10, 12), signature="()",
        )
        ctx = _ctx([in_tree, user_cls, getter])
        result = link_jackson_dispatch(ctx)
        edges = [e for e in result.edges if e.src == user_cls.id]
        assert len(edges) == 1
        edge = edges[0]
        assert edge.confidence == 0.90
        assert (edge.meta or {}).get("disambiguation_fallback") is not True

    def test_dispatches_to_fqn_bean_marker_keeps_high_confidence(self) -> None:
        """``org.springframework.boot.context.properties.ConfigurationProperties``
        is the FQN, so the in-tree collision is irrelevant.
        """
        in_tree = _class_sym(
            "ConfigurationProperties",
            path="src/main/java/com/example/local/ConfigurationProperties.java",
            span=(1, 80),
        )
        user_cls = _class_sym(
            "AppConfig",
            path="src/main/java/com/example/AppConfig.java",
            base_classes=[
                "org.springframework.boot.context.properties.ConfigurationProperties",
            ],
            span=(1, 50),
        )
        getter = _method_sym(
            "AppConfig.getUrl",
            path="src/main/java/com/example/AppConfig.java",
            span=(10, 12), signature="()",
        )
        ctx = _ctx([in_tree, user_cls, getter])
        result = link_jackson_dispatch(ctx)
        edges = [e for e in result.edges if e.src == user_cls.id]
        assert len(edges) == 1
        edge = edges[0]
        assert edge.confidence == 0.90
        assert (edge.meta or {}).get("disambiguation_fallback") is not True

    def test_dispatches_to_no_in_tree_collision_keeps_high_confidence(
        self,
    ) -> None:
        # No in-tree ``ConfigurationProperties`` exists, so an unqualified
        # extension is precision by elimination.
        user_cls = _class_sym(
            "AppConfig",
            path="src/main/java/com/example/AppConfig.java",
            base_classes=["ConfigurationProperties"],
            span=(1, 50),
        )
        getter = _method_sym(
            "AppConfig.getUrl",
            path="src/main/java/com/example/AppConfig.java",
            span=(10, 12), signature="()",
        )
        ctx = _ctx([user_cls, getter])
        result = link_jackson_dispatch(ctx)
        edges = [e for e in result.edges if e.src == user_cls.id]
        assert len(edges) == 1
        assert edges[0].confidence == 0.90
        assert (edges[0].meta or {}).get("disambiguation_fallback") is not True

    def test_dispatches_to_deterministic_fallback_when_ambiguous(self) -> None:
        # In-tree class named ``ConfigurationProperties`` collides with
        # Spring's. A user class extending unqualified ``ConfigurationProperties``
        # is ambiguous, so the edge downgrades.
        in_tree = _class_sym(
            "ConfigurationProperties",
            path="src/main/java/com/example/local/ConfigurationProperties.java",
            span=(1, 80),
        )
        user_cls = _class_sym(
            "AppConfig",
            path="src/main/java/com/example/AppConfig.java",
            base_classes=["ConfigurationProperties"],
            span=(1, 50),
        )
        getter = _method_sym(
            "AppConfig.getUrl",
            path="src/main/java/com/example/AppConfig.java",
            span=(10, 12), signature="()",
        )
        ctx = _ctx([in_tree, user_cls, getter])
        result = link_jackson_dispatch(ctx)
        edges = [e for e in result.edges if e.src == user_cls.id]
        assert len(edges) == 1
        edge = edges[0]
        assert edge.confidence <= 0.5
        assert edge.meta is not None
        assert edge.meta.get("disambiguation_fallback") is True
        assert edge.meta.get("framework_dispatch") == "jackson_bean"
