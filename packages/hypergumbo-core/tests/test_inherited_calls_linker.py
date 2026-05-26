# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the inherited_calls linker (WI-hatip / PR-2 of INV-nilud).

The linker resolves unresolved `calls` edges that carry an
``enclosing_class`` hint in ``Edge.meta`` by walking the ancestor chain
of the named class (using language-specific MRO semantics) and looking
for a matching short-name method. When found, it emits a resolved
``calls`` edge with ``evidence_type="ast_call_inherited"``.

PR-2 implements the Ruby/Groovy walker (``_walk_insertion_order``) only;
other languages stay unwalked until PR-3+ adds their dispatch rules.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.inherited_calls import (
    _MRO_WALKERS,
    _walk_insertion_order,
    _walk_single_then_interfaces,
    link_inherited_calls,
)
from hypergumbo_core.linkers.registry import LinkerContext


def _cls(sid: str, name: str, path: str = "/a.rb",
         language: str = "ruby") -> Symbol:
    return Symbol(
        id=sid, name=name, kind="class", language=language, path=path,
        span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=None,
    )


def _mod(sid: str, name: str, path: str = "/m.rb",
         language: str = "ruby") -> Symbol:
    return Symbol(
        id=sid, name=name, kind="module", language=language, path=path,
        span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=None,
    )


def _method(sid: str, qualified_name: str, path: str = "/a.rb",
            language: str = "ruby") -> Symbol:
    return Symbol(
        id=sid, name=qualified_name, kind="method", language=language,
        path=path,
        span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=None,
    )


def _caller(sid: str = "sym:Caller#run", language: str = "ruby") -> Symbol:
    return Symbol(
        id=sid, name="Caller#run", kind="method", language=language,
        path="/caller.rb",
        span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=None,
    )


def _edge(src: str, dst: str, edge_type: str, line: int = 1) -> Edge:
    return Edge.create(
        src=src, dst=dst, edge_type=edge_type, line=line,
        origin="test", origin_run_id="test",
    )


def _unresolved_call(src_id: str, callee_short: str,
                     enclosing_class: str, line: int = 1,
                     module_hint: str = "external") -> Edge:
    """Build an unresolved-calls edge with enclosing_class hint."""
    from hypergumbo_core.analyze.base import make_unresolved_edge
    return make_unresolved_edge(
        lang="ruby", src_id=src_id, callee_name=callee_short,
        line=line, pass_id="test-pass", run_id="test-run",
        module_hint=module_hint, enclosing_class=enclosing_class,
    )


# ---------------------------------------------------------------------------
# Walker unit tests (synthetic inheritance_index + method_index).
# ---------------------------------------------------------------------------


