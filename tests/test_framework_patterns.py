"""Tests for framework pattern matching (ADR-0003 v0.8.x).

Tests the YAML-based framework pattern system that enriches symbols
with concept metadata (route, model, task, etc.).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.framework_patterns import (
    FrameworkPatternDef,
    Pattern,
    clear_pattern_cache,
    enrich_symbols,
    get_frameworks_dir,
    load_framework_patterns,
    match_patterns,
)
from hypergumbo.ir import Span, Symbol


class TestPattern:
    """Tests for the Pattern dataclass."""

    def test_pattern_matches_decorator(self) -> None:
        """Pattern matches symbol with matching decorator."""
        pattern = Pattern(
            concept="route",
            decorator=r"^(app|router)\.(get|post|put|delete)$",
            extract_method="decorator_suffix",
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="my_endpoint",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.get", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "route"
        assert result["matched_decorator"] == "app.get"
        assert result["method"] == "GET"

    def test_pattern_extracts_path_from_decorator(self) -> None:
        """Pattern extracts route path from decorator args."""
        pattern = Pattern(
            concept="route",
            decorator=r"^app\.get$",
            extract_path="args[0]",
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="get_users",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.get", "args": ["/users/{id}"], "kwargs": {}},
                ],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["path"] == "/users/{id}"

    def test_pattern_matches_base_class(self) -> None:
        """Pattern matches symbol with matching base class."""
        pattern = Pattern(
            concept="model",
            base_class=r"^(pydantic\.)?BaseModel$",
        )

        symbol = Symbol(
            id="test:file.py:1:User:class",
            name="User",
            kind="class",
            language="python",
            path="file.py",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["BaseModel"],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "model"
        assert result["matched_base_class"] == "BaseModel"

    def test_pattern_matches_annotation(self) -> None:
        """Pattern matches Java annotation."""
        pattern = Pattern(
            concept="route",
            annotation=r"^@(Get|Post|Put|Delete)Mapping$",
        )

        symbol = Symbol(
            id="test:Controller.java:1:getUser:method",
            name="getUser",
            kind="method",
            language="java",
            path="Controller.java",
            span=Span(1, 10, 0, 0),
            meta={
                "annotations": [
                    {"name": "@GetMapping", "value": "/users/{id}"},
                ],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "route"
        assert result["matched_annotation"] == "@GetMapping"

    def test_pattern_matches_parameter_type(self) -> None:
        """Pattern matches function parameter type."""
        pattern = Pattern(
            concept="dependency",
            parameter_type=r"^Depends$",
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="create_user",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={
                "parameters": [
                    {"name": "db", "type": "Depends"},
                ],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "dependency"
        assert result["matched_parameter_type"] == "Depends"

    def test_pattern_no_match(self) -> None:
        """Pattern returns None when no match found."""
        pattern = Pattern(
            concept="route",
            decorator=r"^app\.get$",
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="helper",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [],  # No decorators
            },
        )

        result = pattern.matches(symbol)
        assert result is None

    def test_pattern_no_meta(self) -> None:
        """Pattern handles symbol with no metadata."""
        pattern = Pattern(
            concept="route",
            decorator=r"^app\.get$",
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="func",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta=None,  # No metadata
        )

        result = pattern.matches(symbol)
        assert result is None

    def test_pattern_extract_kwargs_method(self) -> None:
        """Pattern extracts HTTP method from kwargs."""
        pattern = Pattern(
            concept="route",
            decorator=r"^app\.route$",
            extract_method="kwargs.methods",
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="handle",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "app.route",
                        "args": ["/path"],
                        "kwargs": {"methods": ["POST", "PUT"]},
                    },
                ],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["method"] == "POST"  # First method

    def test_pattern_decorator_as_string(self) -> None:
        """Pattern handles decorators stored as strings."""
        pattern = Pattern(
            concept="route",
            decorator=r"^app\.get$",
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="get_users",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": ["app.get"],  # Simple string format
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["matched_decorator"] == "app.get"

    def test_pattern_annotation_extracts_path(self) -> None:
        """Pattern extracts path from annotation metadata."""
        pattern = Pattern(
            concept="route",
            annotation=r"^@GetMapping$",
            extract_path="value",
        )

        symbol = Symbol(
            id="test:Controller.java:1:getUser:method",
            name="getUser",
            kind="method",
            language="java",
            path="Controller.java",
            span=Span(1, 10, 0, 0),
            meta={
                "annotations": [
                    {"name": "@GetMapping", "value": "/users/{id}"},
                ],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["path"] == "/users/{id}"

    def test_pattern_extract_path_from_kwargs(self) -> None:
        """Pattern extracts path from kwargs."""
        pattern = Pattern(
            concept="route",
            decorator=r"^app\.route$",
            extract_path="kwargs.path",
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="handle",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "app.route",
                        "args": [],
                        "kwargs": {"path": "/api/users"},
                    },
                ],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["path"] == "/api/users"

    def test_pattern_extract_path_invalid_index(self) -> None:
        """Pattern handles invalid array index in extract_path."""
        pattern = Pattern(
            concept="route",
            decorator=r"^app\.get$",
            extract_path="args[99]",  # Index out of bounds
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="get_users",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.get", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        # Should not have path extracted (invalid index)
        assert "path" not in result

    def test_pattern_extract_path_malformed_index(self) -> None:
        """Pattern handles malformed array index gracefully."""
        pattern = Pattern(
            concept="route",
            decorator=r"^app\.get$",
            extract_path="args[abc]",  # Not a number
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="get_users",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.get", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        # Should not have path extracted (malformed index)
        assert "path" not in result

    def test_pattern_extract_method_single_value(self) -> None:
        """Pattern extracts HTTP method from single value (not list)."""
        pattern = Pattern(
            concept="route",
            decorator=r"^app\.route$",
            extract_method="kwargs.method",
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="handle",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "app.route",
                        "args": ["/path"],
                        "kwargs": {"method": "POST"},  # Single value, not list
                    },
                ],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["method"] == "POST"

    def test_pattern_extract_method_missing(self) -> None:
        """Pattern handles missing method gracefully."""
        pattern = Pattern(
            concept="route",
            decorator=r"^app\.route$",
            extract_method="kwargs.methods",
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="handle",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "app.route",
                        "args": ["/path"],
                        "kwargs": {},  # No methods key
                    },
                ],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert "method" not in result


class TestFrameworkPatternDef:
    """Tests for the FrameworkPatternDef dataclass."""

    def test_from_dict_basic(self) -> None:
        """Creates FrameworkPatternDef from basic dict."""
        data = {
            "id": "fastapi",
            "language": "python",
            "patterns": [
                {"concept": "route", "decorator": r"^app\.get$"},
            ],
            "linkers": ["http"],
        }

        pattern_def = FrameworkPatternDef.from_dict(data)

        assert pattern_def.id == "fastapi"
        assert pattern_def.language == "python"
        assert len(pattern_def.patterns) == 1
        assert pattern_def.patterns[0].concept == "route"
        assert pattern_def.linkers == ["http"]

    def test_from_dict_defaults(self) -> None:
        """Uses defaults for missing fields."""
        data = {}

        pattern_def = FrameworkPatternDef.from_dict(data)

        assert pattern_def.id == "unknown"
        assert pattern_def.language == "unknown"
        assert pattern_def.patterns == []
        assert pattern_def.linkers == []


class TestLoadFrameworkPatterns:
    """Tests for load_framework_patterns function."""

    def test_returns_none_for_missing_file(self) -> None:
        """Returns None when YAML file doesn't exist."""
        clear_pattern_cache()  # Clear cache first
        result = load_framework_patterns("nonexistent_framework")
        assert result is None

    def test_caches_results(self) -> None:
        """Caches loaded patterns to avoid re-reading files."""
        clear_pattern_cache()

        # First call - returns None (file doesn't exist)
        result1 = load_framework_patterns("test_cache_framework")
        assert result1 is None

        # Second call - should use cache
        result2 = load_framework_patterns("test_cache_framework")
        assert result2 is None  # Same result from cache

    def test_loads_yaml_file(self, tmp_path: Path) -> None:
        """Loads patterns from YAML file."""
        clear_pattern_cache()

        # Create a test YAML file
        # Note: In YAML double quotes, backslash needs double escaping
        yaml_content = """
id: test_framework
language: python
patterns:
  - concept: route
    decorator: "^app\\\\.get$"
linkers:
  - http
"""
        yaml_file = tmp_path / "test_fw.yaml"
        yaml_file.write_text(yaml_content)

        # Mock the frameworks directory to use our temp dir
        with patch(
            "hypergumbo.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            result = load_framework_patterns("test_fw")

        assert result is not None
        assert result.id == "test_framework"
        assert result.language == "python"
        assert len(result.patterns) == 1


class TestMatchPatterns:
    """Tests for match_patterns function."""

    def test_matches_single_pattern(self) -> None:
        """Matches symbol against single pattern."""
        pattern_def = FrameworkPatternDef(
            id="fastapi",
            language="python",
            patterns=[
                Pattern(concept="route", decorator=r"^app\.get$"),
            ],
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="get_users",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={"decorators": [{"name": "app.get"}]},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["framework"] == "fastapi"

    def test_matches_multiple_patterns(self) -> None:
        """Matches symbol against multiple pattern definitions."""
        pattern_def1 = FrameworkPatternDef(
            id="fastapi",
            language="python",
            patterns=[Pattern(concept="route", decorator=r"^app\.get$")],
        )
        pattern_def2 = FrameworkPatternDef(
            id="pydantic",
            language="python",
            patterns=[Pattern(concept="model", base_class=r"^BaseModel$")],
        )

        # Symbol that matches neither
        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="helper",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def1, pattern_def2])
        assert len(results) == 0

    def test_no_match_empty_patterns(self) -> None:
        """Returns empty list when no patterns match."""
        pattern_def = FrameworkPatternDef(
            id="fastapi",
            language="python",
            patterns=[],
        )

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="func",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])
        assert results == []


