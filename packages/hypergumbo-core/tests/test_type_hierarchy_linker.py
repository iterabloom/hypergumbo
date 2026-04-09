# SPDX-License-Identifier: AGPL-3.0-or-later
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

    def test_concrete_types_with_same_name_do_not_cross_dispatch(
        self,
    ) -> None:
        """Structs in different files sharing a type name don't cross-dispatch.

        Go regression: multiple packages can define a struct named 'Notifier',
        each with a 'Notify()' method.  The structural interface matcher
        creates 'implements' edges from each concrete Notifier to a single
        interface (e.g., notify.Notifier).  The type hierarchy linker must
        create dispatches_to edges from the interface method to each concrete
        method — but must NOT create edges between concrete methods.

        Previously, ``class_id_by_name`` collapsed all classes with the same
        name to a single ID (first-match-wins), so ``methods_by_class`` for
        the interface ID incorrectly included every concrete struct's method.
        Then each concrete method was treated as a 'parent method' of the
        interface, and ``_find_implementing_methods_indexed`` filtered by
        class *name* (not ID), causing every concrete method to be emitted
        as an override of every other concrete method.  For N implementers,
        this created O(N²) false concrete→concrete edges.
        """
        # Interface: notify.Notifier at notify/notify.go
        iface = Symbol(
            id="go:/app/notify/notify.go:60-70:Notifier:interface",
            name="Notifier",
            kind="interface",
            language="go",
            path="/app/notify/notify.go",
            span=Span(start_line=60, end_line=70, start_col=0, end_col=1),
            origin="go-v1",
            origin_run_id="test",
        )
        iface_method = Symbol(
            id="go:/app/notify/notify.go:62-62:Notifier.Notify:method",
            name="Notifier.Notify",
            kind="method",
            language="go",
            path="/app/notify/notify.go",
            span=Span(start_line=62, end_line=62, start_col=4, end_col=50),
            origin="go-v1",
            origin_run_id="test",
        )

        # 3 concrete Notifier structs in different packages, all named
        # "Notifier" within their package, each with a Notify method.
        concretes = []
        concrete_methods = []
        for pkg in ("discord", "slack", "webhook"):
            struct = Symbol(
                id=f"go:/app/notify/{pkg}/{pkg}.go:10-20:Notifier:struct",
                name="Notifier",
                kind="struct",
                language="go",
                path=f"/app/notify/{pkg}/{pkg}.go",
                span=Span(
                    start_line=10, end_line=20, start_col=0, end_col=1,
                ),
                origin="go-v1",
                origin_run_id="test",
            )
            method = Symbol(
                id=f"go:/app/notify/{pkg}/{pkg}.go:30-40:Notifier.Notify:method",
                name="Notifier.Notify",
                kind="method",
                language="go",
                path=f"/app/notify/{pkg}/{pkg}.go",
                span=Span(
                    start_line=30, end_line=40, start_col=0, end_col=1,
                ),
                origin="go-v1",
                origin_run_id="test",
            )
            concretes.append(struct)
            concrete_methods.append(method)

        # Each concrete struct implements the interface
        implements_edges = [
            Edge.create(
                src=c.id,
                dst=iface.id,
                edge_type="implements",
                line=10,
                origin="go-v1",
                evidence_type="ast_implements",
            )
            for c in concretes
        ]

        symbols = [iface, iface_method] + concretes + concrete_methods
        ctx = LinkerContext(
            repo_root="/app",
            symbols=symbols,
            edges=implements_edges,
        )

        result = link_type_hierarchy(ctx)

        # Expected: 3 dispatches_to edges (interface → each concrete)
        # Buggy: 3 correct + 6 concrete→concrete false positives (9 total)
        iface_to_concrete = [
            e for e in result.edges
            if e.src == iface_method.id and e.edge_type == "dispatches_to"
        ]
        concrete_to_concrete = [
            e for e in result.edges
            if e.src in {m.id for m in concrete_methods}
            and e.edge_type == "dispatches_to"
        ]

        assert len(iface_to_concrete) == 3, (
            f"Expected 3 interface→concrete edges, got "
            f"{len(iface_to_concrete)}"
        )
        assert len(concrete_to_concrete) == 0, (
            f"Concrete methods should not dispatch to each other, got "
            f"{len(concrete_to_concrete)} false edges: "
            f"{[(e.src, e.dst) for e in concrete_to_concrete]}"
        )


