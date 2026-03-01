"""Tests for containment linker.

The containment linker creates `contains` edges from class/interface symbols
to their method symbols, based on naming conventions (ClassName.method,
ClassName#method, ClassName::method).
"""

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.containment import _find_parent, link_containment
from hypergumbo_core.linkers.registry import LinkerContext


def _sym(
    id: str,
    name: str,
    kind: str,
    language: str = "python",
    path: str = "app.py",
    start: int = 1,
    end: int = 5,
    canonical_name: str | None = None,
) -> Symbol:
    """Helper to create a Symbol with minimal boilerplate."""
    return Symbol(
        id=id,
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=Span(start_line=start, end_line=end, start_col=0, end_col=0),
        origin="test",
        origin_run_id="test-run",
        meta=None,
        canonical_name=canonical_name,
    )


class TestContainmentLinker:
    """Tests for the containment linker."""

    def test_dot_separated_method(self) -> None:
        """Creates contains edge for Python-style ClassName.method."""
        cls = _sym("py:app.py:1-10:User:class", "User", "class")
        method = _sym("py:app.py:3-5:User.save:method", "User.save", "method", start=3, end=5)

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[cls, method],
            edges=[],
        )
        result = link_containment(ctx)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == cls.id
        assert edge.dst == method.id
        assert edge.edge_type == "contains"

    def test_ruby_hash_separated_method(self) -> None:
        """Creates contains edge for Ruby-style ClassName#method."""
        cls = _sym("ruby:app.rb:1-10:User:class", "User", "class", language="ruby", path="app.rb")
        method = _sym(
            "ruby:app.rb:3-5:User#save:method",
            "User#save",
            "method",
            language="ruby",
            path="app.rb",
            start=3,
            end=5,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[cls, method],
            edges=[],
        )
        result = link_containment(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].src == cls.id
        assert result.edges[0].dst == method.id
        assert result.edges[0].edge_type == "contains"

    def test_rust_double_colon_separated_method(self) -> None:
        """Creates contains edge for Rust-style ImplTarget::method."""
        cls = _sym("rust:lib.rs:1-10:User:class", "User", "class", language="rust", path="lib.rs")
        method = _sym(
            "rust:lib.rs:3-5:User::new:method",
            "User::new",
            "method",
            language="rust",
            path="lib.rs",
            start=3,
            end=5,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[cls, method],
            edges=[],
        )
        result = link_containment(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].src == cls.id
        assert result.edges[0].dst == method.id

    def test_multiple_methods_in_class(self) -> None:
        """Creates contains edges for all methods in a class."""
        cls = _sym("py:app.py:1-20:User:class", "User", "class")
        m1 = _sym("py:app.py:3-5:User.save:method", "User.save", "method", start=3, end=5)
        m2 = _sym("py:app.py:7-9:User.delete:method", "User.delete", "method", start=7, end=9)
        m3 = _sym("py:app.py:11-13:User.__init__:method", "User.__init__", "method", start=11, end=13)

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[cls, m1, m2, m3],
            edges=[],
        )
        result = link_containment(ctx)

        assert len(result.edges) == 3
        dst_ids = {e.dst for e in result.edges}
        assert dst_ids == {m1.id, m2.id, m3.id}
        assert all(e.src == cls.id for e in result.edges)

    def test_no_edge_for_standalone_function(self) -> None:
        """Does not create contains edge for standalone functions (no class prefix)."""
        cls = _sym("py:app.py:1-10:User:class", "User", "class")
        func = _sym("py:app.py:12-15:main:function", "main", "function", start=12, end=15)

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[cls, func],
            edges=[],
        )
        result = link_containment(ctx)

        assert len(result.edges) == 0

    def test_no_edge_for_unqualified_method(self) -> None:
        """Does not create contains edge for method without class prefix in name."""
        # A method with no separator in name (edge case from malformed analysis)
        method = _sym("py:app.py:3-5:save:method", "save", "method", start=3, end=5)

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[method],
            edges=[],
        )
        result = link_containment(ctx)

        assert len(result.edges) == 0

    def test_no_edge_when_class_not_found(self) -> None:
        """Does not create contains edge if parent class symbol doesn't exist."""
        # Method references a class "User" but no User class symbol exists
        method = _sym("py:app.py:3-5:User.save:method", "User.save", "method", start=3, end=5)

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[method],
            edges=[],
        )
        result = link_containment(ctx)

        assert len(result.edges) == 0

    def test_interface_contains_method(self) -> None:
        """Creates contains edge from interface to its methods."""
        iface = _sym(
            "java:IService.java:1-10:IService:interface",
            "IService",
            "interface",
            language="java",
            path="IService.java",
        )
        method = _sym(
            "java:IService.java:3-5:IService.findUser:method",
            "IService.findUser",
            "method",
            language="java",
            path="IService.java",
            start=3,
            end=5,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[iface, method],
            edges=[],
        )
        result = link_containment(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].src == iface.id
        assert result.edges[0].dst == method.id

    def test_nested_class_method(self) -> None:
        """Handles nested class: OuterClass.InnerClass.method -> InnerClass contains method."""
        outer = _sym("py:app.py:1-30:Outer:class", "Outer", "class")
        inner = _sym("py:app.py:5-25:Outer.Inner:class", "Outer.Inner", "class", start=5, end=25)
        method = _sym(
            "py:app.py:7-9:Outer.Inner.do_thing:method",
            "Outer.Inner.do_thing",
            "method",
            start=7,
            end=9,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[outer, inner, method],
            edges=[],
        )
        result = link_containment(ctx)

        # Inner should be contained in Outer, method in Inner
        edges_by_dst = {e.dst: e for e in result.edges}
        assert method.id in edges_by_dst
        assert edges_by_dst[method.id].src == inner.id
        assert inner.id in edges_by_dst
        assert edges_by_dst[inner.id].src == outer.id

    def test_deduplicates_existing_contains_edges(self) -> None:
        """Does not duplicate contains edges that already exist."""
        cls = _sym("py:app.py:1-10:User:class", "User", "class")
        method = _sym("py:app.py:3-5:User.save:method", "User.save", "method", start=3, end=5)

        # Pre-existing contains edge
        existing = Edge.create(
            src=cls.id,
            dst=method.id,
            edge_type="contains",
            line=3,
            confidence=0.95,
            origin="test",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[cls, method],
            edges=[existing],
        )
        result = link_containment(ctx)

        assert len(result.edges) == 0

    def test_java_qualified_class_name(self) -> None:
        """Handles Java package-qualified names like com.example.UserService.getUsers."""
        cls = _sym(
            "java:UserService.java:1-20:com.example.UserService:class",
            "com.example.UserService",
            "class",
            language="java",
            path="UserService.java",
        )
        method = _sym(
            "java:UserService.java:5-10:com.example.UserService.getUsers:method",
            "com.example.UserService.getUsers",
            "method",
            language="java",
            path="UserService.java",
            start=5,
            end=10,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[cls, method],
            edges=[],
        )
        result = link_containment(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].src == cls.id
        assert result.edges[0].dst == method.id

    def test_getter_setter_kinds(self) -> None:
        """Creates contains edges for getter/setter kinds (JS/TS)."""
        cls = _sym(
            "ts:app.ts:1-20:UserService:class",
            "UserService",
            "class",
            language="typescript",
            path="app.ts",
        )
        getter = _sym(
            "ts:app.ts:5-7:UserService.name:getter",
            "UserService.name",
            "getter",
            language="typescript",
            path="app.ts",
            start=5,
            end=7,
        )
        setter = _sym(
            "ts:app.ts:9-11:UserService.name:setter",
            "UserService.name",
            "setter",
            language="typescript",
            path="app.ts",
            start=9,
            end=11,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[cls, getter, setter],
            edges=[],
        )
        result = link_containment(ctx)

        assert len(result.edges) == 2
        dst_ids = {e.dst for e in result.edges}
        assert getter.id in dst_ids
        assert setter.id in dst_ids

    def test_empty_symbols(self) -> None:
        """Returns empty result when no symbols provided."""
        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[],
            edges=[],
        )
        result = link_containment(ctx)
        assert len(result.edges) == 0

    def test_linker_result_has_run_metadata(self) -> None:
        """Linker result includes AnalysisRun with correct pass_id."""
        cls = _sym("py:app.py:1-10:User:class", "User", "class")
        method = _sym("py:app.py:3-5:User.save:method", "User.save", "method", start=3, end=5)

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[cls, method],
            edges=[],
        )
        result = link_containment(ctx)

        assert result.run is not None
        assert result.run.pass_id == "containment-linker-v1"

    def test_edge_confidence(self) -> None:
        """Contains edges have high confidence since naming is deterministic."""
        cls = _sym("py:app.py:1-10:User:class", "User", "class")
        method = _sym("py:app.py:3-5:User.save:method", "User.save", "method", start=3, end=5)

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[cls, method],
            edges=[],
        )
        result = link_containment(ctx)

        assert result.edges[0].confidence == 1.0

    def test_struct_contains_method(self) -> None:
        """Struct symbols (Rust, Go, C) should contain their methods."""
        struct = _sym(
            "rust:lib.rs:1-10:Searcher:struct",
            "Searcher",
            "struct",
            language="rust",
            path="lib.rs",
        )
        method = _sym(
            "rust:lib.rs:3-5:Searcher::search:method",
            "Searcher::search",
            "method",
            language="rust",
            path="lib.rs",
            start=3,
            end=5,
        )
        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[struct, method],
            edges=[],
        )
        result = link_containment(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].src == struct.id
        assert result.edges[0].dst == method.id

    def test_trait_contains_method(self) -> None:
        """Trait symbols (Rust) should contain their methods."""
        trait = _sym(
            "rust:lib.rs:1-10:Display:trait",
            "Display",
            "trait",
            language="rust",
            path="lib.rs",
        )
        method = _sym(
            "rust:lib.rs:3-5:Display::fmt:method",
            "Display::fmt",
            "method",
            language="rust",
            path="lib.rs",
            start=3,
            end=5,
        )
        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[trait, method],
            edges=[],
        )
        result = link_containment(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].src == trait.id
        assert result.edges[0].dst == method.id

    def test_enum_contains_method(self) -> None:
        """Enum symbols with methods should contain them (Rust enums)."""
        enum = _sym(
            "rust:lib.rs:1-10:Color:enum",
            "Color",
            "enum",
            language="rust",
            path="lib.rs",
        )
        method = _sym(
            "rust:lib.rs:3-5:Color::is_warm:method",
            "Color::is_warm",
            "method",
            language="rust",
            path="lib.rs",
            start=3,
            end=5,
        )
        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[enum, method],
            edges=[],
        )
        result = link_containment(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].src == enum.id
        assert result.edges[0].dst == method.id


