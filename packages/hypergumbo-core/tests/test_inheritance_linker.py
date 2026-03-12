# SPDX-License-Identifier: AGPL-3.0-or-later
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

    def test_same_file_preferred_over_name_collision(self) -> None:
        """When multiple classes share a name, prefer the one in the same file."""
        # Base in file A
        base_a = Symbol(
            id="sym:a:Base",
            name="Base",
            kind="class",
            language="csharp",
            path="/A.cs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )
        # Base in file B (same name, different file)
        base_b = Symbol(
            id="sym:b:Base",
            name="Base",
            kind="class",
            language="csharp",
            path="/B.cs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )
        # Child in file B extends Base — should resolve to B's Base
        child = Symbol(
            id="sym:b:Child",
            name="Child",
            kind="class",
            language="csharp",
            path="/B.cs",
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta={"base_classes": ["Base"]},
        )

        # Order: base_b first, then base_a last — so last-writer-wins picks A.
        # The fix should prefer B (same file as child) regardless of processing order.
        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[base_b, base_a, child],
            edges=[],
        )
        result = link_inheritance(ctx)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        child_edges = [e for e in extends_edges if e.src == "sym:b:Child"]
        assert len(child_edges) == 1
        # Should resolve to B's Base (same file), not A's Base
        assert child_edges[0].dst == "sym:b:Base", (
            f"Expected same-file Base (sym:b:Base), got {child_edges[0].dst}"
        )

    def test_deterministic_fallback_when_ambiguous(self) -> None:
        """When no same-file match, uses deterministic fallback (sorted by ID)."""
        # Two files define Base, neither is in the same file as Child
        base_x = Symbol(
            id="sym:x:Base",
            name="Base",
            kind="class",
            language="csharp",
            path="/X.cs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )
        base_y = Symbol(
            id="sym:y:Base",
            name="Base",
            kind="class",
            language="csharp",
            path="/Y.cs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )
        child = Symbol(
            id="sym:z:Child",
            name="Child",
            kind="class",
            language="csharp",
            path="/Z.cs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta={"base_classes": ["Base"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[base_x, base_y, child],
            edges=[],
        )
        result = link_inheritance(ctx)

        extends_edges = [e for e in result.edges if e.src == "sym:z:Child"]
        assert len(extends_edges) == 1
        # Deterministic: first by sorted symbol ID
        assert extends_edges[0].dst == "sym:x:Base", (
            f"Expected deterministic fallback (sym:x:Base), got {extends_edges[0].dst}"
        )

    def test_interface_same_file_preferred_over_collision(self) -> None:
        """When multiple interfaces share a name, prefer same-file for implements edge."""
        iface_a = Symbol(
            id="sym:a:IRepo",
            name="IRepo",
            kind="interface",
            language="csharp",
            path="/A.cs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )
        iface_b = Symbol(
            id="sym:b:IRepo",
            name="IRepo",
            kind="interface",
            language="csharp",
            path="/B.cs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )
        impl = Symbol(
            id="sym:b:UserRepo",
            name="UserRepo",
            kind="class",
            language="csharp",
            path="/B.cs",
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta={"base_classes": ["IRepo"]},
        )

        # Order: iface_b first, then iface_a last — so last-writer-wins picks A.
        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[iface_b, iface_a, impl],
            edges=[],
        )
        result = link_inheritance(ctx)

        impl_edges = [e for e in result.edges if e.src == "sym:b:UserRepo"]
        assert len(impl_edges) == 1
        assert impl_edges[0].dst == "sym:b:IRepo", (
            f"Expected same-file IRepo (sym:b:IRepo), got {impl_edges[0].dst}"
        )
        assert impl_edges[0].edge_type == "implements"

    def test_skips_self_inheritance(self) -> None:
        """No edge created when a class appears to extend itself."""
        # A class that lists itself as a base (degenerate case from bad metadata)
        sym = Symbol(
            id="sym:Model",
            name="Model",
            kind="class",
            language="python",
            path="/test.py",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta={"base_classes": ["Model"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[sym],
            edges=[],
        )
        result = link_inheritance(ctx)

        # Should not create self-referential edge
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

    def test_go_struct_implements_interface(self) -> None:
        """Go struct with base_classes creates implements edge to interface."""
        iface = Symbol(
            id="sym:Reader",
            name="Reader",
            kind="interface",
            language="go",
            path="/reader.go",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="go-v1",
            origin_run_id="test-run",
            meta=None,
        )
        struct = Symbol(
            id="sym:MyReader",
            name="MyReader",
            kind="struct",
            language="go",
            path="/reader.go",
            span=Span(start_line=10, end_line=12, start_col=0, end_col=0),
            origin="go-v1",
            origin_run_id="test-run",
            meta={"base_classes": ["Reader"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[iface, struct],
            edges=[],
        )
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].src == "sym:MyReader"
        assert result.edges[0].dst == "sym:Reader"
        assert result.edges[0].edge_type == "implements"

    def test_go_struct_as_embedding_target(self) -> None:
        """Go struct can be resolved as a target for struct embedding."""
        base_struct = Symbol(
            id="sym:BaseModel",
            name="BaseModel",
            kind="struct",
            language="go",
            path="/models.go",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="go-v1",
            origin_run_id="test-run",
            meta=None,
        )
        derived_struct = Symbol(
            id="sym:User",
            name="User",
            kind="struct",
            language="go",
            path="/models.go",
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
            origin="go-v1",
            origin_run_id="test-run",
            meta={"base_classes": ["BaseModel"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[base_struct, derived_struct],
            edges=[],
        )
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].src == "sym:User"
        assert result.edges[0].dst == "sym:BaseModel"
        assert result.edges[0].edge_type == "extends"

    def test_go_struct_multiple_interfaces(self) -> None:
        """Go struct implementing multiple interfaces creates multiple edges."""
        reader = Symbol(
            id="sym:Reader",
            name="Reader",
            kind="interface",
            language="go",
            path="/io.go",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="go-v1",
            origin_run_id="test-run",
            meta=None,
        )
        closer = Symbol(
            id="sym:Closer",
            name="Closer",
            kind="interface",
            language="go",
            path="/io.go",
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
            origin="go-v1",
            origin_run_id="test-run",
            meta=None,
        )
        my_file = Symbol(
            id="sym:MyFile",
            name="MyFile",
            kind="struct",
            language="go",
            path="/file.go",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="go-v1",
            origin_run_id="test-run",
            meta={"base_classes": ["Reader", "Closer"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[reader, closer, my_file],
            edges=[],
        )
        result = link_inheritance(ctx)

        assert len(result.edges) == 2
        edge_types = {(e.dst, e.edge_type) for e in result.edges}
        assert ("sym:Reader", "implements") in edge_types
        assert ("sym:Closer", "implements") in edge_types

    def test_class_extends_trait_creates_implements_edge(self) -> None:
        """Scala/Groovy class extending a trait creates implements edge."""
        trait = Symbol(
            id="sym:Logging",
            name="Logging",
            kind="trait",
            language="scala",
            path="/Logging.scala",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="scala-v1",
            origin_run_id="test-run",
            meta=None,
        )
        cls = Symbol(
            id="sym:UserService",
            name="UserService",
            kind="class",
            language="scala",
            path="/UserService.scala",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="scala-v1",
            origin_run_id="test-run",
            meta={"base_classes": ["Logging"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[trait, cls],
            edges=[],
        )
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].src == "sym:UserService"
        assert result.edges[0].dst == "sym:Logging"
        assert result.edges[0].edge_type == "implements"

    def test_trait_extends_trait(self) -> None:
        """Trait extending another trait creates implements edge."""
        base_trait = Symbol(
            id="sym:Serializable",
            name="Serializable",
            kind="trait",
            language="scala",
            path="/traits.scala",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="scala-v1",
            origin_run_id="test-run",
            meta=None,
        )
        child_trait = Symbol(
            id="sym:JsonSerializable",
            name="JsonSerializable",
            kind="trait",
            language="scala",
            path="/traits.scala",
            span=Span(start_line=5, end_line=10, start_col=0, end_col=0),
            origin="scala-v1",
            origin_run_id="test-run",
            meta={"base_classes": ["Serializable"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[base_trait, child_trait],
            edges=[],
        )
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].src == "sym:JsonSerializable"
        assert result.edges[0].dst == "sym:Serializable"
        assert result.edges[0].edge_type == "implements"

    def test_class_extends_class_and_trait(self) -> None:
        """Class with both class and trait base_classes gets both edge types."""
        base_class = Symbol(
            id="sym:BaseService",
            name="BaseService",
            kind="class",
            language="scala",
            path="/base.scala",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="scala-v1",
            origin_run_id="test-run",
            meta=None,
        )
        trait = Symbol(
            id="sym:Logging",
            name="Logging",
            kind="trait",
            language="scala",
            path="/logging.scala",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="scala-v1",
            origin_run_id="test-run",
            meta=None,
        )
        cls = Symbol(
            id="sym:UserService",
            name="UserService",
            kind="class",
            language="scala",
            path="/user.scala",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="scala-v1",
            origin_run_id="test-run",
            meta={"base_classes": ["BaseService", "Logging"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[base_class, trait, cls],
            edges=[],
        )
        result = link_inheritance(ctx)

        assert len(result.edges) == 2
        edge_map = {e.dst: e.edge_type for e in result.edges}
        assert edge_map["sym:BaseService"] == "extends"
        assert edge_map["sym:Logging"] == "implements"
