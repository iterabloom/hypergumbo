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

    def test_pattern_handles_none_parameter_type(self) -> None:
        """Pattern handles None parameter type without crashing."""
        pattern = Pattern(
            concept="dependency",
            parameter_type=r"^Depends$",
        )

        # Symbol with None type value (not missing, but explicitly None)
        symbol = Symbol(
            id="test:file.py:1:func:function",
            name="create_user",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 10, 0, 0),
            meta={
                "parameters": [
                    {"name": "db", "type": None},  # Explicit None
                    {"name": "user"},  # Missing type key
                ],
            },
        )

        # Should not crash, and should return None (no match)
        result = pattern.matches(symbol)
        assert result is None

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
        pattern_def = load_framework_patterns("spring-boot")

        assert pattern_def is not None, "Spring patterns YAML should exist"

        # Java analyzer stores annotations as decorators without @ prefix
        symbol = Symbol(
            id="test:UserController.java:10:getUsers:method",
            name="getUsers",
            kind="method",
            language="java",
            path="UserController.java",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "GetMapping", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_decorator"] == "GetMapping"
        assert results[0]["method"] == "GET"
        assert results[0]["path"] == "/users"

    def test_spring_post_mapping_pattern(self) -> None:
        """Spring @PostMapping annotation matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring-boot")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserController.java:20:createUser:method",
            name="createUser",
            kind="method",
            language="java",
            path="UserController.java",
            span=Span(20, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "PostMapping", "args": ["/users"], "kwargs": {}},
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
        pattern_def = load_framework_patterns("spring-boot")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserController.java:1:UserController:class",
            name="UserController",
            kind="class",
            language="java",
            path="UserController.java",
            span=Span(1, 100, 0, 0),
            meta={
                "decorators": [
                    {"name": "RestController", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_spring_service_pattern(self) -> None:
        """Spring @Service annotation matches service pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring-boot")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserService.java:1:UserService:class",
            name="UserService",
            kind="class",
            language="java",
            path="UserService.java",
            span=Span(1, 200, 0, 0),
            meta={
                "decorators": [
                    {"name": "Service", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "service"

    def test_spring_repository_pattern(self) -> None:
        """Spring @Repository annotation matches repository pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring-boot")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserRepository.java:1:UserRepository:interface",
            name="UserRepository",
            kind="interface",
            language="java",
            path="UserRepository.java",
            span=Span(1, 50, 0, 0),
            meta={
                "decorators": [
                    {"name": "Repository", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "repository"

    def test_spring_entity_pattern(self) -> None:
        """Spring @Entity annotation matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring-boot")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:User.java:1:User:class",
            name="User",
            kind="class",
            language="java",
            path="User.java",
            span=Span(1, 50, 0, 0),
            meta={
                "decorators": [
                    {"name": "Entity", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"

    def test_spring_scheduled_task_pattern(self) -> None:
        """Spring @Scheduled annotation matches task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring-boot")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:TaskScheduler.java:10:runDaily:method",
            name="runDaily",
            kind="method",
            language="java",
            path="TaskScheduler.java",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "Scheduled", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "task"

    def test_spring_put_mapping_pattern(self) -> None:
        """Spring @PutMapping annotation matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("spring-boot")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserController.java:30:updateUser:method",
            name="updateUser",
            kind="method",
            language="java",
            path="UserController.java",
            span=Span(30, 40, 0, 0),
            meta={
                "decorators": [
                    {"name": "PutMapping", "args": ["/users/{id}"], "kwargs": {}},
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
        pattern_def = load_framework_patterns("spring-boot")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserController.java:40:deleteUser:method",
            name="deleteUser",
            kind="method",
            language="java",
            path="UserController.java",
            span=Span(40, 50, 0, 0),
            meta={
                "decorators": [
                    {"name": "DeleteMapping", "args": ["/users/{id}"], "kwargs": {}},
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
                "decorators": [
                    {"name": "GetMapping", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"spring-boot"})

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        route_concept = enriched[0].meta["concepts"][0]
        assert route_concept["concept"] == "route"
        assert route_concept["method"] == "GET"
        assert route_concept["path"] == "/users"
        assert route_concept["framework"] == "spring-boot"


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


class TestDjangoPatterns:
    """Tests for Django framework pattern matching."""

    def test_django_api_view_decorator(self) -> None:
        """Django REST Framework @api_view decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("django")

        assert pattern_def is not None, "Django patterns YAML should exist"

        symbol = Symbol(
            id="test:views.py:10:get_users:function",
            name="get_users",
            kind="function",
            language="python",
            path="views.py",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "api_view", "args": [], "kwargs": {"methods": ["GET", "POST"]}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_decorator"] == "api_view"
        assert results[0]["method"] == "GET"  # First method from list

    def test_django_apiview_base_class(self) -> None:
        """Django REST Framework APIView base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("django")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:views.py:1:UserView:class",
            name="UserView",
            kind="class",
            language="python",
            path="views.py",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["APIView"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"
        assert results[0]["matched_base_class"] == "APIView"

    def test_django_model_viewset_base_class(self) -> None:
        """Django REST Framework ModelViewSet base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("django")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:views.py:1:UserViewSet:class",
            name="UserViewSet",
            kind="class",
            language="python",
            path="views.py",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["ModelViewSet"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"
        assert results[0]["matched_base_class"] == "ModelViewSet"

    def test_django_model_serializer_base_class(self) -> None:
        """Django REST Framework ModelSerializer base class matches serializer pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("django")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:serializers.py:1:UserSerializer:class",
            name="UserSerializer",
            kind="class",
            language="python",
            path="serializers.py",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["ModelSerializer"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "serializer"
        assert results[0]["matched_base_class"] == "ModelSerializer"

    def test_django_generic_view_base_class(self) -> None:
        """Django generic ListView base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("django")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:views.py:1:UserListView:class",
            name="UserListView",
            kind="class",
            language="python",
            path="views.py",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["ListView"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"
        assert results[0]["matched_base_class"] == "ListView"

    def test_django_model_base_class(self) -> None:
        """Django Model base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("django")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:models.py:1:User:class",
            name="User",
            kind="class",
            language="python",
            path="models.py",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Model"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"
        assert results[0]["matched_base_class"] == "Model"

    def test_django_model_form_base_class(self) -> None:
        """Django ModelForm base class matches form pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("django")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:forms.py:1:UserForm:class",
            name="UserForm",
            kind="class",
            language="python",
            path="forms.py",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["ModelForm"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "form"
        assert results[0]["matched_base_class"] == "ModelForm"

    def test_django_admin_register_decorator(self) -> None:
        """Django admin.register decorator matches admin pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("django")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:admin.py:1:UserAdmin:class",
            name="UserAdmin",
            kind="class",
            language="python",
            path="admin.py",
            span=Span(1, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "admin.register", "args": ["User"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "admin"
        assert results[0]["matched_decorator"] == "admin.register"

    def test_django_receiver_decorator(self) -> None:
        """Django receiver decorator matches event_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("django")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:signals.py:1:user_created:function",
            name="user_created",
            kind="function",
            language="python",
            path="signals.py",
            span=Span(1, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "receiver", "args": ["post_save"], "kwargs": {"sender": "User"}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "event_handler"
        assert results[0]["matched_decorator"] == "receiver"

    def test_django_base_command_base_class(self) -> None:
        """Django BaseCommand base class matches command pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("django")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:commands/import_data.py:1:Command:class",
            name="Command",
            kind="class",
            language="python",
            path="commands/import_data.py",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["BaseCommand"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "command"
        assert results[0]["matched_base_class"] == "BaseCommand"

    def test_django_celery_shared_task_decorator(self) -> None:
        """Celery @shared_task decorator matches task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("django")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:tasks.py:1:send_email:function",
            name="send_email",
            kind="function",
            language="python",
            path="tasks.py",
            span=Span(1, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "shared_task", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "task"
        assert results[0]["matched_decorator"] == "shared_task"

    def test_django_enrich_symbols_integration(self) -> None:
        """Django patterns enrich symbols with concept metadata."""
        clear_pattern_cache()

        symbols = [
            Symbol(
                id="test:views.py:1:UserViewSet:class",
                name="UserViewSet",
                kind="class",
                language="python",
                path="views.py",
                span=Span(1, 50, 0, 0),
                meta={"base_classes": ["ModelViewSet"]},
            ),
            Symbol(
                id="test:models.py:1:User:class",
                name="User",
                kind="class",
                language="python",
                path="models.py",
                span=Span(1, 30, 0, 0),
                meta={"base_classes": ["Model"]},
            ),
            Symbol(
                id="test:tasks.py:1:send_email:function",
                name="send_email",
                kind="function",
                language="python",
                path="tasks.py",
                span=Span(1, 15, 0, 0),
                meta={"decorators": [{"name": "shared_task", "args": [], "kwargs": {}}]},
            ),
        ]

        enriched = enrich_symbols(symbols, {"django"})

        # Check that concepts were added
        viewset = next(s for s in enriched if s.name == "UserViewSet")
        assert "concepts" in viewset.meta
        assert any(c["concept"] == "controller" for c in viewset.meta["concepts"])

        model = next(s for s in enriched if s.name == "User")
        assert "concepts" in model.meta
        assert any(c["concept"] == "model" for c in model.meta["concepts"])

        task = next(s for s in enriched if s.name == "send_email")
        assert "concepts" in task.meta
        assert any(c["concept"] == "task" for c in task.meta["concepts"])


class TestExpressPatterns:
    """Tests for Express.js framework pattern matching."""

    def test_express_app_get_route_pattern(self) -> None:
        """Express app.get() matches route pattern with method extraction."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("express")

        assert pattern_def is not None, "Express patterns YAML should exist"

        symbol = Symbol(
            id="test:app.js:10:getUsers:function",
            name="getUsers",
            kind="function",
            language="javascript",
            path="app.js",
            span=Span(10, 20, 0, 0),
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

    def test_express_router_post_route_pattern(self) -> None:
        """Express router.post() matches route pattern with method extraction."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("express")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:routes/users.js:5:createUser:function",
            name="createUser",
            kind="function",
            language="javascript",
            path="routes/users.js",
            span=Span(5, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "router.post", "args": ["/"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_decorator"] == "router.post"
        assert results[0]["method"] == "POST"
        assert results[0]["path"] == "/"

    def test_express_put_route_pattern(self) -> None:
        """Express app.put() matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("express")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:app.js:20:updateUser:function",
            name="updateUser",
            kind="function",
            language="javascript",
            path="app.js",
            span=Span(20, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.put", "args": ["/users/:id"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "PUT"
        assert results[0]["path"] == "/users/:id"

    def test_express_delete_route_pattern(self) -> None:
        """Express router.delete() matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("express")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:routes/users.js:25:deleteUser:function",
            name="deleteUser",
            kind="function",
            language="javascript",
            path="routes/users.js",
            span=Span(25, 35, 0, 0),
            meta={
                "decorators": [
                    {"name": "router.delete", "args": ["/users/:id"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "DELETE"

    def test_express_middleware_pattern(self) -> None:
        """Express app.use() matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("express")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:app.js:1:authMiddleware:function",
            name="authMiddleware",
            kind="function",
            language="javascript",
            path="app.js",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.use", "args": ["/api"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"
        assert results[0]["matched_decorator"] == "app.use"

    def test_express_route_method_pattern(self) -> None:
        """Express router.route('/path') matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("express")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:routes/users.js:10:usersRoute:function",
            name="usersRoute",
            kind="function",
            language="javascript",
            path="routes/users.js",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "router.route", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["path"] == "/users"

    def test_express_passport_strategy_pattern(self) -> None:
        """Passport.js LocalStrategy matches auth_strategy pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("express")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:config/passport.js:1:LocalAuth:class",
            name="LocalAuth",
            kind="class",
            language="javascript",
            path="config/passport.js",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["LocalStrategy"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "auth_strategy"
        assert results[0]["matched_base_class"] == "LocalStrategy"

    def test_express_param_middleware_pattern(self) -> None:
        """Express app.param() matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("express")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:app.js:5:idParam:function",
            name="idParam",
            kind="function",
            language="javascript",
            path="app.js",
            span=Span(5, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.param", "args": ["id"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"
        assert results[0]["matched_decorator"] == "app.param"

    def test_express_helmet_middleware_pattern(self) -> None:
        """Helmet security middleware matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("express")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:app.js:3:security:function",
            name="security",
            kind="function",
            language="javascript",
            path="app.js",
            span=Span(3, 5, 0, 0),
            meta={
                "decorators": [
                    {"name": "helmet", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"
        assert results[0]["matched_decorator"] == "helmet"

    def test_express_enrich_symbols_integration(self) -> None:
        """Express patterns enrich symbols with concept metadata."""
        clear_pattern_cache()

        symbols = [
            Symbol(
                id="test:app.js:10:getUsers:function",
                name="getUsers",
                kind="function",
                language="javascript",
                path="app.js",
                span=Span(10, 20, 0, 0),
                meta={"decorators": [{"name": "app.get", "args": ["/users"], "kwargs": {}}]},
            ),
            Symbol(
                id="test:app.js:1:authMiddleware:function",
                name="authMiddleware",
                kind="function",
                language="javascript",
                path="app.js",
                span=Span(1, 10, 0, 0),
                meta={"decorators": [{"name": "app.use", "args": ["/api"], "kwargs": {}}]},
            ),
            Symbol(
                id="test:config/passport.js:1:LocalAuth:class",
                name="LocalAuth",
                kind="class",
                language="javascript",
                path="config/passport.js",
                span=Span(1, 30, 0, 0),
                meta={"base_classes": ["LocalStrategy"]},
            ),
        ]

        enriched = enrich_symbols(symbols, {"express"})

        # Check that concepts were added
        route = next(s for s in enriched if s.name == "getUsers")
        assert "concepts" in route.meta
        assert any(c["concept"] == "route" for c in route.meta["concepts"])
        assert any(c.get("method") == "GET" for c in route.meta["concepts"])

        middleware = next(s for s in enriched if s.name == "authMiddleware")
        assert "concepts" in middleware.meta
        assert any(c["concept"] == "middleware" for c in middleware.meta["concepts"])

        auth = next(s for s in enriched if s.name == "LocalAuth")
        assert "concepts" in auth.meta
        assert any(c["concept"] == "auth_strategy" for c in auth.meta["concepts"])


class TestCeleryPatterns:
    """Tests for Celery framework pattern matching."""

    def test_celery_shared_task_decorator(self) -> None:
        """Celery @shared_task decorator matches task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("celery")

        assert pattern_def is not None, "Celery patterns YAML should exist"

        symbol = Symbol(
            id="test:tasks.py:10:send_email:function",
            name="send_email",
            kind="function",
            language="python",
            path="tasks.py",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "shared_task", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "task"
        assert results[0]["matched_decorator"] == "shared_task"

    def test_celery_task_decorator(self) -> None:
        """Celery @task decorator matches task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("celery")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:tasks.py:5:process_data:function",
            name="process_data",
            kind="function",
            language="python",
            path="tasks.py",
            span=Span(5, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "task", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "task"
        assert results[0]["matched_decorator"] == "task"

    def test_celery_app_task_decorator(self) -> None:
        """Celery @app.task decorator matches task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("celery")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:tasks.py:15:generate_report:function",
            name="generate_report",
            kind="function",
            language="python",
            path="tasks.py",
            span=Span(15, 25, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.task", "args": [], "kwargs": {"bind": True}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "task"
        assert results[0]["matched_decorator"] == "app.task"

    def test_celery_periodic_task_decorator(self) -> None:
        """Celery @periodic_task decorator matches scheduled_task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("celery")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:tasks.py:20:cleanup_expired:function",
            name="cleanup_expired",
            kind="function",
            language="python",
            path="tasks.py",
            span=Span(20, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "periodic_task", "args": [], "kwargs": {"run_every": 3600}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "scheduled_task"
        assert results[0]["matched_decorator"] == "periodic_task"

    def test_celery_task_signal_decorator(self) -> None:
        """Celery @task_success.connect signal decorator matches event_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("celery")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:signals.py:5:on_task_success:function",
            name="on_task_success",
            kind="function",
            language="python",
            path="signals.py",
            span=Span(5, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "task_success.connect", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "event_handler"
        assert results[0]["matched_decorator"] == "task_success.connect"

    def test_celery_worker_signal_decorator(self) -> None:
        """Celery @worker_ready.connect signal decorator matches event_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("celery")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:signals.py:10:on_worker_ready:function",
            name="on_worker_ready",
            kind="function",
            language="python",
            path="signals.py",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "worker_ready.connect", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "event_handler"
        assert results[0]["matched_decorator"] == "worker_ready.connect"

    def test_celery_task_base_class(self) -> None:
        """Celery Task base class matches task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("celery")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:tasks.py:1:CustomTask:class",
            name="CustomTask",
            kind="class",
            language="python",
            path="tasks.py",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Task"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "task"
        assert results[0]["matched_base_class"] == "Task"

    def test_celery_task_failure_signal(self) -> None:
        """Celery @task_failure.connect signal matches event_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("celery")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:signals.py:15:handle_failure:function",
            name="handle_failure",
            kind="function",
            language="python",
            path="signals.py",
            span=Span(15, 25, 0, 0),
            meta={
                "decorators": [
                    {"name": "task_failure.connect", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "event_handler"
        assert results[0]["matched_decorator"] == "task_failure.connect"

    def test_celery_enrich_symbols_integration(self) -> None:
        """Celery patterns enrich symbols with concept metadata."""
        clear_pattern_cache()

        symbols = [
            Symbol(
                id="test:tasks.py:10:send_email:function",
                name="send_email",
                kind="function",
                language="python",
                path="tasks.py",
                span=Span(10, 20, 0, 0),
                meta={"decorators": [{"name": "shared_task", "args": [], "kwargs": {}}]},
            ),
            Symbol(
                id="test:tasks.py:20:cleanup_expired:function",
                name="cleanup_expired",
                kind="function",
                language="python",
                path="tasks.py",
                span=Span(20, 30, 0, 0),
                meta={"decorators": [{"name": "periodic_task", "args": [], "kwargs": {}}]},
            ),
            Symbol(
                id="test:signals.py:5:on_task_success:function",
                name="on_task_success",
                kind="function",
                language="python",
                path="signals.py",
                span=Span(5, 15, 0, 0),
                meta={"decorators": [{"name": "task_success.connect", "args": [], "kwargs": {}}]},
            ),
        ]

        enriched = enrich_symbols(symbols, {"celery"})

        # Check that concepts were added
        task = next(s for s in enriched if s.name == "send_email")
        assert "concepts" in task.meta
        assert any(c["concept"] == "task" for c in task.meta["concepts"])

        scheduled = next(s for s in enriched if s.name == "cleanup_expired")
        assert "concepts" in scheduled.meta
        assert any(c["concept"] == "scheduled_task" for c in scheduled.meta["concepts"])

        handler = next(s for s in enriched if s.name == "on_task_success")
        assert "concepts" in handler.meta
        assert any(c["concept"] == "event_handler" for c in handler.meta["concepts"])


class TestRailsPatterns:
    """Tests for Ruby on Rails framework pattern matching."""

    def test_rails_application_controller_pattern(self) -> None:
        """Rails ApplicationController base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("rails")

        assert pattern_def is not None, "Rails patterns YAML should exist"

        symbol = Symbol(
            id="test:users_controller.rb:1:UsersController:class",
            name="UsersController",
            kind="class",
            language="ruby",
            path="app/controllers/users_controller.rb",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["ApplicationController"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"
        assert results[0]["matched_base_class"] == "ApplicationController"

    def test_rails_action_controller_base_pattern(self) -> None:
        """Rails ActionController::Base base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("rails")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:api_controller.rb:1:ApiController:class",
            name="ApiController",
            kind="class",
            language="ruby",
            path="app/controllers/api_controller.rb",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["ActionController::Base"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"
        assert results[0]["matched_base_class"] == "ActionController::Base"

    def test_rails_application_record_pattern(self) -> None:
        """Rails ApplicationRecord base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("rails")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:user.rb:1:User:class",
            name="User",
            kind="class",
            language="ruby",
            path="app/models/user.rb",
            span=Span(1, 40, 0, 0),
            meta={
                "base_classes": ["ApplicationRecord"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"
        assert results[0]["matched_base_class"] == "ApplicationRecord"

    def test_rails_application_job_pattern(self) -> None:
        """Rails ApplicationJob base class matches task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("rails")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:email_job.rb:1:EmailJob:class",
            name="EmailJob",
            kind="class",
            language="ruby",
            path="app/jobs/email_job.rb",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["ApplicationJob"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "task"
        assert results[0]["matched_base_class"] == "ApplicationJob"

    def test_rails_application_mailer_pattern(self) -> None:
        """Rails ApplicationMailer base class matches mailer pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("rails")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:user_mailer.rb:1:UserMailer:class",
            name="UserMailer",
            kind="class",
            language="ruby",
            path="app/mailers/user_mailer.rb",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["ApplicationMailer"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "mailer"
        assert results[0]["matched_base_class"] == "ApplicationMailer"

    def test_rails_application_cable_channel_pattern(self) -> None:
        """Rails ApplicationCable::Channel base class matches websocket_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("rails")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:chat_channel.rb:1:ChatChannel:class",
            name="ChatChannel",
            kind="class",
            language="ruby",
            path="app/channels/chat_channel.rb",
            span=Span(1, 25, 0, 0),
            meta={
                "base_classes": ["ApplicationCable::Channel"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "websocket_handler"
        assert results[0]["matched_base_class"] == "ApplicationCable::Channel"

    def test_rails_active_model_serializer_pattern(self) -> None:
        """Rails ActiveModel::Serializer base class matches serializer pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("rails")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:user_serializer.rb:1:UserSerializer:class",
            name="UserSerializer",
            kind="class",
            language="ruby",
            path="app/serializers/user_serializer.rb",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["ActiveModel::Serializer"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "serializer"
        assert results[0]["matched_base_class"] == "ActiveModel::Serializer"

    def test_rails_pundit_policy_pattern(self) -> None:
        """Rails ApplicationPolicy base class matches policy pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("rails")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:user_policy.rb:1:UserPolicy:class",
            name="UserPolicy",
            kind="class",
            language="ruby",
            path="app/policies/user_policy.rb",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["ApplicationPolicy"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "policy"
        assert results[0]["matched_base_class"] == "ApplicationPolicy"

    def test_rails_sidekiq_worker_pattern(self) -> None:
        """Sidekiq::Worker base class matches task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("rails")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:hard_worker.rb:1:HardWorker:class",
            name="HardWorker",
            kind="class",
            language="ruby",
            path="app/workers/hard_worker.rb",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["Sidekiq::Worker"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "task"
        assert results[0]["matched_base_class"] == "Sidekiq::Worker"

    def test_rails_enrich_symbols_integration(self) -> None:
        """Rails patterns enrich symbols with concept metadata."""
        clear_pattern_cache()

        symbols = [
            Symbol(
                id="test:users_controller.rb:1:UsersController:class",
                name="UsersController",
                kind="class",
                language="ruby",
                path="app/controllers/users_controller.rb",
                span=Span(1, 50, 0, 0),
                meta={"base_classes": ["ApplicationController"]},
            ),
            Symbol(
                id="test:user.rb:1:User:class",
                name="User",
                kind="class",
                language="ruby",
                path="app/models/user.rb",
                span=Span(1, 40, 0, 0),
                meta={"base_classes": ["ApplicationRecord"]},
            ),
            Symbol(
                id="test:email_job.rb:1:EmailJob:class",
                name="EmailJob",
                kind="class",
                language="ruby",
                path="app/jobs/email_job.rb",
                span=Span(1, 20, 0, 0),
                meta={"base_classes": ["ApplicationJob"]},
            ),
        ]

        enriched = enrich_symbols(symbols, {"rails"})

        # Check that concepts were added
        controller = next(s for s in enriched if s.name == "UsersController")
        assert "concepts" in controller.meta
        assert any(c["concept"] == "controller" for c in controller.meta["concepts"])

        model = next(s for s in enriched if s.name == "User")
        assert "concepts" in model.meta
        assert any(c["concept"] == "model" for c in model.meta["concepts"])

        job = next(s for s in enriched if s.name == "EmailJob")
        assert "concepts" in job.meta
        assert any(c["concept"] == "task" for c in job.meta["concepts"])


class TestPhoenixPatterns:
    """Tests for Phoenix (Elixir) framework pattern matching."""

    def test_phoenix_controller_pattern(self) -> None:
        """Phoenix controller macro matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("phoenix")

        assert pattern_def is not None, "Phoenix patterns YAML should exist"

        symbol = Symbol(
            id="test:user_controller.ex:1:UserController:module",
            name="UserController",
            kind="module",
            language="elixir",
            path="lib/my_app_web/controllers/user_controller.ex",
            span=Span(1, 50, 0, 0),
            meta={
                "decorators": [
                    {"name": "use Phoenix.Controller", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"
        assert results[0]["matched_decorator"] == "use Phoenix.Controller"

    def test_phoenix_web_controller_pattern(self) -> None:
        """Phoenix Web controller macro matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("phoenix")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:page_controller.ex:1:PageController:module",
            name="PageController",
            kind="module",
            language="elixir",
            path="lib/my_app_web/controllers/page_controller.ex",
            span=Span(1, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "use MyAppWeb, :controller", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_phoenix_liveview_pattern(self) -> None:
        """Phoenix LiveView macro matches liveview pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("phoenix")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:user_live.ex:1:UserLive:module",
            name="UserLive",
            kind="module",
            language="elixir",
            path="lib/my_app_web/live/user_live.ex",
            span=Span(1, 100, 0, 0),
            meta={
                "decorators": [
                    {"name": "use Phoenix.LiveView", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "liveview"
        assert results[0]["matched_decorator"] == "use Phoenix.LiveView"

    def test_phoenix_channel_pattern(self) -> None:
        """Phoenix Channel macro matches websocket_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("phoenix")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:room_channel.ex:1:RoomChannel:module",
            name="RoomChannel",
            kind="module",
            language="elixir",
            path="lib/my_app_web/channels/room_channel.ex",
            span=Span(1, 50, 0, 0),
            meta={
                "decorators": [
                    {"name": "use Phoenix.Channel", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "websocket_handler"
        assert results[0]["matched_decorator"] == "use Phoenix.Channel"

    def test_phoenix_ecto_schema_pattern(self) -> None:
        """Ecto Schema macro matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("phoenix")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:user.ex:1:User:module",
            name="User",
            kind="module",
            language="elixir",
            path="lib/my_app/accounts/user.ex",
            span=Span(1, 40, 0, 0),
            meta={
                "decorators": [
                    {"name": "use Ecto.Schema", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"
        assert results[0]["matched_decorator"] == "use Ecto.Schema"

    def test_phoenix_genserver_pattern(self) -> None:
        """GenServer macro matches task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("phoenix")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:worker.ex:1:Worker:module",
            name="Worker",
            kind="module",
            language="elixir",
            path="lib/my_app/worker.ex",
            span=Span(1, 60, 0, 0),
            meta={
                "decorators": [
                    {"name": "use GenServer", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "task"
        assert results[0]["matched_decorator"] == "use GenServer"

    def test_phoenix_oban_worker_pattern(self) -> None:
        """Oban Worker macro matches task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("phoenix")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:email_worker.ex:1:EmailWorker:module",
            name="EmailWorker",
            kind="module",
            language="elixir",
            path="lib/my_app/workers/email_worker.ex",
            span=Span(1, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "use Oban.Worker", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "task"
        assert results[0]["matched_decorator"] == "use Oban.Worker"

    def test_phoenix_absinthe_schema_pattern(self) -> None:
        """Absinthe Schema macro matches graphql_schema pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("phoenix")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:schema.ex:1:Schema:module",
            name="Schema",
            kind="module",
            language="elixir",
            path="lib/my_app_web/schema.ex",
            span=Span(1, 100, 0, 0),
            meta={
                "decorators": [
                    {"name": "use Absinthe.Schema", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "graphql_schema"
        assert results[0]["matched_decorator"] == "use Absinthe.Schema"

    def test_phoenix_plug_builder_pattern(self) -> None:
        """Plug.Builder macro matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("phoenix")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:auth_plug.ex:1:AuthPlug:module",
            name="AuthPlug",
            kind="module",
            language="elixir",
            path="lib/my_app_web/plugs/auth_plug.ex",
            span=Span(1, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "use Plug.Builder", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"
        assert results[0]["matched_decorator"] == "use Plug.Builder"

    def test_phoenix_enrich_symbols_integration(self) -> None:
        """Phoenix patterns enrich symbols with concept metadata."""
        clear_pattern_cache()

        symbols = [
            Symbol(
                id="test:user_controller.ex:1:UserController:module",
                name="UserController",
                kind="module",
                language="elixir",
                path="lib/my_app_web/controllers/user_controller.ex",
                span=Span(1, 50, 0, 0),
                meta={"decorators": [{"name": "use Phoenix.Controller", "args": [], "kwargs": {}}]},
            ),
            Symbol(
                id="test:user.ex:1:User:module",
                name="User",
                kind="module",
                language="elixir",
                path="lib/my_app/accounts/user.ex",
                span=Span(1, 40, 0, 0),
                meta={"decorators": [{"name": "use Ecto.Schema", "args": [], "kwargs": {}}]},
            ),
            Symbol(
                id="test:room_channel.ex:1:RoomChannel:module",
                name="RoomChannel",
                kind="module",
                language="elixir",
                path="lib/my_app_web/channels/room_channel.ex",
                span=Span(1, 50, 0, 0),
                meta={"decorators": [{"name": "use Phoenix.Channel", "args": [], "kwargs": {}}]},
            ),
        ]

        enriched = enrich_symbols(symbols, {"phoenix"})

        # Check that concepts were added
        controller = next(s for s in enriched if s.name == "UserController")
        assert "concepts" in controller.meta
        assert any(c["concept"] == "controller" for c in controller.meta["concepts"])

        model = next(s for s in enriched if s.name == "User")
        assert "concepts" in model.meta
        assert any(c["concept"] == "model" for c in model.meta["concepts"])

        channel = next(s for s in enriched if s.name == "RoomChannel")
        assert "concepts" in channel.meta
        assert any(c["concept"] == "websocket_handler" for c in channel.meta["concepts"])


class TestLaravelPatterns:
    """Tests for Laravel (PHP) framework pattern matching."""

    def test_laravel_controller_pattern(self) -> None:
        """Laravel Controller base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laravel")

        assert pattern_def is not None, "Laravel patterns YAML should exist"

        symbol = Symbol(
            id="test:UserController.php:1:UserController:class",
            name="UserController",
            kind="class",
            language="php",
            path="app/Http/Controllers/UserController.php",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Controller"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"
        assert results[0]["matched_base_class"] == "Controller"

    def test_laravel_eloquent_model_pattern(self) -> None:
        """Laravel Eloquent Model base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laravel")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:User.php:1:User:class",
            name="User",
            kind="class",
            language="php",
            path="app/Models/User.php",
            span=Span(1, 40, 0, 0),
            meta={
                "base_classes": ["Model"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"
        assert results[0]["matched_base_class"] == "Model"

    def test_laravel_form_request_pattern(self) -> None:
        """Laravel FormRequest base class matches form pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laravel")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:CreateUserRequest.php:1:CreateUserRequest:class",
            name="CreateUserRequest",
            kind="class",
            language="php",
            path="app/Http/Requests/CreateUserRequest.php",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["FormRequest"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "form"
        assert results[0]["matched_base_class"] == "FormRequest"

    def test_laravel_mailable_pattern(self) -> None:
        """Laravel Mailable base class matches mailer pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laravel")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:WelcomeMail.php:1:WelcomeMail:class",
            name="WelcomeMail",
            kind="class",
            language="php",
            path="app/Mail/WelcomeMail.php",
            span=Span(1, 40, 0, 0),
            meta={
                "base_classes": ["Mailable"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "mailer"
        assert results[0]["matched_base_class"] == "Mailable"

    def test_laravel_artisan_command_pattern(self) -> None:
        """Laravel Command base class matches command pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laravel")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:ImportData.php:1:ImportData:class",
            name="ImportData",
            kind="class",
            language="php",
            path="app/Console/Commands/ImportData.php",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Command"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "command"
        assert results[0]["matched_base_class"] == "Command"

    def test_laravel_json_resource_pattern(self) -> None:
        """Laravel JsonResource base class matches serializer pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laravel")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserResource.php:1:UserResource:class",
            name="UserResource",
            kind="class",
            language="php",
            path="app/Http/Resources/UserResource.php",
            span=Span(1, 25, 0, 0),
            meta={
                "base_classes": ["JsonResource"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "serializer"
        assert results[0]["matched_base_class"] == "JsonResource"

    def test_laravel_service_provider_pattern(self) -> None:
        """Laravel ServiceProvider base class matches provider pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laravel")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:AppServiceProvider.php:1:AppServiceProvider:class",
            name="AppServiceProvider",
            kind="class",
            language="php",
            path="app/Providers/AppServiceProvider.php",
            span=Span(1, 40, 0, 0),
            meta={
                "base_classes": ["ServiceProvider"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "provider"
        assert results[0]["matched_base_class"] == "ServiceProvider"

    def test_laravel_notification_pattern(self) -> None:
        """Laravel Notification base class matches notification pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laravel")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:InvoicePaid.php:1:InvoicePaid:class",
            name="InvoicePaid",
            kind="class",
            language="php",
            path="app/Notifications/InvoicePaid.php",
            span=Span(1, 35, 0, 0),
            meta={
                "base_classes": ["Notification"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "notification"
        assert results[0]["matched_base_class"] == "Notification"

    def test_laravel_livewire_component_pattern(self) -> None:
        """Livewire Component base class matches component pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laravel")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:Counter.php:1:Counter:class",
            name="Counter",
            kind="class",
            language="php",
            path="app/Http/Livewire/Counter.php",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Livewire\\Component"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "component"
        assert results[0]["matched_base_class"] == "Livewire\\Component"

    def test_laravel_enrich_symbols_integration(self) -> None:
        """Laravel patterns enrich symbols with concept metadata."""
        clear_pattern_cache()

        symbols = [
            Symbol(
                id="test:UserController.php:1:UserController:class",
                name="UserController",
                kind="class",
                language="php",
                path="app/Http/Controllers/UserController.php",
                span=Span(1, 50, 0, 0),
                meta={"base_classes": ["Controller"]},
            ),
            Symbol(
                id="test:User.php:1:User:class",
                name="User",
                kind="class",
                language="php",
                path="app/Models/User.php",
                span=Span(1, 40, 0, 0),
                meta={"base_classes": ["Model"]},
            ),
            Symbol(
                id="test:WelcomeMail.php:1:WelcomeMail:class",
                name="WelcomeMail",
                kind="class",
                language="php",
                path="app/Mail/WelcomeMail.php",
                span=Span(1, 40, 0, 0),
                meta={"base_classes": ["Mailable"]},
            ),
        ]

        enriched = enrich_symbols(symbols, {"laravel"})

        # Check that concepts were added
        controller = next(s for s in enriched if s.name == "UserController")
        assert "concepts" in controller.meta
        assert any(c["concept"] == "controller" for c in controller.meta["concepts"])

        model = next(s for s in enriched if s.name == "User")
        assert "concepts" in model.meta
        assert any(c["concept"] == "model" for c in model.meta["concepts"])

        mailer = next(s for s in enriched if s.name == "WelcomeMail")
        assert "concepts" in mailer.meta
        assert any(c["concept"] == "mailer" for c in mailer.meta["concepts"])


class TestGoWebPatterns:
    """Tests for Go web framework pattern matching."""

    def test_go_gin_get_route_pattern(self) -> None:
        """Gin router.GET matches route pattern with method extraction."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None, "Go-web patterns YAML should exist"

        symbol = Symbol(
            id="test:main.go:10:getUsers:function",
            name="getUsers",
            kind="function",
            language="go",
            path="main.go",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "router.GET", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_decorator"] == "router.GET"
        assert results[0]["method"] == "GET"

    def test_go_echo_post_route_pattern(self) -> None:
        """Echo e.POST matches route pattern with method extraction."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:handlers.go:15:createUser:function",
            name="createUser",
            kind="function",
            language="go",
            path="handlers.go",
            span=Span(15, 25, 0, 0),
            meta={
                "decorators": [
                    {"name": "e.POST", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "POST"

    def test_go_fiber_get_route_pattern(self) -> None:
        """Fiber app.Get matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:main.go:20:getProduct:function",
            name="getProduct",
            kind="function",
            language="go",
            path="main.go",
            span=Span(20, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.Get", "args": ["/products/:id"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "GET"

    def test_go_chi_delete_route_pattern(self) -> None:
        """Chi r.Delete matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:handlers.go:25:deleteUser:function",
            name="deleteUser",
            kind="function",
            language="go",
            path="handlers.go",
            span=Span(25, 35, 0, 0),
            meta={
                "decorators": [
                    {"name": "r.Delete", "args": ["/users/{id}"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "DELETE"

    def test_go_http_handlefunc_pattern(self) -> None:
        """net/http http.HandleFunc matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:main.go:5:healthHandler:function",
            name="healthHandler",
            kind="function",
            language="go",
            path="main.go",
            span=Span(5, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "http.HandleFunc", "args": ["/health"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_decorator"] == "http.HandleFunc"

    def test_go_middleware_pattern(self) -> None:
        """Go middleware pattern (router.Use) matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:middleware.go:1:authMiddleware:function",
            name="authMiddleware",
            kind="function",
            language="go",
            path="middleware.go",
            span=Span(1, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "router.Use", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"
        assert results[0]["matched_decorator"] == "router.Use"

    def test_go_gorm_model_pattern(self) -> None:
        """GORM gorm.Model base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:models/user.go:1:User:struct",
            name="User",
            kind="struct",
            language="go",
            path="models/user.go",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["gorm.Model"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"
        assert results[0]["matched_base_class"] == "gorm.Model"

    def test_go_enrich_symbols_integration(self) -> None:
        """Go web patterns enrich symbols with concept metadata."""
        clear_pattern_cache()

        symbols = [
            Symbol(
                id="test:main.go:10:getUsers:function",
                name="getUsers",
                kind="function",
                language="go",
                path="main.go",
                span=Span(10, 20, 0, 0),
                meta={"decorators": [{"name": "router.GET", "args": ["/users"], "kwargs": {}}]},
            ),
            Symbol(
                id="test:middleware.go:1:authMiddleware:function",
                name="authMiddleware",
                kind="function",
                language="go",
                path="middleware.go",
                span=Span(1, 20, 0, 0),
                meta={"decorators": [{"name": "router.Use", "args": [], "kwargs": {}}]},
            ),
            Symbol(
                id="test:models/user.go:1:User:struct",
                name="User",
                kind="struct",
                language="go",
                path="models/user.go",
                span=Span(1, 20, 0, 0),
                meta={"base_classes": ["gorm.Model"]},
            ),
        ]

        enriched = enrich_symbols(symbols, {"go-web"})

        # Check that concepts were added
        route = next(s for s in enriched if s.name == "getUsers")
        assert "concepts" in route.meta
        assert any(c["concept"] == "route" for c in route.meta["concepts"])

        middleware = next(s for s in enriched if s.name == "authMiddleware")
        assert "concepts" in middleware.meta
        assert any(c["concept"] == "middleware" for c in middleware.meta["concepts"])

        model = next(s for s in enriched if s.name == "User")
        assert "concepts" in model.meta
        assert any(c["concept"] == "model" for c in model.meta["concepts"])


class TestRustWebPatterns:
    """Tests for Rust web framework patterns (Actix-web, Rocket, Axum)."""

    def setup_method(self) -> None:
        """Clear pattern cache before each test."""
        clear_pattern_cache()

    def test_actix_get_route(self) -> None:
        """Actix-web @get annotation matches route pattern."""
        symbol = Symbol(
            id="test:handlers.rs:1:get_users:function",
            name="get_users",
            kind="function",
            language="rust",
            path="handlers.rs",
            span=Span(1, 10, 0, 0),
            meta={
                "annotations": [
                    {"name": "actix_web::get", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"rust-web"})

        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        assert len(concepts) == 1
        assert concepts[0]["concept"] == "route"
        assert concepts[0]["path"] == "/users"

    def test_actix_post_route(self) -> None:
        """Actix-web @post annotation matches route pattern."""
        symbol = Symbol(
            id="test:handlers.rs:1:create_user:function",
            name="create_user",
            kind="function",
            language="rust",
            path="handlers.rs",
            span=Span(1, 10, 0, 0),
            meta={
                "annotations": [
                    {"name": "actix_web::post", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"rust-web"})

        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        assert len(concepts) == 1
        assert concepts[0]["concept"] == "route"
        assert concepts[0]["path"] == "/users"

    def test_rocket_get_route(self) -> None:
        """Rocket @get annotation matches route pattern."""
        symbol = Symbol(
            id="test:routes.rs:1:index:function",
            name="index",
            kind="function",
            language="rust",
            path="routes.rs",
            span=Span(1, 10, 0, 0),
            meta={
                "annotations": [
                    {"name": "rocket::get", "args": ["/"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"rust-web"})

        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        assert len(concepts) == 1
        assert concepts[0]["concept"] == "route"
        assert concepts[0]["path"] == "/"

    def test_rocket_post_route(self) -> None:
        """Rocket @post annotation matches route pattern."""
        symbol = Symbol(
            id="test:routes.rs:1:create:function",
            name="create",
            kind="function",
            language="rust",
            path="routes.rs",
            span=Span(1, 10, 0, 0),
            meta={
                "annotations": [
                    {"name": "rocket::post", "args": ["/items"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"rust-web"})

        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        assert len(concepts) == 1
        assert concepts[0]["concept"] == "route"
        assert concepts[0]["path"] == "/items"

    def test_diesel_model(self) -> None:
        """Diesel Queryable/Insertable derives match model pattern."""
        symbol = Symbol(
            id="test:models.rs:1:User:struct",
            name="User",
            kind="struct",
            language="rust",
            path="models.rs",
            span=Span(1, 20, 0, 0),
            meta={
                "annotations": [
                    {"name": "diesel::Queryable", "args": [], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"rust-web"})

        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        assert len(concepts) == 1
        assert concepts[0]["concept"] == "model"

    def test_tokio_spawn_task(self) -> None:
        """Tokio spawn annotation matches task pattern."""
        symbol = Symbol(
            id="test:tasks.rs:1:background_job:function",
            name="background_job",
            kind="function",
            language="rust",
            path="tasks.rs",
            span=Span(1, 10, 0, 0),
            meta={
                "annotations": [
                    {"name": "tokio::spawn", "args": [], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([symbol], {"rust-web"})

        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        assert len(concepts) == 1
        assert concepts[0]["concept"] == "task"

    def test_multiple_rust_symbols(self) -> None:
        """Multiple Rust symbols are enriched correctly."""
        symbols = [
            Symbol(
                id="test:handlers.rs:1:get_users:function",
                name="get_users",
                kind="function",
                language="rust",
                path="handlers.rs",
                span=Span(1, 10, 0, 0),
                meta={
                    "annotations": [
                        {"name": "actix_web::get", "args": ["/users"], "kwargs": {}},
                    ],
                },
            ),
            Symbol(
                id="test:models.rs:1:User:struct",
                name="User",
                kind="struct",
                language="rust",
                path="models.rs",
                span=Span(1, 20, 0, 0),
                meta={
                    "annotations": [
                        {"name": "diesel::Queryable", "args": [], "kwargs": {}},
                    ],
                },
            ),
        ]

        enriched = enrich_symbols(symbols, {"rust-web"})

        route = next(s for s in enriched if s.name == "get_users")
        assert "concepts" in route.meta
        assert any(c["concept"] == "route" for c in route.meta["concepts"])

        model = next(s for s in enriched if s.name == "User")
        assert "concepts" in model.meta
        assert any(c["concept"] == "model" for c in model.meta["concepts"])


class TestHapiPatterns:
    """Tests for Hapi.js framework pattern matching."""

    def test_hapi_server_route_pattern(self) -> None:
        """Hapi server.route() matches route pattern with kwargs extraction."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("hapi")

        assert pattern_def is not None, "Hapi patterns YAML should exist"

        symbol = Symbol(
            id="test:server.js:10:getUsers:function",
            name="getUsers",
            kind="function",
            language="javascript",
            path="server.js",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "server.route",
                        "args": [],
                        "kwargs": {"method": "GET", "path": "/users"},
                    },
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_decorator"] == "server.route"
        assert results[0]["path"] == "/users"
        assert results[0]["method"] == "GET"

    def test_hapi_server_route_post(self) -> None:
        """Hapi server.route() matches POST method."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("hapi")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:server.js:20:createUser:function",
            name="createUser",
            kind="function",
            language="javascript",
            path="server.js",
            span=Span(20, 30, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "server.route",
                        "args": [],
                        "kwargs": {"method": "POST", "path": "/users"},
                    },
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["method"] == "POST"
        assert results[0]["path"] == "/users"

    def test_hapi_server_register_plugin(self) -> None:
        """Hapi server.register() matches plugin pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("hapi")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:server.js:30:registerPlugins:function",
            name="registerPlugins",
            kind="function",
            language="javascript",
            path="server.js",
            span=Span(30, 40, 0, 0),
            meta={
                "decorators": [
                    {"name": "server.register", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "plugin"

    def test_hapi_server_ext_middleware(self) -> None:
        """Hapi server.ext() matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("hapi")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:server.js:40:onPreHandler:function",
            name="onPreHandler",
            kind="function",
            language="javascript",
            path="server.js",
            span=Span(40, 50, 0, 0),
            meta={
                "decorators": [
                    {"name": "server.ext", "args": ["onPreHandler"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_hapi_auth_strategy(self) -> None:
        """Hapi server.auth.strategy() matches auth_strategy pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("hapi")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:auth.js:10:jwtAuth:function",
            name="jwtAuth",
            kind="function",
            language="javascript",
            path="auth.js",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "server.auth.strategy", "args": ["jwt"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "auth_strategy"

    def test_hapi_joi_validator(self) -> None:
        """Hapi Joi.object() matches validator pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("hapi")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:validators.js:10:userSchema:variable",
            name="userSchema",
            kind="variable",
            language="javascript",
            path="validators.js",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "Joi.object", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "validator"

    def test_hapi_boom_error_handler(self) -> None:
        """Hapi Boom.badRequest() matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("hapi")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:handlers.js:10:handleError:function",
            name="handleError",
            kind="function",
            language="javascript",
            path="handlers.js",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "Boom.badRequest", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "error_handler"

    def test_hapi_enrich_symbols(self) -> None:
        """Hapi symbols are enriched with concept metadata."""
        clear_pattern_cache()

        symbols = [
            Symbol(
                id="test:server.js:10:getUsers:function",
                name="getUsers",
                kind="function",
                language="javascript",
                path="server.js",
                span=Span(10, 20, 0, 0),
                meta={
                    "decorators": [
                        {
                            "name": "server.route",
                            "args": [],
                            "kwargs": {"method": "GET", "path": "/users"},
                        },
                    ],
                },
            ),
            Symbol(
                id="test:server.js:30:registerAuth:function",
                name="registerAuth",
                kind="function",
                language="javascript",
                path="server.js",
                span=Span(30, 40, 0, 0),
                meta={
                    "decorators": [
                        {"name": "server.auth.strategy", "args": [], "kwargs": {}},
                    ],
                },
            ),
        ]

        enriched = enrich_symbols(symbols, {"hapi"})

        route = next(s for s in enriched if s.name == "getUsers")
        assert "concepts" in route.meta
        assert any(c["concept"] == "route" for c in route.meta["concepts"])

        auth = next(s for s in enriched if s.name == "registerAuth")
        assert "concepts" in auth.meta
        assert any(c["concept"] == "auth_strategy" for c in auth.meta["concepts"])


class TestKoaPatterns:
    """Tests for Koa.js framework pattern matching."""

    def test_koa_router_get_pattern(self) -> None:
        """Koa router.get() matches route pattern with method extraction."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("koa")

        assert pattern_def is not None, "Koa patterns YAML should exist"

        symbol = Symbol(
            id="test:routes.js:10:getUsers:function",
            name="getUsers",
            kind="function",
            language="javascript",
            path="routes.js",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "router.get", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_decorator"] == "router.get"
        assert results[0]["method"] == "GET"
        assert results[0]["path"] == "/users"

    def test_koa_router_post_pattern(self) -> None:
        """Koa router.post() matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("koa")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:routes.js:20:createUser:function",
            name="createUser",
            kind="function",
            language="javascript",
            path="routes.js",
            span=Span(20, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "router.post", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "POST"
        assert results[0]["path"] == "/users"

    def test_koa_router_put_pattern(self) -> None:
        """Koa router.put() matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("koa")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:routes.js:30:updateUser:function",
            name="updateUser",
            kind="function",
            language="javascript",
            path="routes.js",
            span=Span(30, 40, 0, 0),
            meta={
                "decorators": [
                    {"name": "router.put", "args": ["/users/:id"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["method"] == "PUT"
        assert results[0]["path"] == "/users/:id"

    def test_koa_router_delete_pattern(self) -> None:
        """Koa router.delete() matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("koa")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:routes.js:40:deleteUser:function",
            name="deleteUser",
            kind="function",
            language="javascript",
            path="routes.js",
            span=Span(40, 50, 0, 0),
            meta={
                "decorators": [
                    {"name": "router.delete", "args": ["/users/:id"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["method"] == "DELETE"

    def test_koa_router_use_middleware(self) -> None:
        """Koa router.use() matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("koa")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:routes.js:50:authMiddleware:function",
            name="authMiddleware",
            kind="function",
            language="javascript",
            path="routes.js",
            span=Span(50, 60, 0, 0),
            meta={
                "decorators": [
                    {"name": "router.use", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_koa_app_use_middleware(self) -> None:
        """Koa app.use() matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("koa")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:app.js:10:globalMiddleware:function",
            name="globalMiddleware",
            kind="function",
            language="javascript",
            path="app.js",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.use", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_koa_passport_auth(self) -> None:
        """Koa passport.authenticate() matches auth_strategy pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("koa")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:auth.js:10:jwtAuth:function",
            name="jwtAuth",
            kind="function",
            language="javascript",
            path="auth.js",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "passport.authenticate", "args": ["jwt"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "auth_strategy"

    def test_koa_jwt_middleware(self) -> None:
        """Koa jwt() matches auth_middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("koa")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:middleware.js:10:jwtMiddleware:function",
            name="jwtMiddleware",
            kind="function",
            language="javascript",
            path="middleware.js",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "jwt", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "auth_middleware"

    def test_koa_enrich_symbols(self) -> None:
        """Koa symbols are enriched with concept metadata."""
        clear_pattern_cache()

        symbols = [
            Symbol(
                id="test:routes.js:10:getUsers:function",
                name="getUsers",
                kind="function",
                language="javascript",
                path="routes.js",
                span=Span(10, 20, 0, 0),
                meta={
                    "decorators": [
                        {"name": "router.get", "args": ["/users"], "kwargs": {}},
                    ],
                },
            ),
            Symbol(
                id="test:middleware.js:10:logger:function",
                name="logger",
                kind="function",
                language="javascript",
                path="middleware.js",
                span=Span(10, 20, 0, 0),
                meta={
                    "decorators": [
                        {"name": "logger", "args": [], "kwargs": {}},
                    ],
                },
            ),
        ]

        enriched = enrich_symbols(symbols, {"koa"})

        route = next(s for s in enriched if s.name == "getUsers")
        assert "concepts" in route.meta
        assert any(c["concept"] == "route" for c in route.meta["concepts"])

        mw = next(s for s in enriched if s.name == "logger")
        assert "concepts" in mw.meta
        assert any(c["concept"] == "middleware" for c in mw.meta["concepts"])


class TestAspNetPatterns:
    """Tests for ASP.NET Core framework pattern matching."""

    def test_aspnet_http_get_route_pattern(self) -> None:
        """ASP.NET HttpGet attribute matches route pattern with method extraction."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("aspnet")

        assert pattern_def is not None, "ASP.NET patterns YAML should exist"

        symbol = Symbol(
            id="test:UsersController.cs:10:GetUsers:method",
            name="UsersController.GetUsers",
            kind="method",
            language="csharp",
            path="Controllers/UsersController.cs",
            span=Span(10, 20, 0, 0),
            meta={
                "annotations": [
                    {"name": "HttpGet", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_annotation"] == "HttpGet"
        assert results[0]["path"] == "/users"
        assert results[0]["method"] == "GET"

    def test_aspnet_http_post_route_pattern(self) -> None:
        """ASP.NET HttpPost attribute matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("aspnet")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UsersController.cs:20:CreateUser:method",
            name="UsersController.CreateUser",
            kind="method",
            language="csharp",
            path="Controllers/UsersController.cs",
            span=Span(20, 30, 0, 0),
            meta={
                "annotations": [
                    {"name": "HttpPost", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "POST"
        assert results[0]["path"] == "/users"

    def test_aspnet_http_put_route_pattern(self) -> None:
        """ASP.NET HttpPut attribute matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("aspnet")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UsersController.cs:30:UpdateUser:method",
            name="UsersController.UpdateUser",
            kind="method",
            language="csharp",
            path="Controllers/UsersController.cs",
            span=Span(30, 40, 0, 0),
            meta={
                "annotations": [
                    {"name": "HttpPut", "args": ["{id}"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["method"] == "PUT"
        assert results[0]["path"] == "{id}"

    def test_aspnet_http_delete_route_pattern(self) -> None:
        """ASP.NET HttpDelete attribute matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("aspnet")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UsersController.cs:40:DeleteUser:method",
            name="UsersController.DeleteUser",
            kind="method",
            language="csharp",
            path="Controllers/UsersController.cs",
            span=Span(40, 50, 0, 0),
            meta={
                "annotations": [
                    {"name": "HttpDelete", "args": ["{id}"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["method"] == "DELETE"

    def test_aspnet_api_controller_pattern(self) -> None:
        """ASP.NET ApiController attribute matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("aspnet")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UsersController.cs:1:UsersController:class",
            name="UsersController",
            kind="class",
            language="csharp",
            path="Controllers/UsersController.cs",
            span=Span(1, 50, 0, 0),
            meta={
                "annotations": [
                    {"name": "ApiController", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_aspnet_authorize_pattern(self) -> None:
        """ASP.NET Authorize attribute matches auth_middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("aspnet")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:SecureController.cs:10:SecureMethod:method",
            name="SecureController.SecureMethod",
            kind="method",
            language="csharp",
            path="Controllers/SecureController.cs",
            span=Span(10, 20, 0, 0),
            meta={
                "annotations": [
                    {"name": "Authorize", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "auth_middleware"

    def test_aspnet_validation_pattern(self) -> None:
        """ASP.NET validation attributes match validator pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("aspnet")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserModel.cs:10:Name:property",
            name="User.Name",
            kind="property",
            language="csharp",
            path="Models/User.cs",
            span=Span(10, 12, 0, 0),
            meta={
                "annotations": [
                    {"name": "Required", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "validator"

    def test_aspnet_enrich_symbols(self) -> None:
        """ASP.NET symbols are enriched with concept metadata."""
        clear_pattern_cache()

        symbols = [
            Symbol(
                id="test:UsersController.cs:10:GetUsers:method",
                name="UsersController.GetUsers",
                kind="method",
                language="csharp",
                path="Controllers/UsersController.cs",
                span=Span(10, 20, 0, 0),
                meta={
                    "annotations": [
                        {"name": "HttpGet", "args": ["/users"], "kwargs": {}},
                    ],
                },
            ),
            Symbol(
                id="test:UsersController.cs:1:UsersController:class",
                name="UsersController",
                kind="class",
                language="csharp",
                path="Controllers/UsersController.cs",
                span=Span(1, 50, 0, 0),
                meta={
                    "annotations": [
                        {"name": "ApiController", "args": [], "kwargs": {}},
                    ],
                },
            ),
        ]

        enriched = enrich_symbols(symbols, {"aspnet"})

        route = next(s for s in enriched if s.name == "UsersController.GetUsers")
        assert "concepts" in route.meta
        assert any(c["concept"] == "route" for c in route.meta["concepts"])

        controller = next(s for s in enriched if s.name == "UsersController")
        assert "concepts" in controller.meta
        assert any(c["concept"] == "controller" for c in controller.meta["concepts"])