class TestWalkInsertionOrder:
    """BFS walk through inheritance edges in declaration order."""

    def test_direct_method_match_on_starting_class(self) -> None:
        """If the starting class itself has the method, return it immediately."""
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        foo = _cls("sym:Foo", "Foo")
        foo_bar = _method("sym:Foo#bar", "Foo#bar")
        idx = build_method_index(
            [foo, foo_bar],
            class_ids_by_name={"Foo": [foo.id]},
            class_symbols={foo.id: foo},
        )
        result = _walk_insertion_order(
            start_class_id=foo.id, callee_short_name="bar",
            inheritance_index={}, method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == foo_bar.id

    def test_one_hop_via_extends(self) -> None:
        """A class that doesn't define the method walks to its superclass."""
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        parent = _cls("sym:Parent", "Parent")
        parent_save = _method("sym:Parent#save", "Parent#save")
        child = _cls("sym:Child", "Child")
        idx = build_method_index(
            [parent, parent_save, child],
            class_ids_by_name={"Parent": [parent.id], "Child": [child.id]},
            class_symbols={parent.id: parent, child.id: child},
        )
        result = _walk_insertion_order(
            start_class_id=child.id, callee_short_name="save",
            inheritance_index={child.id: [(parent.id, "extends")]},
            method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == parent_save.id

    def test_three_hop_chain(self) -> None:
        """Walks past two intermediate classes to find the method."""
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        a = _cls("sym:A", "A")
        a_init = _method("sym:A#initialize", "A#initialize")
        b = _cls("sym:B", "B")
        c = _cls("sym:C", "C")
        idx = build_method_index(
            [a, a_init, b, c],
            class_ids_by_name={"A": [a.id], "B": [b.id], "C": [c.id]},
            class_symbols={a.id: a, b.id: b, c.id: c},
        )
        result = _walk_insertion_order(
            start_class_id=c.id, callee_short_name="initialize",
            inheritance_index={
                c.id: [(b.id, "extends")],
                b.id: [(a.id, "extends")],
            },
            method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == a_init.id

    def test_returns_none_when_no_ancestor_defines_method(self) -> None:
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        a = _cls("sym:A", "A")
        b = _cls("sym:B", "B")
        idx = build_method_index(
            [a, b],
            class_ids_by_name={"A": [a.id], "B": [b.id]},
            class_symbols={a.id: a, b.id: b},
        )
        result = _walk_insertion_order(
            start_class_id=b.id, callee_short_name="missing",
            inheritance_index={b.id: [(a.id, "extends")]},
            method_index=idx, depth_cap=10,
        )
        assert result is None

    def test_walks_through_includes_for_mixin(self) -> None:
        """A class that includes a module finds the module's method via mixin."""
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        sidekiq_worker = _mod("sym:Sidekiq::Worker", "Sidekiq::Worker")
        worker_perform_async = _method(
            "sym:Sidekiq::Worker#perform_async",
            "Sidekiq::Worker#perform_async",
        )
        # Note: perform_async lives on the mixed-in module.
        email_worker = _cls("sym:EmailWorker", "EmailWorker")
        idx = build_method_index(
            [sidekiq_worker, worker_perform_async, email_worker],
            class_ids_by_name={
                "Sidekiq::Worker": [sidekiq_worker.id],
                "EmailWorker": [email_worker.id],
            },
            class_symbols={
                sidekiq_worker.id: sidekiq_worker,
                email_worker.id: email_worker,
            },
        )
        # EmailWorker --includes--> Sidekiq::Worker (build_inheritance_index
        # would normally produce this from `includes` edges).
        result = _walk_insertion_order(
            start_class_id=email_worker.id,
            callee_short_name="perform_async",
            inheritance_index={
                email_worker.id: [(sidekiq_worker.id, "includes")],
            },
            method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == worker_perform_async.id

    def test_depth_cap_stops_runaway_walk(self) -> None:
        """A long chain past the depth cap returns None even if a match exists."""
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        classes: list[Symbol] = [_cls(f"sym:C{i}", f"C{i}") for i in range(20)]
        # Method only on the deepest class.
        m = _method(f"sym:C{19}#bar", "C19#bar")
        idx = build_method_index(
            classes + [m],
            class_ids_by_name={c.name: [c.id] for c in classes},
            class_symbols={c.id: c for c in classes},
        )
        # C0 -> C1 -> ... -> C19 chain.
        inheritance: dict[str, list[tuple[str, str]]] = {
            classes[i].id: [(classes[i + 1].id, "extends")] for i in range(19)
        }
        capped = _walk_insertion_order(
            start_class_id=classes[0].id, callee_short_name="bar",
            inheritance_index=inheritance, method_index=idx, depth_cap=5,
        )
        assert capped is None
        # Sanity: the same walk with a generous cap finds it.
        uncapped = _walk_insertion_order(
            start_class_id=classes[0].id, callee_short_name="bar",
            inheritance_index=inheritance, method_index=idx, depth_cap=25,
        )
        assert uncapped is not None and uncapped.id == m.id

    def test_cycle_protection(self) -> None:
        """A cyclic inheritance index doesn't infinite-loop."""
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        a = _cls("sym:A", "A")
        b = _cls("sym:B", "B")
        idx = build_method_index(
            [a, b],
            class_ids_by_name={"A": [a.id], "B": [b.id]},
            class_symbols={a.id: a, b.id: b},
        )
        # A->B->A cycle, no method anywhere.
        result = _walk_insertion_order(
            start_class_id=a.id, callee_short_name="missing",
            inheritance_index={
                a.id: [(b.id, "extends")],
                b.id: [(a.id, "extends")],
            },
            method_index=idx, depth_cap=10,
        )
        assert result is None


class TestWalkSingleThenInterfaces:
    """BFS walk prioritizing extends parents over implements/includes
    (Java/Kotlin/C#/Scala-class MRO).
    """

    def test_direct_method_match_on_starting_class(self) -> None:
        """If the starting class itself has the method, return it immediately."""
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        foo = _cls("sym:Foo", "Foo", path="/Foo.java", language="java")
        foo_bar = _method(
            "sym:Foo.bar", "Foo.bar", path="/Foo.java", language="java",
        )
        idx = build_method_index(
            [foo, foo_bar],
            class_ids_by_name={"Foo": [foo.id]},
            class_symbols={foo.id: foo},
        )
        result = _walk_single_then_interfaces(
            start_class_id=foo.id, callee_short_name="bar",
            inheritance_index={}, method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == foo_bar.id

    def test_walks_extends_chain_to_grandparent(self) -> None:
        """Method on a 2-hop extends ancestor resolves."""
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        base = _cls("sym:Base", "Base", path="/Base.java", language="java")
        base_validate = _method(
            "sym:Base.validate", "Base.validate",
            path="/Base.java", language="java",
        )
        middle = _cls(
            "sym:Middle", "Middle", path="/Middle.java", language="java",
        )
        child = _cls(
            "sym:Child", "Child", path="/Child.java", language="java",
        )
        idx = build_method_index(
            [base, base_validate, middle, child],
            class_ids_by_name={
                "Base": [base.id], "Middle": [middle.id],
                "Child": [child.id],
            },
            class_symbols={
                base.id: base, middle.id: middle, child.id: child,
            },
        )
        result = _walk_single_then_interfaces(
            start_class_id=child.id, callee_short_name="validate",
            inheritance_index={
                child.id: [(middle.id, "extends")],
                middle.id: [(base.id, "extends")],
            },
            method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == base_validate.id

    def test_extends_parent_preferred_over_interface(self) -> None:
        """When both the extends-parent AND an implemented interface have
        the method, the extends-parent wins (Java's single-superclass MRO).
        """
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        # Two ancestors of Child, both define `run`. Child extends Parent,
        # implements Iface. Parent wins.
        parent = _cls(
            "sym:Parent", "Parent", path="/Parent.java", language="java",
        )
        parent_run = _method(
            "sym:Parent.run", "Parent.run",
            path="/Parent.java", language="java",
        )
        iface = _cls(
            "sym:Iface", "Iface", path="/Iface.java", language="java",
        )
        iface_run = _method(
            "sym:Iface.run", "Iface.run",
            path="/Iface.java", language="java",
        )
        child = _cls(
            "sym:Child", "Child", path="/Child.java", language="java",
        )
        idx = build_method_index(
            [parent, parent_run, iface, iface_run, child],
            class_ids_by_name={
                "Parent": [parent.id], "Iface": [iface.id],
                "Child": [child.id],
            },
            class_symbols={
                parent.id: parent, iface.id: iface, child.id: child,
            },
        )
        # implements listed first in the index (out of declaration order).
        # The walker must still pick extends-parent's method.
        result = _walk_single_then_interfaces(
            start_class_id=child.id, callee_short_name="run",
            inheritance_index={
                child.id: [
                    (iface.id, "implements"),
                    (parent.id, "extends"),
                ],
            },
            method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == parent_run.id

    def test_falls_through_to_interface_when_extends_chain_lacks_method(
        self,
    ) -> None:
        """If the extends chain doesn't define the method but an interface
        does (Java 8+ default methods), the interface method resolves.
        """
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        parent = _cls(
            "sym:Parent", "Parent", path="/Parent.java", language="java",
        )
        iface = _cls(
            "sym:Iface", "Iface", path="/Iface.java", language="java",
        )
        iface_default = _method(
            "sym:Iface.greet", "Iface.greet",
            path="/Iface.java", language="java",
        )
        child = _cls(
            "sym:Child", "Child", path="/Child.java", language="java",
        )
        idx = build_method_index(
            [parent, iface, iface_default, child],
            class_ids_by_name={
                "Parent": [parent.id], "Iface": [iface.id],
                "Child": [child.id],
            },
            class_symbols={
                parent.id: parent, iface.id: iface, child.id: child,
            },
        )
        result = _walk_single_then_interfaces(
            start_class_id=child.id, callee_short_name="greet",
            inheritance_index={
                child.id: [
                    (parent.id, "extends"),
                    (iface.id, "implements"),
                ],
            },
            method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == iface_default.id

    def test_returns_none_when_no_ancestor_defines_method(self) -> None:
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        a = _cls("sym:A", "A", path="/A.java", language="java")
        b = _cls("sym:B", "B", path="/B.java", language="java")
        idx = build_method_index(
            [a, b],
            class_ids_by_name={"A": [a.id], "B": [b.id]},
            class_symbols={a.id: a, b.id: b},
        )
        result = _walk_single_then_interfaces(
            start_class_id=b.id, callee_short_name="missing",
            inheritance_index={b.id: [(a.id, "extends")]},
            method_index=idx, depth_cap=10,
        )
        assert result is None

    def test_depth_cap_stops_runaway_walk(self) -> None:
        """A long chain past the depth cap returns None even if a match exists."""
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        classes: list[Symbol] = [
            _cls(f"sym:C{i}", f"C{i}", path=f"/C{i}.java", language="java")
            for i in range(20)
        ]
        m = _method(
            f"sym:C{19}.bar", "C19.bar", path="/C19.java", language="java",
        )
        idx = build_method_index(
            classes + [m],
            class_ids_by_name={c.name: [c.id] for c in classes},
            class_symbols={c.id: c for c in classes},
        )
        inheritance: dict[str, list[tuple[str, str]]] = {
            classes[i].id: [(classes[i + 1].id, "extends")] for i in range(19)
        }
        capped = _walk_single_then_interfaces(
            start_class_id=classes[0].id, callee_short_name="bar",
            inheritance_index=inheritance, method_index=idx, depth_cap=5,
        )
        assert capped is None
        uncapped = _walk_single_then_interfaces(
            start_class_id=classes[0].id, callee_short_name="bar",
            inheritance_index=inheritance, method_index=idx, depth_cap=25,
        )
        assert uncapped is not None and uncapped.id == m.id

    def test_cycle_protection(self) -> None:
        """A cyclic inheritance index doesn't infinite-loop."""
        from hypergumbo_core.linkers.type_hierarchy import build_method_index

        a = _cls("sym:A", "A", path="/A.java", language="java")
        b = _cls("sym:B", "B", path="/B.java", language="java")
        idx = build_method_index(
            [a, b],
            class_ids_by_name={"A": [a.id], "B": [b.id]},
            class_symbols={a.id: a, b.id: b},
        )
        result = _walk_single_then_interfaces(
            start_class_id=a.id, callee_short_name="missing",
            inheritance_index={
                a.id: [(b.id, "extends")],
                b.id: [(a.id, "extends")],
            },
            method_index=idx, depth_cap=10,
        )
        assert result is None


class TestMROWalkerRegistry:
    """The _MRO_WALKERS dispatch table is keyed by language."""

    def test_ruby_resolves_to_insertion_order(self) -> None:
        assert _MRO_WALKERS["ruby"] is _walk_insertion_order

    def test_groovy_resolves_to_insertion_order(self) -> None:
        assert _MRO_WALKERS["groovy"] is _walk_insertion_order

    def test_java_resolves_to_single_then_interfaces(self) -> None:
        assert _MRO_WALKERS["java"] is _walk_single_then_interfaces


# ---------------------------------------------------------------------------
# End-to-end linker tests (LinkerContext-driven, synthetic graph).
# ---------------------------------------------------------------------------


class TestEndToEndInheritedCalls:
    """link_inherited_calls converts unresolved edges with enclosing_class
    hints into resolved `calls` edges by walking ancestor chains.
    """

    def test_resolves_unresolved_call_via_extends_chain(self) -> None:
        """Caller -> ruby:C:0-0:initialize:unresolved (enclosing_class=C)
        with C->B->A and A#initialize present resolves to A#initialize.
        """
        a = _cls("sym:A", "A")
        a_init = _method("sym:A#initialize", "A#initialize")
        b = _cls("sym:B", "B")
        c = _cls("sym:C", "C")
        caller = _caller()
        extends_b_a = _edge(b.id, a.id, "extends")
        extends_c_b = _edge(c.id, b.id, "extends")
        unresolved = _unresolved_call(
            src_id=caller.id, callee_short="initialize",
            enclosing_class="C", line=42,
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[a, a_init, b, c, caller],
            edges=[extends_b_a, extends_c_b, unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [
            e for e in result.edges
            if e.src == caller.id and e.dst == a_init.id
        ]
        assert len(resolved) == 1
        assert resolved[0].evidence_type == "ast_call_inherited"
        assert resolved[0].confidence == 0.90
        assert resolved[0].line == 42

    def test_resolves_via_includes_mixin(self) -> None:
        """Caller -> :perform via include Sidekiq::Worker."""
        mod = _mod("sym:Sidekiq::Worker", "Sidekiq::Worker")
        mod_perform = _method(
            "sym:Sidekiq::Worker#perform", "Sidekiq::Worker#perform",
        )
        worker = _cls("sym:EmailWorker", "EmailWorker")
        caller = _caller()
        includes_edge = _edge(worker.id, mod.id, "includes")
        unresolved = _unresolved_call(
            src_id=caller.id, callee_short="perform",
            enclosing_class="EmailWorker",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[mod, mod_perform, worker, caller],
            edges=[includes_edge, unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [
            e for e in result.edges
            if e.src == caller.id and e.dst == mod_perform.id
        ]
        assert len(resolved) == 1
        assert resolved[0].evidence_type == "ast_call_inherited"

    def test_does_not_resolve_when_no_match_in_chain(self) -> None:
        a = _cls("sym:A", "A")
        b = _cls("sym:B", "B")
        caller = _caller()
        extends = _edge(b.id, a.id, "extends")
        unresolved = _unresolved_call(
            src_id=caller.id, callee_short="missing",
            enclosing_class="B",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[a, b, caller], edges=[extends, unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_no_op_when_unresolved_edge_has_no_enclosing_class_hint(self) -> None:
        """Unresolved edges without the hint are ignored (PR-1 backward compat)."""
        a = _cls("sym:A", "A")
        a_init = _method("sym:A#initialize", "A#initialize")
        caller = _caller()
        # Bare unresolved (no hint).
        unresolved = Edge.create(
            src=caller.id, dst="ruby:external:0-0:initialize:unresolved",
            edge_type="calls", line=1, origin="test", origin_run_id="test",
            evidence_type="ast_call_direct", is_resolved=False,
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[a, a_init, caller],
            edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_no_op_for_unregistered_language(self) -> None:
        """Languages without an MRO walker are silently skipped. PR-3
        registers ``java``, so this test uses ``python`` (not yet
        registered) to exercise the ``walker is None`` branch."""
        a = _cls("sym:A", "A", language="python")
        a_init = _method("sym:A.foo", "A.foo", language="python")
        b = _cls("sym:B", "B", language="python")
        caller = _caller(sid="sym:Caller.bar", language="python")
        extends = _edge(b.id, a.id, "extends")
        from hypergumbo_core.analyze.base import make_unresolved_edge
        unresolved = make_unresolved_edge(
            lang="python", src_id=caller.id, callee_name="foo",
            line=1, pass_id="test", run_id="test",
            enclosing_class="B",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[a, a_init, b, caller], edges=[extends, unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_resolves_java_unresolved_call_via_extends_chain(self) -> None:
        """PR-3: Java analyzer emits make_unresolved_edge(...,
        enclosing_class=<owner>); linker walks extends chain and emits
        ast_call_inherited at confidence 0.90."""
        a = _cls("sym:A", "A", language="java")
        a_foo = _method("sym:A.foo", "A.foo", language="java")
        b = _cls("sym:B", "B", language="java")
        caller = _caller(sid="sym:Caller.bar", language="java")
        extends = _edge(b.id, a.id, "extends")
        from hypergumbo_core.analyze.base import make_unresolved_edge
        unresolved = make_unresolved_edge(
            lang="java", src_id=caller.id, callee_name="foo",
            line=12, pass_id="test", run_id="test",
            enclosing_class="B",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[a, a_foo, b, caller], edges=[extends, unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [
            e for e in result.edges
            if e.src == caller.id and e.dst == a_foo.id
        ]
        assert len(resolved) == 1
        assert resolved[0].evidence_type == "ast_call_inherited"
        assert resolved[0].confidence == 0.90
        assert resolved[0].line == 12

    def test_resolves_java_through_implements_when_extends_chain_lacks_method(
        self,
    ) -> None:
        """PR-3 enhancement: with Java 8+ default methods, an interface
        method should resolve via implements when the extends chain has
        no match. This goes beyond the old in-analyzer walk which only
        followed extends.
        """
        parent = _cls("sym:Parent", "Parent", language="java")
        iface = _cls("sym:Iface", "Iface", language="java")
        iface_greet = _method("sym:Iface.greet", "Iface.greet", language="java")
        child = _cls("sym:Child", "Child", language="java")
        caller = _caller(sid="sym:Child.bar", language="java")
        extends_child_parent = _edge(child.id, parent.id, "extends")
        implements_child_iface = _edge(child.id, iface.id, "implements")
        from hypergumbo_core.analyze.base import make_unresolved_edge
        unresolved = make_unresolved_edge(
            lang="java", src_id=caller.id, callee_name="greet",
            line=5, pass_id="test", run_id="test",
            enclosing_class="Child",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[parent, iface, iface_greet, child, caller],
            edges=[
                extends_child_parent, implements_child_iface, unresolved,
            ],
        )
        result = link_inherited_calls(ctx)
        resolved = [
            e for e in result.edges
            if e.src == caller.id and e.dst == iface_greet.id
        ]
        assert len(resolved) == 1
        assert resolved[0].evidence_type == "ast_call_inherited"

    def test_does_not_duplicate_existing_resolved_edge(self) -> None:
        """If a direct resolved edge to the same target already exists,
        the linker doesn't emit a duplicate."""
        a = _cls("sym:A", "A")
        a_init = _method("sym:A#initialize", "A#initialize")
        b = _cls("sym:B", "B")
        caller = _caller()
        extends = _edge(b.id, a.id, "extends")
        # Pre-existing direct resolved edge from caller to A#initialize.
        existing = _edge(caller.id, a_init.id, "calls", line=42)
        unresolved = _unresolved_call(
            src_id=caller.id, callee_short="initialize",
            enclosing_class="B", line=42,
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[a, a_init, b, caller],
            edges=[extends, existing, unresolved],
        )
        result = link_inherited_calls(ctx)
        # No NEW edge to A#initialize from caller — the existing one
        # already covers it.
        new_to_init = [
            e for e in result.edges
            if e.src == caller.id and e.dst == a_init.id
        ]
        assert len(new_to_init) == 0

    def test_skips_unresolved_edge_when_enclosing_class_not_in_tree(
        self,
    ) -> None:
        """If the named enclosing class isn't a project Symbol, do nothing."""
        caller = _caller()
        unresolved = _unresolved_call(
            src_id=caller.id, callee_short="initialize",
            enclosing_class="NoSuchClass",
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[caller], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_skips_edge_whose_src_is_not_a_known_symbol(self) -> None:
        """Defensive: an unresolved edge whose src id has no Symbol is skipped."""
        from hypergumbo_core.analyze.base import make_unresolved_edge
        a = _cls("sym:A", "A")
        a_init = _method("sym:A#initialize", "A#initialize")
        # caller id is not in symbols list -> src_sym lookup returns None
        unresolved = make_unresolved_edge(
            lang="ruby",
            src_id="sym:GhostCaller",
            callee_name="initialize",
            line=1, pass_id="test", run_id="test",
            enclosing_class="A",
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[a, a_init], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_skips_malformed_unresolved_dst(self) -> None:
        """Defensive: unresolved-call dst that doesn't parse yields no edge."""
        caller = _caller()
        a = _cls("sym:A", "A")
        # Manually craft an Edge with is_resolved=False but a malformed
        # dst that doesn't conform to the {lang}:{module}:{span}:{name}:unresolved
        # shape parse_unresolved_name expects.
        bad = Edge.create(
            src=caller.id,
            dst="malformed:unresolved",  # too few parts
            edge_type="calls", line=1, origin="test", origin_run_id="test",
            evidence_type="ast_call_direct", is_resolved=False,
            meta={"enclosing_class": "A"},
        )
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[a, caller], edges=[bad],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []


# ---------------------------------------------------------------------------
# Site-2 (typed-receiver) + Site-3 (inherited-field-receiver) resolution
# (WI-puvil / PR-5 of INV-nilud).
# ---------------------------------------------------------------------------


def _java_cls(sid: str, name: str, path: str = "/a.java",
              fields: dict | None = None) -> Symbol:
    meta: dict | None = None
    if fields is not None:
        meta = {"fields": dict(fields)}
    return Symbol(
        id=sid, name=name, kind="class", language="java", path=path,
        span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=meta,
    )


def _java_iface(sid: str, name: str, path: str = "/a.java") -> Symbol:
    return Symbol(
        id=sid, name=name, kind="interface", language="java", path=path,
        span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=None,
    )


def _java_method(sid: str, qualified_name: str,
                 path: str = "/a.java") -> Symbol:
    return Symbol(
        id=sid, name=qualified_name, kind="method", language="java",
        path=path,
        span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=None,
    )


def _java_caller(sid: str = "sym:Caller.run") -> Symbol:
    return Symbol(
        id=sid, name="Caller.run", kind="method", language="java",
        path="/caller.java",
        span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=None,
    )


def _unresolved_site2(
    src_id: str, callee_name: str, receiver_type_hint: str, line: int = 1,
) -> Edge:
    """Unresolved-call edge with receiver_type_hint (Site 2)."""
    from hypergumbo_core.analyze.base import make_unresolved_edge
    return make_unresolved_edge(
        lang="java", src_id=src_id, callee_name=callee_name,
        line=line, pass_id="test-pass", run_id="test-run",
        module_hint="external",
        receiver_type_hint=receiver_type_hint,
    )


def _unresolved_site3(
    src_id: str, callee_name: str, enclosing_class: str,
    inherited_field_receiver: str, line: int = 1,
) -> Edge:
    """Unresolved-call edge with enclosing_class + inherited_field_receiver
    (Site 3)."""
    from hypergumbo_core.analyze.base import make_unresolved_edge
    return make_unresolved_edge(
        lang="java", src_id=src_id, callee_name=callee_name,
        line=line, pass_id="test-pass", run_id="test-run",
        module_hint="external",
        enclosing_class=enclosing_class,
        inherited_field_receiver=inherited_field_receiver,
    )


class TestSite2TypedReceiverResolution:
    """Site 2 resolves ``var.method()`` calls where ``var``'s type was
    inferred by the Java analyzer and threaded into ``Edge.meta`` as
    ``receiver_type_hint`` (PR-4)."""

    def test_resolves_method_directly_on_type(self) -> None:
        """``var.method()`` where ``Type`` has ``method`` defined directly
        resolves to that method at confidence 0.85 (ast_call_type_inferred).
        """
        repo_iface = _java_iface(
            "sym:OwnerRepository", "OwnerRepository",
        )
        find_method = _java_method(
            "sym:OwnerRepository.findByLastName",
            "OwnerRepository.findByLastName",
        )
        caller = _java_caller()
        unresolved = _unresolved_site2(
            src_id=caller.id, callee_name="owners.findByLastName",
            receiver_type_hint="OwnerRepository", line=42,
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[repo_iface, find_method, caller],
            edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == find_method.id
        assert resolved[0].evidence_type == "ast_call_type_inferred"
        assert resolved[0].confidence == 0.85
        assert resolved[0].line == 42

    def test_resolves_method_via_mro_on_parent(self) -> None:
        """``var.method()`` where ``Type`` doesn't define ``method``
        directly but its extends-parent does — resolves to the parent
        method at confidence 0.70 (ast_call_inherited_method)."""
        parent = _java_cls("sym:BaseRepo", "BaseRepo")
        save_method = _java_method("sym:BaseRepo.save", "BaseRepo.save")
        sub = _java_cls("sym:OwnerRepo", "OwnerRepo")
        caller = _java_caller()
        extends_edge = _edge(sub.id, parent.id, "extends")
        unresolved = _unresolved_site2(
            src_id=caller.id, callee_name="owners.save",
            receiver_type_hint="OwnerRepo",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[parent, save_method, sub, caller],
            edges=[extends_edge, unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == save_method.id
        assert resolved[0].evidence_type == "ast_call_inherited_method"
        assert resolved[0].confidence == 0.70

    def test_falls_back_to_type_symbol_when_method_not_found(self) -> None:
        """``var.method()`` where ``Type`` exists but method isn't on it
        or any ancestor — fall back to an edge to the type symbol itself
        at confidence 0.70 (ast_call_inherited_method, matching the
        analyzer's pre-PR-5 behavior)."""
        repo_iface = _java_iface(
            "sym:OwnerRepository", "OwnerRepository",
        )
        caller = _java_caller()
        unresolved = _unresolved_site2(
            src_id=caller.id, callee_name="owners.save",
            receiver_type_hint="OwnerRepository",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[repo_iface, caller],
            edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == repo_iface.id
        assert resolved[0].evidence_type == "ast_call_inherited_method"
        assert resolved[0].confidence == 0.70

    def test_no_resolution_when_type_not_in_project(self) -> None:
        """``var.method()`` where the inferred type isn't a project Symbol
        (e.g., stdlib InputStream) — no Site-2 edge emitted."""
        caller = _java_caller()
        unresolved = _unresolved_site2(
            src_id=caller.id, callee_name="stream.read",
            receiver_type_hint="InputStream",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[caller], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_no_resolution_without_hint(self) -> None:
        """An unresolved edge without ``receiver_type_hint`` (and without
        the other hints) gets no Site-2 resolution."""
        from hypergumbo_core.analyze.base import make_unresolved_edge
        caller = _java_caller()
        repo = _java_iface("sym:Repo", "Repo")
        unresolved = make_unresolved_edge(
            lang="java", src_id=caller.id, callee_name="x.foo",
            line=1, pass_id="test", run_id="test",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[repo, caller], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_site2_walker_skipped_for_unregistered_language(self) -> None:
        """If the source language lacks an MRO walker, Site-2 still
        attempts the *direct* type-method lookup (no MRO walk needed),
        but doesn't synthesize an MRO-walk edge or a fallback. Falls back
        to type-symbol-only edge."""
        # Python is not in _MRO_WALKERS yet. With direct method lookup
        # and the fallback-to-type both not requiring an MRO walker, the
        # linker still emits the direct match.
        repo = Symbol(
            id="sym:py.Repo", name="Repo", kind="class", language="python",
            path="/r.py",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run", meta=None,
        )
        save = Symbol(
            id="sym:py.Repo.save", name="Repo.save", kind="method",
            language="python", path="/r.py",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run", meta=None,
        )
        caller = Symbol(
            id="sym:py.Caller", name="Caller.run", kind="method",
            language="python", path="/c.py",
            span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run", meta=None,
        )
        from hypergumbo_core.analyze.base import make_unresolved_edge
        unresolved = make_unresolved_edge(
            lang="python", src_id=caller.id, callee_name="r.save",
            line=1, pass_id="test", run_id="test",
            receiver_type_hint="Repo",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[repo, save, caller], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        # Direct match works (no walker needed). Python is the source
        # language so direct-method lookup happens for it.
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == save.id
        assert resolved[0].evidence_type == "ast_call_type_inferred"


class TestSite3InheritedFieldResolution:
    """Site 3 resolves ``field.method()`` calls where ``field`` is declared
    on a parent of the enclosing class. The Java analyzer threads both
    ``enclosing_class`` and ``inherited_field_receiver`` into
    ``Edge.meta`` (PR-5 extends PR-4 to also stash enclosing_class on
    the Case-3.5 path), and each class symbol carries
    ``meta["fields"] = {name: type, ...}`` (PR-5).
    """

    def test_resolves_field_declared_on_immediate_parent(self) -> None:
        """``log.info()`` on a class extending a parent that declares
        ``protected Logger log;`` and ``Logger.info`` exists — resolves
        to ``Logger.info`` at confidence 0.80 (ast_call_inherited_field).
        """
        logger_cls = _java_cls("sym:Logger", "Logger")
        info_method = _java_method("sym:Logger.info", "Logger.info")
        parent = _java_cls("sym:Base", "Base", fields={"log": "Logger"})
        sub = _java_cls("sym:Sub", "Sub")
        caller = _java_method("sym:Sub.run", "Sub.run")
        extends_edge = _edge(sub.id, parent.id, "extends")
        unresolved = _unresolved_site3(
            src_id=caller.id, callee_name="log.info",
            enclosing_class="Sub", inherited_field_receiver="log",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger_cls, info_method, parent, sub, caller],
            edges=[extends_edge, unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == info_method.id
        assert resolved[0].evidence_type == "ast_call_inherited_field"
        assert resolved[0].confidence == 0.80

    def test_resolves_field_declared_on_grandparent(self) -> None:
        """Field declared on grandparent through a 2-hop extends chain."""
        repo_cls = _java_cls("sym:Repo", "Repo")
        save_method = _java_method("sym:Repo.save", "Repo.save")
        grandparent = _java_cls(
            "sym:G", "G", fields={"repo": "Repo"},
        )
        parent = _java_cls("sym:P", "P")
        sub = _java_cls("sym:S", "S")
        caller = _java_method("sym:S.use", "S.use")
        e1 = _edge(sub.id, parent.id, "extends")
        e2 = _edge(parent.id, grandparent.id, "extends")
        unresolved = _unresolved_site3(
            src_id=caller.id, callee_name="repo.save",
            enclosing_class="S", inherited_field_receiver="repo",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[repo_cls, save_method, grandparent, parent,
                     sub, caller],
            edges=[e1, e2, unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == save_method.id
        assert resolved[0].evidence_type == "ast_call_inherited_field"

    def test_no_resolution_when_no_parent_declares_field(self) -> None:
        """If no ancestor declares ``inherited_field_receiver`` as a
        field, emit nothing."""
        parent = _java_cls("sym:Base", "Base", fields={"other": "Foo"})
        sub = _java_cls("sym:Sub", "Sub")
        caller = _java_method("sym:Sub.run", "Sub.run")
        e1 = _edge(sub.id, parent.id, "extends")
        unresolved = _unresolved_site3(
            src_id=caller.id, callee_name="missing.foo",
            enclosing_class="Sub", inherited_field_receiver="missing",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[parent, sub, caller], edges=[e1, unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_no_resolution_when_field_type_not_in_project(self) -> None:
        """Parent declares the field, but the field's type isn't a
        project Symbol — no resolved edge (the method can't be looked
        up without the type symbol)."""
        parent = _java_cls(
            "sym:Base", "Base", fields={"log": "ExternalLogger"},
        )
        sub = _java_cls("sym:Sub", "Sub")
        caller = _java_method("sym:Sub.run", "Sub.run")
        e1 = _edge(sub.id, parent.id, "extends")
        unresolved = _unresolved_site3(
            src_id=caller.id, callee_name="log.info",
            enclosing_class="Sub", inherited_field_receiver="log",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[parent, sub, caller], edges=[e1, unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_no_resolution_when_method_not_on_field_type(self) -> None:
        """Parent declares the field, field's type is a known class,
        but the method isn't on it or any ancestor — no edge."""
        logger_cls = _java_cls("sym:Logger", "Logger")  # no methods
        parent = _java_cls(
            "sym:Base", "Base", fields={"log": "Logger"},
        )
        sub = _java_cls("sym:Sub", "Sub")
        caller = _java_method("sym:Sub.run", "Sub.run")
        e1 = _edge(sub.id, parent.id, "extends")
        unresolved = _unresolved_site3(
            src_id=caller.id, callee_name="log.unknown",
            enclosing_class="Sub", inherited_field_receiver="log",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger_cls, parent, sub, caller],
            edges=[e1, unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_no_resolution_without_enclosing_class_hint(self) -> None:
        """An edge with ``inherited_field_receiver`` but no
        ``enclosing_class`` hint — no Site-3 walk."""
        from hypergumbo_core.analyze.base import make_unresolved_edge
        logger_cls = _java_cls("sym:Logger", "Logger")
        info_method = _java_method("sym:Logger.info", "Logger.info")
        parent = _java_cls("sym:Base", "Base", fields={"log": "Logger"})
        sub = _java_cls("sym:Sub", "Sub")
        caller = _java_method("sym:Sub.run", "Sub.run")
        unresolved = make_unresolved_edge(
            lang="java", src_id=caller.id, callee_name="log.info",
            line=1, pass_id="test", run_id="test",
            inherited_field_receiver="log",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger_cls, info_method, parent, sub, caller],
            edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_site3_field_type_method_via_mro_walk(self) -> None:
        """Site 3 where the field's type doesn't define the method
        directly, but its extends-parent does — Site-3 invokes the MRO
        walker on the field's type and resolves the method via the
        chain. Emits ast_call_inherited_field at 0.80 to the chain
        method (not the field type itself)."""
        base_logger = _java_cls("sym:BaseLogger", "BaseLogger")
        info_method = _java_method(
            "sym:BaseLogger.info", "BaseLogger.info",
        )
        derived_logger = _java_cls("sym:DerivedLogger", "DerivedLogger")
        parent = _java_cls(
            "sym:Base", "Base", fields={"log": "DerivedLogger"},
        )
        sub = _java_cls("sym:Sub", "Sub")
        caller = _java_method("sym:Sub.run", "Sub.run")
        e_sub_parent = _edge(sub.id, parent.id, "extends")
        e_dl_bl = _edge(derived_logger.id, base_logger.id, "extends")
        unresolved = _unresolved_site3(
            src_id=caller.id, callee_name="log.info",
            enclosing_class="Sub", inherited_field_receiver="log",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base_logger, info_method, derived_logger,
                     parent, sub, caller],
            edges=[e_sub_parent, e_dl_bl, unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == info_method.id
        assert resolved[0].evidence_type == "ast_call_inherited_field"
        assert resolved[0].confidence == 0.80

    def test_walk_parents_for_field_respects_depth_cap(self) -> None:
        """``_walk_parents_for_field`` halts at ``depth_cap`` even if the
        chain extends further. Confirms the depth-cap branch (defensive
        symmetry with the method MRO walkers)."""
        from hypergumbo_core.linkers.inherited_calls import (
            _walk_parents_for_field,
        )
        # Build a 5-deep extends chain; place the field on the
        # deepest ancestor. With depth_cap=2 the field is unreachable.
        c0 = _java_cls("sym:C0", "C0")
        c1 = _java_cls("sym:C1", "C1")
        c2 = _java_cls("sym:C2", "C2")
        c3 = _java_cls("sym:C3", "C3")
        c4 = _java_cls("sym:C4", "C4", fields={"target": "Logger"})
        inheritance_index = {
            c0.id: [(c1.id, "extends")],
            c1.id: [(c2.id, "extends")],
            c2.id: [(c3.id, "extends")],
            c3.id: [(c4.id, "extends")],
        }
        class_symbols = {s.id: s for s in (c0, c1, c2, c3, c4)}
        # depth_cap=2 — won't reach c4 (depth 4 from c0).
        result = _walk_parents_for_field(
            c0.id, "target", inheritance_index, class_symbols,
            depth_cap=2,
        )
        assert result is None

    def test_walk_parents_for_field_cycle_protection(self) -> None:
        """Cycle in the parent chain doesn't loop forever; the visited
        set short-circuits already-seen parents."""
        from hypergumbo_core.linkers.inherited_calls import (
            _walk_parents_for_field,
        )
        a = _java_cls("sym:A", "A")
        b = _java_cls("sym:B", "B")
        # A -> B -> A cycle (Symbol set is acyclic by construction in
        # real codebases, but defensive guards belong in the walker).
        inheritance_index = {
            a.id: [(b.id, "extends")],
            b.id: [(a.id, "extends")],
        }
        class_symbols = {s.id: s for s in (a, b)}
        result = _walk_parents_for_field(
            a.id, "nothing", inheritance_index, class_symbols,
        )
        assert result is None  # No field; cycle didn't hang.

    def test_site3_takes_priority_over_site1_when_both_hints_present(
        self,
    ) -> None:
        """When an edge carries both ``enclosing_class`` and
        ``inherited_field_receiver``, Site-3 walk fires (more specific
        about call shape)."""
        logger_cls = _java_cls("sym:Logger", "Logger")
        info_method = _java_method("sym:Logger.info", "Logger.info")
        parent = _java_cls("sym:Base", "Base", fields={"log": "Logger"})
        sub = _java_cls("sym:Sub", "Sub")
        caller = _java_method("sym:Sub.run", "Sub.run")
        e1 = _edge(sub.id, parent.id, "extends")
        unresolved = _unresolved_site3(
            src_id=caller.id, callee_name="log.info",
            enclosing_class="Sub", inherited_field_receiver="log",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger_cls, info_method, parent, sub, caller],
            edges=[e1, unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].evidence_type == "ast_call_inherited_field"