class TestEnrichSymbols:
    """Tests for enrich_symbols function."""

    def test_enriches_symbols_with_concepts(self, tmp_path: Path) -> None:
        """Adds concept metadata to matching symbols."""
        clear_pattern_cache()

        # Create a test YAML file (double escape backslash in YAML)
        yaml_content = """
id: test_fw
language: python
patterns:
  - concept: route
    decorator: "^app\\\\.get$"
"""
        yaml_file = tmp_path / "test_fw.yaml"
        yaml_file.write_text(yaml_content)

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="get_users",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={"decorators": [{"name": "app.get"}]},
        )

        with patch(
            "hypergumbo.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            enriched = enrich_symbols([symbol], {"test_fw"})

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        assert enriched[0].meta["concepts"][0]["concept"] == "route"

    def test_no_enrichment_for_unknown_frameworks(self) -> None:
        """Skips enrichment when no patterns found for framework."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="func",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={},
        )

        # No YAML file exists for "unknown_fw"
        enriched = enrich_symbols([symbol], {"unknown_fw"})

        assert len(enriched) == 1
        # Should not have concepts (no pattern matched)
        assert "concepts" not in enriched[0].meta

    def test_handles_symbol_with_no_meta(self, tmp_path: Path) -> None:
        """Enriches symbol that has no initial metadata."""
        clear_pattern_cache()

        # Create a test YAML with base_class pattern
        yaml_content = """
id: pydantic
language: python
patterns:
  - concept: model
    base_class: "^BaseModel$"
"""
        yaml_file = tmp_path / "pydantic.yaml"
        yaml_file.write_text(yaml_content)

        # Symbol with no meta at all
        symbol = Symbol(
            id="test:file.py:1:User:class",
            name="User",
            kind="class",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta=None,
        )

        with patch(
            "hypergumbo.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            enriched = enrich_symbols([symbol], {"pydantic"})

        # Should not crash, and symbol should remain unenriched
        # (can't match base_class without meta)
        assert len(enriched) == 1

    def test_enriches_symbol_creating_meta(self, tmp_path: Path) -> None:
        """Creates meta dict when symbol has none and pattern matches."""
        clear_pattern_cache()

        yaml_content = """
id: test_fw
language: python
patterns:
  - concept: route
    decorator: "^app\\\\.get$"
"""
        yaml_file = tmp_path / "test_fw.yaml"
        yaml_file.write_text(yaml_content)

        # Symbol with meta containing matching decorator
        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="get_users",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={"decorators": [{"name": "app.get"}]},
        )

        with patch(
            "hypergumbo.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            enriched = enrich_symbols([symbol], {"test_fw"})

        assert enriched[0].meta is not None
        assert "concepts" in enriched[0].meta


class TestGetFrameworksDir:
    """Tests for get_frameworks_dir function."""

    def test_returns_path(self) -> None:
        """Returns a Path object."""
        result = get_frameworks_dir()
        assert isinstance(result, Path)
        assert result.name == "frameworks"


class TestEnrichSymbolsEdgeCases:
    """Additional edge case tests for enrich_symbols."""

    def test_creates_meta_dict_when_none(self, tmp_path: Path) -> None:
        """Creates meta dict when symbol starts with meta=None and pattern matches."""
        clear_pattern_cache()

        # Use base_class pattern since it doesn't require meta to have decorators
        yaml_content = """
id: test_fw
language: python
patterns:
  - concept: model
    base_class: "^BaseModel$"
"""
        yaml_file = tmp_path / "test_fw.yaml"
        yaml_file.write_text(yaml_content)

        # Symbol with meta that has base_classes but nothing else
        symbol = Symbol(
            id="test:file.py:1:User:class",
            name="User",
            kind="class",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={"base_classes": ["BaseModel"]},  # Has required field
        )

        # Create a second symbol with no meta to test initialization
        symbol_no_meta = Symbol(
            id="test:file.py:20:Item:class",
            name="Item",
            kind="class",
            language="python",
            path="file.py",
            span=Span(20, 30, 0, 0),
            meta=None,
        )

        with patch(
            "hypergumbo.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            enriched = enrich_symbols([symbol, symbol_no_meta], {"test_fw"})

        # First symbol should have concepts
        assert enriched[0].meta is not None
        assert "concepts" in enriched[0].meta
        assert enriched[0].meta["concepts"][0]["concept"] == "model"

        # Second symbol should remain unchanged (no meta to match against)
        # It won't match because it has no base_classes


class TestFlaskPatterns:
    """Tests for Flask framework pattern matching."""

    def test_flask_get_route_pattern(self) -> None:
        """Flask 2.0+ @app.get decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("flask")

        assert pattern_def is not None, "Flask patterns YAML should exist"

        symbol = Symbol(
            id="test:app.py:1:get_users:function",
            name="get_users",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.get", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_decorator"] == "app.get"
        assert results[0]["method"] == "GET"
        assert results[0]["path"] == "/users"

    def test_flask_post_route_pattern(self) -> None:
        """Flask @app.post decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("flask")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:app.py:1:create_user:function",
            name="create_user",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.post", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "POST"

    def test_flask_classic_route_pattern(self) -> None:
        """Classic Flask @app.route decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("flask")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:app.py:1:handle:function",
            name="handle",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "app.route",
                        "args": ["/api/data"],
                        "kwargs": {"methods": ["POST", "PUT"]},
                    },
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["path"] == "/api/data"
        assert results[0]["method"] == "POST"  # First method

    def test_flask_blueprint_route_pattern(self) -> None:
        """Flask blueprint route decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("flask")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:routes.py:1:get_item:function",
            name="get_item",
            kind="function",
            language="python",
            path="routes.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "blueprint.get", "args": ["/items/<id>"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "GET"
        assert results[0]["path"] == "/items/<id>"

    def test_flask_bp_route_pattern(self) -> None:
        """Flask bp.route decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("flask")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:routes.py:1:delete_item:function",
            name="delete_item",
            kind="function",
            language="python",
            path="routes.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "bp.delete", "args": ["/items/<id>"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "DELETE"

    def test_flask_before_request_hook(self) -> None:
        """Flask @app.before_request matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("flask")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:app.py:1:check_auth:function",
            name="check_auth",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.before_request", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_flask_errorhandler(self) -> None:
        """Flask @app.errorhandler matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("flask")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:app.py:1:handle_404:function",
            name="handle_404",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.errorhandler", "args": [404], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "error_handler"

    def test_flask_restful_resource(self) -> None:
        """Flask-RESTful Resource base class matches api_resource pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("flask")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:resources.py:1:UserResource:class",
            name="UserResource",
            kind="class",
            language="python",
            path="resources.py",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["Resource"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "api_resource"

    def test_flask_wtf_form(self) -> None:
        """Flask-WTF FlaskForm base class matches form pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("flask")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:forms.py:1:LoginForm:class",
            name="LoginForm",
            kind="class",
            language="python",
            path="forms.py",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["FlaskForm"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "form"

    def test_flask_sqlalchemy_model(self) -> None:
        """Flask-SQLAlchemy db.Model base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("flask")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:models.py:1:User:class",
            name="User",
            kind="class",
            language="python",
            path="models.py",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["db.Model"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"

    def test_flask_enrich_symbols_integration(self) -> None:
        """Integration test: enrich_symbols adds Flask route concepts."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:app.py:1:get_users:function",
            name="get_users",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.get", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"flask"})

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        route_concept = enriched[0].meta["concepts"][0]
        assert route_concept["concept"] == "route"
        assert route_concept["method"] == "GET"
        assert route_concept["path"] == "/users"
        assert route_concept["framework"] == "flask"


class TestNestJSPatterns:
    """Tests for NestJS framework pattern matching."""

    def test_nestjs_get_route_pattern(self) -> None:
        """NestJS @Get() decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nestjs")

        assert pattern_def is not None, "NestJS patterns YAML should exist"

        symbol = Symbol(
            id="test:users.controller.ts:10:findAll:method",
            name="findAll",
            kind="method",
            language="typescript",
            path="users.controller.ts",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "Get", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_decorator"] == "Get"
        assert results[0]["method"] == "GET"

    def test_nestjs_get_with_path_pattern(self) -> None:
        """NestJS @Get(':id') decorator matches route pattern with path."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nestjs")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:users.controller.ts:20:findOne:method",
            name="findOne",
            kind="method",
            language="typescript",
            path="users.controller.ts",
            span=Span(20, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "Get", "args": [":id"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "GET"
        assert results[0]["path"] == ":id"

    def test_nestjs_post_route_pattern(self) -> None:
        """NestJS @Post() decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nestjs")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:users.controller.ts:30:create:method",
            name="create",
            kind="method",
            language="typescript",
            path="users.controller.ts",
            span=Span(30, 40, 0, 0),
            meta={
                "decorators": [
                    {"name": "Post", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "POST"

    def test_nestjs_controller_pattern(self) -> None:
        """NestJS @Controller decorator matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nestjs")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:users.controller.ts:1:UsersController:class",
            name="UsersController",
            kind="class",
            language="typescript",
            path="users.controller.ts",
            span=Span(1, 50, 0, 0),
            meta={
                "decorators": [
                    {"name": "Controller", "args": ["users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"
        assert results[0]["matched_decorator"] == "Controller"

    def test_nestjs_injectable_pattern(self) -> None:
        """NestJS @Injectable decorator matches service pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nestjs")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:users.service.ts:1:UsersService:class",
            name="UsersService",
            kind="class",
            language="typescript",
            path="users.service.ts",
            span=Span(1, 100, 0, 0),
            meta={
                "decorators": [
                    {"name": "Injectable", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "service"

    def test_nestjs_module_pattern(self) -> None:
        """NestJS @Module decorator matches module pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nestjs")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:users.module.ts:1:UsersModule:class",
            name="UsersModule",
            kind="class",
            language="typescript",
            path="users.module.ts",
            span=Span(1, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "Module", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "module"

    def test_nestjs_use_guards_pattern(self) -> None:
        """NestJS @UseGuards decorator matches guard pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nestjs")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:users.controller.ts:5:AdminController:class",
            name="AdminController",
            kind="class",
            language="typescript",
            path="users.controller.ts",
            span=Span(5, 50, 0, 0),
            meta={
                "decorators": [
                    {"name": "UseGuards", "args": ["AuthGuard"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "guard"

    def test_nestjs_websocket_gateway_pattern(self) -> None:
        """NestJS @WebSocketGateway decorator matches websocket_gateway pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nestjs")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:events.gateway.ts:1:EventsGateway:class",
            name="EventsGateway",
            kind="class",
            language="typescript",
            path="events.gateway.ts",
            span=Span(1, 50, 0, 0),
            meta={
                "decorators": [
                    {"name": "WebSocketGateway", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "websocket_gateway"

    def test_nestjs_subscribe_message_pattern(self) -> None:
        """NestJS @SubscribeMessage decorator matches websocket_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nestjs")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:events.gateway.ts:10:handleEvent:method",
            name="handleEvent",
            kind="method",
            language="typescript",
            path="events.gateway.ts",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "SubscribeMessage", "args": ["events"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "websocket_handler"

    def test_nestjs_enrich_symbols_integration(self) -> None:
        """Integration test: enrich_symbols adds NestJS route concepts."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:users.controller.ts:10:findAll:method",
            name="findAll",
            kind="method",
            language="typescript",
            path="users.controller.ts",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "Get", "args": ["users"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"nestjs"})

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        route_concept = enriched[0].meta["concepts"][0]
        assert route_concept["concept"] == "route"
        assert route_concept["method"] == "GET"
        assert route_concept["path"] == "users"
        assert route_concept["framework"] == "nestjs"


class TestSpringPatterns:
    """Tests for Spring Framework pattern matching."""

    def test_spring_get_mapping_pattern(self) -> None:
        """Spring @GetMapping annotation matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring")

        assert pattern_def is not None, "Spring patterns YAML should exist"

        symbol = Symbol(
            id="test:UserController.java:10:getUsers:method",
            name="getUsers",
            kind="method",
            language="java",
            path="UserController.java",
            span=Span(10, 20, 0, 0),
            meta={
                "annotations": [
                    {"name": "@GetMapping", "value": "/users"},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_annotation"] == "@GetMapping"
        assert results[0]["method"] == "GET"
        assert results[0]["path"] == "/users"

    def test_spring_post_mapping_pattern(self) -> None:
        """Spring @PostMapping annotation matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserController.java:20:createUser:method",
            name="createUser",
            kind="method",
            language="java",
            path="UserController.java",
            span=Span(20, 30, 0, 0),
            meta={
                "annotations": [
                    {"name": "@PostMapping", "value": "/users"},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "POST"

    def test_spring_rest_controller_pattern(self) -> None:
        """Spring @RestController annotation matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserController.java:1:UserController:class",
            name="UserController",
            kind="class",
            language="java",
            path="UserController.java",
            span=Span(1, 100, 0, 0),
            meta={
                "annotations": [
                    {"name": "@RestController"},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_spring_service_pattern(self) -> None:
        """Spring @Service annotation matches service pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserService.java:1:UserService:class",
            name="UserService",
            kind="class",
            language="java",
            path="UserService.java",
            span=Span(1, 200, 0, 0),
            meta={
                "annotations": [
                    {"name": "@Service"},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "service"

    def test_spring_repository_pattern(self) -> None:
        """Spring @Repository annotation matches repository pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserRepository.java:1:UserRepository:interface",
            name="UserRepository",
            kind="interface",
            language="java",
            path="UserRepository.java",
            span=Span(1, 50, 0, 0),
            meta={
                "annotations": [
                    {"name": "@Repository"},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "repository"

    def test_spring_entity_pattern(self) -> None:
        """Spring @Entity annotation matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:User.java:1:User:class",
            name="User",
            kind="class",
            language="java",
            path="User.java",
            span=Span(1, 50, 0, 0),
            meta={
                "annotations": [
                    {"name": "@Entity"},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"

    def test_spring_scheduled_task_pattern(self) -> None:
        """Spring @Scheduled annotation matches task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:TaskScheduler.java:10:runDaily:method",
            name="runDaily",
            kind="method",
            language="java",
            path="TaskScheduler.java",
            span=Span(10, 20, 0, 0),
            meta={
                "annotations": [
                    {"name": "@Scheduled"},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "task"

    def test_spring_put_mapping_pattern(self) -> None:
        """Spring @PutMapping annotation matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserController.java:30:updateUser:method",
            name="updateUser",
            kind="method",
            language="java",
            path="UserController.java",
            span=Span(30, 40, 0, 0),
            meta={
                "annotations": [
                    {"name": "@PutMapping", "value": "/users/{id}"},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "PUT"
        assert results[0]["path"] == "/users/{id}"

    def test_spring_delete_mapping_pattern(self) -> None:
        """Spring @DeleteMapping annotation matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserController.java:40:deleteUser:method",
            name="deleteUser",
            kind="method",
            language="java",
            path="UserController.java",
            span=Span(40, 50, 0, 0),
            meta={
                "annotations": [
                    {"name": "@DeleteMapping", "value": "/users/{id}"},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "DELETE"

    def test_spring_enrich_symbols_integration(self) -> None:
        """Integration test: enrich_symbols adds Spring route concepts."""
        clear_pattern_cache()

        symbol = Symbol(
            id="test:UserController.java:10:getUsers:method",
            name="getUsers",
            kind="method",
            language="java",
            path="UserController.java",
            span=Span(10, 20, 0, 0),
            meta={
                "annotations": [
                    {"name": "@GetMapping", "value": "/users"},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"spring"})

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        route_concept = enriched[0].meta["concepts"][0]
        assert route_concept["concept"] == "route"
        assert route_concept["method"] == "GET"
        assert route_concept["path"] == "/users"
        assert route_concept["framework"] == "spring"


class TestAnnotationMethodExtraction:
    """Tests for annotation-based method extraction modes."""

    def test_annotation_name_upper_extraction(self, tmp_path: Path) -> None:
        """Test annotation_name_upper extraction mode."""
        clear_pattern_cache()

        # Create a custom YAML file with annotation_name_upper extraction
        yaml_content = """
id: custom_fw
language: java
patterns:
  - concept: route
    annotation: "^@(GET|POST|PUT|DELETE)$"
    extract_method: "annotation_name_upper"
"""
        yaml_file = tmp_path / "custom_fw.yaml"
        yaml_file.write_text(yaml_content)

        symbol = Symbol(
            id="test:Resource.java:1:getAll:method",
            name="getAll",
            kind="method",
            language="java",
            path="Resource.java",
            span=Span(1, 10, 0, 0),
            meta={
                "annotations": [
                    {"name": "@GET"},
                ],
            },
        )

        with patch(
            "hypergumbo.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            pattern_def = load_framework_patterns("custom_fw")
            results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["method"] == "GET"

    def test_annotation_name_upper_without_at_prefix(self, tmp_path: Path) -> None:
        """Test annotation_name_upper when annotation doesn't have @ prefix."""
        clear_pattern_cache()

        yaml_content = """
id: custom_fw
language: java
patterns:
  - concept: route
    annotation: "^(GET|POST)$"
    extract_method: "annotation_name_upper"
"""
        yaml_file = tmp_path / "custom_fw.yaml"
        yaml_file.write_text(yaml_content)

        symbol = Symbol(
            id="test:Resource.java:1:getAll:method",
            name="getAll",
            kind="method",
            language="java",
            path="Resource.java",
            span=Span(1, 10, 0, 0),
            meta={
                "annotations": [
                    {"name": "GET"},  # No @ prefix
                ],
            },
        )

        with patch(
            "hypergumbo.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            pattern_def = load_framework_patterns("custom_fw")
            results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["method"] == "GET"

    def test_annotation_no_method_extraction(self, tmp_path: Path) -> None:
        """Test annotation matching with no method extraction configured."""
        clear_pattern_cache()

        yaml_content = """
id: custom_fw
language: java
patterns:
  - concept: service
    annotation: "^@Service$"
"""
        yaml_file = tmp_path / "custom_fw.yaml"
        yaml_file.write_text(yaml_content)

        symbol = Symbol(
            id="test:UserService.java:1:UserService:class",
            name="UserService",
            kind="class",
            language="java",
            path="UserService.java",
            span=Span(1, 50, 0, 0),
            meta={
                "annotations": [
                    {"name": "@Service"},
                ],
            },
        )

        with patch(
            "hypergumbo.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            pattern_def = load_framework_patterns("custom_fw")
            results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "service"
        # No method field since no extraction configured
        assert "method" not in results[0]

    def test_annotation_unknown_extraction_mode(self, tmp_path: Path) -> None:
        """Test annotation matching with unknown extraction mode returns no method."""
        clear_pattern_cache()

        yaml_content = """
id: custom_fw
language: java
patterns:
  - concept: route
    annotation: "^@Get$"
    extract_method: "unknown_mode"
"""
        yaml_file = tmp_path / "custom_fw.yaml"
        yaml_file.write_text(yaml_content)

        symbol = Symbol(
            id="test:Controller.java:1:get:method",
            name="get",
            kind="method",
            language="java",
            path="Controller.java",
            span=Span(1, 10, 0, 0),
            meta={
                "annotations": [
                    {"name": "@Get"},
                ],
            },
        )

        with patch(
            "hypergumbo.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            pattern_def = load_framework_patterns("custom_fw")
            results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        # Unknown extraction mode should not add method
        assert "method" not in results[0]
