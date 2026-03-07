"""Tests for the decorator dispatch linker.

Validates that the linker creates ``dispatches_to`` edges from registry
iteration call sites to all functions registered via decorator patterns
like ``@register_analyzer("go")``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.decorator_dispatch import (
    DISPATCH_DECORATOR_PATTERNS,
    _find_decorated_symbols,
    _find_dispatch_sites,
    link_decorator_dispatch,
)
from hypergumbo_core.linkers.registry import LinkerContext


def _sym(
    name: str,
    path: str = "pkg/mod.py",
    kind: str = "function",
    lang: str = "python",
    decorators: list | None = None,
    span: tuple[int, int] = (1, 10),
) -> Symbol:
    """Helper to create a Symbol with optional decorators."""
    meta = {}
    if decorators:
        meta["decorators"] = decorators
    sid = f"{lang}:{path}:{span[0]}-{span[1]}:{name}:{kind}"
    return Symbol(
        id=sid,
        name=name,
        kind=kind,
        language=lang,
        path=path,
        span=Span(span[0], span[1], 0, 0),
        meta=meta if meta else None,
    )


def _edge(src_id: str, dst_id: str, edge_type: str = "calls") -> Edge:
    """Helper to create an Edge."""
    return Edge.create(src=src_id, dst=dst_id, edge_type=edge_type, line=1)


class TestFindDecoratedSymbols:
    """Test _find_decorated_symbols detection."""

    def test_finds_register_analyzer(self) -> None:
        sym = _sym(
            "analyze_go",
            decorators=[{"name": "register_analyzer", "args": ["go"], "kwargs": {}}],
        )
        result = _find_decorated_symbols([sym], DISPATCH_DECORATOR_PATTERNS)
        assert len(result) == 1
        assert result[0][0] == sym
        assert result[0][1] == "register_analyzer"

    def test_finds_register_linker(self) -> None:
        sym = _sym(
            "link_grpc",
            decorators=[
                {"name": "register_linker", "args": ["grpc"], "kwargs": {"priority": 30}}
            ],
        )
        result = _find_decorated_symbols([sym], DISPATCH_DECORATOR_PATTERNS)
        assert len(result) == 1
        assert result[0][1] == "register_linker"

    def test_ignores_unrelated_decorators(self) -> None:
        sym = _sym(
            "my_func",
            decorators=[{"name": "staticmethod", "args": [], "kwargs": {}}],
        )
        result = _find_decorated_symbols([sym], DISPATCH_DECORATOR_PATTERNS)
        assert len(result) == 0

    def test_no_decorators(self) -> None:
        sym = _sym("plain_func")
        result = _find_decorated_symbols([sym], DISPATCH_DECORATOR_PATTERNS)
        assert len(result) == 0

    def test_multiple_decorated_symbols(self) -> None:
        syms = [
            _sym(
                "analyze_go",
                decorators=[{"name": "register_analyzer", "args": ["go"], "kwargs": {}}],
            ),
            _sym(
                "analyze_rust",
                decorators=[{"name": "register_analyzer", "args": ["rust"], "kwargs": {}}],
            ),
            _sym("plain_func"),
        ]
        result = _find_decorated_symbols(syms, DISPATCH_DECORATOR_PATTERNS)
        assert len(result) == 2


class TestFindDispatchSites:
    """Test _find_dispatch_sites detection."""

    def test_finds_run_all_analyzers(self) -> None:
        sym = _sym("run_all_analyzers", path="core/analyze/registry.py")
        result = _find_dispatch_sites([sym], DISPATCH_DECORATOR_PATTERNS)
        assert len(result) == 1
        assert result[0][0] == sym
        assert result[0][1] == "register_analyzer"

    def test_finds_run_all_linkers(self) -> None:
        sym = _sym("run_all_linkers", path="core/linkers/registry.py")
        result = _find_dispatch_sites([sym], DISPATCH_DECORATOR_PATTERNS)
        assert len(result) == 1
        assert result[0][1] == "register_linker"

    def test_ignores_unrelated_functions(self) -> None:
        sym = _sym("do_something")
        result = _find_dispatch_sites([sym], DISPATCH_DECORATOR_PATTERNS)
        assert len(result) == 0


class TestLinkDecoratorDispatch:
    """Test the full linker integration."""

    def test_creates_dispatches_to_edges(self) -> None:
        """Core case: dispatch site → each registered handler."""
        dispatch_site = _sym(
            "run_all_analyzers",
            path="core/analyze/registry.py",
            span=(10, 20),
        )
        handler1 = _sym(
            "analyze_go",
            path="lang/go.py",
            decorators=[{"name": "register_analyzer", "args": ["go"], "kwargs": {}}],
        )
        handler2 = _sym(
            "analyze_rust",
            path="lang/rust.py",
            decorators=[{"name": "register_analyzer", "args": ["rust"], "kwargs": {}}],
        )
        unrelated = _sym("helper_func", path="utils.py")

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[dispatch_site, handler1, handler2, unrelated],
            edges=[],
        )
        result = link_decorator_dispatch(ctx)

        assert len(result.edges) == 2
        edge_dsts = {e.dst for e in result.edges}
        assert handler1.id in edge_dsts
        assert handler2.id in edge_dsts

        for edge in result.edges:
            assert edge.src == dispatch_site.id
            assert edge.edge_type == "dispatches_to"
            assert edge.evidence_type == "registry_dispatch"
            assert edge.confidence == 0.70

    def test_no_dispatch_site_no_edges(self) -> None:
        """If no dispatch site exists, no edges are created."""
        handler = _sym(
            "analyze_go",
            decorators=[{"name": "register_analyzer", "args": ["go"], "kwargs": {}}],
        )
        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[handler],
            edges=[],
        )
        result = link_decorator_dispatch(ctx)
        assert len(result.edges) == 0

    def test_no_handlers_no_edges(self) -> None:
        """If no handlers are registered, no edges are created."""
        dispatch_site = _sym("run_all_analyzers", path="core/analyze/registry.py")
        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[dispatch_site],
            edges=[],
        )
        result = link_decorator_dispatch(ctx)
        assert len(result.edges) == 0

    def test_separate_registries_not_mixed(self) -> None:
        """Analyzer dispatch site should not link to linker handlers."""
        analyzer_dispatch = _sym(
            "run_all_analyzers",
            path="core/analyze/registry.py",
        )
        linker_handler = _sym(
            "link_grpc",
            path="linkers/grpc.py",
            decorators=[
                {"name": "register_linker", "args": ["grpc"], "kwargs": {}}
            ],
        )
        analyzer_handler = _sym(
            "analyze_go",
            path="lang/go.py",
            decorators=[{"name": "register_analyzer", "args": ["go"], "kwargs": {}}],
        )

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[analyzer_dispatch, linker_handler, analyzer_handler],
            edges=[],
        )
        result = link_decorator_dispatch(ctx)

        # Only one edge: analyzer dispatch → analyze_go (not link_grpc)
        assert len(result.edges) == 1
        assert result.edges[0].dst == analyzer_handler.id

    def test_result_has_analysis_run(self) -> None:
        """LinkerResult should include an AnalysisRun."""
        dispatch_site = _sym("run_all_analyzers", path="core/analyze/registry.py")
        handler = _sym(
            "analyze_go",
            decorators=[{"name": "register_analyzer", "args": ["go"], "kwargs": {}}],
        )
        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[dispatch_site, handler],
            edges=[],
        )
        result = link_decorator_dispatch(ctx)
        assert result.run is not None
        assert "decorator-dispatch-linker" in result.run.pass_id
