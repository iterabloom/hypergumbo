# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Cgo linker (Go-C interop).

The cgo linker creates cgo_bridge edges between Go functions that call
C functions via the ``import "C"`` pseudo-package and their corresponding
C function implementations.

Go's cgo mechanism uses ``C.funcName()`` calls in Go code. The Go analyzer
creates unresolved edges with format ``go:C:0-0:funcName:unresolved`` for
these calls. The cgo linker resolves them to C function symbols.
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext


def _make_go_symbol(
    name: str,
    kind: str = "function",
    path: str = "main.go",
    start_line: int = 1,
    end_line: int = 10,
) -> Symbol:
    """Create a test Go symbol."""
    run = AnalysisRun.create(pass_id="test", version="test")
    return Symbol(
        id=f"go:{path}:{start_line}-{end_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language="go",
        path=path,
        span=Span(start_line=start_line, end_line=end_line, start_col=0, end_col=0),
        origin="go",
        origin_run_id=run.execution_id,
    )


def _make_c_symbol(
    name: str,
    kind: str = "function",
    path: str = "native.c",
    start_line: int = 1,
    end_line: int = 10,
) -> Symbol:
    """Create a test C symbol."""
    run = AnalysisRun.create(pass_id="test", version="test")
    return Symbol(
        id=f"c:{path}:{start_line}-{end_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language="c",
        path=path,
        span=Span(start_line=start_line, end_line=end_line, start_col=0, end_col=0),
        origin="c",
        origin_run_id=run.execution_id,
    )


def _make_unresolved_edge(
    src_id: str,
    func_name: str,
    line: int = 5,
    import_path: str = "C",
) -> Edge:
    """Create an unresolved cgo call edge (as the Go analyzer would produce)."""
    return Edge.create(
        src=src_id,
        dst=f"go:{import_path}:0-0:{func_name}:unresolved",
        edge_type="calls",
        line=line,
        evidence_type="method_call",
        is_resolved=False,
        confidence=0.50,
        origin="go",

        origin_run_id="test",
    )


