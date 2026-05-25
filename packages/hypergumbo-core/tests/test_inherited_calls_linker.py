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
            inheritance_index={child.id: [parent.id]},
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
            inheritance_index={c.id: [b.id], b.id: [a.id]},
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
            inheritance_index={b.id: [a.id]}, method_index=idx, depth_cap=10,
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
            inheritance_index={email_worker.id: [sidekiq_worker.id]},
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
        inheritance: dict[str, list[str]] = {
            classes[i].id: [classes[i + 1].id] for i in range(19)
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
            inheritance_index={a.id: [b.id], b.id: [a.id]},
            method_index=idx, depth_cap=10,
        )
        assert result is None


class TestMROWalkerRegistry:
    """The _MRO_WALKERS dispatch table is keyed by language."""

    def test_ruby_resolves_to_insertion_order(self) -> None:
        assert _MRO_WALKERS["ruby"] is _walk_insertion_order

    def test_groovy_resolves_to_insertion_order(self) -> None:
        assert _MRO_WALKERS["groovy"] is _walk_insertion_order


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
        """PR-2 only registers Ruby/Groovy walkers; Java edges are untouched."""
        # Java analyzer is not modified in PR-2, but a hypothetical hint
        # would still be skipped because no java walker is registered yet.
        a = _cls("sym:A", "A", language="java")
        a_init = _method("sym:A.foo", "A.foo", language="java")
        b = _cls("sym:B", "B", language="java")
        caller = _caller(sid="sym:Caller.bar", language="java")
        extends = _edge(b.id, a.id, "extends")
        from hypergumbo_core.analyze.base import make_unresolved_edge
        unresolved = make_unresolved_edge(
            lang="java", src_id=caller.id, callee_name="foo",
            line=1, pass_id="test", run_id="test",
            enclosing_class="B",
        )
        ctx = LinkerContext(
            repo_root=Path("/"),
            symbols=[a, a_init, b, caller], edges=[extends, unresolved],
        )
        result = link_inherited_calls(ctx)
        # PR-2 scope: no java walker yet → no edge.
        assert result.edges == []

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