class TestResolveMethodClassId:
    """Tests for _resolve_method_class_id helper."""

    def test_returns_none_for_method_with_no_class_name(self) -> None:
        """Method whose name is not qualified returns None."""
        from hypergumbo_core.linkers.type_hierarchy import (
            _resolve_method_class_id,
        )

        method = Symbol(
            id="py:/app/mod.py:1-5:bare_function:method",
            name="bare_function",
            kind="method",
            language="python",
            path="/app/mod.py",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="py-v1",
            origin_run_id="test",
        )
        assert _resolve_method_class_id(method, {}, {}) is None

    def test_returns_none_for_unknown_class_name(self) -> None:
        """Method whose class name has no matching class returns None.

        This happens when a method references an external class that
        has no Symbol in the graph (e.g., stdlib types, vendor code).
        """
        from hypergumbo_core.linkers.type_hierarchy import (
            _resolve_method_class_id,
        )

        method = Symbol(
            id="py:/app/mod.py:1-5:External.foo:method",
            name="External.foo",
            kind="method",
            language="python",
            path="/app/mod.py",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="py-v1",
            origin_run_id="test",
        )
        # class_ids_by_name is empty — class "External" is not a known class
        assert _resolve_method_class_id(method, {}, {}) is None

    def test_falls_back_to_first_candidate_when_no_same_file_match(
        self,
    ) -> None:
        """When no candidate class is in the method's file, use first.

        Go allows defining methods in a different file than their
        struct (same package).  When the same-file heuristic fails,
        we fall back to the first candidate, preserving historical
        behavior for cross-file cases.
        """
        from hypergumbo_core.linkers.type_hierarchy import (
            _resolve_method_class_id,
        )

        struct_a = Symbol(
            id="go:/app/types.go:1-10:Store:struct",
            name="Store",
            kind="struct",
            language="go",
            path="/app/types.go",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="go-v1",
            origin_run_id="test",
        )
        # Method defined in a different file than the struct
        method = Symbol(
            id="go:/app/methods.go:1-5:Store.Get:method",
            name="Store.Get",
            kind="method",
            language="go",
            path="/app/methods.go",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="go-v1",
            origin_run_id="test",
        )
        class_symbols = {struct_a.id: struct_a}
        class_ids_by_name = {"Store": [struct_a.id]}

        result = _resolve_method_class_id(
            method, class_ids_by_name, class_symbols,
        )
        assert result == struct_a.id


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


