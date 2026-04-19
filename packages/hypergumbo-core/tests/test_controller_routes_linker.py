# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the controller-routes linker.

Validates that the linker creates ``contains_routes`` edges from
controller-concept symbols (class/module groupings) to the route-concept
method symbols that live inside them.

The controller concept is emitted by 34 framework YAMLs (rails, aspnet,
django, spring-mvc, laravel, symfony, ...). Each framework tags a
class/module as ``concept: controller``; individual handler methods in
that class get ``concept: route``. Containment is established by
span-nesting: a route symbol whose span falls within the controller's
span belongs to that controller.

Phase 2 of WI-gudob (priority inert-concept linkers). See WI-gokop.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.controller_routes import (
    link_controller_routes,
)
from hypergumbo_core.linkers.registry import LinkerContext


def _sym(
    name: str,
    path: str = "app/controllers/users_controller.rb",
    kind: str = "function",
    lang: str = "ruby",
    concepts: list[str] | None = None,
    span: tuple[int, int] = (1, 10),
) -> Symbol:
    """Helper to create a Symbol. ``concepts`` wraps names into the
    production ``list[dict]`` shape (``{"concept": name}``) per
    INV-tuzub."""
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


class TestControllerRoutesLinker:
    """Test contains_routes edge creation from controllers to routes."""

    def test_single_controller_with_one_route(self) -> None:
        """A controller symbol whose span encloses a route method produces one edge."""
        controller = _sym(
            "UsersController", kind="class", concepts=["controller"], span=(1, 50)
        )
        route = _sym(
            "UsersController#index",
            kind="function",
            concepts=["route"],
            span=(5, 15),
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[controller, route])

        result = link_controller_routes(ctx)
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == controller.id
        assert edge.dst == route.id
        assert edge.edge_type == "contains_routes"
        assert edge.evidence_type == "controller_routes"

    def test_controller_with_multiple_routes(self) -> None:
        """All nested routes get their own edge from the controller."""
        controller = _sym("UsersController", kind="class", concepts=["controller"], span=(1, 80))
        idx = _sym("UsersController#index", concepts=["route"], span=(5, 15))
        show = _sym("UsersController#show", concepts=["route"], span=(20, 30))
        create = _sym("UsersController#create", concepts=["route"], span=(35, 45))
        ctx = LinkerContext(
            repo_root=Path("/repo"), symbols=[controller, idx, show, create]
        )
        result = link_controller_routes(ctx)
        assert len(result.edges) == 3
        dst_ids = {e.dst for e in result.edges}
        assert dst_ids == {idx.id, show.id, create.id}
        assert all(e.src == controller.id for e in result.edges)

    def test_route_outside_controller_span_excluded(self) -> None:
        """Routes whose span is NOT inside the controller's span are not linked."""
        controller = _sym("UsersController", kind="class", concepts=["controller"], span=(10, 40))
        outside = _sym(
            "some_helper", kind="function", concepts=["route"], span=(50, 60)
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[controller, outside])
        result = link_controller_routes(ctx)
        assert len(result.edges) == 0

    def test_route_in_different_file_excluded(self) -> None:
        """A route in a different file than the controller does not link."""
        controller = _sym(
            "UsersController",
            path="app/controllers/users_controller.rb",
            kind="class",
            concepts=["controller"],
            span=(1, 50),
        )
        other = _sym(
            "index",
            path="app/controllers/posts_controller.rb",
            concepts=["route"],
            span=(5, 15),
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[controller, other])
        result = link_controller_routes(ctx)
        assert len(result.edges) == 0

    def test_two_controllers_same_file(self) -> None:
        """Two controllers in the same file each link their own routes."""
        users_ctrl = _sym(
            "UsersController", kind="class", concepts=["controller"], span=(1, 40)
        )
        users_index = _sym("UsersController#index", concepts=["route"], span=(5, 15))
        posts_ctrl = _sym(
            "PostsController", kind="class", concepts=["controller"], span=(50, 90)
        )
        posts_index = _sym("PostsController#index", concepts=["route"], span=(55, 65))
        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[users_ctrl, users_index, posts_ctrl, posts_index],
        )
        result = link_controller_routes(ctx)
        assert len(result.edges) == 2
        edge_pairs = {(e.src, e.dst) for e in result.edges}
        assert (users_ctrl.id, users_index.id) in edge_pairs
        assert (posts_ctrl.id, posts_index.id) in edge_pairs

    def test_no_controller_no_edges(self) -> None:
        """Without any controller-concept symbols, no edges are produced."""
        route = _sym("index", concepts=["route"], span=(5, 15))
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[route])
        result = link_controller_routes(ctx)
        assert len(result.edges) == 0

    def test_no_routes_no_edges(self) -> None:
        """A controller with no nested route symbols produces no edges."""
        controller = _sym("UsersController", kind="class", concepts=["controller"], span=(1, 50))
        helper = _sym("helper", kind="function", span=(5, 15))
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[controller, helper])
        result = link_controller_routes(ctx)
        assert len(result.edges) == 0

    def test_controller_without_span_skipped(self) -> None:
        """Controllers missing span info cannot be nested-matched and are skipped."""
        controller = Symbol(
            id="ruby:app/users.rb:0-0:UsersController:class",
            name="UsersController",
            kind="class",
            language="ruby",
            path="app/users.rb",
            span=None,
            meta={"concepts": [{"concept": "controller"}]},
        )
        route = _sym(
            "UsersController#index",
            path="app/users.rb",
            concepts=["route"],
            span=(5, 15),
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[controller, route])
        result = link_controller_routes(ctx)
        assert len(result.edges) == 0

    def test_route_without_span_skipped(self) -> None:
        """Routes missing span info cannot be nested-matched and are skipped."""
        controller = _sym(
            "UsersController",
            path="app/users.rb",
            kind="class",
            concepts=["controller"],
            span=(1, 50),
        )
        route = Symbol(
            id="ruby:app/users.rb:0-0:index:function",
            name="index",
            kind="function",
            language="ruby",
            path="app/users.rb",
            span=None,
            meta={"concepts": [{"concept": "route"}]},
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[controller, route])
        result = link_controller_routes(ctx)
        assert len(result.edges) == 0

    def test_test_file_excluded(self) -> None:
        """Symbols in test files are excluded to prevent spurious edges."""
        controller = _sym(
            "UsersControllerTest",
            path="tests/users_controller_test.rb",
            kind="class",
            concepts=["controller"],
            span=(1, 50),
        )
        route = _sym(
            "UsersControllerTest#test_index",
            path="tests/users_controller_test.rb",
            concepts=["route"],
            span=(5, 15),
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[controller, route])
        result = link_controller_routes(ctx)
        assert len(result.edges) == 0

    def test_bare_string_concept_not_matched(self) -> None:
        """Regression: bare-string concept shape (legacy/wrong) must NOT match (INV-tuzub)."""
        controller = Symbol(
            id="ruby:app/users.rb:1-50:UsersController:class",
            name="UsersController",
            kind="class",
            language="ruby",
            path="app/users.rb",
            span=Span(1, 50, 0, 0),
            meta={"concepts": ["controller"]},
        )
        route = Symbol(
            id="ruby:app/users.rb:5-15:index:function",
            name="index",
            kind="function",
            language="ruby",
            path="app/users.rb",
            span=Span(5, 15, 0, 0),
            meta={"concepts": ["route"]},
        )
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[controller, route])
        result = link_controller_routes(ctx)
        assert len(result.edges) == 0

    def test_nested_controllers_innermost_wins(self) -> None:
        """When two controllers both enclose the same route, only the innermost (tightest) links."""
        outer = _sym(
            "BaseController", kind="class", concepts=["controller"], span=(1, 100)
        )
        inner = _sym(
            "UsersController", kind="class", concepts=["controller"], span=(10, 60)
        )
        route = _sym("UsersController#index", concepts=["route"], span=(15, 25))
        ctx = LinkerContext(
            repo_root=Path("/repo"), symbols=[outer, inner, route]
        )
        result = link_controller_routes(ctx)
        # Innermost controller gets the edge; outer does not.
        assert len(result.edges) == 1
        assert result.edges[0].src == inner.id
        assert result.edges[0].dst == route.id

    def test_cross_framework_coverage(self) -> None:
        """Verify the linker is framework-agnostic: works for Rails (ruby),
        ASP.NET (csharp), and Django (python) shapes.

        Per WI-gokop acceptance #1: linker must emit meaningful edges on at
        least 3 frameworks. This unit test stands in for one such verification;
        bakeoff cohort validates at scale.
        """
        rails = _sym(
            "UsersController",
            path="app/controllers/users_controller.rb",
            kind="class",
            lang="ruby",
            concepts=["controller"],
            span=(1, 40),
        )
        rails_idx = _sym(
            "UsersController#index",
            path="app/controllers/users_controller.rb",
            lang="ruby",
            concepts=["route"],
            span=(5, 10),
        )
        aspnet = _sym(
            "UsersController",
            path="Controllers/UsersController.cs",
            kind="class",
            lang="csharp",
            concepts=["controller"],
            span=(1, 40),
        )
        aspnet_idx = _sym(
            "UsersController.Get",
            path="Controllers/UsersController.cs",
            lang="csharp",
            concepts=["route"],
            span=(5, 10),
        )
        django = _sym(
            "UserViewSet",
            path="app/views.py",
            kind="class",
            lang="python",
            concepts=["controller"],
            span=(1, 40),
        )
        django_list = _sym(
            "UserViewSet.list",
            path="app/views.py",
            lang="python",
            concepts=["route"],
            span=(5, 10),
        )
        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[
                rails, rails_idx, aspnet, aspnet_idx, django, django_list,
            ],
        )
        result = link_controller_routes(ctx)
        assert len(result.edges) == 3
        # Each controller gets exactly one route edge
        by_src: dict[str, list] = {}
        for e in result.edges:
            by_src.setdefault(e.src, []).append(e)
        assert len(by_src[rails.id]) == 1
        assert len(by_src[aspnet.id]) == 1
        assert len(by_src[django.id]) == 1

    def test_run_metadata_present(self) -> None:
        """Result should include AnalysisRun metadata."""
        controller = _sym(
            "UsersController", kind="class", concepts=["controller"], span=(1, 50)
        )
        route = _sym("UsersController#index", concepts=["route"], span=(5, 15))
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[controller, route])
        result = link_controller_routes(ctx)
        assert result.run is not None
        assert "controller-routes" in result.run.pass_id

    def test_edge_confidence(self) -> None:
        """Controller-routes edges should have 0.80 confidence."""
        controller = _sym(
            "UsersController", kind="class", concepts=["controller"], span=(1, 50)
        )
        route = _sym("UsersController#index", concepts=["route"], span=(5, 15))
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[controller, route])
        result = link_controller_routes(ctx)
        assert result.edges[0].confidence == 0.80

    def test_empty_symbol_list(self) -> None:
        """Empty symbol list yields no edges (and no exceptions)."""
        ctx = LinkerContext(repo_root=Path("/repo"), symbols=[])
        result = link_controller_routes(ctx)
        assert result.edges == []
