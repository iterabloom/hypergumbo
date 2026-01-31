"""Tests for type hierarchy linker.

The type hierarchy linker creates `dispatches_to` edges from interface/abstract class
methods to their concrete implementations, enabling polymorphic call resolution.

Example use case:
- Interface `UserService` with method `findUser()`
- Class `UserServiceImpl implements UserService` with `findUser()`
- Existing call edge: `controller.findUser()` -> `UserService.findUser`
- New edge: `UserService.findUser` --dispatches_to--> `UserServiceImpl.findUser`

This allows code navigation tools to show "this interface method is implemented by..."
"""

import pytest

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.type_hierarchy import (
    link_type_hierarchy,
    build_inheritance_maps,
    find_implementing_methods,
    PASS_ID,
)
from hypergumbo_core.linkers.registry import LinkerContext


class TestBuildInheritanceMaps:
    """Tests for building inheritance maps from extends/implements edges."""

    def test_extends_edge_creates_parent_to_child_map(self) -> None:
        """Extends edges are indexed as parent -> [children]."""
        parent = Symbol(
            id="java:/app/Person.java:1-10:Person:class",
            name="Person",
            kind="class",
            language="java",
            path="/app/Person.java",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        child = Symbol(
            id="java:/app/Employee.java:1-20:Employee:class",
            name="Employee",
            kind="class",
            language="java",
            path="/app/Employee.java",
            span=Span(start_line=1, end_line=20, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        extends_edge = Edge.create(
            src=child.id,
            dst=parent.id,
            edge_type="extends",
            line=1,
            origin="java-v1",
            evidence_type="ast_extends",
        )

        parent_to_children, interface_to_impls = build_inheritance_maps(
            [parent, child], [extends_edge]
        )

        assert parent.id in parent_to_children
        assert child.id in parent_to_children[parent.id]

    def test_implements_edge_creates_interface_to_impl_map(self) -> None:
        """Implements edges are indexed as interface -> [implementations]."""
        interface = Symbol(
            id="java:/app/UserService.java:1-10:UserService:interface",
            name="UserService",
            kind="interface",
            language="java",
            path="/app/UserService.java",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        impl = Symbol(
            id="java:/app/UserServiceImpl.java:1-50:UserServiceImpl:class",
            name="UserServiceImpl",
            kind="class",
            language="java",
            path="/app/UserServiceImpl.java",
            span=Span(start_line=1, end_line=50, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        implements_edge = Edge.create(
            src=impl.id,
            dst=interface.id,
            edge_type="implements",
            line=1,
            origin="java-v1",
            evidence_type="ast_implements",
        )

        parent_to_children, interface_to_impls = build_inheritance_maps(
            [interface, impl], [implements_edge]
        )

        assert interface.id in interface_to_impls
        assert impl.id in interface_to_impls[interface.id]


class TestFindImplementingMethods:
    """Tests for finding method implementations across class hierarchy."""

    def test_finds_override_in_child_class(self) -> None:
        """Method in child class with same name is found as override."""
        parent_class = Symbol(
            id="java:/app/Parent.java:1-20:Parent:class",
            name="Parent",
            kind="class",
            language="java",
            path="/app/Parent.java",
            span=Span(start_line=1, end_line=20, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        parent_method = Symbol(
            id="java:/app/Parent.java:5-10:Parent.process:method",
            name="Parent.process",
            kind="method",
            language="java",
            path="/app/Parent.java",
            span=Span(start_line=5, end_line=10, start_col=4, end_col=5),
            origin="java-v1",
            origin_run_id="test",
        )
        child_class = Symbol(
            id="java:/app/Child.java:1-30:Child:class",
            name="Child",
            kind="class",
            language="java",
            path="/app/Child.java",
            span=Span(start_line=1, end_line=30, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        child_method = Symbol(
            id="java:/app/Child.java:10-20:Child.process:method",
            name="Child.process",
            kind="method",
            language="java",
            path="/app/Child.java",
            span=Span(start_line=10, end_line=20, start_col=4, end_col=5),
            origin="java-v1",
            origin_run_id="test",
        )

        extends_edge = Edge.create(
            src=child_class.id,
            dst=parent_class.id,
            edge_type="extends",
            line=1,
            origin="java-v1",
            evidence_type="ast_extends",
        )

        parent_to_children, _ = build_inheritance_maps(
            [parent_class, child_class, parent_method, child_method],
            [extends_edge],
        )

        overrides = find_implementing_methods(
            parent_method,
            parent_class,
            parent_to_children,
            [parent_class, child_class, parent_method, child_method],
        )

        assert len(overrides) == 1
        assert overrides[0].id == child_method.id


class TestLinkTypeHierarchy:
    """Tests for the full type hierarchy linking process."""

    def test_creates_dispatches_to_edge_for_override(self) -> None:
        """Parent method gets dispatches_to edge to child override."""
        parent_class = Symbol(
            id="java:/app/Animal.java:1-20:Animal:class",
            name="Animal",
            kind="class",
            language="java",
            path="/app/Animal.java",
            span=Span(start_line=1, end_line=20, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        parent_method = Symbol(
            id="java:/app/Animal.java:5-10:Animal.speak:method",
            name="Animal.speak",
            kind="method",
            language="java",
            path="/app/Animal.java",
            span=Span(start_line=5, end_line=10, start_col=4, end_col=5),
            origin="java-v1",
            origin_run_id="test",
        )
        child_class = Symbol(
            id="java:/app/Dog.java:1-30:Dog:class",
            name="Dog",
            kind="class",
            language="java",
            path="/app/Dog.java",
            span=Span(start_line=1, end_line=30, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        child_method = Symbol(
            id="java:/app/Dog.java:10-20:Dog.speak:method",
            name="Dog.speak",
            kind="method",
            language="java",
            path="/app/Dog.java",
            span=Span(start_line=10, end_line=20, start_col=4, end_col=5),
            origin="java-v1",
            origin_run_id="test",
        )

        extends_edge = Edge.create(
            src=child_class.id,
            dst=parent_class.id,
            edge_type="extends",
            line=1,
            origin="java-v1",
            evidence_type="ast_extends",
        )

        symbols = [parent_class, parent_method, child_class, child_method]
        edges = [extends_edge]
        ctx = LinkerContext(
            repo_root="/app",
            symbols=symbols,
            edges=edges,
        )

        result = link_type_hierarchy(ctx)

        # Should create one dispatches_to edge: Animal.speak -> Dog.speak
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == parent_method.id
        assert edge.dst == child_method.id
        assert edge.edge_type == "dispatches_to"

    def test_interface_method_to_implementation(self) -> None:
        """Interface method gets dispatches_to edge to implementing class method."""
        interface = Symbol(
            id="java:/app/Service.java:1-10:Service:interface",
            name="Service",
            kind="interface",
            language="java",
            path="/app/Service.java",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        interface_method = Symbol(
            id="java:/app/Service.java:3-3:Service.execute:method",
            name="Service.execute",
            kind="method",
            language="java",
            path="/app/Service.java",
            span=Span(start_line=3, end_line=3, start_col=4, end_col=30),
            origin="java-v1",
            origin_run_id="test",
        )
        impl_class = Symbol(
            id="java:/app/ServiceImpl.java:1-50:ServiceImpl:class",
            name="ServiceImpl",
            kind="class",
            language="java",
            path="/app/ServiceImpl.java",
            span=Span(start_line=1, end_line=50, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        impl_method = Symbol(
            id="java:/app/ServiceImpl.java:10-20:ServiceImpl.execute:method",
            name="ServiceImpl.execute",
            kind="method",
            language="java",
            path="/app/ServiceImpl.java",
            span=Span(start_line=10, end_line=20, start_col=4, end_col=5),
            origin="java-v1",
            origin_run_id="test",
        )

        implements_edge = Edge.create(
            src=impl_class.id,
            dst=interface.id,
            edge_type="implements",
            line=1,
            origin="java-v1",
            evidence_type="ast_implements",
        )

        symbols = [interface, interface_method, impl_class, impl_method]
        edges = [implements_edge]
        ctx = LinkerContext(
            repo_root="/app",
            symbols=symbols,
            edges=edges,
        )

        result = link_type_hierarchy(ctx)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == interface_method.id
        assert edge.dst == impl_method.id
        assert edge.edge_type == "dispatches_to"

    def test_no_edge_when_no_override(self) -> None:
        """No edge created when child doesn't override parent method."""
        parent_class = Symbol(
            id="java:/app/Parent.java:1-20:Parent:class",
            name="Parent",
            kind="class",
            language="java",
            path="/app/Parent.java",
            span=Span(start_line=1, end_line=20, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        parent_method = Symbol(
            id="java:/app/Parent.java:5-10:Parent.compute:method",
            name="Parent.compute",
            kind="method",
            language="java",
            path="/app/Parent.java",
            span=Span(start_line=5, end_line=10, start_col=4, end_col=5),
            origin="java-v1",
            origin_run_id="test",
        )
        child_class = Symbol(
            id="java:/app/Child.java:1-30:Child:class",
            name="Child",
            kind="class",
            language="java",
            path="/app/Child.java",
            span=Span(start_line=1, end_line=30, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        # Child has a DIFFERENT method, not an override
        child_method = Symbol(
            id="java:/app/Child.java:10-20:Child.validate:method",
            name="Child.validate",
            kind="method",
            language="java",
            path="/app/Child.java",
            span=Span(start_line=10, end_line=20, start_col=4, end_col=5),
            origin="java-v1",
            origin_run_id="test",
        )

        extends_edge = Edge.create(
            src=child_class.id,
            dst=parent_class.id,
            edge_type="extends",
            line=1,
            origin="java-v1",
            evidence_type="ast_extends",
        )

        symbols = [parent_class, parent_method, child_class, child_method]
        edges = [extends_edge]
        ctx = LinkerContext(
            repo_root="/app",
            symbols=symbols,
            edges=edges,
        )

        result = link_type_hierarchy(ctx)

        assert len(result.edges) == 0

    def test_multiple_implementations(self) -> None:
        """Parent method with multiple children creates multiple edges."""
        parent_class = Symbol(
            id="java:/app/Shape.java:1-20:Shape:class",
            name="Shape",
            kind="class",
            language="java",
            path="/app/Shape.java",
            span=Span(start_line=1, end_line=20, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        parent_method = Symbol(
            id="java:/app/Shape.java:5-10:Shape.draw:method",
            name="Shape.draw",
            kind="method",
            language="java",
            path="/app/Shape.java",
            span=Span(start_line=5, end_line=10, start_col=4, end_col=5),
            origin="java-v1",
            origin_run_id="test",
        )
        circle_class = Symbol(
            id="java:/app/Circle.java:1-30:Circle:class",
            name="Circle",
            kind="class",
            language="java",
            path="/app/Circle.java",
            span=Span(start_line=1, end_line=30, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        circle_method = Symbol(
            id="java:/app/Circle.java:10-20:Circle.draw:method",
            name="Circle.draw",
            kind="method",
            language="java",
            path="/app/Circle.java",
            span=Span(start_line=10, end_line=20, start_col=4, end_col=5),
            origin="java-v1",
            origin_run_id="test",
        )
        square_class = Symbol(
            id="java:/app/Square.java:1-30:Square:class",
            name="Square",
            kind="class",
            language="java",
            path="/app/Square.java",
            span=Span(start_line=1, end_line=30, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        square_method = Symbol(
            id="java:/app/Square.java:10-20:Square.draw:method",
            name="Square.draw",
            kind="method",
            language="java",
            path="/app/Square.java",
            span=Span(start_line=10, end_line=20, start_col=4, end_col=5),
            origin="java-v1",
            origin_run_id="test",
        )

        extends_edge1 = Edge.create(
            src=circle_class.id,
            dst=parent_class.id,
            edge_type="extends",
            line=1,
            origin="java-v1",
            evidence_type="ast_extends",
        )
        extends_edge2 = Edge.create(
            src=square_class.id,
            dst=parent_class.id,
            edge_type="extends",
            line=1,
            origin="java-v1",
            evidence_type="ast_extends",
        )

        symbols = [
            parent_class, parent_method,
            circle_class, circle_method,
            square_class, square_method,
        ]
        edges = [extends_edge1, extends_edge2]
        ctx = LinkerContext(
            repo_root="/app",
            symbols=symbols,
            edges=edges,
        )

        result = link_type_hierarchy(ctx)

        assert len(result.edges) == 2
        dst_ids = {e.dst for e in result.edges}
        assert circle_method.id in dst_ids
        assert square_method.id in dst_ids
        for edge in result.edges:
            assert edge.src == parent_method.id
            assert edge.edge_type == "dispatches_to"


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_method_short_name_ruby_style(self) -> None:
        """Ruby-style Class#method extracts method name."""
        from hypergumbo_core.linkers.type_hierarchy import _get_method_short_name
        assert _get_method_short_name("UsersController#index") == "index"

    def test_get_method_short_name_plain(self) -> None:
        """Plain method name without separators returns unchanged."""
        from hypergumbo_core.linkers.type_hierarchy import _get_method_short_name
        assert _get_method_short_name("myMethod") == "myMethod"

    def test_get_class_name_from_meta(self) -> None:
        """Class name extracted from meta.class field."""
        from hypergumbo_core.linkers.type_hierarchy import _get_class_name_from_method

        method = Symbol(
            id="test:method",
            name="doSomething",
            kind="method",
            language="java",
            path="/app/Test.java",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=10),
            meta={"class": "MyController"},
            origin="test",
            origin_run_id="test",
        )
        assert _get_class_name_from_method(method) == "MyController"

    def test_get_class_name_from_ruby_qualified_name(self) -> None:
        """Class name extracted from Ruby-style qualified name."""
        from hypergumbo_core.linkers.type_hierarchy import _get_class_name_from_method

        method = Symbol(
            id="ruby:test:method",
            name="UsersController#show",
            kind="method",
            language="ruby",
            path="/app/users_controller.rb",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=10),
            origin="test",
            origin_run_id="test",
        )
        assert _get_class_name_from_method(method) == "UsersController"

    def test_get_class_name_returns_none_for_unqualified(self) -> None:
        """Returns None when class name cannot be determined."""
        from hypergumbo_core.linkers.type_hierarchy import _get_class_name_from_method

        method = Symbol(
            id="test:method",
            name="plainFunction",
            kind="method",
            language="python",
            path="/app/test.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=10),
            origin="test",
            origin_run_id="test",
        )
        assert _get_class_name_from_method(method) is None


class TestEdgeCases:
    """Tests for edge cases and early returns."""

    def test_no_inheritance_edges_returns_empty(self) -> None:
        """When no inheritance edges exist, linker returns empty result."""
        # Just some classes with no inheritance
        class1 = Symbol(
            id="java:/app/Foo.java:1-10:Foo:class",
            name="Foo",
            kind="class",
            language="java",
            path="/app/Foo.java",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        call_edge = Edge.create(
            src="test:caller",
            dst="test:callee",
            edge_type="calls",
            line=1,
            origin="test",
            evidence_type="test",
        )

        ctx = LinkerContext(
            repo_root="/app",
            symbols=[class1],
            edges=[call_edge],
        )

        result = link_type_hierarchy(ctx)
        assert len(result.edges) == 0

    def test_find_implementing_methods_no_children(self) -> None:
        """Returns empty list when class has no children."""
        parent_class = Symbol(
            id="java:/app/Parent.java:1-20:Parent:class",
            name="Parent",
            kind="class",
            language="java",
            path="/app/Parent.java",
            span=Span(start_line=1, end_line=20, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        parent_method = Symbol(
            id="java:/app/Parent.java:5-10:Parent.process:method",
            name="Parent.process",
            kind="method",
            language="java",
            path="/app/Parent.java",
            span=Span(start_line=5, end_line=10, start_col=4, end_col=5),
            origin="java-v1",
            origin_run_id="test",
        )

        # Empty parent_to_children - no children for this class
        parent_to_children: dict[str, list[str]] = {}

        overrides = find_implementing_methods(
            parent_method,
            parent_class,
            parent_to_children,
            [parent_class, parent_method],
        )

        assert overrides == []

    def test_linker_entry_point_called_via_registry(self) -> None:
        """Linker entry point is callable via registry."""
        import importlib

        from hypergumbo_core.linkers.registry import run_linker
        # Re-import the linker module to force re-registration
        # (needed when registry is cleared by other tests)
        import hypergumbo_core.linkers.type_hierarchy as th_module
        importlib.reload(th_module)

        ctx = LinkerContext(
            repo_root="/app",
            symbols=[],
            edges=[],
        )

        result = run_linker("type_hierarchy", ctx)
        assert result is not None
        assert result.edges == []


class TestDuplicateHandling:
    """Tests for duplicate edge prevention."""

    def test_duplicate_edges_prevented(self) -> None:
        """Same parent-child pair doesn't create duplicate edges.

        This can happen with diamond inheritance or complex hierarchies.
        """
        # Create a hierarchy where same method appears via multiple paths
        interface = Symbol(
            id="java:/app/Runnable.java:1-10:Runnable:interface",
            name="Runnable",
            kind="interface",
            language="java",
            path="/app/Runnable.java",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        interface_method = Symbol(
            id="java:/app/Runnable.java:3-3:Runnable.run:method",
            name="Runnable.run",
            kind="method",
            language="java",
            path="/app/Runnable.java",
            span=Span(start_line=3, end_line=3, start_col=4, end_col=30),
            origin="java-v1",
            origin_run_id="test",
        )
        impl_class = Symbol(
            id="java:/app/Worker.java:1-50:Worker:class",
            name="Worker",
            kind="class",
            language="java",
            path="/app/Worker.java",
            span=Span(start_line=1, end_line=50, start_col=0, end_col=1),
            origin="java-v1",
            origin_run_id="test",
        )
        impl_method = Symbol(
            id="java:/app/Worker.java:10-20:Worker.run:method",
            name="Worker.run",
            kind="method",
            language="java",
            path="/app/Worker.java",
            span=Span(start_line=10, end_line=20, start_col=4, end_col=5),
            origin="java-v1",
            origin_run_id="test",
        )

        # Create TWO implements edges (simulating duplicate in data)
        implements_edge1 = Edge.create(
            src=impl_class.id,
            dst=interface.id,
            edge_type="implements",
            line=1,
            origin="java-v1",
            evidence_type="ast_implements",
        )
        implements_edge2 = Edge.create(
            src=impl_class.id,
            dst=interface.id,
            edge_type="implements",
            line=2,  # Different line to make unique edge
            origin="java-v1",
            evidence_type="ast_implements",
        )

        symbols = [interface, interface_method, impl_class, impl_method]
        edges = [implements_edge1, implements_edge2]
        ctx = LinkerContext(
            repo_root="/app",
            symbols=symbols,
            edges=edges,
        )

        result = link_type_hierarchy(ctx)

        # Should still only have ONE dispatches_to edge despite duplicate implements
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == interface_method.id
        assert edge.dst == impl_method.id


class TestLinkerRegistration:
    """Tests for linker registration and activation."""

    def test_linker_registered(self) -> None:
        """Type hierarchy linker is registered with correct metadata."""
        import importlib

        from hypergumbo_core.linkers.registry import get_linker
        # Re-import the linker module to force re-registration
        # (needed when registry is cleared by other tests)
        import hypergumbo_core.linkers.type_hierarchy as th_module
        importlib.reload(th_module)

        linker = get_linker("type_hierarchy")
        assert linker is not None
        assert linker.name == "type_hierarchy"
        assert "dispatch" in linker.description.lower() or "hierarchy" in linker.description.lower()
