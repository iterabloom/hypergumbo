"""Tests for inheritance linker."""

from pathlib import Path

from hypergumbo_core.ir import Symbol, Span, Edge
from hypergumbo_core.linkers.inheritance import link_inheritance
from hypergumbo_core.linkers.registry import LinkerContext


class TestInheritanceLinker:
    """Tests for the inheritance linker."""

    def test_creates_extends_edge(self) -> None:
        """Creates extends edge from class to base class."""
        base = Symbol(
            id="sym:BaseModel",
            name="BaseModel",
            kind="class",
            language="python",
            path="/test.py",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )
        derived = Symbol(
            id="sym:User",
            name="User",
            kind="class",
            language="python",
            path="/test.py",
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta={"base_classes": ["BaseModel"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[base, derived],
            edges=[],
        )
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].src == "sym:User"
        assert result.edges[0].dst == "sym:BaseModel"
        assert result.edges[0].edge_type == "extends"

    def test_creates_implements_edge_for_interface(self) -> None:
        """Creates implements edge from class to interface."""
        interface = Symbol(
            id="sym:IEntity",
            name="IEntity",
            kind="interface",
            language="csharp",
            path="/test.cs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )
        impl = Symbol(
            id="sym:User",
            name="User",
            kind="class",
            language="csharp",
            path="/test.cs",
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta={"base_classes": ["IEntity"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[interface, impl],
            edges=[],
        )
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].src == "sym:User"
        assert result.edges[0].dst == "sym:IEntity"
        assert result.edges[0].edge_type == "implements"

    def test_strips_generic_parameters(self) -> None:
        """Strips generic parameters from base class name."""
        base = Symbol(
            id="sym:Repository",
            name="Repository",
            kind="class",
            language="csharp",
            path="/test.cs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )
        derived = Symbol(
            id="sym:UserRepository",
            name="UserRepository",
            kind="class",
            language="csharp",
            path="/test.cs",
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta={"base_classes": ["Repository<User>"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[base, derived],
            edges=[],
        )
        result = link_inheritance(ctx)

        # Should create edge to Repository, not Repository<User>
        assert len(result.edges) == 1
        assert result.edges[0].dst == "sym:Repository"
        assert result.edges[0].edge_type == "extends"

    def test_handles_scoped_names(self) -> None:
        """Handles Ruby-style scoped names (Foo::Bar)."""
        base = Symbol(
            id="sym:Base",
            name="Base",
            kind="class",
            language="ruby",
            path="/test.rb",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )
        derived = Symbol(
            id="sym:User",
            name="User",
            kind="class",
            language="ruby",
            path="/test.rb",
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta={"base_classes": ["ActiveRecord::Base"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[base, derived],
            edges=[],
        )
        result = link_inheritance(ctx)

        # Should match Base from ActiveRecord::Base
        assert len(result.edges) == 1
        assert result.edges[0].dst == "sym:Base"
        assert result.edges[0].edge_type == "extends"

    def test_handles_qualified_names_with_dots(self) -> None:
        """Handles dot-qualified names (Foo.Bar) like C# namespaces."""
        base = Symbol(
            id="sym:Controller",
            name="Controller",
            kind="class",
            language="csharp",
            path="/test.cs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )
        derived = Symbol(
            id="sym:UserController",
            name="UserController",
            kind="class",
            language="csharp",
            path="/test.cs",
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta={"base_classes": ["Microsoft.AspNetCore.Mvc.Controller"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[base, derived],
            edges=[],
        )
        result = link_inheritance(ctx)

        # Should match Controller from Microsoft.AspNetCore.Mvc.Controller
        assert len(result.edges) == 1
        assert result.edges[0].dst == "sym:Controller"
        assert result.edges[0].edge_type == "extends"

    def test_skips_existing_edges(self) -> None:
        """Skips edge creation if edge already exists from analyzer."""
        base = Symbol(
            id="sym:BaseModel",
            name="BaseModel",
            kind="class",
            language="python",
            path="/test.py",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )
        derived = Symbol(
            id="sym:User",
            name="User",
            kind="class",
            language="python",
            path="/test.py",
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta={"base_classes": ["BaseModel"]},
        )
        existing_edge = Edge.create(
            src="sym:User",
            dst="sym:BaseModel",
            edge_type="extends",
            line=5,
            confidence=0.95,
            origin="analyzer",
            origin_run_id="analyzer-run",
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[base, derived],
            edges=[existing_edge],
        )
        result = link_inheritance(ctx)

        # Should not create duplicate edge
        assert len(result.edges) == 0

    def test_no_edge_for_external_class(self) -> None:
        """No edge created for external base classes not in symbols."""
        derived = Symbol(
            id="sym:User",
            name="User",
            kind="class",
            language="python",
            path="/test.py",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta={"base_classes": ["ExternalClass"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[derived],
            edges=[],
        )
        result = link_inheritance(ctx)

        # Should not create any edge
        assert len(result.edges) == 0
