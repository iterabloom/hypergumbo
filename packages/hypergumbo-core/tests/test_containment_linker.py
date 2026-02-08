"""Tests for containment linker.

The containment linker creates `contains` edges from class/interface symbols
to their method symbols, based on naming conventions (ClassName.method,
ClassName#method, ClassName::method).
"""

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.containment import link_containment
from hypergumbo_core.linkers.registry import LinkerContext


def _sym(
    id: str,
    name: str,
    kind: str,
    language: str = "python",
    path: str = "app.py",
    start: int = 1,
    end: int = 5,
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
