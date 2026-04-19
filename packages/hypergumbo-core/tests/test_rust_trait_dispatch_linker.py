# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Rust trait-impl dispatch linker (WI-kivut).

Validates that the linker consumes ``implements`` edges from the Rust
analyzer and emits ``dispatches_to`` edges from the trait symbol to
each method on the concrete impl block that implements the trait.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_core.linkers.rust_trait_dispatch import (
    _build_struct_method_index,
    _rust_method_owner,
    _symbol_index_by_id,
    link_rust_trait_dispatch,
)


def _ctx(
    symbols: list[Symbol], edges: list[Edge] | None = None,
) -> LinkerContext:
    return LinkerContext(
        repo_root=Path("/repo"),
        symbols=symbols,
        edges=list(edges or []),
    )


def _struct(
    name: str, *, path: str = "src/lib.rs", span: tuple[int, int] = (1, 10),
    language: str = "rust",
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{span[0]}-{span[1]}:{name}:struct",
        name=name,
        kind="struct",
        language=language,
        path=path,
        span=Span(span[0], span[1], 0, 0),
    )


def _trait(
    name: str, *, path: str = "src/lib.rs", span: tuple[int, int] = (20, 30),
    language: str = "rust",
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{span[0]}-{span[1]}:{name}:trait",
        name=name,
        kind="trait",
        language=language,
        path=path,
        span=Span(span[0], span[1], 0, 0),
    )


def _method(
    qualified_name: str, *, path: str = "src/lib.rs",
    span: tuple[int, int] = (40, 45),
    language: str = "rust",
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{span[0]}-{span[1]}:{qualified_name}:method",
        name=qualified_name,
        kind="method",
        language=language,
        path=path,
        span=Span(span[0], span[1], 0, 0),
    )


def _implements_edge(struct_sym: Symbol, trait_sym: Symbol) -> Edge:
    return Edge.create(
        src=struct_sym.id,
        dst=trait_sym.id,
        edge_type="implements",
        line=struct_sym.span.start_line,
        origin="inheritance",
        origin_run_id="r0",
    )


class TestRustMethodOwner:
    def test_simple_struct_method(self) -> None:
        assert _rust_method_owner("MyStruct::foo") == "MyStruct"

    def test_nested_module_path_preserved(self) -> None:
        # Last :: wins; `foo::bar::baz` → owner `foo::bar`. This matches
        # the Rust analyzer's qualified method emission for nested impls.
        assert _rust_method_owner("outer::Inner::method") == "outer::Inner"

    def test_no_separator_returns_none(self) -> None:
        assert _rust_method_owner("plain_function") is None


class TestBuildStructMethodIndex:
    def test_groups_by_file_and_struct(self) -> None:
        methods = [
            _method("Foo::a", path="a.rs"),
            _method("Foo::b", path="a.rs"),
            _method("Bar::c", path="b.rs"),
        ]
        idx = _build_struct_method_index(methods)
        assert {m.name for m in idx[("a.rs", "Foo")]} == {"Foo::a", "Foo::b"}
        assert [m.name for m in idx[("b.rs", "Bar")]] == ["Bar::c"]

    def test_non_rust_skipped(self) -> None:
        methods = [
            _method("Foo::a", language="cpp", path="a.cpp"),
        ]
        assert _build_struct_method_index(methods) == {}

    def test_non_method_skipped(self) -> None:
        methods = [_struct("Foo")]
        assert _build_struct_method_index(methods) == {}

    def test_bare_function_without_owner_skipped(self) -> None:
        bare = _method("plain_function")
        assert _build_struct_method_index([bare]) == {}

    def test_same_struct_name_in_different_files_isolated(self) -> None:
        """Structs named Config in two modules must not share methods."""
        methods = [
            _method("Config::a", path="a.rs"),
            _method("Config::b", path="b.rs"),
        ]
        idx = _build_struct_method_index(methods)
        assert [m.name for m in idx[("a.rs", "Config")]] == ["Config::a"]
        assert [m.name for m in idx[("b.rs", "Config")]] == ["Config::b"]


