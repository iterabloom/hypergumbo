# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for inheritance linker."""

from pathlib import Path

from hypergumbo_core.ir import Symbol, Span, Edge, ExternalRef
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

    def test_external_base_emits_unresolved_extends(self) -> None:
        """WI-jubag Approach C: an external base class (not in symbols) no
        longer drops silently — the chokepoint mints an unresolved-external
        ``extends`` edge so the relationship is represented (was: no edge)."""
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

        assert len(result.edges) == 1
        e = result.edges[0]
        assert e.src == "sym:User"
        assert e.edge_type == "extends"
        assert e.dst == "python:external:0-0:ExternalClass:unresolved"
        assert e.is_resolved is False
        assert e.dst_ref is None

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
            language="objc",
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
            language="objc",
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


# ---------------------------------------------------------------------------
# WI-hatip (PR-2 of INV-nilud inherited_calls campaign):
# The inheritance linker now emits `includes` edges from Ruby
# `included_modules` metadata (and any future language with the same
# mixin shape). The new edge_type is `includes` with evidence_type
# `ast_includes`.
# ---------------------------------------------------------------------------


class TestIncludesEdges:
    """Tests for the new `includes` edge type emitted from included_modules."""

    def _cls(self, sid: str, name: str, path: str = "/a.rb",
             base_classes: list[str] | None = None,
             included_modules: list[str] | None = None) -> Symbol:
        meta: dict[str, object] = {}
        if base_classes is not None:
            meta["base_classes"] = base_classes
        if included_modules is not None:
            meta["included_modules"] = included_modules
        return Symbol(
            id=sid, name=name, kind="class", language="ruby", path=path,
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run", meta=meta or None,
        )

    def _mod(self, sid: str, name: str, path: str = "/m.rb",
             included_modules: list[str] | None = None) -> Symbol:
        meta: dict[str, object] = {}
        if included_modules is not None:
            meta["included_modules"] = included_modules
        return Symbol(
            id=sid, name=name, kind="module", language="ruby", path=path,
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run", meta=meta or None,
        )

    def test_emits_includes_edge_for_class(self) -> None:
        """A class with `include ModuleX` produces an includes edge."""
        worker_mod = self._mod("sym:Worker", "Worker")
        klass = self._cls(
            "sym:EmailWorker", "EmailWorker",
            included_modules=["Worker"],
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[worker_mod, klass], edges=[],
        )
        result = link_inheritance(ctx)
        includes = [e for e in result.edges if e.edge_type == "includes"]
        assert len(includes) == 1
        assert includes[0].src == "sym:EmailWorker"
        assert includes[0].dst == "sym:Worker"
        assert includes[0].evidence_type == "ast_includes"

    def test_emits_includes_edge_for_module(self) -> None:
        """A module with `include` (e.g., Concern) also produces the edge."""
        concern_mod = self._mod("sym:Concern", "Concern")
        helper_mod = self._mod(
            "sym:AuthHelper", "AuthHelper",
            included_modules=["Concern"],
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[concern_mod, helper_mod], edges=[],
        )
        result = link_inheritance(ctx)
        includes = [e for e in result.edges if e.edge_type == "includes"]
        assert len(includes) == 1
        assert includes[0].src == "sym:AuthHelper"
        assert includes[0].dst == "sym:Concern"

    def test_emits_extends_and_includes_when_both_present(self) -> None:
        """Class with both superclass and include yields two edges."""
        base = self._cls("sym:Base", "Base")
        validations = self._mod("sym:Validations", "Validations")
        user = self._cls(
            "sym:User", "User",
            base_classes=["Base"],
            included_modules=["Validations"],
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, validations, user],
            edges=[],
        )
        result = link_inheritance(ctx)
        by_type = {e.edge_type: e for e in result.edges}
        assert by_type["extends"].dst == "sym:Base"
        assert by_type["includes"].dst == "sym:Validations"

    def test_skips_external_module_with_no_in_tree_symbol(self) -> None:
        """`include Sidekiq::Worker` where Sidekiq is external -> no edge."""
        klass = self._cls(
            "sym:MyWorker", "MyWorker",
            included_modules=["Sidekiq::Worker"],
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[klass], edges=[],
        )
        result = link_inheritance(ctx)
        assert [e for e in result.edges if e.edge_type == "includes"] == []

    def test_qualified_name_falls_back_to_short_segment(self) -> None:
        """`include Foo::Bar` resolves to Bar module when only short is in tree."""
        bar_mod = self._mod("sym:Bar", "Bar")
        klass = self._cls(
            "sym:Klass", "Klass", included_modules=["Foo::Bar"],
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[bar_mod, klass], edges=[],
        )
        result = link_inheritance(ctx)
        includes = [e for e in result.edges if e.edge_type == "includes"]
        assert len(includes) == 1
        assert includes[0].dst == "sym:Bar"

    def test_dot_qualified_name_falls_back_to_short_segment(self) -> None:
        """`Foo.Bar`-style qualified mixin name (non-Ruby producers) also
        falls back to the short segment. Ruby uses `::` exclusively, but
        the resolution code keeps shape parity with extends/implements
        which supports both Java-style `.` and Ruby-style `::`.
        """
        bar_mod = Symbol(
            id="sym:Bar", name="Bar", kind="module", language="groovy",
            path="/m.groovy",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run", meta=None,
        )
        klass = Symbol(
            id="sym:Klass", name="Klass", kind="class", language="groovy",
            path="/k.groovy",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run",
            meta={"included_modules": ["Foo.Bar"]},
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[bar_mod, klass], edges=[],
        )
        result = link_inheritance(ctx)
        includes = [e for e in result.edges if e.edge_type == "includes"]
        assert len(includes) == 1
        assert includes[0].dst == "sym:Bar"

    def test_skips_self_include(self) -> None:
        """A symbol that names itself as included gets no self-edge."""
        mod = self._mod(
            "sym:Selfish", "Selfish", included_modules=["Selfish"],
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[mod], edges=[],
        )
        result = link_inheritance(ctx)
        assert [e for e in result.edges if e.edge_type == "includes"] == []

    def test_skips_duplicate_when_includes_edge_already_exists(self) -> None:
        """Pre-existing `includes` edges from analyzers are not duplicated."""
        mod = self._mod("sym:X", "X")
        klass = self._cls(
            "sym:C", "C", included_modules=["X"],
        )
        existing = Edge.create(
            src="sym:C", dst="sym:X", edge_type="includes", line=1,
            origin="test", origin_run_id="test",
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[mod, klass], edges=[existing],
        )
        result = link_inheritance(ctx)
        new_includes = [e for e in result.edges if e.edge_type == "includes"]
        assert len(new_includes) == 0