class TestContainmentNameCollision:
    """Tests for name collision handling in the containment linker.

    When multiple classes share the same name (e.g., Django's Model class
    and 237 test Model classes), the linker must prefer the same-file
    match for method containment.
    """

    def test_same_name_classes_prefer_same_file(self) -> None:
        """When multiple classes share a name, methods link to the same-file class."""
        # Real Model class in base.py with methods
        real_model = _sym(
            "py:base.py:500-2500:Model:class",
            "Model",
            "class",
            path="base.py",
            start=500,
            end=2500,
        )
        method1 = _sym(
            "py:base.py:600-610:Model.save:method",
            "Model.save",
            "method",
            path="base.py",
            start=600,
            end=610,
        )
        method2 = _sym(
            "py:base.py:700-710:Model.delete:method",
            "Model.delete",
            "method",
            path="base.py",
            start=700,
            end=710,
        )
        # Test Model class in a different file (no methods of its own)
        test_model = _sym(
            "py:test_models.py:10-20:Model:class",
            "Model",
            "class",
            path="test_models.py",
            start=10,
            end=20,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[real_model, method1, method2, test_model],
            edges=[],
        )
        result = link_containment(ctx)

        # Both methods should be linked to real_model, not test_model
        assert len(result.edges) == 2
        for edge in result.edges:
            assert edge.src == real_model.id, (
                f"Method should be contained by same-file class, "
                f"got src={edge.src}"
            )

    def test_same_name_classes_last_wins_without_same_file(self) -> None:
        """When no same-file class exists, falls back to any matching class."""
        # Two Model classes, neither in the method's file
        model_a = _sym(
            "py:models_a.py:1-10:Model:class",
            "Model",
            "class",
            path="models_a.py",
        )
        model_b = _sym(
            "py:models_b.py:1-10:Model:class",
            "Model",
            "class",
            path="models_b.py",
        )
        # Method in a third file — no same-file match possible
        method = _sym(
            "py:other.py:5-10:Model.save:method",
            "Model.save",
            "method",
            path="other.py",
            start=5,
            end=10,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[model_a, model_b, method],
            edges=[],
        )
        result = link_containment(ctx)

        # Should still create an edge (fallback to some match)
        assert len(result.edges) == 1

    def test_many_duplicate_classes_correct_linkage(self) -> None:
        """Simulates Django: 1 real Model + many test Models, methods link correctly."""
        # Real Model class with methods
        real_model = _sym(
            "py:django/db/models/base.py:501-2512:Model:class",
            "Model",
            "class",
            path="django/db/models/base.py",
            start=501,
            end=2512,
        )
        real_methods = []
        for i, name in enumerate(["_is_pk_set", "save", "delete", "clean"]):
            real_methods.append(_sym(
                f"py:django/db/models/base.py:{600+i*20}-{610+i*20}:Model.{name}:method",
                f"Model.{name}",
                "method",
                path="django/db/models/base.py",
                start=600 + i * 20,
                end=610 + i * 20,
            ))

        # 10 test Model classes in different files (simulating Django's 237)
        test_models = []
        for i in range(10):
            test_models.append(_sym(
                f"py:tests/test_{i}.py:10-20:Model:class",
                "Model",
                "class",
                path=f"tests/test_{i}.py",
                start=10,
                end=20,
            ))

        all_symbols = [real_model] + real_methods + test_models
        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=all_symbols,
            edges=[],
        )
        result = link_containment(ctx)

        # All 4 methods should be contained by real_model
        method_edges = [e for e in result.edges if e.dst in {m.id for m in real_methods}]
        assert len(method_edges) == 4
        for edge in method_edges:
            assert edge.src == real_model.id, (
                f"Expected src={real_model.id}, got src={edge.src}"
            )