class TestCgoLinkerBasic:
    """Tests for basic cgo linking."""

    def test_links_go_cgo_call_to_c_function(self) -> None:
        """Links a Go C.funcName() call to its C implementation."""
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func = _make_go_symbol("processData", path="main.go")
        c_func = _make_c_symbol("processData", path="native.c")

        unresolved_edge = _make_unresolved_edge(go_func.id, "processData")

        result = link_cgo(
            go_symbols=[go_func],
            c_symbols=[c_func],
            edges=[unresolved_edge],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "calls"
        assert edge.src == go_func.id
        assert edge.dst == c_func.id

    def test_links_multiple_cgo_calls(self) -> None:
        """Links multiple Go cgo calls to their C implementations."""
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func1 = _make_go_symbol("caller1", path="main.go", start_line=1, end_line=5)
        go_func2 = _make_go_symbol("caller2", path="main.go", start_line=10, end_line=15)
        c_malloc = _make_c_symbol("malloc", path="stdlib.c")
        c_free = _make_c_symbol("free", path="stdlib.c", start_line=20, end_line=25)

        edges = [
            _make_unresolved_edge(go_func1.id, "malloc", line=3),
            _make_unresolved_edge(go_func2.id, "free", line=12),
        ]

        result = link_cgo(
            go_symbols=[go_func1, go_func2],
            c_symbols=[c_malloc, c_free],
            edges=edges,
        )

        assert len(result.edges) == 2
        dst_names = {e.dst for e in result.edges}
        assert c_malloc.id in dst_names
        assert c_free.id in dst_names

    def test_no_link_when_c_function_missing(self) -> None:
        """No edge when C function doesn't exist in repo."""
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func = _make_go_symbol("caller", path="main.go")
        unresolved_edge = _make_unresolved_edge(go_func.id, "nonexistent_func")

        result = link_cgo(
            go_symbols=[go_func],
            c_symbols=[],
            edges=[unresolved_edge],
        )

        assert len(result.edges) == 0

    def test_ignores_non_cgo_unresolved_edges(self) -> None:
        """Ignores unresolved edges that aren't cgo calls (different import path)."""
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func = _make_go_symbol("caller", path="main.go")
        c_func = _make_c_symbol("Get", path="native.c")

        # This is an unresolved call to a Go package, not to C
        non_cgo_edge = _make_unresolved_edge(
            go_func.id, "Get", import_path="net/http",
        )

        result = link_cgo(
            go_symbols=[go_func],
            c_symbols=[c_func],
            edges=[non_cgo_edge],
        )

        assert len(result.edges) == 0

    def test_result_includes_run_metadata(self) -> None:
        """Result includes analysis run metadata."""
        from hypergumbo_core.linkers.cgo import link_cgo

        result = link_cgo(go_symbols=[], c_symbols=[], edges=[])

        assert result.run is not None
        assert result.run.pass_id == "cgo-linker"

    def test_edge_confidence(self) -> None:
        """Edge has appropriate confidence level."""
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func = _make_go_symbol("caller", path="main.go")
        c_func = _make_c_symbol("process", path="native.c")
        edge = _make_unresolved_edge(go_func.id, "process")

        result = link_cgo(
            go_symbols=[go_func],
            c_symbols=[c_func],
            edges=[edge],
        )

        assert len(result.edges) == 1
        assert result.edges[0].confidence >= 0.85

    def test_edge_evidence_type(self) -> None:
        """Edge has cgo_call evidence type."""
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func = _make_go_symbol("caller", path="main.go")
        c_func = _make_c_symbol("process", path="native.c")
        edge = _make_unresolved_edge(go_func.id, "process")

        result = link_cgo(
            go_symbols=[go_func],
            c_symbols=[c_func],
            edges=[edge],
        )

        assert result.edges[0].evidence_type == "cgo_call"

    def test_edge_has_access_mode(self) -> None:
        """cgo_bridge edges have access_mode=write (Go passes data to C)."""
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func = _make_go_symbol("caller", path="main.go")
        c_func = _make_c_symbol("process", path="native.c")
        edge = _make_unresolved_edge(go_func.id, "process")

        result = link_cgo(
            go_symbols=[go_func],
            c_symbols=[c_func],
            edges=[edge],
        )

        assert len(result.edges) == 1
        bridge = result.edges[0]
        assert bridge.meta is not None
        assert bridge.meta.get("data_direction") == "src_to_dst"
        assert bridge.meta.get("access_mode") is None

    def test_multiple_edges_all_have_access_mode(self) -> None:
        """All cgo_bridge edges get access_mode annotation, not just the first."""
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func1 = _make_go_symbol("caller1", path="main.go", start_line=1, end_line=5)
        go_func2 = _make_go_symbol("caller2", path="main.go", start_line=10, end_line=15)
        c_read = _make_c_symbol("readData", path="io.c")
        c_write = _make_c_symbol("writeData", path="io.c", start_line=20, end_line=25)

        edges = [
            _make_unresolved_edge(go_func1.id, "readData", line=3),
            _make_unresolved_edge(go_func2.id, "writeData", line=12),
        ]

        result = link_cgo(
            go_symbols=[go_func1, go_func2],
            c_symbols=[c_read, c_write],
            edges=edges,
        )

        assert len(result.edges) == 2
        for bridge in result.edges:
            assert bridge.meta is not None
            assert bridge.meta.get("data_direction") == "src_to_dst"
            assert bridge.meta.get("access_mode") is None


class TestCgoLinkerEdgeCases:
    """Edge case tests for cgo linker."""

    def test_empty_inputs(self) -> None:
        """Handles empty inputs gracefully."""
        from hypergumbo_core.linkers.cgo import link_cgo

        result = link_cgo(go_symbols=[], c_symbols=[], edges=[])

        assert result.edges == []
        assert result.run is not None

    def test_cpp_symbols_also_linked(self) -> None:
        """C++ functions are also matched (cgo can call C++ with extern C)."""
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func = _make_go_symbol("caller", path="main.go")
        # C++ function with extern "C" linkage
        run = AnalysisRun.create(pass_id="test", version="test")
        cpp_func = Symbol(
            id="cpp:native.cpp:1-10:processData:function",
            name="processData",
            kind="function",
            language="cpp",
            path="native.cpp",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="cpp",
            origin_run_id=run.execution_id,
        )

        edge = _make_unresolved_edge(go_func.id, "processData")

        result = link_cgo(
            go_symbols=[go_func],
            c_symbols=[cpp_func],
            edges=[edge],
        )

        assert len(result.edges) == 1
        assert result.edges[0].dst == cpp_func.id

    def test_non_function_c_symbols_ignored(self) -> None:
        """C struct/variable symbols are not matched."""
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func = _make_go_symbol("caller", path="main.go")
        c_struct = _make_c_symbol("data_struct", kind="struct", path="types.h")
        edge = _make_unresolved_edge(go_func.id, "data_struct")

        result = link_cgo(
            go_symbols=[go_func],
            c_symbols=[c_struct],
            edges=[edge],
        )

        assert len(result.edges) == 0

    def test_ignores_resolved_edges(self) -> None:
        """Ignores edges that are already resolved (don't end with :unresolved)."""
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func = _make_go_symbol("caller", path="main.go")
        c_func = _make_c_symbol("process", path="native.c")

        # Already resolved edge
        resolved_edge = Edge.create(
            src=go_func.id,
            dst=c_func.id,
            edge_type="calls",
            line=5,
            evidence_type="function_call",
            confidence=0.85,
            origin="go",

            origin_run_id="test",
        )

        result = link_cgo(
            go_symbols=[go_func],
            c_symbols=[c_func],
            edges=[resolved_edge],
        )

        assert len(result.edges) == 0

    def test_multiple_c_functions_same_name_takes_first(self) -> None:
        """When multiple C functions share a name, links to the first one found."""
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func = _make_go_symbol("caller", path="main.go")
        c_func1 = _make_c_symbol("process", path="impl1.c", start_line=1, end_line=5)
        c_func2 = _make_c_symbol("process", path="impl2.c", start_line=1, end_line=5)

        edge = _make_unresolved_edge(go_func.id, "process")

        result = link_cgo(
            go_symbols=[go_func],
            c_symbols=[c_func1, c_func2],
            edges=[edge],
        )

        # Should link to one of them (first match)
        assert len(result.edges) == 1
        assert result.edges[0].dst in (c_func1.id, c_func2.id)


class TestCgoLinkerRegistry:
    """Tests for cgo linker registry integration."""

    @pytest.fixture(autouse=True)
    def ensure_cgo_registered(self) -> None:
        """Ensure cgo linker is registered before each test."""
        import importlib
        import hypergumbo_core.linkers.cgo as cgo_module
        importlib.reload(cgo_module)

    def test_cgo_linker_registered(self) -> None:
        """Cgo linker is registered in the linker registry."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("cgo-linker")
        assert linker is not None
        assert linker.name == "cgo-linker"
        assert linker.priority == 15

    def test_cgo_linker_has_requirements(self) -> None:
        """Cgo linker declares its requirements."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("cgo-linker")
        assert linker is not None
        assert len(linker.requirements) == 2

        req_names = [r.name for r in linker.requirements]
        assert "go_cgo_calls" in req_names
        assert "c_cpp_functions" in req_names

    def test_cgo_linker_activation(self) -> None:
        """Cgo linker activates for go+c and go+cpp language pairs."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("cgo-linker")
        assert linker is not None

        # Should activate when go and c are both present
        assert linker.activation.should_run(set(), {"go", "c"})
        # Should activate when go and cpp are both present
        assert linker.activation.should_run(set(), {"go", "cpp"})
        # Should NOT activate when only go is present
        assert not linker.activation.should_run(set(), {"go"})
        # Should NOT activate when only c is present
        assert not linker.activation.should_run(set(), {"c"})

    def test_cgo_linker_via_registry_dispatch(self) -> None:
        """Cgo linker works via registry dispatch."""
        from hypergumbo_core.linkers.registry import run_linker

        go_func = _make_go_symbol("caller", path="main.go")
        c_func = _make_c_symbol("process", path="native.c")
        unresolved_edge = _make_unresolved_edge(go_func.id, "process")

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[go_func, c_func],
            edges=[unresolved_edge],
        )

        result = run_linker("cgo-linker", ctx)

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "calls"

    def test_cgo_requirements_met(self) -> None:
        """Cgo requirements report as met when matching data exists."""
        from hypergumbo_core.linkers.registry import check_linker_requirements

        go_func = _make_go_symbol("caller", path="main.go")
        c_func = _make_c_symbol("process", path="native.c")
        unresolved_edge = _make_unresolved_edge(go_func.id, "process")

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[go_func, c_func],
            edges=[unresolved_edge],
        )

        diagnostics = check_linker_requirements(ctx)
        cgo_diag = next((d for d in diagnostics if d.linker_name == "cgo-linker"), None)
        assert cgo_diag is not None
        assert cgo_diag.all_met is True

    def test_cgo_requirements_unmet_no_c_functions(self) -> None:
        """Cgo requirements unmet when no C/C++ functions exist."""
        from hypergumbo_core.linkers.registry import check_linker_requirements

        go_func = _make_go_symbol("caller", path="main.go")
        unresolved_edge = _make_unresolved_edge(go_func.id, "process")

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[go_func],
            edges=[unresolved_edge],
        )

        diagnostics = check_linker_requirements(ctx)
        cgo_diag = next((d for d in diagnostics if d.linker_name == "cgo-linker"), None)
        assert cgo_diag is not None
        assert cgo_diag.all_met is False

        c_req = next((r for r in cgo_diag.requirements if r.name == "c_cpp_functions"), None)
        assert c_req is not None
        assert c_req.met is False


# ---------------------------------------------------------------------------
# INV-zuhub property tests: cgo cross-file collision fallback (WI-ruzin PR1)
# ---------------------------------------------------------------------------


class TestInvZuhubCgoFallback:
    """INV-zuhub item 1, calls family — cgo cross-file symbol collision.

    The C/C++ function lookup is by short name. When more than one
    in-tree C/C++ symbol shares the name (common with per-file static
    helpers, duplicate forward declarations, or platform-specific
    shims that the analyzer indexes regardless of preprocessor gates),
    the dispatch target is unresolvable from the cgo call alone — the
    deterministic-by-id candidate is picked and the edge downgrades.
    """

    def test_single_c_match_keeps_high_confidence(self) -> None:
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func = _make_go_symbol("caller", path="main.go")
        c_func = _make_c_symbol(
            "processData", path="native.c", start_line=10, end_line=20,
        )
        unresolved = _make_unresolved_edge(go_func.id, "processData")
        result = link_cgo([go_func], [c_func], [unresolved])
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.confidence == 0.90
        assert (edge.meta or {}).get("disambiguation_fallback") is not True

    def test_multiple_c_matches_emit_fallback_edge(self) -> None:
        from hypergumbo_core.linkers.cgo import link_cgo

        go_func = _make_go_symbol("caller", path="main.go")
        # Two in-tree C functions with the same name across files —
        # static helpers in two .c files for example.
        c_func_a = _make_c_symbol(
            "processData", path="native_a.c", start_line=1, end_line=5,
        )
        c_func_b = _make_c_symbol(
            "processData", path="native_b.c", start_line=1, end_line=5,
        )
        unresolved = _make_unresolved_edge(go_func.id, "processData")
        result = link_cgo([go_func], [c_func_a, c_func_b], [unresolved])
        assert len(result.edges) == 1
        edge = result.edges[0]
        # deterministic-by-id pick of one candidate
        assert edge.dst in {c_func_a.id, c_func_b.id}
        assert edge.confidence <= 0.5
        assert edge.meta is not None
        assert edge.meta.get("disambiguation_fallback") is True
        assert edge.meta.get("bridge_kind") == "cgo"