class TestExternalBaseFallbackApproachC:
    """WI-jubag Approach C — the core inheritance-linker chokepoint mints
    unresolved-external ``extends`` edges for base classes that resolve to no
    in-tree symbol, uniformly across every OO language, instead of dropping
    them (py.py/js_ts already do this per-analyzer; the chokepoint generalizes
    it to Kotlin/Ruby/Java/... and to Python dotted bases that py.py defers).

    External targets use the ``external`` sentinel module —
    ``{lang}:external:0-0:{name}:unresolved`` with ``dst_ref=None`` (the
    sanctioned WI-huzuv "unidentified reference" shape) — which is INV-nuzas-safe
    by construction (never a workspace-prefixed phantom).
    """

    @staticmethod
    def _cls(
        sym_id: str,
        name: str,
        bases: list[str] | None,
        *,
        language: str = "python",
        path: str = "/t.py",
        kind: str = "class",
    ) -> Symbol:
        return Symbol(
            id=sym_id,
            name=name,
            kind=kind,
            language=language,
            path=path,
            span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test-run",
            meta={"base_classes": bases} if bases else None,
        )

    def test_bare_external_base_emits_unresolved_extends(self) -> None:
        """A bare external base (Kotlin ``AbstractController``) that resolves to
        no in-tree class now emits an unresolved external ``extends`` edge."""
        klass = self._cls(
            "k:UserController", "UserController", ["AbstractController"],
            language="kotlin", path="/c.kt",
        )
        ctx = LinkerContext(repo_root=Path("/"), symbols=[klass], edges=[])
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        e = result.edges[0]
        assert e.src == "k:UserController"
        assert e.edge_type == "extends"
        assert e.dst == "kotlin:external:0-0:AbstractController:unresolved"
        assert e.is_resolved is False
        assert e.dst_ref is None
        assert e.evidence_type == "ast_extends"

    def test_external_base_confidence_is_evidence_derived(self) -> None:
        """External-extends confidence is evidence-derived 0.95 (ADR-0039): the
        extends DETECTION is AST-certain, ``is_resolved=False`` carries the
        unresolved target — matching the merged py.py fallback, not js_ts's 0.5.
        """
        klass = self._cls("p:Foo", "Foo", ["SomeLib"])
        ctx = LinkerContext(repo_root=Path("/"), symbols=[klass], edges=[])
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].confidence == 0.95
        assert result.edges[0].confidence_source == "evidence_derived"

    def test_dotted_external_base_uses_last_segment(self) -> None:
        """A dotted external base (``argparse.ArgumentParser``) — dropped by
        py.py and DEFERRED to Approach C — now emits an external edge keyed on
        the last segment."""
        klass = self._cls("p:MyParser", "MyParser", ["argparse.ArgumentParser"])
        ctx = LinkerContext(repo_root=Path("/"), symbols=[klass], edges=[])
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst == "python:external:0-0:ArgumentParser:unresolved"
        assert result.edges[0].edge_type == "extends"

    def test_scoped_external_base_uses_last_segment(self) -> None:
        """A Ruby scoped external base (``ActiveRecord::Base``) emits an
        external edge on the last segment."""
        klass = self._cls(
            "r:User", "User", ["ActiveRecord::Base"], language="ruby", path="/u.rb",
        )
        ctx = LinkerContext(repo_root=Path("/"), symbols=[klass], edges=[])
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst == "ruby:external:0-0:Base:unresolved"

    def test_generic_external_base_strips_brackets(self) -> None:
        """Python-style generic externals (``Protocol[T]``) strip the ``[...]``
        before externalizing, so the canonical name is clean."""
        klass = self._cls("p:P", "P", ["Protocol[T]"])
        ctx = LinkerContext(repo_root=Path("/"), symbols=[klass], edges=[])
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst == "python:external:0-0:Protocol:unresolved"

    def test_in_tree_namesake_base_is_not_externalized(self) -> None:
        """INV-nuzas guard: if the base's simple name matches ANY in-tree symbol
        (here a module-level variable, not a class), it is NOT minted as an
        external — a base whose in-tree definition simply was not extracted as a
        class must be dropped, never turned into a false external (the same
        conservative bias py.py's ``_base_module_is_in_tree`` guard takes)."""
        helper_var = Symbol(
            id="p:helpers:Helper", name="Helper", kind="variable",
            language="python", path="/helpers.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run", meta=None,
        )
        klass = self._cls("p:Foo", "Foo", ["Helper"])
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[helper_var, klass], edges=[],
        )
        result = link_inheritance(ctx)

        assert result.edges == []

    def test_bare_external_owned_by_analyzer_is_not_re_emitted(self) -> None:
        """Coexistence with py.py/js_ts: when an analyzer already emitted an
        UNRESOLVED external edge for this class (``is_resolved=False``), the
        chokepoint defers the class's BARE external bases to that analyzer — it
        must NOT re-mint a sentinel edge (which, for an aliased base like
        ``from enum import Enum as E``, would carry the alias name and double the
        analyzer's original-name edge)."""
        klass = self._cls("p:Color", "Color", ["Enum"])
        analyzer_edge = Edge.create(
            src="p:Color", dst="python:enum:0-0:Enum:unresolved",
            edge_type="extends", line=1, origin="python", origin_run_id="a-run",
            evidence_type="ast_extends", is_resolved=False,
            dst_ref=ExternalRef(lang="python", module_path="enum", name="Enum"),
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[klass], edges=[analyzer_edge],
        )
        result = link_inheritance(ctx)

        assert result.edges == []

    def test_bare_external_owned_by_analyzer_builtin_edge(self) -> None:
        """Same ownership deferral when the analyzer edge is a bare-builtin
        external (``dst_ref=None``): ``is_resolved=False`` marks the class as
        analyzer-owned for bare bases."""
        klass = self._cls("p:MyErr", "MyErr", ["Exception"])
        analyzer_edge = Edge.create(
            src="p:MyErr", dst="python:external:0-0:Exception:unresolved",
            edge_type="extends", line=1, origin="python", origin_run_id="a-run",
            evidence_type="ast_extends", is_resolved=False, dst_ref=None,
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[klass], edges=[analyzer_edge],
        )
        result = link_inheritance(ctx)

        assert result.edges == []

    def test_analyzer_owned_bare_but_chokepoint_adds_deferred_dotted(self) -> None:
        """The ownership split, precise: for a class the analyzer partly handled
        (a bare external base) the chokepoint STILL adds the dotted/qualified
        base the analyzer deferred — ``class C(Enum, vendor.Widget)`` keeps the
        analyzer's ``Enum`` edge (bare, deferred to the analyzer) and gains the
        chokepoint's ``vendor.Widget`` edge (dotted, analyzer-deferred)."""
        klass = self._cls("p:C", "C", ["Enum", "vendor.Widget"])
        analyzer_edge = Edge.create(
            src="p:C", dst="python:enum:0-0:Enum:unresolved",
            edge_type="extends", line=1, origin="python", origin_run_id="a-run",
            evidence_type="ast_extends", is_resolved=False,
            dst_ref=ExternalRef(lang="python", module_path="enum", name="Enum"),
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[klass], edges=[analyzer_edge],
        )
        result = link_inheritance(ctx)

        # Only the dotted base is added; the bare Enum stays analyzer-owned.
        assert len(result.edges) == 1
        assert result.edges[0].dst == "python:external:0-0:Widget:unresolved"

    def test_duplicate_external_base_same_name_emits_once(self) -> None:
        """Two external bases whose last segment is the same name collapse to a
        single external edge (they are indistinguishable at the sentinel level)."""
        klass = self._cls("p:Foo", "Foo", ["a.Widget", "b.Widget"])
        ctx = LinkerContext(repo_root=Path("/"), symbols=[klass], edges=[])
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst == "python:external:0-0:Widget:unresolved"

    def test_garbage_base_name_not_externalized(self) -> None:
        """Ruby dynamic superclass (``class Foo < Struct.new(:a)``) records the
        raw expression text as a base_classes string; the chokepoint must not
        mint a garbage external edge — only syntactically-valid identifiers
        externalize."""
        klass = self._cls(
            "r:Point", "Point", ["Struct.new(:a)"], language="ruby", path="/p.rb",
        )
        ctx = LinkerContext(repo_root=Path("/"), symbols=[klass], edges=[])
        result = link_inheritance(ctx)

        assert result.edges == []

    def test_cross_language_external_bases_all_emit(self) -> None:
        """The chokepoint win: a Kotlin, a Ruby, and a Python class each with an
        external base ALL get external extends edges in one pass — no
        per-analyzer fallback required."""
        kt = self._cls("k:A", "A", ["KtBase"], language="kotlin", path="/a.kt")
        rb = self._cls("r:B", "B", ["RbBase"], language="ruby", path="/b.rb")
        py = self._cls("p:C", "C", ["PyBase"], language="python", path="/c.py")
        ctx = LinkerContext(repo_root=Path("/"), symbols=[kt, rb, py], edges=[])
        result = link_inheritance(ctx)

        dsts = {e.src: e.dst for e in result.edges if e.edge_type == "extends"}
        assert dsts["k:A"] == "kotlin:external:0-0:KtBase:unresolved"
        assert dsts["r:B"] == "ruby:external:0-0:RbBase:unresolved"
        assert dsts["p:C"] == "python:external:0-0:PyBase:unresolved"

    def test_dangling_existing_edge_dst_does_not_block_new_external(self) -> None:
        """``_edge_target_name`` returns None for an extends edge whose dst is
        neither an in-tree symbol nor an unresolved id — that edge contributes
        no dedup name, and a fresh external base still emits."""
        klass = self._cls("p:Foo", "Foo", ["RealExternal"])
        dangling = Edge.create(
            src="p:Foo", dst="sym:GoneMissing", edge_type="extends", line=1,
            origin="x", origin_run_id="x-run",
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[klass], edges=[dangling],
        )
        result = link_inheritance(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst == "python:external:0-0:RealExternal:unresolved"
