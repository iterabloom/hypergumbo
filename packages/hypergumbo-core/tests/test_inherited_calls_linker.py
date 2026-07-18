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
    _walk_left_to_right,
    _walk_linearization,
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
        """Languages without an MRO walker are silently skipped. WI-hiziz
        registered ``python`` (C3 walker), so this test uses ``php`` (still
        unregistered — the future ``_walk_left_to_right``) to exercise the
        ``walker is None`` branch."""
        a = _cls("sym:A", "A", language="php")
        a_init = _method("sym:A.foo", "A.foo", language="php")
        b = _cls("sym:B", "B", language="php")
        caller = _caller(sid="sym:Caller.bar", language="php")
        extends = _edge(b.id, a.id, "extends")
        from hypergumbo_core.analyze.base import make_unresolved_edge
        unresolved = make_unresolved_edge(
            lang="php", src_id=caller.id, callee_name="foo",
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
        attempts the *direct* type-method lookup (no MRO walk needed).
        Python is a strict INV-fahub language (not in _LEGACY_SITE2_LANGS),
        so it gets NO Step-3 type-symbol fallback — but the direct match
        here needs no fallback and resolves via Step 1."""
        # Python is not in _MRO_WALKERS yet. Direct method lookup does not
        # require an MRO walker, so the single unambiguous Repo.save resolves.
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


def _py_cls(sid: str, name: str, path: str = "/m.py") -> Symbol:
    return Symbol(
        id=sid, name=name, kind="class", language="python", path=path,
        span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=None,
    )


def _py_method(sid: str, qualified_name: str, path: str = "/m.py") -> Symbol:
    return Symbol(
        id=sid, name=qualified_name, kind="method", language="python",
        path=path,
        span=Span(start_line=2, end_line=4, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=None,
    )


def _py_caller(sid: str = "sym:py.Caller.run") -> Symbol:
    return Symbol(
        id=sid, name="Caller.run", kind="method", language="python",
        path="/c.py",
        span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=None,
    )


def _unresolved_site2_lang(
    src_id: str, callee_name: str, receiver_type_hint: str,
    lang: str, line: int = 1, receiver_type_id: str | None = None,
) -> Edge:
    """Site-2 unresolved-call edge for an arbitrary source language.

    WI-supat: an optional ``receiver_type_id`` is post-injected into
    ``edge.meta`` (mirroring how the Python producer builds the meta dict
    inline — ``make_unresolved_edge`` stays lean and carries no id kwargs since
    no analyzer routes Site ids through it).
    """
    from hypergumbo_core.analyze.base import make_unresolved_edge
    edge = make_unresolved_edge(
        lang=lang, src_id=src_id, callee_name=callee_name,
        line=line, pass_id="test-pass", run_id="test-run",
        module_hint="external",
        receiver_type_hint=receiver_type_hint,
    )
    if receiver_type_id is not None:
        edge.meta["receiver_type_id"] = receiver_type_id
    return edge


def _unresolved_site1_lang(
    src_id: str, callee_name: str, enclosing_class: str,
    lang: str, line: int = 1, enclosing_class_id: str | None = None,
) -> Edge:
    """Site-1 unresolved-call edge (enclosing_class hint) for an arbitrary
    source language. Dispatch keys on the SOURCE symbol's language, not the
    edge's dst prefix, so the caller symbol must carry ``lang``. WI-supat: an
    optional ``enclosing_class_id`` is post-injected into ``edge.meta``."""
    from hypergumbo_core.analyze.base import make_unresolved_edge
    edge = make_unresolved_edge(
        lang=lang, src_id=src_id, callee_name=callee_name,
        line=line, pass_id="test-pass", run_id="test-run",
        module_hint="external",
        enclosing_class=enclosing_class,
    )
    if enclosing_class_id is not None:
        edge.meta["enclosing_class_id"] = enclosing_class_id
    return edge


def _unresolved_site3_lang(
    src_id: str, callee_name: str, enclosing_class: str,
    inherited_field_receiver: str, lang: str, line: int = 1,
    enclosing_class_id: str | None = None,
) -> Edge:
    """Site-3 unresolved-call edge (inherited_field_receiver + enclosing_class)
    for an arbitrary source language. ``callee_name`` is just the METHOD short
    name (matching the Python producer, which stamps the field in meta, not the
    dst). WI-supat: an optional ``enclosing_class_id`` is post-injected into
    ``edge.meta`` (the Site-3 ENCLOSING disambiguation; the field-TYPE id rides
    the parent class symbol's meta, added in PR-B)."""
    from hypergumbo_core.analyze.base import make_unresolved_edge
    edge = make_unresolved_edge(
        lang=lang, src_id=src_id, callee_name=callee_name,
        line=line, pass_id="test-pass", run_id="test-run",
        module_hint="external",
        enclosing_class=enclosing_class,
        inherited_field_receiver=inherited_field_receiver,
    )
    if enclosing_class_id is not None:
        edge.meta["enclosing_class_id"] = enclosing_class_id
    return edge


class TestPythonSite2StrictMode:
    """WI-noham Part A: Python receiver_type_hint edges (emitted by py.py's
    final unresolved-emit else) get the STRICT INV-fahub Site-2 mode — the
    method must be DIRECTLY on the concretely-named, unambiguous type (Step 1).
    Two gates key off ``_LEGACY_SITE2_LANGS`` (only ``java`` today, which keeps
    its INV-nilud-validated permissive behavior): the Step-3 type-symbol
    fallback is OFF, and a same-name-class collision biases to unresolved
    (INV-fahub: never bind an under-determined receiver to an arbitrary
    same-named internal def)."""

    def test_direct_method_on_unambiguous_type_resolves(self) -> None:
        """The in-PR recall path: ``receiver_type_hint='Foo'`` + a single
        in-repo ``Foo`` with ``Foo.bar`` directly on it → Step-1 resolves
        (ast_call_type_inferred @0.85). Guards the gate does not break the
        direct path."""
        foo = _py_cls("sym:py.Foo", "Foo")
        bar = _py_method("sym:py.Foo.bar", "Foo.bar")
        caller = _py_caller()
        unresolved = _unresolved_site2_lang(
            src_id=caller.id, callee_name="bar",
            receiver_type_hint="Foo", lang="python",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[foo, bar, caller], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == bar.id
        assert resolved[0].evidence_type == "ast_call_type_inferred"
        assert resolved[0].is_resolved is True

    def test_step3_type_symbol_fallback_gated_off_for_python(self) -> None:
        """Method nowhere on the (single, walker-less) type → Python gets NO
        Step-3 fallback edge (would be a ``calls→class`` edge — a new
        runtime_coherence partition AND an INV-fahub under-determined bind).
        Stays unresolved. Java (legacy) still falls back — asserted by the
        existing ``test_falls_back_to_type_symbol_when_method_not_found``."""
        foo = _py_cls("sym:py.Foo", "Foo")  # Foo defines NO 'save'
        caller = _py_caller()
        unresolved = _unresolved_site2_lang(
            src_id=caller.id, callee_name="save",
            receiver_type_hint="Foo", lang="python",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[foo, caller], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_same_name_class_collision_biases_to_unresolved(self) -> None:
        """INV-fahub airtight: two in-repo classes both named ``Foo`` (only
        one defines ``bar``). The hint carries only the NAME, so the receiver
        is under-determined → NO resolution (the ambiguity guard), NOT a
        confident bind to whichever ``Foo`` happens to have ``bar``. D3
        (concrete class-id) later recovers this recall."""
        foo_a = _py_cls("sym:py.a.Foo", "Foo", path="/a.py")
        bar = _py_method("sym:py.a.Foo.bar", "Foo.bar", path="/a.py")
        foo_b = _py_cls("sym:py.b.Foo", "Foo", path="/b.py")  # no 'bar'
        caller = _py_caller()
        unresolved = _unresolved_site2_lang(
            src_id=caller.id, callee_name="bar",
            receiver_type_hint="Foo", lang="python",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[foo_a, bar, foo_b, caller], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_java_same_name_class_collision_still_resolves(self) -> None:
        """Legacy guard: the ambiguity guard is scoped to non-legacy langs, so
        Java's INV-nilud-validated first-match Site-2 behavior is preserved
        even when two classes share a short name."""
        foo_a = _java_cls("sym:a.Foo", "Foo", path="/a.java")
        bar = _java_method("sym:a.Foo.bar", "Foo.bar", path="/a.java")
        foo_b = _java_cls("sym:b.Foo", "Foo", path="/b.java")
        caller = _java_caller()
        unresolved = _unresolved_site2(
            src_id=caller.id, callee_name="r.bar",
            receiver_type_hint="Foo",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[foo_a, bar, foo_b, caller], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == bar.id

    def test_ancestor_only_method_resolves_via_python_walker(self) -> None:
        """WI-hiziz (D1): a typed receiver whose method lives ONLY on an ancestor
        now resolves for Python via Site-2 Step-2 (the C3 MRO walker). Sub
        extends Base, Base defines ``save``, receiver_type_hint='Sub' → the
        walker finds Base.save (``ast_call_inherited_method`` @ 0.70). This is
        the flipped counterpart of the pre-D1 no-walker freeze; the Step-3
        fallback stays gated off (python not in _LEGACY_SITE2_LANGS), so the
        resolution is a genuine calls→method MRO hit, never a calls→class."""
        parent = _py_cls("sym:py.Base", "Base", path="/base.py")
        save = _py_method("sym:py.Base.save", "Base.save", path="/base.py")
        sub = _py_cls("sym:py.Sub", "Sub", path="/sub.py")  # defines no 'save'
        caller = _py_caller()
        extends_edge = _edge(sub.id, parent.id, "extends")
        unresolved = _unresolved_site2_lang(
            src_id=caller.id, callee_name="save",
            receiver_type_hint="Sub", lang="python",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[parent, save, sub, caller],
            edges=[extends_edge, unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == save.id
        assert resolved[0].evidence_type == "ast_call_inherited_method"
        assert resolved[0].confidence == 0.70
        assert resolved[0].is_resolved is True

    def test_python_c3_diamond_resolves_correct_ancestor(self) -> None:
        """WI-hiziz (D1) C3 precision: E(C, D), C(B), B(A), D(A); both B and D
        define ``save``. Python's C3 MRO is [E, C, B, D, A], so E().save()
        resolves to B.save — NOT D.save, which the shallower insertion-order BFS
        would pick. A confidently-wrong ancestor is worse than unresolved, so
        the Python walker must honor C3, not depth-order."""
        a = _py_cls("sym:py.A", "A", path="/a.py")
        b = _py_cls("sym:py.B", "B", path="/b.py")
        b_save = _py_method("sym:py.B.save", "B.save", path="/b.py")
        c = _py_cls("sym:py.C", "C", path="/c.py")
        d = _py_cls("sym:py.D", "D", path="/d.py")
        d_save = _py_method("sym:py.D.save", "D.save", path="/d.py")
        e = _py_cls("sym:py.E", "E", path="/e.py")  # defines no 'save'
        caller = _py_caller()
        edges = [
            # E(C, D) — base order matters for C3; C listed before D.
            _edge(e.id, c.id, "extends"), _edge(e.id, d.id, "extends"),
            _edge(c.id, b.id, "extends"), _edge(b.id, a.id, "extends"),
            _edge(d.id, a.id, "extends"),
            _unresolved_site2_lang(
                src_id=caller.id, callee_name="save",
                receiver_type_hint="E", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[a, b, b_save, c, d, d_save, e, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [ed for ed in result.edges if ed.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == b_save.id, "C3 must pick B.save, not D.save"
        assert resolved[0].evidence_type == "ast_call_inherited_method"

    def test_python_c3_respects_source_base_order_over_edge_arrival(
        self,
    ) -> None:
        """WI-hiziz (D1) base-order robustness: a QUALIFIED in-tree base written
        first (``class D(mod.Bar, Baz)``) has its ``extends`` edge recovered
        LATE by the inheritance-linker, so it ARRIVES after Baz's. The C3 walker
        must still honor SOURCE order (from ``meta['base_classes']``) — D().m()
        resolves to Bar.m (the first base), NOT Baz.m — else the reversed
        edge-arrival order silently picks the wrong ancestor on a method-name
        collision. Regression for the adversarial-review base-order blocker."""
        bar = _py_cls("sym:py.Bar", "Bar", path="/base.py")
        bar_m = _py_method("sym:py.Bar.m", "Bar.m", path="/base.py")
        baz = _py_cls("sym:py.Baz", "Baz", path="/main.py")
        baz_m = _py_method("sym:py.Baz.m", "Baz.m", path="/main.py")
        # D(Bar, Baz) in SOURCE order, with Bar written qualified as `base.Bar`.
        d = Symbol(
            id="sym:py.D", name="D", kind="class", language="python",
            path="/main.py",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run",
            meta={"base_classes": ["base.Bar", "Baz"]},
        )
        caller = _py_caller()
        # Edges ARRIVE reversed: Baz (py.py, resolved early) before Bar
        # (inheritance-linker, recovered late).
        edges = [
            _edge(d.id, baz.id, "extends"),
            _edge(d.id, bar.id, "extends"),
            _unresolved_site2_lang(
                src_id=caller.id, callee_name="m",
                receiver_type_hint="D", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[bar, bar_m, baz, baz_m, d, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == bar_m.id, (
            "C3 must resolve to Bar.m (first source base), not Baz.m — the "
            "reversed edge-arrival order was corrected from meta['base_classes']"
        )

    def test_python_walker_cycle_biases_to_unresolved(self) -> None:
        """A malformed inheritance CYCLE (Sub extends Base, Base extends Sub)
        cannot be linearized; the C3 walker biases to unresolved rather than
        emitting a best-effort (possibly wrong) resolution — and never loops."""
        sub = _py_cls("sym:py.Sub", "Sub", path="/s.py")
        base = _py_cls("sym:py.Base", "Base", path="/b.py")
        base_m = _py_method("sym:py.Base.m", "Base.m", path="/b.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _edge(base.id, sub.id, "extends"),  # cycle
            _unresolved_site2_lang(
                src_id=caller.id, callee_name="m",
                receiver_type_hint="Sub", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[sub, base, base_m, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_python_walker_method_on_unrelated_class_stays_unresolved(
        self,
    ) -> None:
        """The C3 walker returns None when the callee exists on some class but
        NOT on any class in the receiver's linearization (the walked-the-whole-
        chain-no-match path). Foo has no bases and no ``save``; ``save`` lives
        only on an unrelated ``Bar`` → stays unresolved (no wrong bind)."""
        foo = _py_cls("sym:py.Foo", "Foo", path="/foo.py")  # no bases, no save
        bar = _py_cls("sym:py.Bar", "Bar", path="/bar.py")
        bar_save = _py_method("sym:py.Bar.save", "Bar.save", path="/bar.py")
        caller = _py_caller()
        unresolved = _unresolved_site2_lang(
            src_id=caller.id, callee_name="save",
            receiver_type_hint="Foo", lang="python",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[foo, bar, bar_save, caller], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_legacy_site2_langs_membership_and_walker_decoupling(self) -> None:
        """``_LEGACY_SITE2_LANGS`` is the explicit opt-in for permissive
        Site-2 (Step-3 fallback + no ambiguity guard). Only Java qualifies
        today. It is DELIBERATELY decoupled from ``_MRO_WALKERS`` so that
        landing a Python MRO walker (deferred D1) does not silently re-enable
        Python's Step-3 fallback and re-breach the ratchet."""
        from hypergumbo_core.linkers.inherited_calls import _LEGACY_SITE2_LANGS
        assert "java" in _LEGACY_SITE2_LANGS
        assert "python" not in _LEGACY_SITE2_LANGS
        # Manifest decoupling: the two sets are not equal, and the
        # walker-equipped ruby/groovy are NOT auto-granted permissive Site-2.
        assert _LEGACY_SITE2_LANGS != set(_MRO_WALKERS)
        assert {"ruby", "groovy"} & _LEGACY_SITE2_LANGS == set()


class TestPythonSite1SelfCalls:
    """WI-hiziz PR-2: Site-1 (``enclosing_class`` hint) resolution for Python
    ``self.method()`` inherited calls, plus the new INV-fahub ambiguity guard.

    py.py stamps ``enclosing_class`` on the unresolved ``self.method()`` edge;
    the linker walks the class's C3 MRO (Python walker landed in PR-1) and mints
    the resolved edge. A same-short-name class collision biases to unresolved
    (the guard) for non-legacy langs; Java stays permissive.
    """

    def test_site1_python_self_method_resolves_via_c3(self) -> None:
        # Sub(Base); Base defines `m`, Sub does not. A python self.m() call
        # carrying enclosing_class="Sub" resolves to Base.m up the C3 MRO.
        base = _py_cls("sym:py.Base", "Base", path="/base.py")
        base_m = _py_method("sym:py.Base.m", "Base.m", path="/base.py")
        sub = _py_cls("sym:py.Sub", "Sub", path="/sub.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="m",
                enclosing_class="Sub", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, base_m, sub, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == base_m.id
        assert resolved[0].evidence_type == "ast_call_inherited"
        assert resolved[0].confidence == 0.90
        assert resolved[0].is_resolved is True
        # FM7: the minted edge does NOT inherit the producer's meta payload
        # (call_construct / resolution_quality live on the surviving unresolved
        # twin only, never on the resolved edge).
        assert "call_construct" not in (resolved[0].meta or {})
        assert "resolution_quality" not in (resolved[0].meta or {})

    def test_site1_python_collision_biases_unresolved(self) -> None:
        # Two in-tree python classes both named "Worker" make the enclosing
        # receiver under-determined by NAME → the guard biases to unresolved,
        # even though one Worker would resolve the method. (WI-supat/D3 recovers
        # this with a concrete class id.)
        base = _py_cls("sym:py.Base", "Base", path="/base.py")
        base_run = _py_method("sym:py.Base.run", "Base.run", path="/base.py")
        w1 = _py_cls("sym:py.Worker1", "Worker", path="/w1.py")
        w2 = _py_cls("sym:py.Worker2", "Worker", path="/w2.py")
        caller = _py_caller()
        edges = [
            _edge(w1.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="run",
                enclosing_class="Worker", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, base_run, w1, w2, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_site1_ruby_reopened_class_still_resolves(self) -> None:
        # Ruby is NOT in _SITE1_STRICT_LANGS: two same-short-name "Worker" class
        # symbols are a class REOPENING (one logical class in Ruby's single global
        # namespace — ubiquitous in Rails), NOT a collision. The guard must NOT
        # fire; Ruby keeps its loop-all-first-match Site-1 resolution, preserving
        # the INV-nilud-validated `X.new -> inherited #initialize` edges this
        # linker was built for. (Regression lock for the review-caught Ruby
        # recall regression from reusing the java-only exemption set.)
        base = _cls("sym:rb.Base", "Base", path="/base.rb", language="ruby")
        base_init = _method(
            "sym:rb.Base#initialize", "Base#initialize",
            path="/base.rb", language="ruby",
        )
        # `class Worker < Base` and a reopen `class Worker` — two symbols, one
        # logical class. Only one carries the extends edge (mirrors the analyzer).
        w1 = _cls("sym:rb.Worker1", "Worker", path="/w.rb", language="ruby")
        w2 = _cls("sym:rb.Worker2", "Worker", path="/w_reopen.rb", language="ruby")
        caller = _caller(sid="sym:rb.Factory#build", language="ruby")
        edges = [
            _edge(w1.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="initialize",
                enclosing_class="Worker", lang="ruby",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, base_init, w1, w2, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == base_init.id

    def test_site1_java_collision_still_loops_all_legacy(self) -> None:
        # Java is in _LEGACY_SITE2_LANGS: the guard is skipped, so two same-name
        # "Svc" classes still resolve via loop-all-first-match (preserves the
        # INV-nilud java edges). Covers the guard's src_lang-not-legacy False arm.
        base = _cls("sym:jv.Base", "Base", path="/Base.java", language="java")
        base_do = _method(
            "sym:jv.Base.doIt", "Base.doIt", path="/Base.java", language="java"
        )
        s1 = _cls("sym:jv.Svc1", "Svc", path="/Svc1.java", language="java")
        s2 = _cls("sym:jv.Svc2", "Svc", path="/Svc2.java", language="java")
        caller = _caller(sid="sym:jv.Caller.go", language="java")
        edges = [
            _edge(s1.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="doIt",
                enclosing_class="Svc", lang="java",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, base_do, s1, s2, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == base_do.id

    def test_site1_python_method_not_on_chain_stays_unresolved(self) -> None:
        # Sub(Base), neither defines "ghost" → the C3 walk finds nothing →
        # Site-1 returns None (covers the resolved_target-is-None path).
        base = _py_cls("sym:py.Base", "Base", path="/base.py")
        sub = _py_cls("sym:py.Sub", "Sub", path="/sub.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="ghost",
                enclosing_class="Sub", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, sub, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_site1_python_external_base_mixin_known_gap(self) -> None:
        # RESIDUAL (FM1, INV-guviv): the C3 walker spans only in-tree ``extends``
        # edges. Sub subclasses an EXTERNAL base (no edge, invisible) plus an
        # in-tree mixin; if the external base defined `save` it would win the
        # real MRO, but the walk sees only the mixin and binds Mixin.save at 0.90.
        # INV-guviv's stdlib-base-method catalog gates the BUILTIN subset
        # (``dict``/``list``/... — see TestInvGuvivStdlibBaseShadow), but a
        # generic 3rd-party base like ``ext.SqlBase`` is NOT cataloged (the
        # catalog is deliberately not open-ended, and over-cataloging risks
        # suppressing the external-mixin-first idiom). So this generic-external
        # case stays the documented residual — resolution to Mixin.save is
        # asserted as the current (possibly-wrong) behavior, eyes-open.
        mixin = _py_cls("sym:py.LogMixin", "LogMixin", path="/mixin.py")
        mixin_save = _py_method(
            "sym:py.LogMixin.save", "LogMixin.save", path="/mixin.py"
        )
        sub = Symbol(
            id="sym:py.Repo", name="Repo", kind="class", language="python",
            path="/repo.py",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run",
            meta={"base_classes": ["ext.SqlBase", "LogMixin"]},
        )
        caller = _py_caller()
        edges = [
            # Only the in-tree mixin gets an extends edge; ext.SqlBase is invisible.
            _edge(sub.id, mixin.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="save",
                enclosing_class="Repo", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[mixin, mixin_save, sub, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == mixin_save.id  # FM1: possibly-wrong; documented

    def test_site1_python_crosslang_namesake_resolves(self) -> None:
        # INV-milud (was FM3, "accepted over-suppression"): class_ids_by_name is
        # language-agnostic, but the name->ids lookup is now filtered to
        # src_lang, so a java "Config" no longer inflates the python "Config"
        # collision count. The python enclosing class is actually UNIQUE, so the
        # call resolves to its inherited python method instead of being
        # over-suppressed as a false-negative.
        base = _py_cls("sym:py.PyBase", "PyBase", path="/base.py")
        base_m = _py_method("sym:py.PyBase.m", "PyBase.m", path="/base.py")
        py_config = _py_cls("sym:py.Config", "Config", path="/cfg.py")
        jv_config = _cls(
            "sym:jv.Config", "Config", path="/Config.java", language="java"
        )
        caller = _py_caller()
        edges = [
            _edge(py_config.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="m",
                enclosing_class="Config", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, base_m, py_config, jv_config, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == base_m.id

    def test_site1_strict_langs_membership(self) -> None:
        # The Site-1 ambiguity guard is opt-in per language via a DEDICATED set,
        # NOT "all but _LEGACY_SITE2_LANGS". Python (module namespaces → same
        # short name = distinct classes) is in; Ruby/Groovy (single global
        # namespace → same short name = class reopening) and Java must be OUT, so
        # their loop-all-first-match Site-1 resolution is preserved.
        from hypergumbo_core.linkers.inherited_calls import _SITE1_STRICT_LANGS
        assert _SITE1_STRICT_LANGS == frozenset({"python"})
        assert "ruby" not in _SITE1_STRICT_LANGS
        assert "groovy" not in _SITE1_STRICT_LANGS
        assert "java" not in _SITE1_STRICT_LANGS


def _py_cls_fields(
    sid: str, name: str, fields: dict, path: str = "/m.py",
    field_ids: dict | None = None,
) -> Symbol:
    """Python class symbol carrying meta['fields'] (mirrors the PR-3 producer).

    WI-supat PR-B: an optional ``field_ids`` populates the parallel
    ``meta['field_type_ids'] = {field: type_id}`` map the field-type-id resolver
    reads."""
    meta: dict = {"fields": dict(fields)}
    if field_ids is not None:
        meta["field_type_ids"] = dict(field_ids)
    return Symbol(
        id=sid, name=name, kind="class", language="python", path=path,
        span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=meta,
    )


class TestPythonSite3InheritedField:
    """WI-hiziz PR-3 part (c): Site-3 (self.field.method()) resolution for
    Python, with the INV-fahub same-short-name ambiguity guards that Site-1/
    Site-2 carry (Python is in _SITE1_STRICT_LANGS; Java stays permissive)."""

    def test_site3_python_resolves_inherited_field(self) -> None:
        # Sub(Base); Base declares field `log: Logger`; Logger.info exists.
        # self.log.info() in Sub resolves to Logger.info @0.80.
        logger = _py_cls("sym:py.Logger", "Logger", path="/log.py")
        info = _py_method("sym:py.Logger.info", "Logger.info", path="/log.py")
        base = _py_cls_fields("sym:py.Base", "Base", {"log": "Logger"}, path="/base.py")
        sub = _py_cls("sym:py.Sub", "Sub", path="/sub.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger, info, base, sub, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == info.id
        assert resolved[0].evidence_type == "ast_call_inherited_field"
        assert resolved[0].confidence == 0.80

    def test_site3_python_ambiguous_enclosing_biases_unresolved(self) -> None:
        # Two in-tree python classes named "Sub" -> the enclosing class is
        # under-determined -> guard 1 biases to unresolved.
        logger = _py_cls("sym:py.Logger", "Logger", path="/log.py")
        info = _py_method("sym:py.Logger.info", "Logger.info", path="/log.py")
        base = _py_cls_fields("sym:py.Base", "Base", {"log": "Logger"}, path="/base.py")
        sub1 = _py_cls("sym:py.Sub1", "Sub", path="/s1.py")
        sub2 = _py_cls("sym:py.Sub2", "Sub", path="/s2.py")
        caller = _py_caller()
        edges = [
            _edge(sub1.id, base.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger, info, base, sub1, sub2, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_site3_python_ambiguous_field_type_biases_unresolved(self) -> None:
        # Unique enclosing Sub, but the field's TYPE name "Logger" resolves to
        # two distinct classes -> guard 2 biases to unresolved.
        logger1 = _py_cls("sym:py.Logger1", "Logger", path="/log1.py")
        info1 = _py_method("sym:py.Logger1.info", "Logger.info", path="/log1.py")
        logger2 = _py_cls("sym:py.Logger2", "Logger", path="/log2.py")
        base = _py_cls_fields("sym:py.Base", "Base", {"log": "Logger"}, path="/base.py")
        sub = _py_cls("sym:py.Sub", "Sub", path="/sub.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger1, info1, logger2, base, sub, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_site3_python_dedup_no_double_edge(self) -> None:
        # Sub.run already has a resolved calls edge to Logger.info; the Site-3
        # resolution of self.log.info() would target the same pair -> deduped
        # (covers the un-pragma'd existing_call_pairs guard).
        logger = _py_cls("sym:py.Logger", "Logger", path="/log.py")
        info = _py_method("sym:py.Logger.info", "Logger.info", path="/log.py")
        base = _py_cls_fields("sym:py.Base", "Base", {"log": "Logger"}, path="/base.py")
        sub = _py_cls("sym:py.Sub", "Sub", path="/sub.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            # a pre-existing RESOLVED calls edge caller -> Logger.info
            Edge.create(
                src=caller.id, dst=info.id, edge_type="calls", line=1,
                origin="test", origin_run_id="test", is_resolved=True,
            ),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger, info, base, sub, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        # No NEW edge minted (the resolved target is already covered).
        assert result.edges == []

    def test_site3_java_ambiguous_still_permissive(self) -> None:
        # Java is NOT in _SITE1_STRICT_LANGS: two same-name enclosing classes
        # still resolve via first-match (proves the guard is Python-scoped).
        logger = _cls("sym:jv.Logger", "Logger", path="/Logger.java", language="java")
        info = _method(
            "sym:jv.Logger.info", "Logger.info", path="/Logger.java", language="java"
        )
        base = Symbol(
            id="sym:jv.Base", name="Base", kind="class", language="java",
            path="/Base.java",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run", meta={"fields": {"log": "Logger"}},
        )
        s1 = _cls("sym:jv.Sub1", "Sub", path="/Sub1.java", language="java")
        s2 = _cls("sym:jv.Sub2", "Sub", path="/Sub2.java", language="java")
        caller = _caller(sid="sym:jv.Sub.run", language="java")
        edges = [
            _edge(s1.id, base.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="log.info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="java",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger, info, base, s1, s2, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == info.id


class TestWiSupatConcreteClassId:
    """WI-supat (D3): a CONCRETE, authoritative class id threaded through the
    Site meta contract lets the linker resolve a same-short-name / cross-language
    namesake PRECISELY instead of biasing to unresolved via the len>1 ambiguity
    guard. The id is trusted only when it is a real ``class_symbols`` key of the
    caller's OWN language; absent / stale / foreign-language ids fall back to the
    name+guard path (byte-identical to pre-WI-supat behavior — the existing
    ``*_biases_unresolved`` tests stay GREEN as the no-id fallback locks)."""

    # ---- Site-1 (enclosing_class_id) ----

    def test_site1_id_resolves_right_namesake(self) -> None:
        # Two python "Worker" classes: w1 extends Base (Base.run), w2 unrelated.
        # NAME-only biases to unresolved (test_site1_python_collision_biases_...);
        # a concrete enclosing_class_id=w1.id skips the guard and resolves Base.run.
        base = _py_cls("sym:py.Base", "Base", path="/base.py")
        base_run = _py_method("sym:py.Base.run", "Base.run", path="/base.py")
        w1 = _py_cls("sym:py.Worker1", "Worker", path="/w1.py")
        w2 = _py_cls("sym:py.Worker2", "Worker", path="/w2.py")
        caller = _py_caller()
        edges = [
            _edge(w1.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="run",
                enclosing_class="Worker", lang="python",
                enclosing_class_id=w1.id,
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, base_run, w1, w2, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == base_run.id
        assert resolved[0].evidence_type == "ast_call_inherited"
        assert resolved[0].confidence == 0.90

    def test_site1_id_selects_wrong_namesake_stays_unresolved(self) -> None:
        # PAIRED NEGATIVE: enclosing_class_id=w2.id (the namesake WITHOUT the
        # inherited method). The linker walks w2's (empty) MRO and finds nothing;
        # it must NOT fall back to "whichever same-name class resolves". Pins that
        # the id SELECTS the concrete enclosing class.
        base = _py_cls("sym:py.Base", "Base", path="/base.py")
        base_run = _py_method("sym:py.Base.run", "Base.run", path="/base.py")
        w1 = _py_cls("sym:py.Worker1", "Worker", path="/w1.py")
        w2 = _py_cls("sym:py.Worker2", "Worker", path="/w2.py")
        caller = _py_caller()
        edges = [
            _edge(w1.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="run",
                enclosing_class="Worker", lang="python",
                enclosing_class_id=w2.id,
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, base_run, w1, w2, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_site1_crosslang_namesake_id_resolves(self) -> None:
        # FM3 recovery: a python "Config" + a java "Config" both inflate
        # class_ids_by_name["Config"] (language-agnostic), so the NAME path
        # over-suppresses a python call whose python enclosing class is unique.
        # enclosing_class_id=py_config.id resolves it precisely (sibling of the
        # existing test_site1_python_crosslang_namesake_over_suppressed no-id lock).
        base = _py_cls("sym:py.PyBase", "PyBase", path="/base.py")
        base_m = _py_method("sym:py.PyBase.m", "PyBase.m", path="/base.py")
        py_config = _py_cls("sym:py.Config", "Config", path="/cfg.py")
        jv_config = _cls(
            "sym:jv.Config", "Config", path="/Config.java", language="java"
        )
        caller = _py_caller()
        edges = [
            _edge(py_config.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="m",
                enclosing_class="Config", lang="python",
                enclosing_class_id=py_config.id,
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, base_m, py_config, jv_config, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == base_m.id

    def test_site1_foreign_language_id_rejected(self) -> None:
        # A python edge whose enclosing_class_id is a JAVA class id (present in
        # class_symbols, wrong language). The ``language == src_lang`` guard
        # rejects it -> name+guard fallback -> two python Config collide (len>1)
        # -> unresolved. Closes FM3's cross-language leak fully.
        # NON-VACUITY: jv_config is made RESOLVABLE (extends jv_base which defines
        # `m`), so WITHOUT the language guard the python C3 walker would bind the
        # java JBase.m @0.90 — the test's `edges == []` would then FAIL. The guard
        # is thus load-bearing, not merely asserted against an inert fixture.
        base = _py_cls("sym:py.PyBase", "PyBase", path="/base.py")
        base_m = _py_method("sym:py.PyBase.m", "PyBase.m", path="/base.py")
        py_config = _py_cls("sym:py.Config", "Config", path="/cfg.py")
        py_config2 = _py_cls("sym:py.Config2", "Config", path="/cfg2.py")
        jv_config = _cls(
            "sym:jv.Config", "Config", path="/Config.java", language="java"
        )
        jv_base = _cls("sym:jv.JBase", "JBase", path="/JBase.java", language="java")
        jv_base_m = _method(
            "sym:jv.JBase.m", "JBase.m", path="/JBase.java", language="java"
        )
        caller = _py_caller()
        edges = [
            _edge(py_config.id, base.id, "extends"),
            _edge(jv_config.id, jv_base.id, "extends"),  # makes the java id resolvable
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="m",
                enclosing_class="Config", lang="python",
                enclosing_class_id=jv_config.id,
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, base_m, py_config, py_config2, jv_config,
                     jv_base, jv_base_m, caller],
            edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_site1_stale_id_falls_back_to_guard(self) -> None:
        # enclosing_class_id points at a symbol NOT in class_symbols (removed /
        # renamed between producer and linker). Falls back to name+guard; the
        # same-name collision biases to unresolved.
        base = _py_cls("sym:py.Base", "Base", path="/base.py")
        base_run = _py_method("sym:py.Base.run", "Base.run", path="/base.py")
        w1 = _py_cls("sym:py.Worker1", "Worker", path="/w1.py")
        w2 = _py_cls("sym:py.Worker2", "Worker", path="/w2.py")
        caller = _py_caller()
        edges = [
            _edge(w1.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="run",
                enclosing_class="Worker", lang="python",
                enclosing_class_id="sym:py.GhostWorker",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, base_run, w1, w2, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_site1_id_unambiguous_no_regression(self) -> None:
        # A single "Sub" (no collision). id and name path agree; assert EXACTLY
        # ONE edge, same evidence/confidence as the name path (no double-emit).
        base = _py_cls("sym:py.Base", "Base", path="/base.py")
        base_m = _py_method("sym:py.Base.m", "Base.m", path="/base.py")
        sub = _py_cls("sym:py.Sub", "Sub", path="/sub.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="m",
                enclosing_class="Sub", lang="python",
                enclosing_class_id=sub.id,
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, base_m, sub, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == base_m.id
        assert resolved[0].evidence_type == "ast_call_inherited"

    # ---- Site-2 (receiver_type_id) ----

    def test_site2_id_resolves_right_namesake(self) -> None:
        # Two python "Foo" (foo_a has Foo.bar, foo_b none). receiver_type_id=
        # foo_a.id skips the len>1 guard and resolves Step-1 direct @0.85.
        foo_a = _py_cls("sym:py.FooA", "Foo", path="/a.py")
        bar = _py_method("sym:py.FooA.bar", "Foo.bar", path="/a.py")
        foo_b = _py_cls("sym:py.FooB", "Foo", path="/b.py")
        caller = _py_caller()
        unresolved = _unresolved_site2_lang(
            src_id=caller.id, callee_name="bar",
            receiver_type_hint="Foo", lang="python",
            receiver_type_id=foo_a.id,
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[foo_a, bar, foo_b, caller], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == bar.id
        assert resolved[0].evidence_type == "ast_call_type_inferred"
        assert resolved[0].confidence == 0.85

    def test_site2_id_via_mro_disambiguated(self) -> None:
        # receiver_type_id points at sub1 (no direct bar); bar is on Base up the
        # C3 MRO -> Step-2 -> ast_call_inherited_method @0.70. Two "Sub" classes
        # would collide by name; the id disambiguates.
        base = _py_cls("sym:py.Base", "Base", path="/base.py")
        bar = _py_method("sym:py.Base.bar", "Base.bar", path="/base.py")
        sub1 = _py_cls("sym:py.Sub1", "Sub", path="/s1.py")
        sub2 = _py_cls("sym:py.Sub2", "Sub", path="/s2.py")
        caller = _py_caller()
        edges = [
            _edge(sub1.id, base.id, "extends"),
            _unresolved_site2_lang(
                src_id=caller.id, callee_name="bar",
                receiver_type_hint="Sub", lang="python",
                receiver_type_id=sub1.id,
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, bar, sub1, sub2, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == bar.id
        assert resolved[0].evidence_type == "ast_call_inherited_method"

    def test_site2_id_wrong_namesake_no_step3_stays_unresolved(self) -> None:
        # receiver_type_id=foo_b.id (the "Foo" WITHOUT bar). Not on foo_b directly
        # nor via MRO. Python is strict -> Step-3 (calls->class type-symbol
        # fallback) stays OFF -> unresolved. Pins that the id selects the TYPE and
        # NEVER re-opens the calls->class ratchet partition.
        foo_a = _py_cls("sym:py.FooA", "Foo", path="/a.py")
        bar = _py_method("sym:py.FooA.bar", "Foo.bar", path="/a.py")
        foo_b = _py_cls("sym:py.FooB", "Foo", path="/b.py")
        caller = _py_caller()
        unresolved = _unresolved_site2_lang(
            src_id=caller.id, callee_name="bar",
            receiver_type_hint="Foo", lang="python",
            receiver_type_id=foo_b.id,
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[foo_a, bar, foo_b, caller], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_site2_foreign_language_id_rejected(self) -> None:
        # python edge, receiver_type_id = a java class id. language guard rejects
        # -> name+guard fallback -> two python Foo collide -> unresolved.
        # NON-VACUITY: jv_foo defines `bar` DIRECTLY (jv_bar, same java file), so
        # WITHOUT the language guard Site-2 Step-1 would resolve jv_foo.bar — the
        # `edges == []` assertion would then FAIL, proving the guard load-bearing.
        foo_a = _py_cls("sym:py.FooA", "Foo", path="/a.py")
        bar = _py_method("sym:py.FooA.bar", "Foo.bar", path="/a.py")
        foo_b = _py_cls("sym:py.FooB", "Foo", path="/b.py")
        jv_foo = _cls("sym:jv.Foo", "Foo", path="/Foo.java", language="java")
        jv_bar = _method(
            "sym:jv.Foo.bar", "Foo.bar", path="/Foo.java", language="java"
        )
        caller = _py_caller()
        unresolved = _unresolved_site2_lang(
            src_id=caller.id, callee_name="bar",
            receiver_type_hint="Foo", lang="python",
            receiver_type_id=jv_foo.id,
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[foo_a, bar, foo_b, jv_foo, jv_bar, caller],
            edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_site2_stale_id_falls_back(self) -> None:
        foo_a = _py_cls("sym:py.FooA", "Foo", path="/a.py")
        bar = _py_method("sym:py.FooA.bar", "Foo.bar", path="/a.py")
        foo_b = _py_cls("sym:py.FooB", "Foo", path="/b.py")
        caller = _py_caller()
        unresolved = _unresolved_site2_lang(
            src_id=caller.id, callee_name="bar",
            receiver_type_hint="Foo", lang="python",
            receiver_type_id="sym:py.GhostFoo",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[foo_a, bar, foo_b, caller], edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    # ---- Site-3 enclosing (enclosing_class_id) ----

    def test_site3_enclosing_id_resolves_right_namesake(self) -> None:
        # Two python "Sub" (sub1 extends Base{fields log:Logger}, sub2 unrelated);
        # Logger.info exists. enclosing_class_id=sub1.id skips the enclosing guard.
        logger = _py_cls("sym:py.Logger", "Logger", path="/log.py")
        info = _py_method("sym:py.Logger.info", "Logger.info", path="/log.py")
        base = _py_cls_fields(
            "sym:py.Base", "Base", {"log": "Logger"}, path="/base.py"
        )
        sub1 = _py_cls("sym:py.Sub1", "Sub", path="/s1.py")
        sub2 = _py_cls("sym:py.Sub2", "Sub", path="/s2.py")
        caller = _py_caller()
        edges = [
            _edge(sub1.id, base.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python", enclosing_class_id=sub1.id,
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger, info, base, sub1, sub2, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == info.id
        assert resolved[0].evidence_type == "ast_call_inherited_field"
        assert resolved[0].confidence == 0.80

    def test_site3_enclosing_id_wrong_namesake_stays_unresolved(self) -> None:
        # PAIRED NEGATIVE: enclosing_class_id=sub2.id (the Sub WITHOUT the base
        # carrying the field) -> parent-walk finds no field -> unresolved.
        logger = _py_cls("sym:py.Logger", "Logger", path="/log.py")
        info = _py_method("sym:py.Logger.info", "Logger.info", path="/log.py")
        base = _py_cls_fields(
            "sym:py.Base", "Base", {"log": "Logger"}, path="/base.py"
        )
        sub1 = _py_cls("sym:py.Sub1", "Sub", path="/s1.py")
        sub2 = _py_cls("sym:py.Sub2", "Sub", path="/s2.py")
        caller = _py_caller()
        edges = [
            _edge(sub1.id, base.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python", enclosing_class_id=sub2.id,
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger, info, base, sub1, sub2, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_site3_enclosing_foreign_language_id_rejected(self) -> None:
        # python edge, enclosing_class_id = a java "Sub" class id. language guard
        # rejects -> name+guard fallback -> three "Sub" (2 py + 1 jv) collide by
        # name (len>1, strict) -> unresolved.
        # NON-VACUITY: jv_sub is RESOLVABLE (extends jv_base whose meta['fields']
        # declares log:Logger, and Logger.info exists), so WITHOUT the language
        # guard the parent-field walk from the java Sub would resolve Logger.info
        # -> the `edges == []` assertion would FAIL. Guard is load-bearing.
        logger = _py_cls("sym:py.Logger", "Logger", path="/log.py")
        info = _py_method("sym:py.Logger.info", "Logger.info", path="/log.py")
        base = _py_cls_fields(
            "sym:py.Base", "Base", {"log": "Logger"}, path="/base.py"
        )
        sub1 = _py_cls("sym:py.Sub1", "Sub", path="/s1.py")
        sub2 = _py_cls("sym:py.Sub2", "Sub", path="/s2.py")
        jv_sub = _cls("sym:jv.Sub", "Sub", path="/Sub.java", language="java")
        jv_base = Symbol(
            id="sym:jv.JBase", name="JBase", kind="class", language="java",
            path="/JBase.java",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run",
            meta={"fields": {"log": "Logger"}},
        )
        caller = _py_caller()
        edges = [
            _edge(sub1.id, base.id, "extends"),
            _edge(jv_sub.id, jv_base.id, "extends"),  # makes the java id resolvable
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python", enclosing_class_id=jv_sub.id,
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger, info, base, sub1, sub2, jv_sub, jv_base, caller],
            edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []


class TestWiSupatFieldTypeId:
    """WI-supat (D3) PR-B: a concrete field-TYPE id, threaded on the PARENT
    class symbol's meta['field_type_ids'], lets Site-3 resolve a same-short-name
    field-type collision precisely instead of biasing to unresolved. Absent
    (java/legacy fields-only), stale, or foreign-language ids fall back to the
    name+guard path."""

    def test_walk_parents_for_field_returns_type_id_on_hit(self) -> None:
        # The 2-tuple contract: (type_name, type_id) when the parent carries
        # field_type_ids.
        from hypergumbo_core.linkers.inherited_calls import (
            _walk_parents_for_field,
        )
        child = _py_cls("sym:py.Child", "Child", path="/c.py")
        parent = _py_cls_fields(
            "sym:py.Parent", "Parent", {"log": "Logger"},
            path="/p.py", field_ids={"log": "sym:py.Logger"},
        )
        inheritance_index = {child.id: [(parent.id, "extends")]}
        class_symbols = {child.id: child, parent.id: parent}
        result = _walk_parents_for_field(
            child.id, "log", inheritance_index, class_symbols,
        )
        assert result == ("Logger", "sym:py.Logger")

    def test_walk_parents_for_field_none_id_when_absent(self) -> None:
        # Legacy / java shape: meta['fields'] present, no field_type_ids ->
        # (type_name, None). Preserves java Site-3 (falls to the name path).
        from hypergumbo_core.linkers.inherited_calls import (
            _walk_parents_for_field,
        )
        child = _py_cls("sym:py.Child", "Child", path="/c.py")
        parent = _py_cls_fields(
            "sym:py.Parent", "Parent", {"log": "Logger"}, path="/p.py",
        )
        inheritance_index = {child.id: [(parent.id, "extends")]}
        class_symbols = {child.id: child, parent.id: parent}
        result = _walk_parents_for_field(
            child.id, "log", inheritance_index, class_symbols,
        )
        assert result == ("Logger", None)

    def test_field_type_id_resolves_right_namesake(self) -> None:
        # Unique enclosing Sub; the field's TYPE name "Logger" resolves to TWO
        # classes (logger1 has info, logger2 none). field_type_ids picks logger1
        # -> resolves (was [] under the field-type len>1 guard, B9).
        logger1 = _py_cls("sym:py.Logger1", "Logger", path="/log1.py")
        info1 = _py_method("sym:py.Logger1.info", "Logger.info", path="/log1.py")
        logger2 = _py_cls("sym:py.Logger2", "Logger", path="/log2.py")
        base = _py_cls_fields(
            "sym:py.Base", "Base", {"log": "Logger"}, path="/base.py",
            field_ids={"log": logger1.id},
        )
        sub = _py_cls("sym:py.Sub", "Sub", path="/sub.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger1, info1, logger2, base, sub, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == info1.id
        assert resolved[0].evidence_type == "ast_call_inherited_field"
        assert resolved[0].confidence == 0.80

    def test_field_type_id_wrong_namesake_stays_unresolved(self) -> None:
        # PAIRED NEGATIVE: field_type_ids points at logger2 (the "Logger" WITHOUT
        # info) -> resolves nothing on logger2 -> unresolved (the id SELECTS).
        logger1 = _py_cls("sym:py.Logger1", "Logger", path="/log1.py")
        info1 = _py_method("sym:py.Logger1.info", "Logger.info", path="/log1.py")
        logger2 = _py_cls("sym:py.Logger2", "Logger", path="/log2.py")
        base = _py_cls_fields(
            "sym:py.Base", "Base", {"log": "Logger"}, path="/base.py",
            field_ids={"log": logger2.id},
        )
        sub = _py_cls("sym:py.Sub", "Sub", path="/sub.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger1, info1, logger2, base, sub, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_field_type_stale_id_falls_back_to_guard(self) -> None:
        # field_type_ids points at a symbol not in class_symbols -> fall back to
        # the field-type name path; the "Logger" collision then biases to [].
        logger1 = _py_cls("sym:py.Logger1", "Logger", path="/log1.py")
        info1 = _py_method("sym:py.Logger1.info", "Logger.info", path="/log1.py")
        logger2 = _py_cls("sym:py.Logger2", "Logger", path="/log2.py")
        base = _py_cls_fields(
            "sym:py.Base", "Base", {"log": "Logger"}, path="/base.py",
            field_ids={"log": "sym:py.GhostLogger"},
        )
        sub = _py_cls("sym:py.Sub", "Sub", path="/sub.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger1, info1, logger2, base, sub, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_field_type_foreign_language_id_rejected(self) -> None:
        # A python edge whose field_type_ids points at a JAVA "Logger" (with
        # info). The language guard rejects it -> name path -> three "Logger"
        # (1 jv + 2 py) collide -> unresolved.
        # NON-VACUITY: jv_logger.info is DIRECTLY resolvable, so WITHOUT the
        # language guard the field-type id path would bind jv.Logger.info.
        jv_logger = _cls(
            "sym:jv.Logger", "Logger", path="/Logger.java", language="java"
        )
        jv_info = _method(
            "sym:jv.Logger.info", "Logger.info", path="/Logger.java",
            language="java",
        )
        py_logger1 = _py_cls("sym:py.Logger1", "Logger", path="/log1.py")
        py_logger2 = _py_cls("sym:py.Logger2", "Logger", path="/log2.py")
        base = _py_cls_fields(
            "sym:py.Base", "Base", {"log": "Logger"}, path="/base.py",
            field_ids={"log": jv_logger.id},
        )
        sub = _py_cls("sym:py.Sub", "Sub", path="/sub.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[jv_logger, jv_info, py_logger1, py_logger2, base, sub,
                     caller],
            edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert result.edges == []

    def test_field_type_id_absent_but_unambiguous_name_resolves(self) -> None:
        # Mixed / backward-compat: no field_type_ids, single "Logger" -> the name
        # path resolves with no collision (existing python Site-3 edges survive
        # the walker return-shape change).
        logger = _py_cls("sym:py.Logger", "Logger", path="/log.py")
        info = _py_method("sym:py.Logger.info", "Logger.info", path="/log.py")
        base = _py_cls_fields(
            "sym:py.Base", "Base", {"log": "Logger"}, path="/base.py",
        )
        sub = _py_cls("sym:py.Sub", "Sub", path="/sub.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger, info, base, sub, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == info.id

    def test_enclosing_id_and_field_type_id_both_skip_guards(self) -> None:
        # BOTH id-preference guard-skips active in ONE edge (belt-and-suspenders,
        # review follow-up): a same-name enclosing 'Sub' collision (PR-A
        # enclosing_class_id disambiguates) AND a same-name field TYPE 'Logger'
        # collision (PR-B field_type_ids disambiguates). Resolves iff both skips
        # compose; dropping either id would bias to unresolved.
        logger1 = _py_cls("sym:py.Logger1", "Logger", path="/log1.py")
        info1 = _py_method("sym:py.Logger1.info", "Logger.info", path="/log1.py")
        logger2 = _py_cls("sym:py.Logger2", "Logger", path="/log2.py")
        base = _py_cls_fields(
            "sym:py.Base", "Base", {"log": "Logger"}, path="/base.py",
            field_ids={"log": logger1.id},
        )
        sub1 = _py_cls("sym:py.Sub1", "Sub", path="/s1.py")
        sub2 = _py_cls("sym:py.Sub2", "Sub", path="/s2.py")
        caller = _py_caller()
        edges = [
            _edge(sub1.id, base.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="info",
                enclosing_class="Sub", inherited_field_receiver="log",
                lang="python", enclosing_class_id=sub1.id,
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[logger1, info1, logger2, base, sub1, sub2, caller],
            edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == info1.id
        assert resolved[0].evidence_type == "ast_call_inherited_field"


class TestCrossLanguageReceiverFilter:
    """INV-milud: every Site turns a receiver / enclosing-class / field-type
    NAME into candidate class ids via the language-agnostic
    ``class_ids_by_name`` index. Without a source-language filter a call in one
    language can bind to a same-short-name class (or its method) in ANOTHER
    language — a confidently-wrong cross-language ``calls`` edge — and a
    foreign-language namesake also inflates the strict ambiguity guard's
    candidate count, suppressing a legitimate same-language resolution as a
    false-negative. MRO / typed-receiver dispatch never crosses a language
    boundary (that is the FFI/bridge linkers' job), so every name->ids lookup
    is filtered to ``src_lang`` at one chokepoint (``_same_language_class_ids``).
    """

    def test_site2_java_fallback_does_not_bind_to_python_namesake_type(
        self,
    ) -> None:
        """Java Site-2 Step-3 fallback (method nowhere on the chain) binds to
        the JAVA type symbol, not a same-named Python class listed first."""
        py_shared = _py_cls("sym:py.Shared", "Shared", path="/shared.py")
        java_shared = _java_cls("sym:java.Shared", "Shared", path="/a.java")
        caller = _java_caller()
        unresolved = _unresolved_site2(
            src_id=caller.id, callee_name="obj.save",
            receiver_type_hint="Shared",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            # Python namesake FIRST so an unfiltered fallback would pick it.
            symbols=[py_shared, java_shared, caller],
            edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == java_shared.id
        by_id = {s.id: s for s in [py_shared, java_shared, caller]}
        assert by_id[resolved[0].dst].language == "java"

    def test_site2_java_does_not_bind_to_python_namesake_method(self) -> None:
        """Java Site-2 Step-1 direct lookup must NOT resolve to a Python
        namesake class's method (the confidently-wrong cross-language bind);
        it falls back to the Java type symbol instead."""
        py_shared = _py_cls("sym:py.Shared", "Shared", path="/shared.py")
        py_save = _py_method(
            "sym:py.Shared.save", "Shared.save", path="/shared.py",
        )
        java_shared = _java_cls("sym:java.Shared", "Shared", path="/a.java")
        caller = _java_caller()
        unresolved = _unresolved_site2(
            src_id=caller.id, callee_name="obj.save",
            receiver_type_hint="Shared",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[py_shared, py_save, java_shared, caller],
            edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert all(e.dst != py_save.id for e in result.edges)
        assert len(resolved) == 1
        assert resolved[0].dst == java_shared.id

    def test_site2_python_resolves_despite_foreign_language_namesake(
        self,
    ) -> None:
        """A Java namesake must not inflate the Python strict-mode ambiguity
        count: exactly one Python ``Widget`` exists, so the call resolves."""
        py_widget = _py_cls("sym:py.Widget", "Widget", path="/w.py")
        py_render = _py_method(
            "sym:py.Widget.render", "Widget.render", path="/w.py",
        )
        java_widget = _java_cls("sym:java.Widget", "Widget", path="/a.java")
        caller = _py_caller()
        unresolved = _unresolved_site2_lang(
            src_id=caller.id, callee_name="w.render",
            receiver_type_hint="Widget", lang="python",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[py_widget, py_render, java_widget, caller],
            edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == py_render.id

    def test_site1_java_does_not_bind_to_python_namesake_method(self) -> None:
        """Site-1 (bare / ``this`` call) must not walk a Python namesake's MRO
        for a Java caller and bind to its method."""
        py_shared = _py_cls("sym:py.Shared", "Shared", path="/shared.py")
        py_save = _py_method(
            "sym:py.Shared.save", "Shared.save", path="/shared.py",
        )
        java_shared = _java_cls("sym:java.Shared", "Shared", path="/a.java")
        caller = _java_caller()
        unresolved = _unresolved_site1_lang(
            src_id=caller.id, callee_name="save",
            enclosing_class="Shared", lang="java",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[py_shared, py_save, java_shared, caller],
            edges=[unresolved],
        )
        result = link_inherited_calls(ctx)
        # Java Shared has no ``save`` and no parent -> unresolved; the Python
        # namesake's ``save`` must never be the target.
        assert all(e.dst != py_save.id for e in result.edges)
        assert [e for e in result.edges if e.src == caller.id] == []

    def test_site3_java_does_not_bind_field_method_cross_language(self) -> None:
        """Site-3 (inherited field receiver) filters the field-TYPE lookup to
        the source language: a Java field-method call resolves to the Java
        type's method, never a same-named Python class's method."""
        py_service = _py_cls("sym:py.Service", "Service", path="/s.py")
        py_run = _py_method(
            "sym:py.Service.run", "Service.run", path="/s.py",
        )
        java_service = _java_cls(
            "sym:java.Service", "Service", path="/svc.java",
        )
        java_run = _java_method(
            "sym:java.Service.run", "Service.run", path="/svc.java",
        )
        base = _java_cls(
            "sym:java.Base", "Base", path="/base.java",
            fields={"dep": "Service"},
        )
        sub = _java_cls("sym:java.Sub", "Sub", path="/sub.java")
        caller = _java_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _unresolved_site3(
                src_id=caller.id, callee_name="dep.run",
                enclosing_class="Sub", inherited_field_receiver="dep",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            # Python namesake FIRST so an unfiltered lookup would pick it.
            symbols=[py_service, py_run, java_service, java_run,
                     base, sub, caller],
            edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert all(e.dst != py_run.id for e in result.edges)
        assert len(resolved) == 1
        assert resolved[0].dst == java_run.id


class TestSite3FieldWalkC3Order:
    """WI-rarab: Site-3's field-declarer walk (``_walk_parents_for_field``)
    must honor Python's C3 MRO order, not insertion-order BFS. On an
    uneven-depth diamond where the same field name is declared at different
    depths with DIVERGENT types, BFS returns a direct base's field while C3
    returns the MRO-earlier ancestor's — the inherited-field method then
    resolves on the correct type."""

    def test_uneven_diamond_field_resolves_via_c3_not_bfs(self) -> None:
        # E(C, D); C(B). B and D both declare field ``dep`` with divergent
        # types. Real MRO of E is [E, C, B, D] -> ``dep`` is B's (BDep). BFS
        # from E checks the direct bases C, D first and would pick D's (DDep).
        bdep = _py_cls("sym:py.BDep", "BDep", path="/bdep.py")
        bdep_run = _py_method("sym:py.BDep.run", "BDep.run", path="/bdep.py")
        ddep = _py_cls("sym:py.DDep", "DDep", path="/ddep.py")
        ddep_run = _py_method("sym:py.DDep.run", "DDep.run", path="/ddep.py")
        b = _py_cls_fields("sym:py.B", "B", {"dep": "BDep"}, path="/b.py",
                           field_ids={"dep": bdep.id})
        c = _py_cls("sym:py.C", "C", path="/c.py")
        d = _py_cls_fields("sym:py.D", "D", {"dep": "DDep"}, path="/d.py",
                           field_ids={"dep": ddep.id})
        e = _py_cls("sym:py.E", "E", path="/e.py")
        caller = _py_caller()
        edges = [
            _edge(c.id, b.id, "extends"),
            _edge(e.id, c.id, "extends"),
            _edge(e.id, d.id, "extends"),
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="run",
                enclosing_class="E", inherited_field_receiver="dep",
                lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[bdep, bdep_run, ddep, ddep_run, b, c, d, e, caller],
            edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [x for x in result.edges if x.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == bdep_run.id   # C3-correct (B), not DDep
        assert all(x.dst != ddep_run.id for x in result.edges)

    def test_field_walk_cyclic_hierarchy_biases_unresolved(self) -> None:
        # An un-linearizable (cyclic) hierarchy -> C3 returns None -> the field
        # walk finds nothing -> no confidently-wrong edge (matches _walk_c3).
        x = _py_cls_fields("sym:py.X", "X", {"own": "Own"}, path="/x.py")
        y = _py_cls("sym:py.Y", "Y", path="/y.py")
        caller = _py_caller()
        edges = [
            _edge(x.id, y.id, "extends"),
            _edge(y.id, x.id, "extends"),   # cycle -> un-linearizable
            _unresolved_site3_lang(
                src_id=caller.id, callee_name="run",
                enclosing_class="X", inherited_field_receiver="dep",
                lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[x, y, caller],
            edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert [x2 for x2 in result.edges if x2.src == caller.id] == []


def _py_cls_bases(sid: str, name: str, bases: list, path: str = "/m.py") -> Symbol:
    """Python class symbol carrying meta['base_classes'] (declared base order)."""
    return Symbol(
        id=sid, name=name, kind="class", language="python", path=path,
        span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
        meta={"base_classes": list(bases)},
    )


class TestInvGuvivStdlibBaseShadow:
    """INV-guviv: an external stdlib base (``dict``/``list``/...) is invisible to
    the in-tree C3 walk. When such a base is declared BEFORE the in-tree ancestor
    AND defines the called method, Python's real MRO dispatches to the (unseen)
    stdlib method — so the walk's in-tree binding is confidently wrong. A
    hardcoded stdlib-base-method catalog (sibling of DJANGO_BASE_METHODS /
    THIRD_PARTY_BASE_METHODS, per ADR-0029) gates those to unresolved, WITHOUT
    over-suppressing the external-mixin-first idiom (non-stdlib bases are not
    cataloged).
    """

    def test_catalog_has_container_types(self) -> None:
        from hypergumbo_core.linkers.inherited_calls import _STDLIB_BASE_METHODS
        assert "pop" in _STDLIB_BASE_METHODS["dict"]
        assert "keys" in _STDLIB_BASE_METHODS["dict"]
        assert "append" in _STDLIB_BASE_METHODS["list"]
        assert "add" in _STDLIB_BASE_METHODS["set"]
        assert "split" in _STDLIB_BASE_METHODS["str"]

    def test_site1_stdlib_base_shadows_inherited_method(self) -> None:
        # class Repo(dict, LogMixin): self.pop() — dict (external, invisible)
        # defines pop and is declared first → Python dispatches dict.pop, not
        # LogMixin.pop. Bias to unresolved.
        mixin = _py_cls("sym:py.LogMixin", "LogMixin", path="/mixin.py")
        mixin_pop = _py_method("sym:py.LogMixin.pop", "LogMixin.pop", path="/mixin.py")
        repo = _py_cls_bases("sym:py.Repo", "Repo", ["dict", "LogMixin"], path="/repo.py")
        caller = _py_caller()
        edges = [
            _edge(repo.id, mixin.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="pop",
                enclosing_class="Repo", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[mixin, mixin_pop, repo, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert [e for e in result.edges if e.src == caller.id] == []

    def test_site1_stdlib_base_not_defining_method_resolves(self) -> None:
        # class Repo(dict, LogMixin): self.log_it() — dict does NOT define
        # log_it, so LogMixin.log_it is the correct (unshadowed) target.
        mixin = _py_cls("sym:py.LogMixin", "LogMixin", path="/mixin.py")
        mixin_m = _py_method("sym:py.LogMixin.log_it", "LogMixin.log_it", path="/mixin.py")
        repo = _py_cls_bases("sym:py.Repo", "Repo", ["dict", "LogMixin"], path="/repo.py")
        caller = _py_caller()
        edges = [
            _edge(repo.id, mixin.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="log_it",
                enclosing_class="Repo", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[mixin, mixin_m, repo, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == mixin_m.id

    def test_site1_stdlib_base_after_intree_base_resolves(self) -> None:
        # class Repo(LogMixin, dict): self.pop() — dict is AFTER LogMixin, so
        # LogMixin.pop wins the real MRO (no shadow).
        mixin = _py_cls("sym:py.LogMixin", "LogMixin", path="/mixin.py")
        mixin_pop = _py_method("sym:py.LogMixin.pop", "LogMixin.pop", path="/mixin.py")
        repo = _py_cls_bases("sym:py.Repo", "Repo", ["LogMixin", "dict"], path="/repo.py")
        caller = _py_caller()
        edges = [
            _edge(repo.id, mixin.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="pop",
                enclosing_class="Repo", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[mixin, mixin_pop, repo, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == mixin_pop.id

    def test_site1_class_defines_method_itself_not_shadowed(self) -> None:
        # class Repo(dict, LogMixin) where Repo ITSELF defines pop → Repo.pop
        # (the class's own method is first in the MRO; never shadowed).
        mixin = _py_cls("sym:py.LogMixin", "LogMixin", path="/mixin.py")
        repo = _py_cls_bases("sym:py.Repo", "Repo", ["dict", "LogMixin"], path="/repo.py")
        repo_pop = _py_method("sym:py.Repo.pop", "Repo.pop", path="/repo.py")
        caller = _py_caller()
        edges = [
            _edge(repo.id, mixin.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="pop",
                enclosing_class="Repo", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[mixin, repo, repo_pop, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == repo_pop.id

    def test_site1_nonstdlib_external_mixin_still_resolves(self) -> None:
        # The external-mixin-first idiom: class C(LoginRequiredMixin, AuditMixin)
        # calling self.log_action(). LoginRequiredMixin is external but NOT a
        # stdlib type, so it is not cataloged → no shadow → AuditMixin.log_action
        # resolves (the exact over-suppression the naive gate would have caused).
        audit = _py_cls("sym:py.AuditMixin", "AuditMixin", path="/audit.py")
        audit_m = _py_method("sym:py.AuditMixin.log_action", "AuditMixin.log_action", path="/audit.py")
        view = _py_cls_bases(
            "sym:py.UserView", "UserView",
            ["auth.LoginRequiredMixin", "AuditMixin"], path="/view.py",
        )
        caller = _py_caller()
        edges = [
            _edge(view.id, audit.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="log_action",
                enclosing_class="UserView", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[audit, audit_m, view, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == audit_m.id

    def test_site2_stdlib_base_shadows(self) -> None:
        # d: MyDict where class MyDict(dict, Base): d.pop() — same shadow at the
        # typed-receiver site.
        base = _py_cls("sym:py.Base", "Base", path="/base.py")
        base_pop = _py_method("sym:py.Base.pop", "Base.pop", path="/base.py")
        mydict = _py_cls_bases("sym:py.MyDict", "MyDict", ["dict", "Base"], path="/md.py")
        caller = _py_caller()
        edges = [
            _edge(mydict.id, base.id, "extends"),
            _unresolved_site2_lang(
                src_id=caller.id, callee_name="pop",
                receiver_type_hint="MyDict", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[base, base_pop, mydict, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        assert [e for e in result.edges if e.src == caller.id] == []

    def test_site2_stdlib_base_not_defining_method_resolves(self) -> None:
        base = _py_cls("sym:py.Base", "Base", path="/base.py")
        base_m = _py_method("sym:py.Base.query", "Base.query", path="/base.py")
        mydict = _py_cls_bases("sym:py.MyDict", "MyDict", ["dict", "Base"], path="/md.py")
        caller = _py_caller()
        edges = [
            _edge(mydict.id, base.id, "extends"),
            _unresolved_site2_lang(
                src_id=caller.id, callee_name="query",
                receiver_type_hint="MyDict", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[base, base_m, mydict, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == base_m.id

    def test_site1_declared_base_name_mismatch_no_false_shadow(self) -> None:
        # The declared base name (an import alias "BaseAlias") does not match the
        # in-tree parent symbol's name ("RealBase"); the walker resolves via the
        # extends edge regardless, and the shadow scan (which keys on declared
        # names) must NOT fire — a non-stdlib, non-matching name is not a shadow.
        base = _py_cls("sym:py.RealBase", "RealBase", path="/base.py")
        base_m = _py_method("sym:py.RealBase.compute", "RealBase.compute", path="/base.py")
        sub = _py_cls_bases("sym:py.Sub", "Sub", ["BaseAlias"], path="/sub.py")
        caller = _py_caller()
        edges = [
            _edge(sub.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="compute",
                enclosing_class="Sub", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=[base, base_m, sub, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == base_m.id

    def test_site1_intree_base_aliased_to_builtin_name_no_false_shadow(self) -> None:
        # F1: an in-tree class import-aliased to a builtin's exact name
        # (`from .m import DictBase as dict; class Repo(dict, Base)`). The extends
        # edge names the parent "DictBase" but meta['base_classes'] records "dict".
        # The alignment guard (in-tree parent names must appear among declared
        # bases) detects the divergence and biases to no-shadow — resolving the
        # correct DictBase.pop rather than falsely suppressing it.
        dictbase = _py_cls("sym:py.DictBase", "DictBase", path="/m.py")
        dictbase_pop = _py_method("sym:py.DictBase.pop", "DictBase.pop", path="/m.py")
        base = _py_cls("sym:py.Base", "Base", path="/base.py")
        repo = _py_cls_bases("sym:py.Repo", "Repo", ["dict", "Base"], path="/repo.py")
        caller = _py_caller()
        edges = [
            _edge(repo.id, dictbase.id, "extends"),
            _edge(repo.id, base.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="pop",
                enclosing_class="Repo", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[dictbase, dictbase_pop, base, repo, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == dictbase_pop.id

    def test_site1_builtin_between_intree_bases_is_undersuppression_residual(self) -> None:
        # F2 (documented UNDER-suppression residual, SAFE direction): class
        # Repo(FirstMixin, dict, SecondMixin) where FirstMixin lacks pop, dict has
        # pop, SecondMixin has pop. Real MRO dispatches dict.pop, but the scan
        # stops at the first in-tree base (FirstMixin) and does not fire — leaving
        # the pre-existing (possibly-wrong) SecondMixin.pop edge. Never
        # over-suppresses; asserted to lock the residual eyes-open.
        first = _py_cls("sym:py.FirstMixin", "FirstMixin", path="/f.py")
        second = _py_cls("sym:py.SecondMixin", "SecondMixin", path="/s.py")
        second_pop = _py_method("sym:py.SecondMixin.pop", "SecondMixin.pop", path="/s.py")
        repo = _py_cls_bases(
            "sym:py.Repo", "Repo", ["FirstMixin", "dict", "SecondMixin"], path="/repo.py",
        )
        caller = _py_caller()
        edges = [
            _edge(repo.id, first.id, "extends"),
            _edge(repo.id, second.id, "extends"),
            _unresolved_site1_lang(
                src_id=caller.id, callee_name="pop",
                enclosing_class="Repo", lang="python",
            ),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[first, second, second_pop, repo, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == second_pop.id


# ---------------------------------------------------------------------------
# Scala / Swift MRO walkers (WI-nazab / WI-sojim).
# ---------------------------------------------------------------------------


def _mk_index(*symbols: Symbol) -> "object":
    """build_method_index over class + method symbols (classes by short name)."""
    from hypergumbo_core.linkers.type_hierarchy import build_method_index

    class_ids_by_name: dict[str, list[str]] = {}
    class_symbols: dict[str, Symbol] = {}
    for s in symbols:
        if s.kind in ("class", "struct", "module", "interface", "trait",
                      "protocol"):
            class_ids_by_name.setdefault(s.name, []).append(s.id)
            class_symbols[s.id] = s
    return build_method_index(list(symbols), class_ids_by_name, class_symbols)


class TestWalkLinearization:
    """Scala right-to-left BFS (rightmost mixin wins, superclass last)."""

    def test_direct_method_on_start_class(self) -> None:
        c = _cls("sym:C", "C", language="scala")
        c_foo = _method("sym:C#foo", "C#foo", language="scala")
        idx = _mk_index(c, c_foo)
        result = _walk_linearization(
            start_class_id=c.id, callee_short_name="foo",
            inheritance_index={}, method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == c_foo.id

    def test_one_hop_via_superclass(self) -> None:
        base = _cls("sym:Base", "Base", language="scala")
        base_save = _method("sym:Base#save", "Base#save", language="scala")
        child = _cls("sym:Child", "Child", language="scala")
        idx = _mk_index(base, base_save, child)
        result = _walk_linearization(
            start_class_id=child.id, callee_short_name="save",
            inheritance_index={child.id: [(base.id, "extends")]},
            method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == base_save.id

    def test_rightmost_mixin_wins(self) -> None:
        """C extends B with T1 with T2; both traits define foo → T2 wins."""
        t1 = _cls("sym:T1", "T1", language="scala")
        t1_foo = _method("sym:T1#foo", "T1#foo", language="scala")
        t2 = _cls("sym:T2", "T2", language="scala")
        t2_foo = _method("sym:T2#foo", "T2#foo", language="scala")
        c = _cls("sym:C", "C", language="scala")
        idx = _mk_index(t1, t1_foo, t2, t2_foo, c)
        # Declaration order [T1, T2]; linearization reverses → T2 first.
        result = _walk_linearization(
            start_class_id=c.id, callee_short_name="foo",
            inheritance_index={c.id: [(t1.id, "extends"), (t2.id, "extends")]},
            method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == t2_foo.id

    def test_returns_none_when_not_found(self) -> None:
        a = _cls("sym:A", "A", language="scala")
        b = _cls("sym:B", "B", language="scala")
        idx = _mk_index(a, b)
        result = _walk_linearization(
            start_class_id=b.id, callee_short_name="missing",
            inheritance_index={b.id: [(a.id, "extends")]},
            method_index=idx, depth_cap=10,
        )
        assert result is None

    def test_depth_cap_stops_walk(self) -> None:
        chain = [_cls(f"sym:S{i}", f"S{i}", language="scala") for i in range(6)]
        deep_foo = _method("sym:S5#foo", "S5#foo", language="scala")
        idx = _mk_index(*chain, deep_foo)
        inheritance = {
            chain[i].id: [(chain[i + 1].id, "extends")]
            for i in range(5)
        }
        result = _walk_linearization(
            start_class_id=chain[0].id, callee_short_name="foo",
            inheritance_index=inheritance, method_index=idx, depth_cap=2,
        )
        assert result is None

    def test_diamond_visited_dedup(self) -> None:
        """A parent reachable via two paths is examined once (no re-queue)."""
        d = _cls("sym:D", "D", language="scala")
        d_foo = _method("sym:D#foo", "D#foo", language="scala")
        a = _cls("sym:A", "A", language="scala")
        b = _cls("sym:B", "B", language="scala")
        c = _cls("sym:C", "C", language="scala")
        idx = _mk_index(d, d_foo, a, b, c)
        result = _walk_linearization(
            start_class_id=c.id, callee_short_name="foo",
            inheritance_index={
                c.id: [(a.id, "extends"), (b.id, "extends")],
                a.id: [(d.id, "extends")],
                b.id: [(d.id, "extends")],
            },
            method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == d_foo.id


class TestWalkLeftToRight:
    """Swift left-to-right pre-order DFS (superclass subtree before siblings)."""

    def test_direct_method_on_start_class(self) -> None:
        c = _cls("sym:C", "C", language="swift")
        c_foo = _method("sym:C#foo", "C#foo", language="swift")
        idx = _mk_index(c, c_foo)
        result = _walk_left_to_right(
            start_class_id=c.id, callee_short_name="foo",
            inheritance_index={}, method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == c_foo.id

    def test_one_hop_via_superclass(self) -> None:
        base = _cls("sym:Base", "Base", language="swift")
        base_run = _method("sym:Base#run", "Base#run", language="swift")
        child = _cls("sym:Child", "Child", language="swift")
        idx = _mk_index(base, base_run, child)
        result = _walk_left_to_right(
            start_class_id=child.id, callee_short_name="run",
            inheritance_index={child.id: [(base.id, "extends")]},
            method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == base_run.id

    def test_depth_first_explores_superclass_subtree_before_sibling(
        self,
    ) -> None:
        """C: SuperA, ProtoB; GrandA (SuperA's parent) and ProtoB both define
        foo → DFS returns GrandA's foo (deep in the left subtree), NOT the
        shallower ProtoB (which a BFS walker would pick first)."""
        grand = _cls("sym:GrandA", "GrandA", language="swift")
        grand_foo = _method("sym:GrandA#foo", "GrandA#foo", language="swift")
        supera = _cls("sym:SuperA", "SuperA", language="swift")
        protob = _cls("sym:ProtoB", "ProtoB", language="swift")
        protob_foo = _method("sym:ProtoB#foo", "ProtoB#foo", language="swift")
        c = _cls("sym:C", "C", language="swift")
        idx = _mk_index(grand, grand_foo, supera, protob, protob_foo, c)
        result = _walk_left_to_right(
            start_class_id=c.id, callee_short_name="foo",
            inheritance_index={
                c.id: [(supera.id, "extends"), (protob.id, "implements")],
                supera.id: [(grand.id, "extends")],
            },
            method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == grand_foo.id

    def test_returns_none_when_not_found(self) -> None:
        a = _cls("sym:A", "A", language="swift")
        b = _cls("sym:B", "B", language="swift")
        idx = _mk_index(a, b)
        result = _walk_left_to_right(
            start_class_id=b.id, callee_short_name="missing",
            inheritance_index={b.id: [(a.id, "extends")]},
            method_index=idx, depth_cap=10,
        )
        assert result is None

    def test_depth_cap_stops_walk(self) -> None:
        chain = [_cls(f"sym:S{i}", f"S{i}", language="swift") for i in range(6)]
        deep_foo = _method("sym:S5#foo", "S5#foo", language="swift")
        idx = _mk_index(*chain, deep_foo)
        inheritance = {
            chain[i].id: [(chain[i + 1].id, "extends")]
            for i in range(5)
        }
        result = _walk_left_to_right(
            start_class_id=chain[0].id, callee_short_name="foo",
            inheritance_index=inheritance, method_index=idx, depth_cap=2,
        )
        assert result is None

    def test_diamond_visited_dedup(self) -> None:
        d = _cls("sym:D", "D", language="swift")
        d_foo = _method("sym:D#foo", "D#foo", language="swift")
        a = _cls("sym:A", "A", language="swift")
        b = _cls("sym:B", "B", language="swift")
        c = _cls("sym:C", "C", language="swift")
        idx = _mk_index(d, d_foo, a, b, c)
        result = _walk_left_to_right(
            start_class_id=c.id, callee_short_name="foo",
            inheritance_index={
                c.id: [(a.id, "extends"), (b.id, "extends")],
                a.id: [(d.id, "extends")],
                b.id: [(d.id, "extends")],
            },
            method_index=idx, depth_cap=10,
        )
        assert result is not None and result.id == d_foo.id

    def test_diamond_visited_skip_on_miss(self) -> None:
        """A miss walks both diamond arms; the second arm's already-visited
        shared ancestor is skipped (the ``parent_id in visited`` guard)."""
        d = _cls("sym:D", "D", language="swift")
        a = _cls("sym:A", "A", language="swift")
        b = _cls("sym:B", "B", language="swift")
        c = _cls("sym:C", "C", language="swift")
        idx = _mk_index(d, a, b, c)  # no method anywhere → walk exhausts
        result = _walk_left_to_right(
            start_class_id=c.id, callee_short_name="foo",
            inheritance_index={
                c.id: [(a.id, "extends"), (b.id, "implements")],
                a.id: [(d.id, "extends")],
                b.id: [(d.id, "extends")],
            },
            method_index=idx, depth_cap=10,
        )
        assert result is None


class TestScalaSwiftMroRegistration:
    def test_scala_and_swift_registered(self) -> None:
        assert _MRO_WALKERS.get("scala") is _walk_linearization
        assert _MRO_WALKERS.get("swift") is _walk_left_to_right


class TestScalaSwiftInheritedCallIntegration:
    """End-to-end Site-2 Step-2 resolution through link_inherited_calls."""

    @staticmethod
    def _caller(lang: str) -> Symbol:
        return Symbol(
            id=f"sym:{lang}.Caller#run", name="Caller#run", kind="method",
            language=lang, path=f"/caller.{lang}",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            origin="test", origin_run_id="test-run", meta=None,
        )

    @staticmethod
    def _site2(src_id: str, callee: str, receiver_type: str,
               lang: str) -> Edge:
        from hypergumbo_core.analyze.base import make_unresolved_edge
        return make_unresolved_edge(
            lang=lang, src_id=src_id, callee_name=callee,
            line=7, pass_id="test-pass", run_id="test-run",
            receiver_type_hint=receiver_type,
        )

    def test_scala_trait_inherited_method_resolves(self) -> None:
        trait = _cls("sym:Loggable", "Loggable", language="scala")
        trait_log = _method("sym:Loggable#log", "Loggable#log",
                            language="scala")
        svc = _cls("sym:Service", "Service", language="scala")
        caller = self._caller("scala")
        edges = [
            _edge(svc.id, trait.id, "extends"),  # Service extends/with Loggable
            self._site2(caller.id, "log", "Service", "scala"),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[trait, trait_log, svc, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == trait_log.id
        assert resolved[0].evidence_type == "ast_call_inherited_method"

    def test_swift_superclass_inherited_method_resolves(self) -> None:
        base = _cls("sym:BaseVC", "BaseVC", language="swift")
        base_load = _method("sym:BaseVC#load", "BaseVC#load", language="swift")
        vc = _cls("sym:HomeVC", "HomeVC", language="swift")
        caller = self._caller("swift")
        edges = [
            _edge(vc.id, base.id, "extends"),
            self._site2(caller.id, "load", "HomeVC", "swift"),
        ]
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[base, base_load, vc, caller], edges=edges,
        )
        result = link_inherited_calls(ctx)
        resolved = [e for e in result.edges if e.src == caller.id]
        assert len(resolved) == 1
        assert resolved[0].dst == base_load.id
        assert resolved[0].evidence_type == "ast_call_inherited_method"
