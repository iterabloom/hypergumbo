# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the router-routes linker.

Validates that the linker creates ``registers_routes`` edges from
router-concept symbols (module / combinator / route-table groupings)
to the route-concept registration symbols that live inside them.

The router concept is emitted by 14 framework YAMLs (phoenix, http4s,
http4k, yesod, giraffe, pedestal, ring-compojure, cowboy, laminas,
nuxt, plumber, remix, sveltekit, vertx). Each framework tags a
module/combinator/table as ``concept: router``; individual route
registration call sites inside get ``concept: route``. Containment
is established by span-nesting.

Phase 3 of WI-gudob (priority inert-concept linkers).
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_core.linkers.router_routes import link_router_routes


def _sym(
    name: str,
    path: str = "lib/my_app_web/router.ex",
    kind: str = "call",
    lang: str = "elixir",
    concepts: list[str] | None = None,
    span: tuple[int, int] = (1, 10),
) -> Symbol:
    meta: dict = {}
    if concepts:
        meta["concepts"] = [{"concept": c} for c in concepts]
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


class TestRouterRoutesLinker:
    """Test registers_routes edge creation from routers to nested routes."""

    def test_single_router_with_one_route(self) -> None:
        """A router symbol whose span encloses a route registration produces one edge."""
        router = _sym(
            "MyAppWeb.Router", kind="module", concepts=["router"], span=(1, 50)
        )
        route = _sym("get", concepts=["route"], span=(5, 5))
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[router, route])

        result = link_router_routes(ctx)
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == router.id
        assert edge.dst == route.id
        assert edge.edge_type == "registers_routes"
        assert edge.evidence_type == "router_routes"

    def test_router_with_multiple_routes(self) -> None:
        """All nested route registrations get their own edge from the router."""
        router = _sym(
            "MyAppWeb.Router", kind="module", concepts=["router"], span=(1, 80)
        )
        idx = _sym("get", concepts=["route"], span=(5, 5))
        create = _sym("post", concepts=["route"], span=(6, 6))
        update = _sym("put", concepts=["route"], span=(7, 7))
        ctx = LinkerContext(
            repo_root=Path("/repo"), symbols=[router, idx, create, update]
        )
        result = link_router_routes(ctx)
        assert len(result.edges) == 3
        dst_ids = {e.dst for e in result.edges}
        assert dst_ids == {idx.id, create.id, update.id}
        assert all(e.src == router.id for e in result.edges)

    def test_route_outside_router_span_excluded(self) -> None:
        """Routes whose span is NOT inside any router's span are not linked."""
        router = _sym(
            "MyAppWeb.Router", kind="module", concepts=["router"], span=(10, 40)
        )
        outside = _sym("get", concepts=["route"], span=(50, 60))
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[router, outside])
        result = link_router_routes(ctx)
        assert len(result.edges) == 0

    def test_route_in_different_file_excluded(self) -> None:
        """A route in a different file than the router does not link."""
        router = _sym(
            "MyAppWeb.Router",
            path="lib/my_app_web/router.ex",
            kind="module",
            concepts=["router"],
            span=(1, 50),
        )
        other = _sym(
            "get",
            path="lib/my_app_web/other_router.ex",
            concepts=["route"],
            span=(5, 5),
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[router, other])
        result = link_router_routes(ctx)
        assert len(result.edges) == 0

    def test_two_routers_same_file(self) -> None:
        """Two disjoint routers in the same file each link their own routes."""
        r1 = _sym(
            "apiRoutes", kind="call", concepts=["router"], span=(1, 40)
        )
        r1_get = _sym("bind", concepts=["route"], span=(5, 5))
        r2 = _sym(
            "adminRoutes", kind="call", concepts=["router"], span=(50, 90)
        )
        r2_get = _sym("bind", concepts=["route"], span=(55, 55))
        ctx = LinkerContext(
            repo_root=Path("/repo"), symbols=[r1, r1_get, r2, r2_get],
        )
        result = link_router_routes(ctx)
        assert len(result.edges) == 2
        edge_pairs = {(e.src, e.dst) for e in result.edges}
        assert (r1.id, r1_get.id) in edge_pairs
        assert (r2.id, r2_get.id) in edge_pairs

    def test_no_router_no_edges(self) -> None:
        """Without any router-concept symbols, no edges are produced."""
        route = _sym("get", concepts=["route"], span=(5, 5))
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[route])
        result = link_router_routes(ctx)
        assert len(result.edges) == 0

    def test_no_routes_no_edges(self) -> None:
        """A router with no nested route symbols produces no edges."""
        router = _sym(
            "MyAppWeb.Router", kind="module", concepts=["router"], span=(1, 50)
        )
        helper = _sym("helper", kind="function", span=(5, 15))
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[router, helper])
        result = link_router_routes(ctx)
        assert len(result.edges) == 0

    def test_router_without_span_skipped(self) -> None:
        """Routers missing span info cannot be nested-matched and are skipped."""
        router = Symbol(
            id="elixir:lib/my_app_web/router.ex:0-0:MyAppWeb.Router:module",
            name="MyAppWeb.Router",
            kind="module",
            language="elixir",
            path="lib/my_app_web/router.ex",
            span=None,
            meta={"concepts": [{"concept": "router"}]},
        )
        route = _sym(
            "get",
            path="lib/my_app_web/router.ex",
            concepts=["route"],
            span=(5, 5),
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[router, route])
        result = link_router_routes(ctx)
        assert len(result.edges) == 0

    def test_route_without_span_skipped(self) -> None:
        """Routes missing span info cannot be nested-matched and are skipped."""
        router = _sym(
            "MyAppWeb.Router",
            kind="module",
            concepts=["router"],
            span=(1, 50),
        )
        route = Symbol(
            id="elixir:lib/my_app_web/router.ex:0-0:get:call",
            name="get",
            kind="call",
            language="elixir",
            path="lib/my_app_web/router.ex",
            span=None,
            meta={"concepts": [{"concept": "route"}]},
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[router, route])
        result = link_router_routes(ctx)
        assert len(result.edges) == 0

    def test_test_file_excluded(self) -> None:
        """Symbols in test files are excluded to prevent spurious edges."""
        router = _sym(
            "RouterTest",
            path="test/my_app_web/router_test.exs",
            kind="module",
            concepts=["router"],
            span=(1, 50),
        )
        route = _sym(
            "get",
            path="test/my_app_web/router_test.exs",
            concepts=["route"],
            span=(5, 5),
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[router, route])
        result = link_router_routes(ctx)
        assert len(result.edges) == 0

    def test_bare_string_concept_not_matched(self) -> None:
        """Regression: bare-string concept shape (legacy/wrong) must NOT match (INV-tuzub)."""
        router = Symbol(
            id="elixir:lib/router.ex:1-50:Router:module",
            name="Router",
            kind="module",
            language="elixir",
            path="lib/router.ex",
            span=Span(1, 50, 0, 0),
            meta={"concepts": ["router"]},
        )
        route = Symbol(
            id="elixir:lib/router.ex:5-5:get:call",
            name="get",
            kind="call",
            language="elixir",
            path="lib/router.ex",
            span=Span(5, 5, 0, 0),
            meta={"concepts": ["route"]},
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[router, route])
        result = link_router_routes(ctx)
        assert len(result.edges) == 0

    def test_nested_routers_innermost_wins(self) -> None:
        """When two routers both enclose the same route, only the innermost (tightest) links.

        Models http4s / http4k where an outer routing composition may
        wrap an inner ``HttpRoutes.of`` block; both carry
        ``concept: router`` and both enclose the same arm.
        """
        outer = _sym(
            "HttpRoutes", kind="call", concepts=["router"], span=(1, 100)
        )
        inner = _sym(
            "HttpRoutes.of", kind="call", concepts=["router"], span=(10, 60)
        )
        route = _sym("case_arm", concepts=["route"], span=(15, 25))
        ctx = LinkerContext(
            repo_root=Path("/repo"), symbols=[outer, inner, route],
        )
        result = link_router_routes(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].src == inner.id
        assert result.edges[0].dst == route.id

    def test_cross_framework_coverage(self) -> None:
        """Verify the linker is framework-agnostic: Phoenix (elixir),
        http4s (scala), and Yesod (haskell) shapes.

        Per WI-gudob acceptance: linker emits meaningful edges on at
        least 3 frameworks. Bakeoff cohort validates at scale.
        """
        phoenix = _sym(
            "MyAppWeb.Router",
            path="lib/my_app_web/router.ex",
            kind="module",
            lang="elixir",
            concepts=["router"],
            span=(1, 40),
        )
        phoenix_get = _sym(
            "get",
            path="lib/my_app_web/router.ex",
            lang="elixir",
            concepts=["route"],
            span=(5, 5),
        )
        http4s = _sym(
            "HttpRoutes.of",
            path="src/main/scala/com/example/Routes.scala",
            kind="call",
            lang="scala",
            concepts=["router"],
            span=(1, 40),
        )
        http4s_arm = _sym(
            "case_arm",
            path="src/main/scala/com/example/Routes.scala",
            kind="call",
            lang="scala",
            concepts=["route"],
            span=(5, 10),
        )
        yesod = _sym(
            "parseRoutes",
            path="config/routes.hs",
            kind="call",
            lang="haskell",
            concepts=["router"],
            span=(1, 40),
        )
        yesod_entry = _sym(
            "HomeR",
            path="config/routes.hs",
            kind="call",
            lang="haskell",
            concepts=["route"],
            span=(5, 5),
        )
        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[
                phoenix, phoenix_get,
                http4s, http4s_arm,
                yesod, yesod_entry,
            ],
        )
        result = link_router_routes(ctx)
        assert len(result.edges) == 3
        by_src: dict[str, list] = {}
        for e in result.edges:
            by_src.setdefault(e.src, []).append(e)
        assert len(by_src[phoenix.id]) == 1
        assert len(by_src[http4s.id]) == 1
        assert len(by_src[yesod.id]) == 1

    def test_run_metadata_present(self) -> None:
        """Result should include AnalysisRun metadata."""
        router = _sym(
            "MyAppWeb.Router", kind="module", concepts=["router"], span=(1, 50)
        )
        route = _sym("get", concepts=["route"], span=(5, 5))
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[router, route])
        result = link_router_routes(ctx)
        assert result.run is not None
        assert "router-routes" in result.run.pass_id

    def test_edge_confidence(self) -> None:
        """Router-routes edges should have 0.80 confidence."""
        router = _sym(
            "MyAppWeb.Router", kind="module", concepts=["router"], span=(1, 50)
        )
        route = _sym("get", concepts=["route"], span=(5, 5))
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[router, route])
        result = link_router_routes(ctx)
        assert result.edges[0].confidence == 0.80

    def test_empty_symbol_list(self) -> None:
        """Empty symbol list yields no edges (and no exceptions)."""
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[])
        result = link_router_routes(ctx)
        assert result.edges == []

    def test_python_test_file_pattern_excluded(self) -> None:
        """Python test_*.py files are excluded."""
        router = _sym(
            "router",
            path="packages/app/tests/test_router.py",
            kind="module",
            lang="python",
            concepts=["router"],
            span=(1, 50),
        )
        route = _sym(
            "route",
            path="packages/app/tests/test_router.py",
            lang="python",
            concepts=["route"],
            span=(5, 5),
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[router, route])
        result = link_router_routes(ctx)
        assert len(result.edges) == 0

    def test_ruby_test_file_pattern_excluded(self) -> None:
        """Ruby *_test.rb files are excluded."""
        router = _sym(
            "Router",
            path="test/router_test.rb",
            kind="class",
            lang="ruby",
            concepts=["router"],
            span=(1, 50),
        )
        route = _sym(
            "get",
            path="test/router_test.rb",
            lang="ruby",
            concepts=["route"],
            span=(5, 5),
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[router, route])
        result = link_router_routes(ctx)
        assert len(result.edges) == 0