class TestTestFileConfidencePenalty:
    """Tests for test-file dispatches_to confidence penalty (WI-supok)."""

    def test_production_override_full_confidence(self) -> None:
        """Override in production code gets full 0.85 confidence."""
        parent = Symbol(
            id="java:/app/Service.java:1-5:Service:class",
            name="Service", kind="class", language="java",
            path="/app/Service.java",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        parent_method = Symbol(
            id="java:/app/Service.java:2-4:Service.process:method",
            name="Service.process", kind="method", language="java",
            path="/app/Service.java",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        child = Symbol(
            id="java:/app/ServiceImpl.java:1-5:ServiceImpl:class",
            name="ServiceImpl", kind="class", language="java",
            path="/app/ServiceImpl.java",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        child_method = Symbol(
            id="java:/app/ServiceImpl.java:2-4:ServiceImpl.process:method",
            name="ServiceImpl.process", kind="method", language="java",
            path="/app/ServiceImpl.java",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        extends_edge = Edge.create(
            src=child.id, dst=parent.id, edge_type="extends", line=1,
        )
        ctx = LinkerContext(
            symbols=[parent, parent_method, child, child_method],
            edges=[extends_edge], repo_root=None,
        )
        result = link_type_hierarchy(ctx)
        dispatch_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        assert len(dispatch_edges) == 1
        assert dispatch_edges[0].confidence == 0.85

    def test_many_overrides_scale_confidence_by_inverse_sqrt(self) -> None:
        """With N overrides, confidence scales as base/sqrt(N) (WI-kabom).

        When an interface has many implementors (e.g., 19 Notifier impls),
        each dispatches_to edge should have proportionally lower confidence
        to reduce ranking noise from N*M dispatch edges.
        """
        import math

        parent = Symbol(
            id="java:/app/Notifier.java:1-5:Notifier:interface",
            name="Notifier", kind="interface", language="java",
            path="/app/Notifier.java",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        parent_method = Symbol(
            id="java:/app/Notifier.java:2-4:Notifier.notify:method",
            name="Notifier.notify", kind="method", language="java",
            path="/app/Notifier.java",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        # Create 8 implementors — enough to clearly show 1/sqrt(N) scaling.
        impl_symbols: list[Symbol] = []
        impl_methods: list[Symbol] = []
        impl_edges: list[Edge] = []
        for i in range(8):
            cls = Symbol(
                id=f"java:/app/Impl{i}.java:1-5:Impl{i}:class",
                name=f"Impl{i}", kind="class", language="java",
                path=f"/app/Impl{i}.java",
                span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
                origin="java-v1", origin_run_id="test",
            )
            method = Symbol(
                id=f"java:/app/Impl{i}.java:2-4:Impl{i}.notify:method",
                name=f"Impl{i}.notify", kind="method", language="java",
                path=f"/app/Impl{i}.java",
                span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
                origin="java-v1", origin_run_id="test",
            )
            edge = Edge.create(
                src=cls.id, dst=parent.id, edge_type="implements", line=1,
            )
            impl_symbols.append(cls)
            impl_methods.append(method)
            impl_edges.append(edge)

        ctx = LinkerContext(
            symbols=[parent, parent_method] + impl_symbols + impl_methods,
            edges=impl_edges, repo_root=None,
        )
        result = link_type_hierarchy(ctx)
        dispatch_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        assert len(dispatch_edges) == 8

        # With 8 overrides: 0.85 / sqrt(8) ≈ 0.30
        expected = 0.85 / math.sqrt(8)
        for edge in dispatch_edges:
            assert abs(edge.confidence - expected) < 0.01, (
                f"Expected confidence ~{expected:.2f} for 8 overrides, "
                f"got {edge.confidence}"
            )

    def test_single_override_keeps_full_confidence(self) -> None:
        """With only 1 override, confidence stays at 0.85 (no scaling)."""
        parent = Symbol(
            id="java:/app/Base.java:1-5:Base:class",
            name="Base", kind="class", language="java",
            path="/app/Base.java",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        parent_method = Symbol(
            id="java:/app/Base.java:2-4:Base.run:method",
            name="Base.run", kind="method", language="java",
            path="/app/Base.java",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        child = Symbol(
            id="java:/app/Derived.java:1-5:Derived:class",
            name="Derived", kind="class", language="java",
            path="/app/Derived.java",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        child_method = Symbol(
            id="java:/app/Derived.java:2-4:Derived.run:method",
            name="Derived.run", kind="method", language="java",
            path="/app/Derived.java",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        extends_edge = Edge.create(
            src=child.id, dst=parent.id, edge_type="extends", line=1,
        )
        ctx = LinkerContext(
            symbols=[parent, parent_method, child, child_method],
            edges=[extends_edge], repo_root=None,
        )
        result = link_type_hierarchy(ctx)
        dispatch_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        assert len(dispatch_edges) == 1
        assert dispatch_edges[0].confidence == 0.85

    def test_test_file_override_penalized(self) -> None:
        """Override in test file gets reduced 0.30 confidence (WI-supok)."""
        parent = Symbol(
            id="java:/app/Service.java:1-5:Service:class",
            name="Service", kind="class", language="java",
            path="/app/Service.java",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        parent_method = Symbol(
            id="java:/app/Service.java:2-4:Service.process:method",
            name="Service.process", kind="method", language="java",
            path="/app/Service.java",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        test_child = Symbol(
            id="java:/test/ServiceTest.java:1-5:TestImpl:class",
            name="TestImpl", kind="class", language="java",
            path="/test/ServiceTest.java",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        test_method = Symbol(
            id="java:/test/ServiceTest.java:2-4:TestImpl.process:method",
            name="TestImpl.process", kind="method", language="java",
            path="/test/ServiceTest.java",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        extends_edge = Edge.create(
            src=test_child.id, dst=parent.id, edge_type="extends", line=1,
        )
        ctx = LinkerContext(
            symbols=[parent, parent_method, test_child, test_method],
            edges=[extends_edge], repo_root=None,
        )
        result = link_type_hierarchy(ctx)
        dispatch_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        assert len(dispatch_edges) == 1
        assert dispatch_edges[0].confidence == 0.30, (
            "Test file override should get 0.30 confidence penalty"
        )


class TestPerLanguageConcreteExtendsDispatch:
    """Tests for WI-sukav A1: per-language extends-virtual-dispatch flag.

    Some languages model concrete inheritance/composition with an `extends`
    edge but do NOT use virtual dispatch through it.  For these languages,
    the type hierarchy linker must NOT create dispatches_to edges from a
    parent's method to a child's same-named method via an `extends` edge.

    Languages with virtual dispatch through `extends` (current behavior preserved):
    - Java, Kotlin, Python, Ruby, Scala, Swift

    Languages WITHOUT virtual dispatch through `extends` (filtered):
    - Go (struct embedding is composition, not inheritance)
    - C++ (only `virtual` methods dispatch polymorphically)
    - Rust (no inheritance; struct/trait are separate concepts)
    - C# (pessimistic until per-method virtual/override tracking lands)

    `implements` edges are unaffected — interface satisfaction is virtual
    dispatch in every language that has the concept.
    """

    @staticmethod
    def _make_struct(
        lang: str, pkg: str, sname: str, mname: str
    ) -> tuple[Symbol, Symbol]:
        """Build a (struct, method) pair under /app/<pkg>/<pkg>.<ext>."""
        ext = {
            "go": "go", "cpp": "cpp", "rust": "rs",
            "csharp": "cs", "java": "java", "python": "py",
        }[lang]
        path = f"/app/{pkg}/{pkg}.{ext}"
        struct = Symbol(
            id=f"{lang}:{path}:1-50:{sname}:struct",
            name=sname, kind="struct", language=lang, path=path,
            span=Span(start_line=1, end_line=50, start_col=0, end_col=1),
            origin=f"{lang}-v1", origin_run_id="test",
        )
        method = Symbol(
            id=f"{lang}:{path}:10-15:{sname}.{mname}:method",
            name=f"{sname}.{mname}", kind="method", language=lang, path=path,
            span=Span(start_line=10, end_line=15, start_col=0, end_col=1),
            origin=f"{lang}-v1", origin_run_id="test",
        )
        return struct, method

    def _run_concrete_extends(
        self, lang: str
    ) -> list[Edge]:
        """Build a parent struct + 4 children that 'extend' it, each with
        its own override.  Returns the dispatches_to edges produced.

        Mirrors alertmanager's timeinterval.go: InclusiveRange and 4
        embedders (WeekdayRange/DayOfMonthRange/MonthRange/YearRange),
        each with their own MarshalText.
        """
        parent_s, parent_m = self._make_struct(
            lang, "timeinterval_parent", "InclusiveRange", "MarshalText",
        )
        children = [
            self._make_struct(lang, f"timeinterval_{c}", c, "MarshalText")
            for c in (
                "WeekdayRange", "DayOfMonthRange", "MonthRange", "YearRange",
            )
        ]
        symbols = [parent_s, parent_m]
        edges: list[Edge] = []
        for cs, cm in children:
            symbols.extend([cs, cm])
            edges.append(Edge.create(
                src=cs.id, dst=parent_s.id, edge_type="extends",
                line=1, origin=f"{lang}-v1", evidence_type="ast_extends",
            ))
        ctx = LinkerContext(
            repo_root="/app", symbols=symbols, edges=edges,
        )
        result = link_type_hierarchy(ctx)
        return [e for e in result.edges if e.edge_type == "dispatches_to"]

    def test_go_concrete_extends_does_not_dispatch(self) -> None:
        """Go struct embedding (extends) must not produce dispatches_to.

        Regression for WI-sukav: alertmanager timeinterval.go had 4 false
        InclusiveRange.* dispatches_to edges because the 4 sibling structs
        embed InclusiveRange, and the linker treated their own MarshalText
        methods as polymorphic overrides.  In Go, embedding is composition,
        not virtual dispatch — calling `ir.MarshalText()` on a *InclusiveRange
        always lands in InclusiveRange.MarshalText regardless of any embedder.
        """
        edges = self._run_concrete_extends("go")
        assert edges == [], (
            f"Go concrete extends must not produce dispatches_to edges; "
            f"got {len(edges)}: {[(e.src, e.dst) for e in edges]}"
        )

    def test_cpp_concrete_extends_does_not_dispatch(self) -> None:
        """C++ inheritance without `virtual` is statically resolved.

        Pessimistic default: until per-method virtual tracking lands,
        C++ extends must not produce dispatches_to. False negatives on
        truly virtual methods are preferable to the false-positive flood
        observed for non-virtual methods (which dominate most C++ code).
        """
        edges = self._run_concrete_extends("cpp")
        assert edges == [], (
            f"C++ concrete extends must not produce dispatches_to edges; "
            f"got {len(edges)}"
        )

    def test_rust_concrete_extends_does_not_dispatch(self) -> None:
        """Rust has no inheritance; only trait dispatch is virtual."""
        edges = self._run_concrete_extends("rust")
        assert edges == [], (
            f"Rust concrete extends must not produce dispatches_to edges; "
            f"got {len(edges)}"
        )

    def test_csharp_concrete_extends_does_not_dispatch(self) -> None:
        """C# is pessimistic until virtual/override keyword tracking lands."""
        edges = self._run_concrete_extends("csharp")
        assert edges == [], (
            f"C# concrete extends must not produce dispatches_to edges; "
            f"got {len(edges)}"
        )

    def test_java_concrete_extends_still_dispatches(self) -> None:
        """Java retains current behavior: concrete extends → dispatches_to.

        Lock the per-language split: only the no-virtual-dispatch
        languages are filtered.  Java methods are virtual by default.
        """
        edges = self._run_concrete_extends("java")
        assert len(edges) == 4, (
            f"Java concrete extends must still produce 4 dispatches_to "
            f"edges (one per child override); got {len(edges)}"
        )

    def test_go_implements_still_dispatches(self) -> None:
        """`implements` edges are unaffected by the per-language gate.

        Interface satisfaction is virtual dispatch in every language that
        has the concept.  Go's structural interface matcher emits
        `implements` edges that must continue to drive dispatches_to.
        """
        iface = Symbol(
            id="go:/app/notify/notify.go:60-70:Notifier:interface",
            name="Notifier", kind="interface", language="go",
            path="/app/notify/notify.go",
            span=Span(start_line=60, end_line=70, start_col=0, end_col=1),
            origin="go-v1", origin_run_id="test",
        )
        iface_method = Symbol(
            id="go:/app/notify/notify.go:62-62:Notifier.Notify:method",
            name="Notifier.Notify", kind="method", language="go",
            path="/app/notify/notify.go",
            span=Span(start_line=62, end_line=62, start_col=4, end_col=50),
            origin="go-v1", origin_run_id="test",
        )
        impl_struct, impl_method = self._make_struct(
            "go", "discord", "Notifier", "Notify",
        )
        implements_edge = Edge.create(
            src=impl_struct.id, dst=iface.id, edge_type="implements",
            line=10, origin="go-v1", evidence_type="ast_implements",
        )
        ctx = LinkerContext(
            repo_root="/app",
            symbols=[iface, iface_method, impl_struct, impl_method],
            edges=[implements_edge],
        )
        result = link_type_hierarchy(ctx)
        dispatches = [
            e for e in result.edges if e.edge_type == "dispatches_to"
        ]
        assert len(dispatches) == 1, (
            f"Go implements edges must still produce dispatches_to; "
            f"got {len(dispatches)}"
        )
        assert dispatches[0].src == iface_method.id
        assert dispatches[0].dst == impl_method.id

    def test_unknown_language_extends_still_dispatches(self) -> None:
        """Languages not in the no-virtual list keep current behavior.

        Default-allow keeps the linker conservative for analyzers we
        haven't yet investigated.  Only explicit no-virtual-dispatch
        languages are filtered.
        """
        edges = self._run_concrete_extends("python")
        assert len(edges) == 4, (
            "Python (default-allow) must still produce dispatches_to "
            "via concrete extends"
        )

    def test_extends_with_unknown_src_symbol_defaults_to_allow(self) -> None:
        """An extends edge whose src symbol is not in the symbol list
        falls back to default-allow behavior (no language to filter on)."""
        parent = Symbol(
            id="java:/app/P.java:1-5:P:class",
            name="P", kind="class", language="java", path="/app/P.java",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        parent_method = Symbol(
            id="java:/app/P.java:2-4:P.foo:method",
            name="P.foo", kind="method", language="java", path="/app/P.java",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        child = Symbol(
            id="java:/app/C.java:1-5:C:class",
            name="C", kind="class", language="java", path="/app/C.java",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        child_method = Symbol(
            id="java:/app/C.java:2-4:C.foo:method",
            name="C.foo", kind="method", language="java", path="/app/C.java",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
            origin="java-v1", origin_run_id="test",
        )
        # Edge src points to an ID that is NOT among symbols passed to
        # build_inheritance_maps — exercise the fallback branch.
        extends_edge = Edge.create(
            src="java:/app/UNKNOWN.java:1-5:Ghost:class",
            dst=parent.id, edge_type="extends", line=1,
        )
        # The actual child is wired via a second normal extends edge
        # so the test still produces *some* dispatch graph state.
        normal_extends = Edge.create(
            src=child.id, dst=parent.id, edge_type="extends", line=1,
        )
        ctx = LinkerContext(
            repo_root="/app",
            symbols=[parent, parent_method, child, child_method],
            edges=[extends_edge, normal_extends],
        )
        result = link_type_hierarchy(ctx)
        dispatches = [
            e for e in result.edges if e.edge_type == "dispatches_to"
        ]
        # The known child still drives a dispatches_to; the unknown-src
        # extends is admitted under default-allow but contributes nothing
        # because its child symbol is not in the graph.
        assert len(dispatches) == 1
        assert dispatches[0].dst == child_method.id
