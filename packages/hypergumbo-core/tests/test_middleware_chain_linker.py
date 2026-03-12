# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the middleware chain linker.

Validates that the linker creates ``middleware_chain`` edges between
consecutive middleware symbols in the same file, ordered by source line.
This enables forward/reverse slices to traverse the middleware pipeline.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.middleware_chain import (
    link_middleware_chain,
)
from hypergumbo_core.linkers.registry import LinkerContext


def _sym(
    name: str,
    path: str = "app/middleware.py",
    kind: str = "function",
    lang: str = "python",
    concepts: list[str] | None = None,
    span: tuple[int, int] = (1, 10),
) -> Symbol:
    """Helper to create a Symbol with optional concepts."""
    meta = {}
    if concepts:
        meta["concepts"] = concepts
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


class TestMiddlewareChainLinker:
    """Test middleware chain edge creation."""

    def test_two_middleware_same_file_creates_edge(self) -> None:
        """Two middleware in the same file should produce one chain edge."""
        mw1 = _sym("auth_middleware", concepts=["middleware"], span=(5, 15))
        mw2 = _sym("logging_middleware", concepts=["middleware"], span=(20, 30))

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[mw1, mw2],
        )
        result = link_middleware_chain(ctx)
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == mw1.id
        assert edge.dst == mw2.id
        assert edge.edge_type == "references"
        assert edge.evidence_type == "middleware_chain"

    def test_three_middleware_same_file_creates_two_edges(self) -> None:
        """Three middleware create a chain: mw1 → mw2 → mw3."""
        mw1 = _sym("cors_middleware", concepts=["middleware"], span=(5, 15))
        mw2 = _sym("auth_middleware", concepts=["middleware"], span=(20, 30))
        mw3 = _sym("rate_limit_middleware", concepts=["middleware"], span=(35, 45))

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[mw1, mw2, mw3],
        )
        result = link_middleware_chain(ctx)
        assert len(result.edges) == 2
        # First edge: cors → auth
        assert result.edges[0].src == mw1.id
        assert result.edges[0].dst == mw2.id
        # Second edge: auth → rate_limit
        assert result.edges[1].src == mw2.id
        assert result.edges[1].dst == mw3.id

    def test_middleware_sorted_by_line_not_insertion(self) -> None:
        """Middleware should be chained by source line order, not symbol list order."""
        mw_late = _sym("late_middleware", concepts=["middleware"], span=(50, 60))
        mw_early = _sym("early_middleware", concepts=["middleware"], span=(5, 15))

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[mw_late, mw_early],  # Reverse order in list
        )
        result = link_middleware_chain(ctx)
        assert len(result.edges) == 1
        # Edge should go from early to late (by source line)
        assert result.edges[0].src == mw_early.id
        assert result.edges[0].dst == mw_late.id

    def test_middleware_different_files_no_cross_chain(self) -> None:
        """Middleware in different files should not chain together."""
        mw1 = _sym("auth_middleware", path="auth.py", concepts=["middleware"], span=(5, 15))
        mw2 = _sym("logging_middleware", path="logging.py", concepts=["middleware"], span=(5, 15))

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[mw1, mw2],
        )
        result = link_middleware_chain(ctx)
        assert len(result.edges) == 0

    def test_no_middleware_no_edges(self) -> None:
        """No middleware symbols should produce no edges."""
        func = _sym("handler", kind="function", span=(1, 10))
        route = _sym("GET /users", kind="route", span=(1, 1))

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[func, route],
        )
        result = link_middleware_chain(ctx)
        assert len(result.edges) == 0

    def test_single_middleware_no_edge(self) -> None:
        """A single middleware produces no edges (need at least 2 for a chain)."""
        mw = _sym("auth_middleware", concepts=["middleware"], span=(5, 15))

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[mw],
        )
        result = link_middleware_chain(ctx)
        assert len(result.edges) == 0

    def test_middleware_mixed_with_non_middleware(self) -> None:
        """Only middleware-tagged symbols participate in chaining."""
        func = _sym("handler", kind="function", span=(1, 3))
        mw1 = _sym("auth_middleware", concepts=["middleware"], span=(5, 15))
        other = _sym("helper", kind="function", span=(17, 19))
        mw2 = _sym("logging_middleware", concepts=["middleware"], span=(20, 30))

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[func, mw1, other, mw2],
        )
        result = link_middleware_chain(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].src == mw1.id
        assert result.edges[0].dst == mw2.id

    def test_non_middleware_concept_excluded(self) -> None:
        """Symbols with concepts other than 'middleware' are excluded."""
        mw = _sym("auth", concepts=["middleware"], span=(5, 15))
        route_handler = _sym("handler", concepts=["route"], span=(20, 30))

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[mw, route_handler],
        )
        result = link_middleware_chain(ctx)
        assert len(result.edges) == 0

    def test_middleware_no_span_skipped(self) -> None:
        """Middleware without span should be excluded from chaining."""
        mw1 = _sym("auth_middleware", concepts=["middleware"], span=(5, 15))
        mw_no_span = Symbol(
            id="python:app.py:0-0:broken:function",
            name="broken",
            kind="function",
            language="python",
            path="app/middleware.py",
            span=None,
            meta={"concepts": ["middleware"]},
        )

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[mw1, mw_no_span],
        )
        result = link_middleware_chain(ctx)
        assert len(result.edges) == 0

    def test_test_file_middleware_excluded(self) -> None:
        """Middleware in test files should not be chained."""
        mw1 = _sym(
            "test_auth_middleware",
            path="tests/test_middleware.py",
            concepts=["middleware"],
            span=(5, 15),
        )
        mw2 = _sym(
            "test_logging_middleware",
            path="tests/test_middleware.py",
            concepts=["middleware"],
            span=(20, 30),
        )

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[mw1, mw2],
        )
        result = link_middleware_chain(ctx)
        assert len(result.edges) == 0

    def test_run_metadata_present(self) -> None:
        """Result should include AnalysisRun metadata."""
        mw1 = _sym("auth", concepts=["middleware"], span=(5, 15))
        mw2 = _sym("logging", concepts=["middleware"], span=(20, 30))

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[mw1, mw2],
        )
        result = link_middleware_chain(ctx)
        assert result.run is not None
        assert "middleware-chain" in result.run.pass_id

    def test_edge_confidence(self) -> None:
        """Middleware chain edges should have 0.70 confidence."""
        mw1 = _sym("auth", concepts=["middleware"], span=(5, 15))
        mw2 = _sym("logging", concepts=["middleware"], span=(20, 30))

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[mw1, mw2],
        )
        result = link_middleware_chain(ctx)
        assert result.edges[0].confidence == 0.70

    def test_multiple_files_independent_chains(self) -> None:
        """Each file gets its own independent middleware chain."""
        mw_a1 = _sym("auth_a", path="app/auth.py", concepts=["middleware"], span=(5, 15))
        mw_a2 = _sym("rate_a", path="app/auth.py", concepts=["middleware"], span=(20, 30))
        mw_b1 = _sym("cors_b", path="app/cors.py", concepts=["middleware"], span=(5, 15))
        mw_b2 = _sym("log_b", path="app/cors.py", concepts=["middleware"], span=(20, 30))

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[mw_a1, mw_a2, mw_b1, mw_b2],
        )
        result = link_middleware_chain(ctx)
        assert len(result.edges) == 2
        # File A chain
        file_a_edges = [e for e in result.edges if "auth.py" in e.src]
        assert len(file_a_edges) == 1
        assert file_a_edges[0].src == mw_a1.id
        assert file_a_edges[0].dst == mw_a2.id
        # File B chain
        file_b_edges = [e for e in result.edges if "cors.py" in e.src]
        assert len(file_b_edges) == 1
        assert file_b_edges[0].src == mw_b1.id
        assert file_b_edges[0].dst == mw_b2.id
