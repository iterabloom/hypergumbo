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
        """When no same-file match, uses deterministic fallback (sorted by ID)
        and the resulting edge carries INV-zuhub provenance: confidence <= 0.5
        and meta["disambiguation_fallback"] = True so downstream consumers
        can filter the fallback population from the precision-resolved one.
        """
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
        edge = extends_edges[0]
        # Deterministic: first by sorted symbol ID
        assert edge.dst == "sym:x:Base", (
            f"Expected deterministic fallback (sym:x:Base), got {edge.dst}"
        )
        # INV-zuhub: simple-name fallback edges must carry conf <= 0.5 and
        # the disambiguation_fallback provenance flag.
        assert edge.confidence <= 0.5, (
            f"Fallback edge confidence {edge.confidence} exceeds INV-zuhub cap of 0.5"
        )
        assert edge.meta is not None
        assert edge.meta.get("disambiguation_fallback") is True

    def test_single_candidate_resolution_keeps_high_confidence(self) -> None:
        """Single-candidate resolution is precision (no ambiguity), so the
        edge keeps high confidence and no disambiguation_fallback flag.
        """
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
            path="/other.py",
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
        edge = result.edges[0]
        assert edge.confidence > 0.5
        if edge.meta is not None:
            assert edge.meta.get("disambiguation_fallback") is not True

    def test_same_file_resolution_keeps_high_confidence(self) -> None:
        """Same-file resolution uses import-context disambiguation, so it
        is precision (not fallback) and keeps high confidence.
        """
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

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[base_a, base_b, child],
            edges=[],
        )
        result = link_inheritance(ctx)
        edges = [e for e in result.edges if e.src == "sym:b:Child"]
        assert len(edges) == 1
        edge = edges[0]
        assert edge.dst == "sym:b:Base"
        assert edge.confidence > 0.5
        if edge.meta is not None:
            assert edge.meta.get("disambiguation_fallback") is not True

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
            origin="go",
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
            origin="go",
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
            origin="go",
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
            origin="go",
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
            origin="go",
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
            origin="go",
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
            origin="go",
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
            origin="scala",
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
            origin="scala",
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

    def test_objc_protocol_creates_implements_edge(self) -> None:
        """ObjC class conforming to a protocol creates implements edge."""
        protocol = Symbol(
            id="objc:MBProgressHUD.h:10-15:MBProgressHUDDelegate:protocol",
            name="MBProgressHUDDelegate",
            kind="protocol",
            language="objective-c",
            path="/MBProgressHUD.h",
            span=Span(start_line=10, end_line=15, start_col=0, end_col=0),
            origin="objc",
            origin_run_id="test-run",
            meta=None,
        )
        cls = Symbol(
            id="objc:ViewController.m:1-20:ViewController:class",
            name="ViewController",
            kind="class",
            language="objective-c",
            path="/ViewController.m",
            span=Span(start_line=1, end_line=20, start_col=0, end_col=0),
            origin="objc",
            origin_run_id="test-run",
            meta={"base_classes": ["UIViewController", "MBProgressHUDDelegate"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[protocol, cls],
            edges=[],
        )
        result = link_inheritance(ctx)

        # Should have at least 1 implements edge to the protocol
        implements_edges = [e for e in result.edges if e.edge_type == "implements"]
        assert len(implements_edges) == 1
        assert implements_edges[0].dst == protocol.id
        assert implements_edges[0].src == cls.id

    def test_trait_extends_trait(self) -> None:
        """Trait extending another trait creates implements edge."""
        base_trait = Symbol(
            id="sym:Serializable",
            name="Serializable",
            kind="trait",
            language="scala",
            path="/traits.scala",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="scala",
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
            origin="scala",
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

    def test_rust_struct_resolves_to_trait_when_struct_collision_exists(self) -> None:
        """Rust struct's base_class with both a trait and a struct of the same
        name resolves to the trait (kind discipline — WI-zozuz BUG-03 layer 1).

        ``impl Module for LayerNorm`` makes the analyzer emit
        ``LayerNorm.meta.base_classes = ["Module"]``. If a same-named struct
        exists elsewhere (here in ``candle-kernels``), the linker must NOT
        fall back to it — Rust structs cannot extend other structs.
        """
        module_trait = Symbol(
            id="rust:candle-core/src/nn.rs:1-5:Module:trait",
            name="Module",
            kind="trait",
            language="rust",
            path="/candle-core/src/nn.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="rust",
            origin_run_id="test-run",
            meta=None,
        )
        module_struct = Symbol(
            id="rust:candle-kernels/src/lib.rs:1-3:Module:struct",
            name="Module",
            kind="struct",
            language="rust",
            path="/candle-kernels/src/lib.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="rust",
            origin_run_id="test-run",
            meta=None,
        )
        layer_norm = Symbol(
            id="rust:candle-nn/src/layer_norm.rs:10-20:LayerNorm:struct",
            name="LayerNorm",
            kind="struct",
            language="rust",
            path="/candle-nn/src/layer_norm.rs",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            origin="rust",
            origin_run_id="test-run",
            meta={"base_classes": ["Module"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[module_trait, module_struct, layer_norm],
            edges=[],
        )
        result = link_inheritance(ctx)

        edges_from_layer_norm = [e for e in result.edges if e.src == layer_norm.id]
        assert len(edges_from_layer_norm) == 1
        assert edges_from_layer_norm[0].dst == module_trait.id
        assert edges_from_layer_norm[0].edge_type == "implements"

    def test_rust_struct_no_edge_when_only_struct_target_exists(self) -> None:
        """Rust struct with no trait target produces NO edge — Rust structs
        cannot extend other structs (kind discipline — WI-zozuz BUG-03 layer 1).
        """
        module_struct = Symbol(
            id="rust:candle-kernels/src/lib.rs:1-3:Module:struct",
            name="Module",
            kind="struct",
            language="rust",
            path="/candle-kernels/src/lib.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="rust",
            origin_run_id="test-run",
            meta=None,
        )
        layer_norm = Symbol(
            id="rust:candle-nn/src/layer_norm.rs:10-20:LayerNorm:struct",
            name="LayerNorm",
            kind="struct",
            language="rust",
            path="/candle-nn/src/layer_norm.rs",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            origin="rust",
            origin_run_id="test-run",
            meta={"base_classes": ["Module"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[module_struct, layer_norm],
            edges=[],
        )
        result = link_inheritance(ctx)

        # No trait Module exists; Rust structs cannot extend structs, so no edge.
        assert result.edges == []

    def test_python_class_does_not_implement_rust_trait(self) -> None:
        """A Python class with base_classes=["Module"] must NOT match a Rust
        ``Module`` trait (cross-language gating — WI-zozuz BUG-03 layer 2).

        ``class FooModule(nn.Module)`` in candle-pyo3 normalizes to
        ``base_classes=["Module"]``. Without gating, the inheritance linker
        emits 31 spurious cross-language ``implements`` edges from those
        Python classes to the Rust ``Module`` trait.
        """
        rust_trait = Symbol(
            id="rust:candle-core/src/nn.rs:1-5:Module:trait",
            name="Module",
            kind="trait",
            language="rust",
            path="/candle-core/src/nn.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="rust",
            origin_run_id="test-run",
            meta=None,
        )
        python_class = Symbol(
            id="python:candle-pyo3/py_src/foo.py:1-10:FooModule:class",
            name="FooModule",
            kind="class",
            language="python",
            path="/candle-pyo3/py_src/foo.py",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="python",
            origin_run_id="test-run",
            meta={"base_classes": ["Module"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[rust_trait, python_class],
            edges=[],
        )
        result = link_inheritance(ctx)

        # Cross-language match must not happen.
        assert result.edges == []

    def test_same_language_match_still_works_when_other_language_collides(
        self,
    ) -> None:
        """Cross-language gating must not block legitimate same-language
        resolution when a same-named symbol also exists in another language.
        """
        py_base = Symbol(
            id="python:base.py:1-3:Base:class",
            name="Base",
            kind="class",
            language="python",
            path="/py/base.py",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="python",
            origin_run_id="test-run",
            meta=None,
        )
        rust_base_struct = Symbol(
            id="rust:base.rs:1-3:Base:struct",
            name="Base",
            kind="struct",
            language="rust",
            path="/rust/base.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="rust",
            origin_run_id="test-run",
            meta=None,
        )
        py_child = Symbol(
            id="python:child.py:1-5:Child:class",
            name="Child",
            kind="class",
            language="python",
            path="/py/child.py",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="python",
            origin_run_id="test-run",
            meta={"base_classes": ["Base"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[py_base, rust_base_struct, py_child],
            edges=[],
        )
        result = link_inheritance(ctx)

        edges = [e for e in result.edges if e.src == py_child.id]
        assert len(edges) == 1
        assert edges[0].dst == py_base.id
        assert edges[0].edge_type == "extends"

    def test_class_extends_class_and_trait(self) -> None:
        """Class with both class and trait base_classes gets both edge types."""
        base_class = Symbol(
            id="sym:BaseService",
            name="BaseService",
            kind="class",
            language="scala",
            path="/base.scala",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="scala",
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
            origin="scala",
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
            origin="scala",
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


# ---------------------------------------------------------------------------
# WI-gifar (PR-1 of INV-nilud inherited_calls campaign):
# Promote _resolve_target_symbol to public resolve_target_symbol so the
# upcoming inherited_calls linker can reuse the same-language / same-file
# / sorted-ID disambiguation logic.
# ---------------------------------------------------------------------------


class TestResolveTargetSymbolPublic:
    """Public alias resolve_target_symbol matches private implementation."""

    def _sym(self, sid: str, name: str, path: str, language: str = "python") -> Symbol:
        return Symbol(
            id=sid,
            name=name,
            kind="class",
            language=language,
            path=path,
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta=None,
        )

    def test_public_name_is_importable(self) -> None:
        from hypergumbo_core.linkers.inheritance import resolve_target_symbol
        assert callable(resolve_target_symbol)

    def test_resolves_unique_candidate(self) -> None:
        from hypergumbo_core.linkers.inheritance import resolve_target_symbol

        base = self._sym("sym:Base", "Base", "/lib.py")
        child = self._sym("sym:Child", "Child", "/app.py")
        result = resolve_target_symbol("Base", child, {"Base": [base]})
        assert result is not None
        sym, is_fallback = result
        assert sym.id == "sym:Base"
        assert is_fallback is False

    def test_returns_none_when_no_candidates(self) -> None:
        from hypergumbo_core.linkers.inheritance import resolve_target_symbol

        child = self._sym("sym:Child", "Child", "/app.py")
        assert resolve_target_symbol("Missing", child, {}) is None

    def test_public_and_private_names_share_implementation(self) -> None:
        """The promotion must not fork behavior — both names dispatch the same code."""
        from hypergumbo_core.linkers.inheritance import (
            _resolve_target_symbol,
            resolve_target_symbol,
        )
        assert resolve_target_symbol is _resolve_target_symbol