class TestModuleContainment:
    """Tests for module → class containment in Ruby.

    Ruby modules serve as namespaces: `module Postal; module MessageDB; class Database`.
    Classes inside modules have qualified names with `::` separator.
    The containment linker should create `contains` edges from modules to their
    classes and from parent modules to child modules.
    """

    def test_module_contains_class(self) -> None:
        """Ruby module should contain a nested class."""
        mod = _sym(
            "ruby:lib/postal.rb:1-100:Postal:module",
            "Postal", "module", language="ruby", path="lib/postal.rb",
        )
        cls = _sym(
            "ruby:lib/postal/http.rb:1-50:Postal::HTTP:class",
            "Postal::HTTP", "class", language="ruby", path="lib/postal/http.rb",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[mod, cls],
            edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 1
        assert contains[0].src == mod.id
        assert contains[0].dst == cls.id

    def test_nested_module_contains_class(self) -> None:
        """Nested module should contain its class."""
        outer = _sym(
            "ruby:lib/postal.rb:1-200:Postal:module",
            "Postal", "module", language="ruby", path="lib/postal.rb",
        )
        inner = _sym(
            "ruby:lib/postal/msg_db.rb:1-100:Postal::MessageDB:module",
            "Postal::MessageDB", "module", language="ruby",
            path="lib/postal/msg_db.rb",
        )
        cls = _sym(
            "ruby:lib/postal/msg_db/db.rb:1-50:Postal::MessageDB::Database:class",
            "Postal::MessageDB::Database", "class", language="ruby",
            path="lib/postal/msg_db/db.rb",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[outer, inner, cls],
            edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        # outer → inner, inner → cls
        assert len(contains) == 2

        outer_to_inner = [e for e in contains if e.dst == inner.id]
        inner_to_cls = [e for e in contains if e.dst == cls.id]
        assert len(outer_to_inner) == 1
        assert outer_to_inner[0].src == outer.id
        assert len(inner_to_cls) == 1
        assert inner_to_cls[0].src == inner.id

    def test_module_contains_method(self) -> None:
        """Module should contain module-level methods (mixin methods)."""
        mod = _sym(
            "ruby:lib/helpers.rb:1-30:Helpers:module",
            "Helpers", "module", language="ruby", path="lib/helpers.rb",
        )
        method = _sym(
            "ruby:lib/helpers.rb:5-10:Helpers#format:method",
            "Helpers#format", "method", language="ruby", path="lib/helpers.rb",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[mod, method],
            edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 1
        assert contains[0].src == mod.id
        assert contains[0].dst == method.id


class TestSpanBasedContainment:
    """Tests for span-based containment fallback.

    When a symbol has an unqualified name (no :: or . separator), the naming
    convention approach cannot find its parent.  The span-based fallback checks
    if any container symbol in the same file has a span that fully encloses the
    unqualified symbol's span.
    """

    def test_span_containment_module_class(self) -> None:
        """Module contains class when spans overlap and names are unqualified."""
        mod = _sym(
            "ruby:lib/postal.rb:1-50:Postal:module",
            "Postal", "module", language="ruby", path="lib/postal.rb",
            start=1, end=50,
        )
        cls = _sym(
            "ruby:lib/postal.rb:5-25:HTTP:class",
            "HTTP", "class", language="ruby", path="lib/postal.rb",
            start=5, end=25,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[mod, cls], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 1
        assert contains[0].src == mod.id
        assert contains[0].dst == cls.id
        assert contains[0].evidence_type == "span_overlap"

    def test_span_containment_module_method(self) -> None:
        """Module contains method when names are unqualified and spans overlap."""
        mod = _sym(
            "ruby:lib/helpers.rb:1-30:Helpers:module",
            "Helpers", "module", language="ruby", path="lib/helpers.rb",
            start=1, end=30,
        )
        method = _sym(
            "ruby:lib/helpers.rb:5-10:format:method",
            "format", "method", language="ruby", path="lib/helpers.rb",
            start=5, end=10,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[mod, method], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 1
        assert contains[0].src == mod.id
        assert contains[0].dst == method.id

    def test_span_containment_prefers_tightest_container(self) -> None:
        """When nested containers both enclose a symbol, pick the tightest one."""
        outer = _sym(
            "ruby:lib/postal.rb:1-100:Postal:module",
            "Postal", "module", language="ruby", path="lib/postal.rb",
            start=1, end=100,
        )
        inner = _sym(
            "ruby:lib/postal.rb:10-50:MessageDB:module",
            "MessageDB", "module", language="ruby", path="lib/postal.rb",
            start=10, end=50,
        )
        cls = _sym(
            "ruby:lib/postal.rb:15-30:Database:class",
            "Database", "class", language="ruby", path="lib/postal.rb",
            start=15, end=30,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[outer, inner, cls], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        # outer → inner (span), inner → cls (span)
        assert len(contains) == 2
        inner_to_cls = [e for e in contains if e.dst == cls.id]
        assert len(inner_to_cls) == 1
        assert inner_to_cls[0].src == inner.id

    def test_span_containment_different_file_no_match(self) -> None:
        """Span overlap only applies within the same file."""
        mod = _sym(
            "ruby:lib/postal.rb:1-50:Postal:module",
            "Postal", "module", language="ruby", path="lib/postal.rb",
            start=1, end=50,
        )
        cls = _sym(
            "ruby:lib/other.rb:5-25:HTTP:class",
            "HTTP", "class", language="ruby", path="lib/other.rb",
            start=5, end=25,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[mod, cls], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 0

    def test_span_containment_no_span_no_match(self) -> None:
        """Symbols without span data don't participate in span containment."""
        mod = Symbol(
            id="ruby:lib/postal.rb:1-50:Postal:module",
            name="Postal", kind="module", language="ruby",
            path="lib/postal.rb",
            span=Span(start_line=1, end_line=50, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run",
        )
        cls = Symbol(
            id="ruby:lib/postal.rb:5-25:HTTP:class",
            name="HTTP", kind="class", language="ruby",
            path="lib/postal.rb",
            span=None,
            origin="test", origin_run_id="test-run",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[mod, cls], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 0

    def test_span_containment_naming_convention_takes_priority(self) -> None:
        """When naming convention works, span fallback is not needed."""
        mod = _sym(
            "ruby:lib/postal.rb:1-50:Postal:module",
            "Postal", "module", language="ruby", path="lib/postal.rb",
            start=1, end=50,
        )
        cls = _sym(
            "ruby:lib/postal.rb:5-25:Postal::HTTP:class",
            "Postal::HTTP", "class", language="ruby", path="lib/postal.rb",
            start=5, end=25,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[mod, cls], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 1
        assert contains[0].src == mod.id
        assert contains[0].dst == cls.id
        assert contains[0].evidence_type == "naming_convention"

    def test_span_containment_rpc_with_unqualified_name(self) -> None:
        """Proto RPCs with unqualified names should be linked to services via span fallback."""
        svc = _sym(
            "proto:hello.proto:3-15:HelloService:service",
            "HelloService", "service", language="proto", path="hello.proto",
            start=3, end=15,
        )
        rpc = _sym(
            "proto:hello.proto:5-5:BidiHello:rpc",
            "BidiHello", "rpc", language="proto", path="hello.proto",
            start=5, end=5,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[svc, rpc], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 1
        assert contains[0].src == svc.id
        assert contains[0].dst == rpc.id

    def test_span_containment_dedup_with_existing_edges(self) -> None:
        """Span containment doesn't duplicate edges already in ctx.edges."""
        mod = _sym(
            "ruby:lib/postal.rb:1-50:Postal:module",
            "Postal", "module", language="ruby", path="lib/postal.rb",
            start=1, end=50,
        )
        cls = _sym(
            "ruby:lib/postal.rb:5-25:HTTP:class",
            "HTTP", "class", language="ruby", path="lib/postal.rb",
            start=5, end=25,
        )

        existing_edge = Edge.create(
            src=mod.id, dst=cls.id, edge_type="contains",
            line=5, confidence=1.0, origin="test", origin_run_id="test-run",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[mod, cls], edges=[existing_edge],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 0


class TestCanonicalNameContainment:
    """Tests for canonical_name-based containment fallback.

    Proto analyzers and some other analyzers emit unqualified symbol names
    (e.g., ``name="BidiHello"``) but provide a fully qualified canonical_name
    (e.g., ``canonical_name="hello.HelloService.BidiHello"``).  The containment
    linker should fall back to canonical_name when the plain name has no
    separator, enabling service→rpc and nested message containment.
    """

    def test_proto_service_contains_rpc_via_canonical_name(self) -> None:
        """Proto service contains its RPCs via canonical_name fallback."""
        svc = _sym(
            "proto:hello.proto:3-15:HelloService:service",
            "HelloService", "service", language="proto", path="hello.proto",
            start=3, end=15,
            canonical_name="hello.HelloService",
        )
        rpc = _sym(
            "proto:hello.proto:5-5:BidiHello:rpc",
            "BidiHello", "rpc", language="proto", path="hello.proto",
            start=5, end=5,
            canonical_name="hello.HelloService.BidiHello",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[svc, rpc], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 1
        assert contains[0].src == svc.id
        assert contains[0].dst == rpc.id
        assert contains[0].evidence_type == "canonical_name"

    def test_proto_nested_message_via_canonical_name(self) -> None:
        """Nested proto message linked via canonical_name."""
        outer_msg = _sym(
            "proto:types.proto:10-30:MemoryStats:message",
            "MemoryStats", "message", language="proto", path="types.proto",
            start=10, end=30,
            canonical_name="kong.MemoryStats",
        )
        inner_msg = _sym(
            "proto:types.proto:15-20:LuaSharedDicts:message",
            "LuaSharedDicts", "message", language="proto", path="types.proto",
            start=15, end=20,
            canonical_name="kong.MemoryStats.LuaSharedDicts",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[outer_msg, inner_msg], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 1
        assert contains[0].src == outer_msg.id
        assert contains[0].dst == inner_msg.id
        assert contains[0].evidence_type == "canonical_name"

    def test_canonical_name_prefers_same_file(self) -> None:
        """When multiple containers share canonical_name, prefer same-file."""
        svc_a = _sym(
            "proto:a.proto:1-20:HelloService:service",
            "HelloService", "service", language="proto", path="a.proto",
            start=1, end=20,
            canonical_name="hello.HelloService",
        )
        svc_b = _sym(
            "proto:b.proto:1-20:HelloService:service",
            "HelloService", "service", language="proto", path="b.proto",
            start=1, end=20,
            canonical_name="hello.HelloService",
        )
        rpc = _sym(
            "proto:b.proto:5-5:BidiHello:rpc",
            "BidiHello", "rpc", language="proto", path="b.proto",
            start=5, end=5,
            canonical_name="hello.HelloService.BidiHello",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[svc_a, svc_b, rpc], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 1
        assert contains[0].src == svc_b.id  # same file as rpc

    def test_canonical_name_no_match_when_parent_missing(self) -> None:
        """No edge when canonical_name parent doesn't exist as a symbol."""
        rpc = _sym(
            "proto:hello.proto:5-5:BidiHello:rpc",
            "BidiHello", "rpc", language="proto", path="hello.proto",
            start=5, end=5,
            canonical_name="hello.MissingService.BidiHello",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[rpc], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 0

    def test_canonical_name_skips_when_name_has_separator(self) -> None:
        """canonical_name fallback is only used when name has no separator."""
        svc = _sym(
            "proto:hello.proto:3-15:hello.HelloService:service",
            "hello.HelloService", "service", language="proto", path="hello.proto",
            start=3, end=15,
            canonical_name="hello.HelloService",
        )
        rpc = _sym(
            "proto:hello.proto:5-5:hello.HelloService.BidiHello:rpc",
            "hello.HelloService.BidiHello", "rpc", language="proto",
            path="hello.proto", start=5, end=5,
            canonical_name="hello.HelloService.BidiHello",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[svc, rpc], edges=[],
        )
        result = link_containment(ctx)

        # Should use naming_convention (name has separator), not canonical_name
        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 1
        assert contains[0].evidence_type == "naming_convention"

    def test_multiple_rpcs_in_service(self) -> None:
        """All RPCs in a service should be contained."""
        svc = _sym(
            "proto:plugin.proto:1-50:Kong:service",
            "Kong", "service", language="proto", path="plugin.proto",
            start=1, end=50,
            canonical_name="kong_plugin_protocol.Kong",
        )
        rpc1 = _sym(
            "proto:plugin.proto:5-5:Client_Authenticate:rpc",
            "Client_Authenticate", "rpc", language="proto", path="plugin.proto",
            start=5, end=5,
            canonical_name="kong_plugin_protocol.Kong.Client_Authenticate",
        )
        rpc2 = _sym(
            "proto:plugin.proto:8-8:Client_GetConsumer:rpc",
            "Client_GetConsumer", "rpc", language="proto", path="plugin.proto",
            start=8, end=8,
            canonical_name="kong_plugin_protocol.Kong.Client_GetConsumer",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[svc, rpc1, rpc2], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 2
        assert all(e.src == svc.id for e in contains)
        dst_ids = {e.dst for e in contains}
        assert dst_ids == {rpc1.id, rpc2.id}

    def test_canonical_name_no_separator_skipped(self) -> None:
        """canonical_name without separator is skipped (no parent extractable)."""
        rpc = _sym(
            "proto:hello.proto:5-5:BidiHello:rpc",
            "BidiHello", "rpc", language="proto", path="hello.proto",
            start=5, end=5,
            canonical_name="BidiHello",  # No separator
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[rpc], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 0

    def test_canonical_name_fallback_no_same_file_match(self) -> None:
        """Falls back to first candidate when no same-file match exists."""
        svc_a = _sym(
            "proto:a.proto:1-20:HelloService:service",
            "HelloService", "service", language="proto", path="a.proto",
            start=1, end=20,
            canonical_name="hello.HelloService",
        )
        svc_b = _sym(
            "proto:b.proto:1-20:HelloService:service",
            "HelloService", "service", language="proto", path="b.proto",
            start=1, end=20,
            canonical_name="hello.HelloService",
        )
        # RPC is in a THIRD file — no same-file match
        rpc = _sym(
            "proto:c.proto:5-5:BidiHello:rpc",
            "BidiHello", "rpc", language="proto", path="c.proto",
            start=5, end=5,
            canonical_name="hello.HelloService.BidiHello",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[svc_a, svc_b, rpc], edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 1
        # Falls back to first candidate (svc_a)
        assert contains[0].src == svc_a.id

    def test_canonical_name_dedup_with_existing_edges(self) -> None:
        """canonical_name containment doesn't duplicate existing edges."""
        svc = _sym(
            "proto:hello.proto:3-15:HelloService:service",
            "HelloService", "service", language="proto", path="hello.proto",
            start=3, end=15,
            canonical_name="hello.HelloService",
        )
        rpc = _sym(
            "proto:hello.proto:5-5:BidiHello:rpc",
            "BidiHello", "rpc", language="proto", path="hello.proto",
            start=5, end=5,
            canonical_name="hello.HelloService.BidiHello",
        )

        existing_edge = Edge.create(
            src=svc.id, dst=rpc.id, edge_type="contains",
            line=5, confidence=1.0, origin="test",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"), symbols=[svc, rpc], edges=[existing_edge],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains) == 0

    def test_cross_language_same_name_no_false_edge(self) -> None:
        """Two classes with the same name in different languages must not link.

        Regression: DEEP bakeoff cohort #6 (forgejo) showed JS Source class
        (eventsource.sharedworker.js) linked via contains edges to Go LDAP
        Source struct methods (services/auth/source/ldap/). This was a
        name-collision bug: _find_parent returned the first candidate when
        no same-file match existed, ignoring language.
        """
        # Go Source struct and its method
        go_source = _sym(
            "go:auth/source.go:1-50:Source:class",
            "Source", "class", language="go", path="auth/source.go",
        )
        go_method = _sym(
            "go:auth/source.go:10-20:Source.Authenticate:method",
            "Source.Authenticate", "method", language="go", path="auth/source.go",
            start=10, end=20,
        )
        # JS Source class and its method (different file, different language)
        js_source = _sym(
            "js:web/eventsource.js:1-30:Source:class",
            "Source", "class", language="javascript", path="web/eventsource.js",
        )
        js_method = _sym(
            "js:web/eventsource.js:5-15:Source.connect:method",
            "Source.connect", "method", language="javascript",
            path="web/eventsource.js", start=5, end=15,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[go_source, go_method, js_source, js_method],
            edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        # Should produce exactly 2 contains edges:
        # go_source → go_method, js_source → js_method
        assert len(contains) == 2

        src_dst_pairs = {(e.src, e.dst) for e in contains}
        # Go method should link to Go class, not JS class
        assert (go_source.id, go_method.id) in src_dst_pairs
        # JS method should link to JS class, not Go class
        assert (js_source.id, js_method.id) in src_dst_pairs

    def test_cross_language_isolated_method_no_false_edge(self) -> None:
        """Method should not link to a different-language class when no same-language match.

        If a JS method 'Source.send' exists but there's no JS 'Source' class
        (only a Go one), the linker must NOT create a cross-language edge.
        """
        go_source = _sym(
            "go:auth/source.go:1-50:Source:class",
            "Source", "class", language="go", path="auth/source.go",
        )
        js_method = _sym(
            "js:web/worker.js:5-15:Source.send:method",
            "Source.send", "method", language="javascript",
            path="web/worker.js", start=5, end=15,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[go_source, js_method],
            edges=[],
        )
        result = link_containment(ctx)

        contains = [e for e in result.edges if e.edge_type == "contains"]
        # No edge should be created: JS method must not link to Go class
        assert len(contains) == 0


class TestFindParent:
    """Unit tests for _find_parent language filtering."""

    def test_single_same_lang_different_file(self) -> None:
        """When one candidate shares language but is in a different file."""
        go_class = _sym(
            "go:auth/source.go:1-50:Source:class",
            "Source", "class", language="go", path="auth/source.go",
        )
        java_class = _sym(
            "java:Source.java:1-50:Source:class",
            "Source", "class", language="java", path="Source.java",
        )
        container_by_name = {"Source": [go_class, java_class]}
        # Go method in a DIFFERENT file from go_class
        result = _find_parent(
            "Source", "auth/handler.go", container_by_name, "go",
        )
        assert result is go_class

    def test_no_same_lang_among_multiple(self) -> None:
        """When no candidate shares language, returns None."""
        go_class = _sym(
            "go:auth/source.go:1-50:Source:class",
            "Source", "class", language="go", path="auth/source.go",
        )
        java_class = _sym(
            "java:Source.java:1-50:Source:class",
            "Source", "class", language="java", path="Source.java",
        )
        container_by_name = {"Source": [go_class, java_class]}
        # JS method: no JS class exists
        result = _find_parent(
            "Source", "web/worker.js", container_by_name, "javascript",
        )
        assert result is None

    def test_no_language_info_falls_back(self) -> None:
        """When child_language is None, falls back to first candidate."""
        go_class = _sym(
            "go:auth/source.go:1-50:Source:class",
            "Source", "class", language="go", path="auth/source.go",
        )
        java_class = _sym(
            "java:Source.java:1-50:Source:class",
            "Source", "class", language="java", path="Source.java",
        )
        container_by_name = {"Source": [go_class, java_class]}
        result = _find_parent(
            "Source", "other/file.txt", container_by_name, None,
        )
        # Without language info, returns first candidate (backward compat)
        assert result is go_class