class TestSymbolIndexById:
    def test_basic_indexing(self) -> None:
        a = _struct("A")
        b = _trait("B")
        idx = _symbol_index_by_id([a, b])
        assert idx[a.id] is a
        assert idx[b.id] is b

    def test_empty_input(self) -> None:
        assert _symbol_index_by_id([]) == {}


class TestLinkRustTraitDispatch:
    def test_emits_dispatches_to_for_each_method(self) -> None:
        s = _struct("Writer", span=(1, 5))
        t = _trait("Display", span=(10, 15))
        m1 = _method("Writer::fmt", span=(20, 22))
        m2 = _method("Writer::write", span=(24, 26))
        impl_edge = _implements_edge(s, t)

        result = link_rust_trait_dispatch(_ctx([s, t, m1, m2], [impl_edge]))
        assert {e.dst for e in result.edges} == {m1.id, m2.id}
        for e in result.edges:
            assert e.src == t.id
            assert e.edge_type == "dispatches_to"
            assert e.evidence_type == "rust_trait_dispatch"
            assert e.confidence == 0.85

    def test_no_implements_edges_no_op(self) -> None:
        s = _struct("Foo")
        m = _method("Foo::bar")
        result = link_rust_trait_dispatch(_ctx([s, m], []))
        assert result.edges == []

    def test_no_methods_no_op(self) -> None:
        s = _struct("Foo")
        t = _trait("Bar")
        impl_edge = _implements_edge(s, t)
        result = link_rust_trait_dispatch(_ctx([s, t], [impl_edge]))
        assert result.edges == []

    def test_unresolved_implements_edge_skipped(self) -> None:
        """implements edges whose src or dst is not in the symbol table
        are silently skipped — they point at symbols we can't reach."""
        s = _struct("Foo")
        # Build an edge with a fake dst id that doesn't appear in symbols.
        e = Edge.create(
            src=s.id,
            dst="rust:external:0-0:ExternalTrait:trait",
            edge_type="implements",
            line=1,
            origin="inheritance",
            origin_run_id="r0",
        )
        m = _method("Foo::bar")
        result = link_rust_trait_dispatch(_ctx([s, m], [e]))
        assert result.edges == []

    def test_dedupes_against_existing_dispatches_to_edges(self) -> None:
        s = _struct("Foo")
        t = _trait("Bar")
        m = _method("Foo::method")
        impl = _implements_edge(s, t)
        existing = Edge.create(
            src=t.id, dst=m.id, edge_type="dispatches_to",
            line=1, origin="other-linker", origin_run_id="r1",
        )
        result = link_rust_trait_dispatch(
            _ctx([s, t, m], [impl, existing]),
        )
        assert result.edges == []

    def test_same_struct_name_different_files_each_fan_out_separately(self) -> None:
        """Config in a.rs implementing Display gets a.rs's methods only;
        Config in b.rs gets b.rs's methods only."""
        s_a = _struct("Config", path="a.rs", span=(1, 5))
        s_b = _struct("Config", path="b.rs", span=(1, 5))
        t_a = _trait("Display", path="a.rs", span=(10, 12))
        t_b = _trait("Display", path="b.rs", span=(10, 12))
        m_a = _method("Config::fmt", path="a.rs", span=(20, 22))
        m_b = _method("Config::fmt", path="b.rs", span=(20, 22))
        impl_a = _implements_edge(s_a, t_a)
        impl_b = _implements_edge(s_b, t_b)

        result = link_rust_trait_dispatch(
            _ctx([s_a, s_b, t_a, t_b, m_a, m_b], [impl_a, impl_b]),
        )
        pairs = {(e.src, e.dst) for e in result.edges}
        assert pairs == {(t_a.id, m_a.id), (t_b.id, m_b.id)}

    def test_non_rust_symbols_ignored(self) -> None:
        s = _struct("Foo", language="cpp", path="a.cpp")
        t = _trait("Bar", language="cpp", path="a.cpp")
        m = _method("Foo::method", language="cpp", path="a.cpp")
        impl = _implements_edge(s, t)
        result = link_rust_trait_dispatch(_ctx([s, t, m], [impl]))
        assert result.edges == []

    def test_non_rust_trait_with_rust_struct_ignored(self) -> None:
        """Defensive path: if an implements edge links a Rust struct to
        a non-Rust trait symbol (shouldn't happen in practice, but a
        cross-language linker could construct one), the linker refuses
        to synthesize dispatch edges because it cannot reason about
        the foreign-language trait's dispatch semantics."""
        s = _struct("Foo", language="rust", path="a.rs")
        t = _trait("Bar", language="cpp", path="a.cpp")
        m = _method("Foo::method", language="rust", path="a.rs")
        impl = _implements_edge(s, t)
        result = link_rust_trait_dispatch(_ctx([s, t, m], [impl]))
        assert result.edges == []

    def test_multiple_impls_of_same_trait_emit_union(self) -> None:
        """Two structs implementing the same trait emit edges from the
        trait to each struct's methods."""
        s1 = _struct("A", path="a.rs", span=(1, 3))
        s2 = _struct("B", path="a.rs", span=(10, 12))
        t = _trait("Display", path="a.rs", span=(20, 22))
        m1 = _method("A::fmt", path="a.rs", span=(30, 32))
        m2 = _method("B::fmt", path="a.rs", span=(40, 42))
        impl1 = _implements_edge(s1, t)
        impl2 = _implements_edge(s2, t)

        result = link_rust_trait_dispatch(
            _ctx([s1, s2, t, m1, m2], [impl1, impl2]),
        )
        dsts = {e.dst for e in result.edges}
        assert dsts == {m1.id, m2.id}
        # Both originate from the same trait.
        assert all(e.src == t.id for e in result.edges)

    def test_struct_with_multiple_traits_each_gets_dispatches(self) -> None:
        """When one struct implements two traits, each trait gets its
        own dispatches_to edges to the struct's methods. Phase 1 does
        not try to distinguish which methods belong to which trait (the
        Rust analyzer doesn't emit that info), so every method fan-out
        duplicates per trait. This is documented behavior — a small
        over-count is preferable to missing trait-required methods."""
        s = _struct("Widget")
        t1 = _trait("Display", span=(20, 22))
        t2 = _trait("Debug", span=(30, 32))
        m = _method("Widget::fmt")
        impl1 = _implements_edge(s, t1)
        impl2 = _implements_edge(s, t2)

        result = link_rust_trait_dispatch(
            _ctx([s, t1, t2, m], [impl1, impl2]),
        )
        pairs = {(e.src, e.dst) for e in result.edges}
        assert pairs == {(t1.id, m.id), (t2.id, m.id)}

    def test_empty_input(self) -> None:
        result = link_rust_trait_dispatch(_ctx([], []))
        assert result.edges == []
        assert result.symbols == []

    def test_run_duration_populated(self) -> None:
        s = _struct("Foo")
        t = _trait("Bar")
        m = _method("Foo::method")
        impl = _implements_edge(s, t)
        result = link_rust_trait_dispatch(_ctx([s, t, m], [impl]))
        assert result.run.duration_ms >= 0

    def test_ignores_non_implements_edges(self) -> None:
        """calls / imports / contains edges must not trigger dispatch
        synthesis — only implements edges are consulted."""
        s = _struct("Foo")
        t = _trait("Bar")
        m = _method("Foo::method")
        fake_call = Edge.create(
            src=s.id, dst=t.id, edge_type="calls",
            line=1, origin="x", origin_run_id="r0",
        )
        result = link_rust_trait_dispatch(_ctx([s, t, m], [fake_call]))
        assert result.edges == []
