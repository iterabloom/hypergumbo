"""Tests for FastAPI framework patterns (ADR-0003 v0.8.x).

Verifies that FastAPI patterns correctly match and enrich symbols.
"""

from pathlib import Path

import pytest

from hypergumbo_core.framework_patterns import (
    clear_pattern_cache,
    enrich_symbols,
    load_framework_patterns,
)
from hypergumbo_core.ir import Span, Symbol


class TestFastAPIPatternLoading:
    """Tests for loading FastAPI patterns from YAML."""

    def test_loads_fastapi_yaml(self) -> None:
        """FastAPI patterns load successfully from YAML file."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("fastapi")

        assert pattern_def is not None
        assert pattern_def.id == "fastapi"
        assert pattern_def.language == "python"
        assert len(pattern_def.patterns) > 0
        assert "http" in pattern_def.linkers

    def test_has_route_patterns(self) -> None:
        """FastAPI has route decorator patterns."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("fastapi")
        assert pattern_def is not None

        route_patterns = [p for p in pattern_def.patterns if p.concept == "route"]
        assert len(route_patterns) >= 2  # HTTP method routes and generic route

    def test_has_model_pattern(self) -> None:
        """FastAPI has Pydantic BaseModel pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("fastapi")
        assert pattern_def is not None

        model_patterns = [p for p in pattern_def.patterns if p.concept == "model"]
        assert len(model_patterns) >= 1


class TestFastAPIRouteMatching:
    """Tests for FastAPI route pattern matching."""

    def test_matches_app_get_decorator(self) -> None:
        """Matches @app.get("/users") decorator."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:main.py:10:get_users:function",
            name="get_users",
            kind="function",
            language="python",
            path="main.py",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.get", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        assert enriched[0].meta is not None
        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        assert any(c["concept"] == "route" for c in concepts)

        route_concept = next(c for c in concepts if c["concept"] == "route")
        assert route_concept["path"] == "/users"
        assert route_concept["method"] == "GET"

    def test_matches_router_post_decorator(self) -> None:
        """Matches @router.post("/items") decorator."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:routes.py:5:create_item:function",
            name="create_item",
            kind="function",
            language="python",
            path="routes.py",
            span=Span(5, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "router.post", "args": ["/items"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        assert enriched[0].meta is not None
        concepts = enriched[0].meta["concepts"]
        route_concept = next(c for c in concepts if c["concept"] == "route")
        assert route_concept["path"] == "/items"
        assert route_concept["method"] == "POST"

    def test_matches_app_delete_decorator(self) -> None:
        """Matches @app.delete("/items/{id}") decorator."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:main.py:30:delete_item:function",
            name="delete_item",
            kind="function",
            language="python",
            path="main.py",
            span=Span(30, 40, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.delete", "args": ["/items/{id}"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        concepts = enriched[0].meta["concepts"]
        route_concept = next(c for c in concepts if c["concept"] == "route")
        assert route_concept["path"] == "/items/{id}"
        assert route_concept["method"] == "DELETE"


class TestFastAPIModelMatching:
    """Tests for FastAPI/Pydantic model pattern matching."""

    def test_matches_basemodel_class(self) -> None:
        """Matches class User(BaseModel) pattern."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:models.py:1:User:class",
            name="User",
            kind="class",
            language="python",
            path="models.py",
            span=Span(1, 10, 0, 0),
            meta={
                "base_classes": ["BaseModel"],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        assert enriched[0].meta is not None
        concepts = enriched[0].meta["concepts"]
        assert any(c["concept"] == "model" for c in concepts)

    def test_matches_pydantic_basemodel(self) -> None:
        """Matches class Item(pydantic.BaseModel) pattern."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:schemas.py:1:Item:class",
            name="Item",
            kind="class",
            language="python",
            path="schemas.py",
            span=Span(1, 5, 0, 0),
            meta={
                "base_classes": ["pydantic.BaseModel"],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        concepts = enriched[0].meta["concepts"]
        assert any(c["concept"] == "model" for c in concepts)


class TestFastAPIDependencyMatching:
    """Tests for FastAPI dependency injection pattern matching."""

    def test_matches_depends_parameter(self) -> None:
        """Matches db: Session = Depends(get_db) pattern."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:main.py:10:get_users:function",
            name="get_users",
            kind="function",
            language="python",
            path="main.py",
            span=Span(10, 20, 0, 0),
            meta={
                "parameters": [
                    {"name": "db", "type": "Depends"},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        assert enriched[0].meta is not None
        concepts = enriched[0].meta["concepts"]
        assert any(c["concept"] == "dependency" for c in concepts)

    def test_matches_background_tasks_parameter(self) -> None:
        """Matches background_tasks: BackgroundTasks pattern."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:main.py:30:send_email:function",
            name="send_email",
            kind="function",
            language="python",
            path="main.py",
            span=Span(30, 40, 0, 0),
            meta={
                "parameters": [
                    {"name": "background_tasks", "type": "BackgroundTasks"},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        concepts = enriched[0].meta["concepts"]
        assert any(c["concept"] == "background_task" for c in concepts)


class TestFastAPINamedRouterMatching:
    """Tests for named router patterns like dag_run_router.get."""

    def test_matches_named_router_get(self) -> None:
        """Matches @dag_run_router.get("/dag_runs") decorator."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:core_api.py:10:get_dag_runs:function",
            name="get_dag_runs",
            kind="function",
            language="python",
            path="core_api.py",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "dag_run_router.get", "args": ["/dag_runs"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        assert enriched[0].meta is not None
        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        route_concept = next(c for c in concepts if c["concept"] == "route")
        assert route_concept["path"] == "/dag_runs"
        assert route_concept["method"] == "GET"

    def test_matches_named_router_post(self) -> None:
        """Matches @connections_router.post("/connections") decorator."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:core_api.py:30:create_connection:function",
            name="create_connection",
            kind="function",
            language="python",
            path="core_api.py",
            span=Span(30, 40, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "connections_router.post",
                        "args": ["/connections"],
                        "kwargs": {},
                    },
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        concepts = enriched[0].meta["concepts"]
        route_concept = next(c for c in concepts if c["concept"] == "route")
        assert route_concept["path"] == "/connections"
        assert route_concept["method"] == "POST"

    def test_matches_named_router_route(self) -> None:
        """Matches @api_router.route("/health") generic route."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:routes.py:5:health_check:function",
            name="health_check",
            kind="function",
            language="python",
            path="routes.py",
            span=Span(5, 10, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "api_router.route",
                        "args": ["/health"],
                        "kwargs": {"methods": ["GET"]},
                    },
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        concepts = enriched[0].meta["concepts"]
        route_concept = next(c for c in concepts if c["concept"] == "route")
        assert route_concept["path"] == "/health"

    def test_matches_named_router_middleware(self) -> None:
        """Matches @custom_router.middleware("http") decorator."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:middleware.py:1:add_timing:function",
            name="add_timing",
            kind="function",
            language="python",
            path="middleware.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "custom_router.middleware",
                        "args": ["http"],
                        "kwargs": {},
                    },
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        concepts = enriched[0].meta["concepts"]
        assert any(c["concept"] == "middleware" for c in concepts)

    def test_matches_named_router_exception_handler(self) -> None:
        """Matches @v1_router.exception_handler(HTTPException) decorator."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:errors.py:1:handle_http:function",
            name="handle_http",
            kind="function",
            language="python",
            path="errors.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "v1_router.exception_handler",
                        "args": ["HTTPException"],
                        "kwargs": {},
                    },
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        concepts = enriched[0].meta["concepts"]
        assert any(c["concept"] == "error_handler" for c in concepts)

    def test_no_match_for_unrelated_object(self) -> None:
        """Objects without router/app suffix don't match."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:service.py:10:process:function",
            name="process",
            kind="function",
            language="python",
            path="service.py",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "handler.get", "args": ["/data"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        assert "concepts" not in enriched[0].meta


class TestFastAPINoMatch:
    """Tests that non-FastAPI patterns don't match."""

    def test_no_match_for_flask_decorator(self) -> None:
        """Flask decorators don't match FastAPI patterns."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:app.py:10:home:function",
            name="home",
            kind="function",
            language="python",
            path="app.py",
            span=Span(10, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "flask_app.route", "args": ["/"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        # Should not have concepts (decorator doesn't match)
        assert "concepts" not in enriched[0].meta

    def test_no_match_for_non_pydantic_class(self) -> None:
        """Non-Pydantic classes don't match model pattern."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:utils.py:1:Helper:class",
            name="Helper",
            kind="class",
            language="python",
            path="utils.py",
            span=Span(1, 10, 0, 0),
            meta={
                "base_classes": ["object"],
            },
        )

        enriched = enrich_symbols([symbol], {"fastapi"})

        assert "concepts" not in enriched[0].meta
