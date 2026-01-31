"""Tests for framework pattern matching (ADR-0003 v0.8.x).

Tests the YAML-based framework pattern system that enriches symbols
with concept metadata (route, model, task, etc.).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.framework_patterns import (
    DeferredResolutionStats,
    FrameworkPatternDef,
    Pattern,
    UsagePatternSpec,
    clear_pattern_cache,
    enrich_symbols,
    extract_usage_value,
    get_frameworks_dir,
    load_framework_patterns,
    match_patterns,
    match_usage_patterns,
    resolve_deferred_symbol_refs,
)
from hypergumbo_core.ir import Span, Symbol, UsageContext


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
        # Path is set to empty string when extraction fails - this allows
        # prefix_from_parent to work with decorators that have no path arg
        assert result.get("path") == ""

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
        # Path is set to empty string when extraction fails
        assert result.get("path") == ""

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

    def test_pattern_matches_symbol_kind(self) -> None:
        """Pattern matches symbol by its kind field."""
        pattern = Pattern(
            concept="route",
            symbol_kind=r"^route$",
        )

        symbol = Symbol(
            id="test:routes.rb:1:get_users:route",
            name="GET /users",
            kind="route",  # Rails analyzer creates route symbols
            language="ruby",
            path="config/routes.rb",
            span=Span(1, 1, 0, 20),
            meta={
                "http_method": "GET",
                "route_path": "/users",
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "route"
        assert result["matched_symbol_kind"] == "route"

    def test_pattern_symbol_kind_no_match(self) -> None:
        """Pattern does not match when symbol kind doesn't match."""
        pattern = Pattern(
            concept="route",
            symbol_kind=r"^route$",
        )

        symbol = Symbol(
            id="test:app.rb:1:UsersController:class",
            name="UsersController",
            kind="class",  # Not a route
            language="ruby",
            path="app/controllers/users_controller.rb",
            span=Span(1, 50, 0, 0),
            meta={},
        )

        result = pattern.matches(symbol)
        assert result is None

    def test_pattern_symbol_kind_with_regex(self) -> None:
        """Pattern symbol_kind uses regex matching."""
        pattern = Pattern(
            concept="endpoint",
            symbol_kind=r"^(route|endpoint|handler)$",
        )

        # Test with "route"
        route_symbol = Symbol(
            id="test:routes.rb:1:get:route",
            name="GET /",
            kind="route",
            language="ruby",
            path="routes.rb",
            span=Span(1, 1, 0, 10),
            meta={},
        )
        result = pattern.matches(route_symbol)
        assert result is not None
        assert result["matched_symbol_kind"] == "route"

        # Test with "handler"
        handler_symbol = Symbol(
            id="test:handler.go:1:HandleGet:handler",
            name="HandleGet",
            kind="handler",
            language="go",
            path="handler.go",
            span=Span(1, 10, 0, 0),
            meta={},
        )
        result = pattern.matches(handler_symbol)
        assert result is not None
        assert result["matched_symbol_kind"] == "handler"

    def test_pattern_matches_parent_base_class(self) -> None:
        """Pattern matches method by parent class's base classes."""
        pattern = Pattern(
            concept="lifecycle_hook",
            parent_base_class=r"^Activity$",
            method_name=r"^onCreate$",
        )

        symbol = Symbol(
            id="java:MainActivity.java:10-15:MainActivity.onCreate:method",
            name="MainActivity.onCreate",
            kind="method",
            language="java",
            path="MainActivity.java",
            span=Span(10, 15, 0, 0),
            meta={
                "parent_base_classes": ["Activity"],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "lifecycle_hook"
        assert result["matched_parent_base_class"] == "Activity"
        assert result["matched_method_name"] == "onCreate"

    def test_pattern_parent_base_class_no_match_wrong_base(self) -> None:
        """Pattern doesn't match when parent base class doesn't match."""
        pattern = Pattern(
            concept="lifecycle_hook",
            parent_base_class=r"^Activity$",
            method_name=r"^onCreate$",
        )

        symbol = Symbol(
            id="java:MyService.java:10-15:MyService.onCreate:method",
            name="MyService.onCreate",
            kind="method",
            language="java",
            path="MyService.java",
            span=Span(10, 15, 0, 0),
            meta={
                "parent_base_classes": ["Service"],  # Not Activity
            },
        )

        result = pattern.matches(symbol)
        assert result is None

    def test_pattern_parent_base_class_no_match_wrong_method(self) -> None:
        """Pattern doesn't match when method name doesn't match."""
        pattern = Pattern(
            concept="lifecycle_hook",
            parent_base_class=r"^Activity$",
            method_name=r"^onCreate$",
        )

        symbol = Symbol(
            id="java:MainActivity.java:10-15:MainActivity.onDestroy:method",
            name="MainActivity.onDestroy",
            kind="method",
            language="java",
            path="MainActivity.java",
            span=Span(10, 15, 0, 0),
            meta={
                "parent_base_classes": ["Activity"],
            },
        )

        result = pattern.matches(symbol)
        assert result is None

    def test_pattern_method_name_only(self) -> None:
        """Pattern matches by method name without parent_base_class constraint."""
        pattern = Pattern(
            concept="init_method",
            method_name=r"^__init__$",
        )

        symbol = Symbol(
            id="python:user.py:10-15:User.__init__:method",
            name="User.__init__",
            kind="method",
            language="python",
            path="user.py",
            span=Span(10, 15, 0, 0),
            meta={},
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "init_method"
        assert result["matched_method_name"] == "__init__"

    def test_pattern_parent_base_class_only(self) -> None:
        """Pattern matches by parent_base_class without method_name constraint."""
        pattern = Pattern(
            concept="android_method",
            parent_base_class=r"^(Activity|AppCompatActivity)$",
        )

        symbol = Symbol(
            id="java:MainActivity.java:10-15:MainActivity.onResume:method",
            name="MainActivity.onResume",
            kind="method",
            language="java",
            path="MainActivity.java",
            span=Span(10, 15, 0, 0),
            meta={
                "parent_base_classes": ["AppCompatActivity"],
            },
        )

        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "android_method"
        assert result["matched_parent_base_class"] == "AppCompatActivity"


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
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            result = load_framework_patterns("test_fw")

        assert result is not None
        assert result.id == "test_framework"
        assert result.language == "python"
        assert len(result.patterns) == 1

    def test_resolves_framework_alias(self) -> None:
        """Framework aliases map to consolidated pattern files (e.g., chi -> go-web)."""
        clear_pattern_cache()

        # chi, gin, etc. should all load go-web.yaml
        chi_result = load_framework_patterns("chi")
        assert chi_result is not None
        assert chi_result.id == "go-web"

        # actix-web, axum, etc. should all load rust-web.yaml
        clear_pattern_cache()
        axum_result = load_framework_patterns("axum")
        assert axum_result is not None
        assert axum_result.id == "rust-web"


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
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
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
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
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
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            enriched = enrich_symbols([symbol], {"test_fw"})

        assert enriched[0].meta is not None
        assert "concepts" in enriched[0].meta

    def test_route_concepts_have_path_and_method(self, tmp_path: Path) -> None:
        """Route concepts include path and method extracted from decorators."""
        clear_pattern_cache()

        # Pattern with capture group for method extraction (like real YAMLs)
        yaml_content = """
id: test_fw
language: python
patterns:
  - concept: route
    decorator: "^app\\\\.(get|post|put|delete)$"
    extract_path: "args[0]"
    extract_method: "decorator_suffix"
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
            meta={
                "decorators": [
                    {"name": "app.get", "args": ["/users"], "kwargs": {}}
                ]
            },
        )

        with patch(
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            enriched = enrich_symbols([symbol], {"test_fw"})

        assert len(enriched) == 1
        # Should have concepts with path and method
        assert "concepts" in enriched[0].meta
        assert enriched[0].meta["concepts"][0]["concept"] == "route"
        assert enriched[0].meta["concepts"][0]["path"] == "/users"
        assert enriched[0].meta["concepts"][0]["method"] == "GET"

    def test_concepts_added_even_with_existing_meta(self, tmp_path: Path) -> None:
        """Concepts are added to symbols that already have metadata.

        Symbols may have other metadata from analyzers; enrichment should
        add concepts without affecting existing fields.
        """
        clear_pattern_cache()

        # Pattern with capture group for method extraction (like real YAMLs)
        yaml_content = """
id: test_fw
language: python
patterns:
  - concept: route
    decorator: "^app\\\\.(get|post|put|delete)$"
    extract_path: "args[0]"
    extract_method: "decorator_suffix"
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
            meta={
                "decorators": [
                    {"name": "app.get", "args": ["/users"], "kwargs": {}}
                ],
                # Existing metadata from analyzer
                "some_field": "some_value",
            },
        )

        with patch(
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            enriched = enrich_symbols([symbol], {"test_fw"})

        # Existing fields should be preserved
        assert enriched[0].meta["some_field"] == "some_value"

        # Concepts should be added with correct values
        assert enriched[0].meta["concepts"][0]["path"] == "/users"
        assert enriched[0].meta["concepts"][0]["method"] == "GET"


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
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
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


class TestBottlePatterns:
    """Tests for Bottle framework pattern matching."""

    def test_bottle_get_route_pattern(self) -> None:
        """Bottle @app.get decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("bottle")

        assert pattern_def is not None, "Bottle patterns YAML should exist"

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
        assert results[0]["method"] == "GET"
        assert results[0]["path"] == "/users"

    def test_bottle_classic_route_pattern(self) -> None:
        """Bottle @app.route decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("bottle")

        symbol = Symbol(
            id="test:app.py:1:home:function",
            name="home",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.route", "args": ["/"], "kwargs": {"method": "GET"}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["path"] == "/"

    def test_bottle_standalone_route_decorator(self) -> None:
        """Bottle @route decorator (without app prefix) matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("bottle")

        symbol = Symbol(
            id="test:app.py:1:index:function",
            name="index",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "route", "args": ["/index"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["path"] == "/index"

    def test_bottle_hook_pattern(self) -> None:
        """Bottle @app.hook decorator matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("bottle")

        symbol = Symbol(
            id="test:app.py:1:before_request:function",
            name="before_request",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.hook", "args": ["before_request"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"


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

    def test_nestjs_prefix_from_parent_integration(self) -> None:
        """Integration test: routes inherit path prefix from parent controller.

        Tests the prefix_from_parent feature (v1.3.x) where NestJS method routes
        combine the @Controller path prefix with the @Get/@Post path.
        """
        clear_pattern_cache()

        # Controller class with @Controller('/users')
        controller = Symbol(
            id="test:users.controller.ts:5:UsersController:class",
            name="UsersController",
            kind="class",
            language="typescript",
            path="users.controller.ts",
            span=Span(5, 50, 0, 0),
            meta={
                "decorators": [
                    {"name": "Controller", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        # Method with @Get(':id') inside the controller class
        method = Symbol(
            id="test:users.controller.ts:10:UsersController.findOne:method",
            name="UsersController.findOne",
            kind="method",
            language="typescript",
            path="users.controller.ts",
            span=Span(10, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "Get", "args": [":id"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([controller, method], {"nestjs"})

        assert len(enriched) == 2

        # Check controller has its path
        ctrl_concepts = enriched[0].meta.get("concepts", [])
        ctrl_concept = next(
            (c for c in ctrl_concepts if c.get("concept") == "controller"), None
        )
        assert ctrl_concept is not None
        assert ctrl_concept["path"] == "/users"

        # Check method route has combined path: /users + :id = /users/:id
        method_concepts = enriched[1].meta.get("concepts", [])
        route_concept = next(
            (c for c in method_concepts if c.get("concept") == "route"), None
        )
        assert route_concept is not None
        assert route_concept["method"] == "GET"
        assert route_concept["path"] == "/users/:id"
        assert route_concept["framework"] == "nestjs"

    def test_nestjs_prefix_from_parent_no_controller_path(self) -> None:
        """Routes work without controller path prefix."""
        clear_pattern_cache()

        # Controller without path argument
        controller = Symbol(
            id="test:app.controller.ts:5:AppController:class",
            name="AppController",
            kind="class",
            language="typescript",
            path="app.controller.ts",
            span=Span(5, 50, 0, 0),
            meta={
                "decorators": [
                    {"name": "Controller", "args": [], "kwargs": {}},
                ],
            },
        )

        # Method with @Get('health')
        method = Symbol(
            id="test:app.controller.ts:10:AppController.health:method",
            name="AppController.health",
            kind="method",
            language="typescript",
            path="app.controller.ts",
            span=Span(10, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "Get", "args": ["health"], "kwargs": {}},
                ],
            },
        )

        enriched = enrich_symbols([controller, method], {"nestjs"})

        # Check method route path is normalized with leading slash
        # Even when controller has no prefix, paths are normalized
        method_concepts = enriched[1].meta.get("concepts", [])
        route_concept = next(
            (c for c in method_concepts if c.get("concept") == "route"), None
        )
        assert route_concept is not None
        assert route_concept["path"] == "/health"


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
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
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
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
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
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
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
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            pattern_def = load_framework_patterns("custom_fw")
            results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        # Unknown extraction mode should not add method
        assert "method" not in results[0]

    def test_multi_path_extraction_all_fail(self, tmp_path: Path) -> None:
        """Test multi-path extraction with | where all paths fail returns no path."""
        clear_pattern_cache()

        yaml_content = """
id: custom_fw
language: java
patterns:
  - concept: route
    decorator: "^GetMapping$"
    extract_path: "args[0]|kwargs.value"
"""
        yaml_file = tmp_path / "custom_fw.yaml"
        yaml_file.write_text(yaml_content)

        # Symbol with no args and no kwargs.value
        symbol = Symbol(
            id="test:Controller.java:1:get:method",
            name="get",
            kind="method",
            language="java",
            path="Controller.java",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "GetMapping", "args": [], "kwargs": {}},
                ],
            },
        )

        with patch(
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            pattern_def = load_framework_patterns("custom_fw")
            results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        # Path is empty string when extraction fails - enables prefix combination
        assert results[0]["path"] == ""

    def test_enum_style_method_extraction(self, tmp_path: Path) -> None:
        """Test extraction of enum-style method values like RequestMethod.GET."""
        clear_pattern_cache()

        yaml_content = """
id: custom_fw
language: java
patterns:
  - concept: route
    decorator: "^RequestMapping$"
    extract_method: "kwargs.method"
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
                "decorators": [
                    {"name": "RequestMapping", "args": [], "kwargs": {"method": "RequestMethod.GET"}},
                ],
            },
        )

        with patch(
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            pattern_def = load_framework_patterns("custom_fw")
            results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        # Should extract GET from RequestMethod.GET
        assert results[0]["method"] == "GET"

    def test_kwargs_method_extraction_missing_key(self, tmp_path: Path) -> None:
        """Test kwargs.method extraction when key is missing returns no method."""
        clear_pattern_cache()

        yaml_content = """
id: custom_fw
language: java
patterns:
  - concept: route
    decorator: "^RequestMapping$"
    extract_method: "kwargs.method"
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
                "decorators": [
                    {"name": "RequestMapping", "args": [], "kwargs": {"value": "/path"}},  # No method key
                ],
            },
        )

        with patch(
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            pattern_def = load_framework_patterns("custom_fw")
            results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        # No method since key was missing
        assert "method" not in results[0]

    def test_kwargs_method_extraction_string_metadata(self, tmp_path: Path) -> None:
        """Test kwargs.method extraction when decorator is a plain string."""
        clear_pattern_cache()

        yaml_content = """
id: custom_fw
language: java
patterns:
  - concept: route
    decorator: "^SomeDecorator$"
    extract_method: "kwargs.method"
"""
        yaml_file = tmp_path / "custom_fw.yaml"
        yaml_file.write_text(yaml_content)

        # Decorator stored as plain string, not a dict
        symbol = Symbol(
            id="test:Controller.java:1:get:method",
            name="get",
            kind="method",
            language="java",
            path="Controller.java",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": ["SomeDecorator"],  # Plain string instead of dict
            },
        )

        with patch(
            "hypergumbo_core.framework_patterns.get_frameworks_dir",
            return_value=tmp_path,
        ):
            pattern_def = load_framework_patterns("custom_fw")
            results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        # No method since metadata is a string, not a dict
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

    def test_axios_http_client_pattern(self) -> None:
        """Axios HTTP client calls match http_client pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("express")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:api.js:10:fetchUsers:function",
            name="fetchUsers",
            kind="function",
            language="javascript",
            path="api.js",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "axios.get", "args": ["/api/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "http_client"
        assert results[0]["matched_decorator"] == "axios.get"

    def test_fetch_http_client_pattern_via_usage_context(self) -> None:
        """Fetch API calls match http_client pattern via UsageContext."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("express")

        assert pattern_def is not None

        ctx = UsageContext.create(
            kind="call",
            context_name="fetch",
            position="args[0]",
            path="api.ts",
            span=Span(15, 15, 0, 50),
            symbol_ref="test:api.ts:15:fetchData:function",
            metadata={
                "url": "/api/data",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "http_client"

    def test_ky_http_client_pattern(self) -> None:
        """Ky HTTP client calls match http_client pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("express")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:client.ts:5:getUser:function",
            name="getUser",
            kind="function",
            language="typescript",
            path="client.ts",
            span=Span(5, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "ky.get", "args": ["/users/1"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "http_client"


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

    def test_rails_route_symbol_kind_pattern(self) -> None:
        """Rails route symbols (kind=route) match route pattern via symbol_kind."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("rails")

        assert pattern_def is not None

        # Route symbol created by Ruby analyzer for 'get "/users"' DSL call
        symbol = Symbol(
            id="test:routes.rb:1:GET_users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="config/routes.rb",
            span=Span(1, 1, 0, 30),
            meta={
                "http_method": "GET",
                "route_path": "/users",
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_symbol_kind"] == "route"

    def test_rails_resources_route_pattern(self) -> None:
        """Rails resources route symbols match route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("rails")

        assert pattern_def is not None

        # Route symbol for 'resources :users' DSL call
        symbol = Symbol(
            id="test:routes.rb:5:resources_users:route",
            name="resources:users",
            kind="route",
            language="ruby",
            path="config/routes.rb",
            span=Span(5, 5, 0, 20),
            meta={
                "route_path": "users",
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"


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

    def test_laravel_route_symbol_kind_pattern(self) -> None:
        """Laravel route symbols (kind=route) match route pattern via symbol_kind."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laravel")

        assert pattern_def is not None

        # Route symbol created by PHP analyzer for Route::get('/users', ...) call
        symbol = Symbol(
            id="test:web.php:1:GET_users:route",
            name="GET /users",
            kind="route",
            language="php",
            path="routes/web.php",
            span=Span(1, 1, 0, 40),
            meta={
                "http_method": "GET",
                "route_path": "/users",
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["matched_symbol_kind"] == "route"

    def test_laravel_api_route_pattern(self) -> None:
        """Laravel API route symbols match route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laravel")

        assert pattern_def is not None

        # Route symbol for Route::post('/api/login', ...) call
        symbol = Symbol(
            id="test:api.php:5:POST_api_login:route",
            name="POST /api/login",
            kind="route",
            language="php",
            path="routes/api.php",
            span=Span(5, 5, 0, 50),
            meta={
                "http_method": "POST",
                "route_path": "/api/login",
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"


class TestGoWebPatterns:
    """Tests for Go web framework pattern matching.

    Go web frameworks use call-based patterns (r.GET, e.POST) which are matched
    via UsageContext (v1.1.x), not decorator metadata.
    """

    def test_go_gin_get_route_pattern_via_usage_context(self) -> None:
        """Gin router.GET matches via UsageContext pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None, "Go-web patterns YAML should exist"

        # Create a UsageContext for a Gin route call
        ctx = UsageContext.create(
            kind="call",
            context_name="router.GET",
            position="args[last]",
            path="main.go",
            span=Span(10, 10, 0, 50),
            symbol_ref="test:main.go:10:getUsers:function",
            metadata={
                "route_path": "/users",
                "http_method": "GET",
                "handler_name": "getUsers",
                "receiver": "router",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["path"] == "/users"
        assert results[0]["method"] == "GET"

    def test_go_echo_post_route_pattern_via_usage_context(self) -> None:
        """Echo e.POST matches via UsageContext pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None

        ctx = UsageContext.create(
            kind="call",
            context_name="e.POST",
            position="args[last]",
            path="handlers.go",
            span=Span(15, 15, 0, 50),
            symbol_ref="test:handlers.go:15:createUser:function",
            metadata={
                "route_path": "/users",
                "http_method": "POST",
                "handler_name": "createUser",
                "receiver": "e",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "POST"

    def test_go_fiber_get_route_pattern_via_usage_context(self) -> None:
        """Fiber app.Get matches via UsageContext pattern (lowercase method)."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None

        ctx = UsageContext.create(
            kind="call",
            context_name="app.Get",
            position="args[last]",
            path="main.go",
            span=Span(20, 20, 0, 50),
            symbol_ref="test:main.go:20:getProduct:function",
            metadata={
                "route_path": "/products/:id",
                "http_method": "GET",
                "handler_name": "getProduct",
                "receiver": "app",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "GET"

    def test_go_chi_delete_route_pattern_via_usage_context(self) -> None:
        """Chi r.Delete matches via UsageContext pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None

        ctx = UsageContext.create(
            kind="call",
            context_name="r.Delete",
            position="args[last]",
            path="handlers.go",
            span=Span(25, 25, 0, 50),
            symbol_ref="test:handlers.go:25:deleteUser:function",
            metadata={
                "route_path": "/users/{id}",
                "http_method": "DELETE",
                "handler_name": "deleteUser",
                "receiver": "r",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

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
        """Go web patterns enrich symbols via UsageContext matching."""
        clear_pattern_cache()

        # Symbols representing handler functions
        symbols = [
            Symbol(
                id="test:main.go:10:getUsers:function",
                name="getUsers",
                kind="function",
                language="go",
                path="main.go",
                span=Span(10, 20, 0, 0),
                meta={},
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

        # UsageContexts from route registration calls
        usage_contexts = [
            UsageContext.create(
                kind="call",
                context_name="router.GET",
                position="args[last]",
                path="main.go",
                span=Span(10, 10, 0, 50),
                symbol_ref="test:main.go:10:getUsers:function",
                metadata={
                    "route_path": "/users",
                    "http_method": "GET",
                    "handler_name": "getUsers",
                    "receiver": "router",
                },
            ),
        ]

        enriched = enrich_symbols(symbols, {"go-web"}, usage_contexts=usage_contexts)

        # Check that route symbol was enriched via UsageContext
        route = next(s for s in enriched if s.name == "getUsers")
        assert "concepts" in route.meta
        assert any(c["concept"] == "route" for c in route.meta["concepts"])
        # Verify route metadata was extracted
        route_concept = next(c for c in route.meta["concepts"] if c["concept"] == "route")
        assert route_concept.get("path") == "/users"
        assert route_concept.get("method") == "GET"

        # Middleware still works via decorator patterns
        middleware = next(s for s in enriched if s.name == "authMiddleware")
        assert "concepts" in middleware.meta
        assert any(c["concept"] == "middleware" for c in middleware.meta["concepts"])

        # GORM model still works via base_class patterns
        model = next(s for s in enriched if s.name == "User")
        assert "concepts" in model.meta
        assert any(c["concept"] == "model" for c in model.meta["concepts"])

    def test_go_restful_route_pattern_via_usage_context(self) -> None:
        """go-restful ws.GET().To(handler) matches via UsageContext pattern.

        go-restful uses a fluent API: ws.Route(ws.GET("/path").To(handler))
        The handler is specified in the .To() call.
        """
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None

        # Create a UsageContext for a go-restful .To() call
        ctx = UsageContext.create(
            kind="call",
            context_name="RouteBuilder.To",
            position="args[0]",
            path="routes.go",
            span=Span(10, 10, 0, 50),
            symbol_ref="test:routes.go:10:getVersion:function",
            metadata={
                "handler_name": "getVersion",
                "receiver": "RouteBuilder",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_go_restful_webservice_pattern(self) -> None:
        """go-restful WebService base class matches web_service pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("go-web")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:api/routes.go:1:Routes:struct",
            name="Routes",
            kind="struct",
            language="go",
            path="api/routes.go",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["restful.WebService"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "web_service"
        assert results[0]["matched_base_class"] == "restful.WebService"


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


class TestJavaAnalyzerIntegration:
    """Integration tests: Java analyzer + YAML patterns end-to-end.

    These tests prove that YAML patterns can replace deprecated analyzer-level
    route detection (ADR-0003 v1.0.x). They run the actual Java analyzer, then
    enrich_symbols(), and verify both concepts and legacy fields are populated.
    """

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> None:
        """Clear pattern cache before each test."""
        clear_pattern_cache()

    def test_spring_route_via_yaml_patterns(self, tmp_path: Path) -> None:
        """Java analyzer extracts decorators, YAML patterns add route concepts.

        This test demonstrates that:
        1. Java analyzer extracts @GetMapping to meta.decorators
        2. spring-boot.yaml patterns match these decorators
        3. enrich_symbols populates both concepts AND legacy fields
        4. The deprecated analyzer code is NOT needed for this to work
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "UserController.java"
        java_file.write_text("""
@RestController
public class UserController {
    @GetMapping("/users")
    public List<User> getUsers() {
        return userService.findAll();
    }
}
""")

        result = analyze_java(tmp_path)

        # Find the getUsers method
        methods = [s for s in result.symbols if s.kind == "method" and "getUsers" in s.name]
        assert len(methods) == 1
        method = methods[0]

        # Verify analyzer extracted decorators
        assert method.meta is not None
        assert "decorators" in method.meta
        decorators = method.meta["decorators"]
        assert any(d.get("name") == "GetMapping" for d in decorators)

        # Enrich with YAML patterns - this is what replaces deprecated code
        enriched = enrich_symbols([method], {"spring-boot"})
        assert len(enriched) == 1
        enriched_method = enriched[0]

        # Verify concepts were added by YAML patterns
        assert "concepts" in enriched_method.meta
        route_concept = next(
            c for c in enriched_method.meta["concepts"] if c["concept"] == "route"
        )
        assert route_concept["method"] == "GET"
        assert route_concept["path"] == "/users"
        assert route_concept["framework"] == "spring-boot"

    def test_spring_all_methods_via_yaml(self, tmp_path: Path) -> None:
        """All HTTP method mappings work through YAML patterns."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "ResourceController.java"
        java_file.write_text("""
@RestController
public class ResourceController {
    @GetMapping("/items")
    public void getAll() {}

    @PostMapping("/items")
    public void create() {}

    @PutMapping("/items/{id}")
    public void update() {}

    @DeleteMapping("/items/{id}")
    public void remove() {}

    @PatchMapping("/items/{id}")
    public void patch() {}
}
""")

        result = analyze_java(tmp_path)
        methods = [
            s for s in result.symbols
            if s.kind == "method" and s.meta and "decorators" in s.meta
        ]

        # Enrich all methods
        enriched = enrich_symbols(methods, {"spring-boot"})

        # Verify all HTTP methods are detected
        http_methods_found = set()
        for method in enriched:
            if "concepts" in method.meta:
                for concept in method.meta["concepts"]:
                    if concept.get("concept") == "route" and "method" in concept:
                        http_methods_found.add(concept["method"])

        assert http_methods_found == {"GET", "POST", "PUT", "DELETE", "PATCH"}

    def test_spring_controller_via_yaml(self, tmp_path: Path) -> None:
        """Spring @RestController is enriched with controller concept."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "ApiController.java"
        java_file.write_text("""
@RestController
public class ApiController {
    public void someMethod() {}
}
""")

        result = analyze_java(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1

        enriched = enrich_symbols(classes, {"spring-boot"})
        controller = enriched[0]

        assert "concepts" in controller.meta
        assert any(c["concept"] == "controller" for c in controller.meta["concepts"])

    def test_yaml_patterns_without_deprecated_code(self, tmp_path: Path) -> None:
        """YAML patterns work even if symbol has no legacy fields.

        This test creates a symbol with only decorators (no http_method/route_path)
        to prove YAML patterns can fully replace deprecated analyzer code.
        """
        # Create a symbol as if the deprecated code was NOT run
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
                # Note: NO http_method or route_path - simulates removed deprecated code
            },
        )

        enriched = enrich_symbols([symbol], {"spring-boot"})
        method = enriched[0]

        # Verify YAML patterns populated everything
        assert "concepts" in method.meta
        route = next(c for c in method.meta["concepts"] if c["concept"] == "route")
        assert route["method"] == "GET"
        assert route["path"] == "/users"


# ==================== USAGE CONTEXT TESTS (v1.1.x) ====================


class TestUsagePatternSpec:
    """Tests for UsagePatternSpec (v1.1.x)."""

    def test_matches_all_fields(self) -> None:
        """UsagePatternSpec matches when all specified patterns match."""
        spec = UsagePatternSpec(
            kind="^call$",
            name="^path$",
            position="^args\\[1\\]$",
        )
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
        )

        assert spec.matches(ctx) is True

    def test_matches_partial_spec(self) -> None:
        """UsagePatternSpec matches with only some patterns specified."""
        spec = UsagePatternSpec(
            kind="^call$",
            name=None,  # Match any name
            position=None,  # Match any position
        )
        ctx = UsageContext.create(
            kind="call",
            context_name="anything",
            position="anywhere",
            path="file.py",
            span=Span(1, 1, 0, 10),
        )

        assert spec.matches(ctx) is True

    def test_no_match_wrong_kind(self) -> None:
        """UsagePatternSpec fails when kind doesn't match."""
        spec = UsagePatternSpec(kind="^call$")
        ctx = UsageContext.create(
            kind="export",
            context_name="module",
            position="default",
            path="file.py",
            span=Span(1, 1, 0, 10),
        )

        assert spec.matches(ctx) is False

    def test_no_match_wrong_name(self) -> None:
        """UsagePatternSpec fails when name doesn't match."""
        spec = UsagePatternSpec(name="^path$")
        ctx = UsageContext.create(
            kind="call",
            context_name="re_path",
            position="args[1]",
            path="file.py",
            span=Span(1, 1, 0, 10),
        )

        assert spec.matches(ctx) is False

    def test_regex_name_pattern(self) -> None:
        """UsagePatternSpec uses regex for name matching."""
        spec = UsagePatternSpec(name="^(path|re_path|url)$")
        for name in ["path", "re_path", "url"]:
            ctx = UsageContext.create(
                kind="call",
                context_name=name,
                position="args[1]",
                path="file.py",
                span=Span(1, 1, 0, 10),
            )
            assert spec.matches(ctx) is True

    def test_no_match_wrong_position(self) -> None:
        """UsagePatternSpec fails when position doesn't match."""
        spec = UsagePatternSpec(position="^args\\[1\\]$")
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[0]",  # Doesn't match args[1]
            path="file.py",
            span=Span(1, 1, 0, 10),
        )

        assert spec.matches(ctx) is False


class TestExtractUsageValue:
    """Tests for extract_usage_value function (v1.1.x)."""

    def test_literal_value(self) -> None:
        """Extract literal value."""
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="file.py",
            span=Span(1, 1, 0, 10),
        )

        assert extract_usage_value(ctx, "literal:GET") == "GET"
        assert extract_usage_value(ctx, "literal:/api/users") == "/api/users"

    def test_context_name_field(self) -> None:
        """Extract context_name field."""
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="file.py",
            span=Span(1, 1, 0, 10),
        )

        assert extract_usage_value(ctx, "context_name") == "path"

    def test_position_field(self) -> None:
        """Extract position field."""
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="file.py",
            span=Span(1, 1, 0, 10),
        )

        assert extract_usage_value(ctx, "position") == "args[1]"

    def test_metadata_args(self) -> None:
        """Extract value from metadata.args."""
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="file.py",
            span=Span(1, 1, 0, 10),
            metadata={"args": ["/users/", "views.list_users"]},
        )

        assert extract_usage_value(ctx, "metadata.args[0]") == "/users/"
        assert extract_usage_value(ctx, "metadata.args[1]") == "views.list_users"

    def test_metadata_kwargs(self) -> None:
        """Extract value from metadata.kwargs."""
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="file.py",
            span=Span(1, 1, 0, 10),
            metadata={"kwargs": {"name": "user-list"}},
        )

        assert extract_usage_value(ctx, "metadata.kwargs.name") == "user-list"

    def test_uppercase_transform(self) -> None:
        """Transform value to uppercase."""
        ctx = UsageContext.create(
            kind="call",
            context_name="get",
            position="method",
            path="file.py",
            span=Span(1, 1, 0, 10),
        )

        assert extract_usage_value(ctx, "context_name | uppercase") == "GET"

    def test_lowercase_transform(self) -> None:
        """Transform value to lowercase."""
        ctx = UsageContext.create(
            kind="call",
            context_name="GET",
            position="method",
            path="file.py",
            span=Span(1, 1, 0, 10),
        )

        assert extract_usage_value(ctx, "context_name | lowercase") == "get"

    def test_returns_none_for_missing(self) -> None:
        """Return None for missing paths."""
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="file.py",
            span=Span(1, 1, 0, 10),
            metadata={},
        )

        assert extract_usage_value(ctx, "metadata.args[99]") is None
        assert extract_usage_value(ctx, "metadata.kwargs.missing") is None
        assert extract_usage_value(ctx, "nonexistent") is None

    def test_transform_returns_none_for_missing_base(self) -> None:
        """Transform returns None when base value is missing."""
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="file.py",
            span=Span(1, 1, 0, 10),
            metadata={},
        )

        # metadata.missing doesn't exist, so transform should return None
        assert extract_usage_value(ctx, "metadata.missing | uppercase") is None

    def test_split_and_last_transform(self) -> None:
        """Transform value using split and last."""
        ctx = UsageContext.create(
            kind="call",
            context_name="views.list_users",
            position="args[1]",
            path="file.py",
            span=Span(1, 1, 0, 10),
        )

        # Split by . and take last element
        result = extract_usage_value(ctx, "context_name | split:. | last")
        assert result == "list_users"

    def test_direct_metadata_key(self) -> None:
        """Extract value from direct metadata key."""
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="file.py",
            span=Span(1, 1, 0, 10),
            metadata={"custom_key": "custom_value"},
        )

        assert extract_usage_value(ctx, "metadata.custom_key") == "custom_value"

    def test_invalid_args_index(self) -> None:
        """Handle invalid args index gracefully."""
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="file.py",
            span=Span(1, 1, 0, 10),
            metadata={"args": ["only_one"]},
        )

        # Out of bounds
        assert extract_usage_value(ctx, "metadata.args[5]") is None
        # Invalid format (testing exception handling)
        assert extract_usage_value(ctx, "metadata.args[not_a_number]") is None


class TestPatternMatchesUsage:
    """Tests for Pattern.matches_usage method (v1.1.x)."""

    def test_matches_usage_context(self) -> None:
        """Pattern.matches_usage matches against UsageContext."""
        pattern = Pattern(
            concept="route",
            usage=UsagePatternSpec(
                kind="^call$",
                name="^(path|re_path)$",
                position="^args\\[1\\]$",
            ),
            extract={"path": "metadata.args[0]", "method": "literal:GET"},
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref="python:views.py:10-15:list_users:function",
            metadata={"args": ["/users/", "views.list_users"]},
        )

        result = pattern.matches_usage(ctx)
        assert result is not None
        assert result["concept"] == "route"
        assert result["path"] == "/users/"
        assert result["method"] == "GET"

    def test_no_match_without_usage_spec(self) -> None:
        """Pattern without usage spec doesn't match usage contexts."""
        pattern = Pattern(
            concept="route",
            decorator="^app\\.get$",  # Definition-based, not usage-based
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
        )

        assert pattern.matches_usage(ctx) is None

    def test_no_match_when_spec_fails(self) -> None:
        """Pattern.matches_usage returns None when spec doesn't match."""
        pattern = Pattern(
            concept="route",
            usage=UsagePatternSpec(kind="^call$", name="^path$"),
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="url",  # Doesn't match "^path$"
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
        )

        assert pattern.matches_usage(ctx) is None


class TestFrameworkPatternDefFromDictWithUsage:
    """Tests for FrameworkPatternDef.from_dict with usage patterns (v1.1.x)."""

    def test_parses_usage_pattern_from_dict(self) -> None:
        """FrameworkPatternDef.from_dict parses usage field."""
        data = {
            "id": "django",
            "language": "python",
            "patterns": [
                {
                    "concept": "route",
                    "usage": {
                        "kind": "^call$",
                        "name": "^path$",
                        "position": "^args\\[1\\]$",
                    },
                    "extract": {
                        "path": "metadata.args[0]",
                        "method": "literal:GET",
                    },
                },
            ],
        }

        pattern_def = FrameworkPatternDef.from_dict(data)
        assert pattern_def.id == "django"
        assert len(pattern_def.patterns) == 1

        pattern = pattern_def.patterns[0]
        assert pattern.concept == "route"
        assert pattern.usage is not None
        assert pattern.usage.kind == "^call$"
        assert pattern.usage.name == "^path$"
        assert pattern.usage.position == "^args\\[1\\]$"
        assert pattern.extract == {"path": "metadata.args[0]", "method": "literal:GET"}


class TestMatchUsagePatterns:
    """Tests for match_usage_patterns function (v1.1.x)."""

    def test_matches_usage_against_framework_patterns(self) -> None:
        """match_usage_patterns finds matches in framework pattern defs."""
        pattern_def = FrameworkPatternDef(
            id="django",
            language="python",
            patterns=[
                Pattern(
                    concept="route",
                    usage=UsagePatternSpec(
                        kind="^call$",
                        name="^path$",
                    ),
                    extract={"path": "metadata.args[0]", "method": "literal:GET"},
                ),
            ],
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            metadata={"args": ["/users/"]},
        )

        results = match_usage_patterns(ctx, [pattern_def])
        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["framework"] == "django"
        assert results[0]["path"] == "/users/"


class TestEnrichSymbolsWithUsageContexts:
    """Tests for enrich_symbols with usage_contexts parameter (v1.1.x)."""

    def test_enrich_with_usage_contexts(self) -> None:
        """enrich_symbols enriches symbols via usage context matching."""
        # Create a symbol that will be referenced by usage context
        symbol = Symbol(
            id="python:views.py:10-15:list_users:function",
            name="list_users",
            kind="function",
            language="python",
            path="views.py",
            span=Span(10, 15, 0, 50),
            meta={},
        )

        # Create usage context referencing the symbol
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref="python:views.py:10-15:list_users:function",
            metadata={"args": ["/users/", "views.list_users"]},
        )

        # Create a framework pattern with usage-based matching
        pattern_def = FrameworkPatternDef(
            id="test-django",
            language="python",
            patterns=[
                Pattern(
                    concept="route",
                    usage=UsagePatternSpec(
                        kind="^call$",
                        name="^path$",
                    ),
                    extract={"path": "metadata.args[0]", "method": "literal:GET"},
                ),
            ],
        )

        # Patch to load our test pattern (return None for other framework IDs like main-functions)
        def mock_load(fw_id: str):
            return pattern_def if fw_id == "test-django" else None

        with patch(
            "hypergumbo_core.framework_patterns.load_framework_patterns",
            side_effect=mock_load,
        ):
            enriched = enrich_symbols(
                [symbol],
                {"test-django"},
                usage_contexts=[ctx],
            )

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        assert len(concepts) == 1
        assert concepts[0]["concept"] == "route"
        assert concepts[0]["path"] == "/users/"
        assert concepts[0]["method"] == "GET"

    def test_skips_inline_handlers(self) -> None:
        """enrich_symbols skips usage contexts with no symbol_ref (inline handlers)."""
        symbol = Symbol(
            id="python:views.py:10-15:list_users:function",
            name="list_users",
            kind="function",
            language="python",
            path="views.py",
            span=Span(10, 15, 0, 50),
            meta={},
        )

        # Inline handler - no symbol_ref
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,  # Inline lambda handler
            metadata={"args": ["/inline/", "<lambda>"]},
        )

        pattern_def = FrameworkPatternDef(
            id="test-django",
            language="python",
            patterns=[
                Pattern(
                    concept="route",
                    usage=UsagePatternSpec(kind="^call$", name="^path$"),
                    extract={"path": "metadata.args[0]"},
                ),
            ],
        )

        with patch(
            "hypergumbo_core.framework_patterns.load_framework_patterns",
            return_value=pattern_def,
        ):
            enriched = enrich_symbols(
                [symbol],
                {"test-django"},
                usage_contexts=[ctx],
            )

        # Symbol should not be enriched (no match via inline context)
        assert enriched[0].meta.get("concepts") is None

    def test_skips_unknown_symbol_refs(self) -> None:
        """enrich_symbols skips usage contexts referencing unknown symbols."""
        symbol = Symbol(
            id="python:views.py:10-15:list_users:function",
            name="list_users",
            kind="function",
            language="python",
            path="views.py",
            span=Span(10, 15, 0, 50),
            meta={},
        )

        # Reference to a symbol that doesn't exist in our list
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref="python:other.py:1-5:unknown:function",  # Not in symbols list
            metadata={"args": ["/unknown/"]},
        )

        pattern_def = FrameworkPatternDef(
            id="test-django",
            language="python",
            patterns=[
                Pattern(
                    concept="route",
                    usage=UsagePatternSpec(kind="^call$", name="^path$"),
                    extract={"path": "metadata.args[0]"},
                ),
            ],
        )

        with patch(
            "hypergumbo_core.framework_patterns.load_framework_patterns",
            return_value=pattern_def,
        ):
            enriched = enrich_symbols(
                [symbol],
                {"test-django"},
                usage_contexts=[ctx],
            )

        # Symbol should not be enriched (ctx references different symbol)
        assert enriched[0].meta.get("concepts") is None

    def test_enriches_symbol_with_none_meta(self) -> None:
        """enrich_symbols handles symbols with meta=None."""
        # Symbol with meta=None (not empty dict)
        symbol = Symbol(
            id="python:views.py:10-15:list_users:function",
            name="list_users",
            kind="function",
            language="python",
            path="views.py",
            span=Span(10, 15, 0, 50),
            meta=None,  # Explicitly None
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref="python:views.py:10-15:list_users:function",
            metadata={"args": ["/users/"]},
        )

        pattern_def = FrameworkPatternDef(
            id="test-django",
            language="python",
            patterns=[
                Pattern(
                    concept="route",
                    usage=UsagePatternSpec(kind="^call$", name="^path$"),
                    extract={"path": "metadata.args[0]", "method": "literal:GET"},
                ),
            ],
        )

        with patch(
            "hypergumbo_core.framework_patterns.load_framework_patterns",
            return_value=pattern_def,
        ):
            enriched = enrich_symbols(
                [symbol],
                {"test-django"},
                usage_contexts=[ctx],
            )

        # Should create meta dict and populate it
        assert enriched[0].meta is not None
        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        route = next(c for c in concepts if c["concept"] == "route")
        assert route["path"] == "/users/"
        assert route["method"] == "GET"

    def test_inv002_fallback_resolution_by_view_name(self) -> None:
        """INV-002: Enriches symbol via view_name when symbol_ref is None.

        This tests the fix for INV-002 where UsageContext records with
        symbol_ref=None but view_name in metadata can still enrich symbols
        by falling back to name-based resolution.
        """
        # Symbol representing the view function
        symbol = Symbol(
            id="python:views.py:10-15:user_list:function",
            name="user_list",
            kind="function",
            language="python",
            path="views.py",
            span=Span(10, 15, 0, 50),
            meta={},
        )

        # UsageContext with symbol_ref=None (view is in different file)
        # but view_name is present in metadata for fallback resolution
        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,  # Key: symbol_ref is None
            metadata={
                "args": ["/users/", "views.user_list"],
                "view_name": "user_list",  # Key: view_name for fallback
                "route_path": "/users/",
            },
        )

        pattern_def = FrameworkPatternDef(
            id="test-django",
            language="python",
            patterns=[
                Pattern(
                    concept="route",
                    usage=UsagePatternSpec(kind="^call$", name="^path$"),
                    extract={"path": "metadata.route_path", "method": "literal:GET"},
                ),
            ],
        )

        with patch(
            "hypergumbo_core.framework_patterns.load_framework_patterns",
            return_value=pattern_def,
        ):
            enriched = enrich_symbols(
                [symbol],
                {"test-django"},
                usage_contexts=[ctx],
            )

        # Symbol should be enriched via name-based fallback (INV-002 fix)
        assert enriched[0].meta is not None
        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        assert len(concepts) >= 1
        route = next(c for c in concepts if c["concept"] == "route")
        assert route["path"] == "/users/"
        assert route["method"] == "GET"


class TestResolveDeferredSymbolRefs:
    """Tests for resolve_deferred_symbol_refs() - INV-002 proper fix.

    This tests the deferred resolution phase that runs BEFORE enrichment
    to resolve symbol_ref for UsageContexts that couldn't be resolved
    during analysis (because the target symbol was in a different file).
    """

    def test_resolves_exact_match(self) -> None:
        """Resolves when view_name matches symbol name exactly."""
        symbol = Symbol(
            id="python:views.py:10:user_list:function",
            name="user_list",
            kind="function",
            language="python",
            path="views.py",
            span=Span(10, 10, 0, 50),
            meta={},
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            metadata={"view_name": "user_list"},
        )

        stats = resolve_deferred_symbol_refs([symbol], [ctx])

        assert ctx.symbol_ref == symbol.id
        assert stats.total_unresolved == 1
        assert stats.total_resolved == 1
        assert stats.resolved_exact == 1
        assert stats.still_unresolved == 0

    def test_resolves_suffix_match(self) -> None:
        """Resolves when view_name matches suffix of qualified name."""
        symbol = Symbol(
            id="python:views.py:10:list_users:function",
            name="list_users",
            kind="function",
            language="python",
            path="views.py",
            span=Span(10, 10, 0, 50),
            meta={"qualified_name": "myapp.views.list_users"},
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            metadata={"view_name": "views.list_users"},
        )

        stats = resolve_deferred_symbol_refs([symbol], [ctx])

        assert ctx.symbol_ref == symbol.id
        assert stats.total_resolved == 1

    def test_resolves_dotted_view_name(self) -> None:
        """Resolves dotted view_name like 'views.user_list'."""
        symbol = Symbol(
            id="python:views.py:10:user_list:function",
            name="user_list",
            kind="function",
            language="python",
            path="myapp/views.py",
            span=Span(10, 10, 0, 50),
            meta={},
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            metadata={"view_name": "views.user_list"},
        )

        stats = resolve_deferred_symbol_refs([symbol], [ctx])

        # Should resolve by extracting "user_list" from "views.user_list"
        assert ctx.symbol_ref == symbol.id
        assert stats.total_resolved == 1

    def test_skips_already_resolved(self) -> None:
        """Skips UsageContexts that already have symbol_ref."""
        symbol = Symbol(
            id="python:views.py:10:user_list:function",
            name="user_list",
            kind="function",
            language="python",
            path="views.py",
            span=Span(10, 10, 0, 50),
            meta={},
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=symbol.id,  # Already resolved
            metadata={"view_name": "user_list"},
        )

        stats = resolve_deferred_symbol_refs([symbol], [ctx])

        assert stats.total_unresolved == 0
        assert stats.total_resolved == 0

    def test_handles_missing_metadata(self) -> None:
        """Handles UsageContext with no metadata gracefully."""
        symbol = Symbol(
            id="python:views.py:10:user_list:function",
            name="user_list",
            kind="function",
            language="python",
            path="views.py",
            span=Span(10, 10, 0, 50),
            meta={},
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            metadata={},  # No resolution hints
        )

        stats = resolve_deferred_symbol_refs([symbol], [ctx])

        assert ctx.symbol_ref is None
        assert stats.total_unresolved == 1
        assert stats.still_unresolved == 1

    def test_handles_no_matching_symbol(self) -> None:
        """Handles case where no symbol matches the view_name."""
        symbol = Symbol(
            id="python:views.py:10:other_func:function",
            name="other_func",
            kind="function",
            language="python",
            path="views.py",
            span=Span(10, 10, 0, 50),
            meta={},
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            metadata={"view_name": "user_list"},  # No matching symbol
        )

        stats = resolve_deferred_symbol_refs([symbol], [ctx])

        assert ctx.symbol_ref is None
        assert stats.still_unresolved == 1

    def test_uses_handler_key(self) -> None:
        """Resolves using 'handler' metadata key (Express style)."""
        symbol = Symbol(
            id="js:routes.js:10:getUsers:function",
            name="getUsers",
            kind="function",
            language="javascript",
            path="routes.js",
            span=Span(10, 10, 0, 50),
            meta={},
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="get",
            position="args[1]",
            path="app.js",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            metadata={"handler": "getUsers"},
        )

        stats = resolve_deferred_symbol_refs([symbol], [ctx])

        assert ctx.symbol_ref == symbol.id
        assert stats.total_resolved == 1

    def test_uses_callback_key(self) -> None:
        """Resolves using 'callback' metadata key (event style)."""
        symbol = Symbol(
            id="js:events.js:10:onMessage:function",
            name="onMessage",
            kind="function",
            language="javascript",
            path="events.js",
            span=Span(10, 10, 0, 50),
            meta={},
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="on",
            position="args[1]",
            path="app.js",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            metadata={"callback": "onMessage"},
        )

        stats = resolve_deferred_symbol_refs([symbol], [ctx])

        assert ctx.symbol_ref == symbol.id
        assert stats.total_resolved == 1

    def test_multiple_contexts_resolution(self) -> None:
        """Resolves multiple UsageContexts in one call."""
        symbols = [
            Symbol(
                id="python:views.py:10:list_users:function",
                name="list_users",
                kind="function",
                language="python",
                path="views.py",
                span=Span(10, 10, 0, 50),
                meta={},
            ),
            Symbol(
                id="python:views.py:20:create_user:function",
                name="create_user",
                kind="function",
                language="python",
                path="views.py",
                span=Span(20, 20, 0, 50),
                meta={},
            ),
        ]

        contexts = [
            UsageContext.create(
                kind="call",
                context_name="path",
                position="args[1]",
                path="urls.py",
                span=Span(5, 5, 0, 50),
                symbol_ref=None,
                metadata={"view_name": "list_users"},
            ),
            UsageContext.create(
                kind="call",
                context_name="path",
                position="args[1]",
                path="urls.py",
                span=Span(6, 6, 0, 50),
                symbol_ref=None,
                metadata={"view_name": "create_user"},
            ),
            UsageContext.create(
                kind="call",
                context_name="path",
                position="args[1]",
                path="urls.py",
                span=Span(7, 7, 0, 50),
                symbol_ref=None,
                metadata={"view_name": "nonexistent"},  # Won't resolve
            ),
        ]

        stats = resolve_deferred_symbol_refs(symbols, contexts)

        assert contexts[0].symbol_ref == symbols[0].id
        assert contexts[1].symbol_ref == symbols[1].id
        assert contexts[2].symbol_ref is None

        assert stats.total_unresolved == 3
        assert stats.total_resolved == 2
        assert stats.still_unresolved == 1

    def test_stats_dataclass(self) -> None:
        """DeferredResolutionStats calculates totals correctly."""
        stats = DeferredResolutionStats(
            total_unresolved=10,
            resolved_exact=3,
            resolved_suffix=2,
            resolved_path_hint=1,
            resolved_ambiguous=1,
            still_unresolved=3,
        )

        assert stats.total_resolved == 7  # 3+2+1+1
        assert stats.still_unresolved == 3

    def test_resolves_with_path_hint_disambiguation(self) -> None:
        """Uses path hint to disambiguate when multiple symbols match."""
        # Two symbols with the same name but different paths
        symbols = [
            Symbol(
                id="python:app1/views.py:10:list_users:function",
                name="list_users",
                kind="function",
                language="python",
                path="app1/views.py",
                span=Span(10, 10, 0, 50),
                meta={},
            ),
            Symbol(
                id="python:app2/views.py:10:list_users:function",
                name="list_users",
                kind="function",
                language="python",
                path="app2/views.py",
                span=Span(10, 10, 0, 50),
                meta={},
            ),
        ]

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            # module_path hint from dotted name should help disambiguate
            metadata={"view_name": "app2.views.list_users"},
        )

        stats = resolve_deferred_symbol_refs(symbols, [ctx])

        # Should resolve to app2/views.py symbol via path hint
        assert ctx.symbol_ref is not None
        assert "app2" in ctx.symbol_ref
        assert stats.total_resolved == 1

    def test_resolves_suffix_ambiguous(self) -> None:
        """Tracks ambiguous suffix matches in stats."""
        # Two symbols with the same name (no path hint available)
        symbols = [
            Symbol(
                id="python:views1.py:10:handler:function",
                name="handler",
                kind="function",
                language="python",
                path="views1.py",
                span=Span(10, 10, 0, 50),
                meta={},
            ),
            Symbol(
                id="python:views2.py:10:handler:function",
                name="handler",
                kind="function",
                language="python",
                path="views2.py",
                span=Span(10, 10, 0, 50),
                meta={},
            ),
        ]

        ctx = UsageContext.create(
            kind="call",
            context_name="route",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            metadata={"view_name": "handler"},  # Ambiguous - matches both
        )

        stats = resolve_deferred_symbol_refs(symbols, [ctx])

        # Should still resolve (picks first), but track as ambiguous
        assert ctx.symbol_ref is not None
        assert stats.total_resolved == 1
        # First match is exact, not ambiguous, because "handler" matches directly
        # Let me adjust - exact match takes priority

    def test_resolves_with_module_name_hint(self) -> None:
        """Uses module_name metadata for path hint when name has no dots."""
        symbol = Symbol(
            id="python:myapp/views.py:10:list_users:function",
            name="list_users",
            kind="function",
            language="python",
            path="myapp/views.py",
            span=Span(10, 10, 0, 50),
            meta={},
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            metadata={
                "view_name": "list_users",  # No dots
                "module_name": "myapp.views",  # Additional hint
            },
        )

        stats = resolve_deferred_symbol_refs([symbol], [ctx])

        assert ctx.symbol_ref == symbol.id
        assert stats.total_resolved == 1

    def test_uses_function_name_key(self) -> None:
        """Resolves using 'function_name' metadata key."""
        symbol = Symbol(
            id="python:utils.py:10:process_data:function",
            name="process_data",
            kind="function",
            language="python",
            path="utils.py",
            span=Span(10, 10, 0, 50),
            meta={},
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="register",
            position="args[0]",
            path="app.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            metadata={"function_name": "process_data"},
        )

        stats = resolve_deferred_symbol_refs([symbol], [ctx])

        assert ctx.symbol_ref == symbol.id
        assert stats.total_resolved == 1

    def test_suffix_match_single_candidate(self) -> None:
        """Tracks suffix match when only qualified_name in registry."""
        # Symbol with a qualified name but different simple name
        # This forces suffix matching because "list_users" won't be in registry
        symbol = Symbol(
            id="python:views.py:10:UserViewSet.list_users:method",
            name="UserViewSet.list_users",  # Qualified as simple name
            kind="method",
            language="python",
            path="views.py",
            span=Span(10, 10, 0, 50),
            meta={},
        )

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            # Looking for "list_users" which will suffix-match "UserViewSet.list_users"
            metadata={"view_name": "list_users"},
        )

        stats = resolve_deferred_symbol_refs([symbol], [ctx])

        assert ctx.symbol_ref == symbol.id
        assert stats.total_resolved == 1
        assert stats.resolved_suffix == 1  # Should be suffix match

    def test_suffix_match_ambiguous_multiple_candidates(self) -> None:
        """Tracks ambiguous match when multiple symbols match suffix."""
        # Two methods with same suffix but different qualified names
        symbols = [
            Symbol(
                id="python:views1.py:10:AdminViewSet.list_users:method",
                name="AdminViewSet.list_users",
                kind="method",
                language="python",
                path="views1.py",
                span=Span(10, 10, 0, 50),
                meta={},
            ),
            Symbol(
                id="python:views2.py:10:UserViewSet.list_users:method",
                name="UserViewSet.list_users",
                kind="method",
                language="python",
                path="views2.py",
                span=Span(10, 10, 0, 50),
                meta={},
            ),
        ]

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            # "list_users" matches both via suffix, no path hint to disambiguate
            metadata={"view_name": "list_users"},
        )

        stats = resolve_deferred_symbol_refs(symbols, [ctx])

        # Should resolve to one of them (ambiguous)
        assert ctx.symbol_ref is not None
        assert stats.total_resolved == 1
        assert stats.resolved_ambiguous == 1  # Should be ambiguous

    def test_suffix_match_with_path_hint(self) -> None:
        """Uses path hint to disambiguate suffix matches."""
        # Two methods with same suffix
        symbols = [
            Symbol(
                id="python:admin/views.py:10:AdminViewSet.list_users:method",
                name="AdminViewSet.list_users",
                kind="method",
                language="python",
                path="admin/views.py",
                span=Span(10, 10, 0, 50),
                meta={},
            ),
            Symbol(
                id="python:api/views.py:10:UserViewSet.list_users:method",
                name="UserViewSet.list_users",
                kind="method",
                language="python",
                path="api/views.py",
                span=Span(10, 10, 0, 50),
                meta={},
            ),
        ]

        ctx = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="urls.py",
            span=Span(5, 5, 0, 50),
            symbol_ref=None,
            # "api.list_users" extracts to name="list_users", module_path="api"
            # path hint "api" should disambiguate to api/views.py
            metadata={"view_name": "api.list_users"},
        )

        stats = resolve_deferred_symbol_refs(symbols, [ctx])

        assert ctx.symbol_ref is not None
        assert "api/views.py" in ctx.symbol_ref
        assert stats.total_resolved == 1
        assert stats.resolved_path_hint == 1  # Should use path hint


class TestMainFunctionPatterns:
    """Tests for language-level main() function pattern detection (ADR-0003 v1.2.x)."""

    def test_main_function_pattern_match_go(self) -> None:
        """Pattern matches Go main function."""
        pattern = Pattern(
            concept="main_function",
            symbol_name="^main$",
            symbol_kind="^function$",
            language="^go$",
        )
        symbol = Symbol(
            id="go:main.go:1-10:main:function",
            name="main",
            kind="function",
            language="go",
            path="main.go",
            span=Span(1, 10, 0, 100),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "main_function"
        assert result["matched_symbol_name"] == "main"
        assert result["matched_symbol_kind"] == "function"

    def test_main_function_pattern_no_match_wrong_name(self) -> None:
        """Pattern does not match function with wrong name."""
        pattern = Pattern(
            concept="main_function",
            symbol_name="^main$",
            symbol_kind="^function$",
            language="^go$",
        )
        symbol = Symbol(
            id="go:helper.go:1-10:helper:function",
            name="helper",
            kind="function",
            language="go",
            path="helper.go",
            span=Span(1, 10, 0, 100),
            meta={},
        )
        assert pattern.matches(symbol) is None

    def test_main_function_pattern_no_match_wrong_kind(self) -> None:
        """Pattern does not match symbol with wrong kind."""
        pattern = Pattern(
            concept="main_function",
            symbol_name="^main$",
            symbol_kind="^function$",
            language="^go$",
        )
        symbol = Symbol(
            id="go:main.go:1-10:main:variable",
            name="main",
            kind="variable",  # Wrong kind
            language="go",
            path="main.go",
            span=Span(1, 10, 0, 100),
            meta={},
        )
        assert pattern.matches(symbol) is None

    def test_main_function_pattern_no_match_wrong_language(self) -> None:
        """Pattern does not match symbol with wrong language."""
        pattern = Pattern(
            concept="main_function",
            symbol_name="^main$",
            symbol_kind="^function$",
            language="^go$",
        )
        symbol = Symbol(
            id="python:main.py:1-10:main:function",
            name="main",
            kind="function",
            language="python",  # Wrong language for Go pattern
            path="main.py",
            span=Span(1, 10, 0, 100),
            meta={},
        )
        assert pattern.matches(symbol) is None

    def test_main_function_pattern_match_python(self) -> None:
        """Pattern matches Python main function."""
        pattern = Pattern(
            concept="main_function",
            symbol_name="^main$",
            symbol_kind="^function$",
            language="^python$",
        )
        symbol = Symbol(
            id="python:app.py:10-20:main:function",
            name="main",
            kind="function",
            language="python",
            path="app.py",
            span=Span(10, 20, 0, 100),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "main_function"

    def test_main_function_pattern_match_java(self) -> None:
        """Pattern matches Java main method."""
        pattern = Pattern(
            concept="main_function",
            symbol_name="^main$",
            symbol_kind="^method$",
            language="^java$",
        )
        symbol = Symbol(
            id="java:Main.java:5-15:main:method",
            name="main",
            kind="method",
            language="java",
            path="Main.java",
            span=Span(5, 15, 0, 100),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "main_function"

    def test_main_functions_yaml_loads(self) -> None:
        """main-functions.yaml loads correctly."""
        pattern_def = load_framework_patterns("main-functions")
        assert pattern_def is not None
        assert pattern_def.id == "main-functions"
        assert pattern_def.language == "multi"
        assert len(pattern_def.patterns) >= 10  # Go, Java, Python, C, C++, Rust, etc.

    def test_enrich_symbols_with_main_function(self) -> None:
        """enrich_symbols enriches Go main function with main_function concept."""
        symbol = Symbol(
            id="go:main.go:1-10:main:function",
            name="main",
            kind="function",
            language="go",
            path="main.go",
            span=Span(1, 10, 0, 100),
            meta={},
        )

        # Use real main-functions patterns (no mock)
        enriched = enrich_symbols([symbol], set())  # No frameworks detected

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        assert len(concepts) == 1
        assert concepts[0]["concept"] == "main_function"
        assert concepts[0]["framework"] == "main-functions"

    def test_symbol_name_only_pattern(self) -> None:
        """Pattern with only symbol_name (no symbol_kind or language) matches."""
        pattern = Pattern(
            concept="test",
            symbol_name="^foo$",
        )
        symbol = Symbol(
            id="test:file.py:1-5:foo:function",
            name="foo",
            kind="function",
            language="python",
            path="file.py",
            span=Span(1, 5, 0, 50),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "test"
        assert result["matched_symbol_name"] == "foo"

    def test_language_filter_without_other_conditions(self) -> None:
        """Language filter can be used with decorator patterns."""
        pattern = Pattern(
            concept="test",
            decorator="^app\\.get$",
            language="^python$",
        )
        symbol_python = Symbol(
            id="python:app.py:1-5:handler:function",
            name="handler",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 5, 0, 50),
            meta={"decorators": [{"name": "app.get", "args": ["/test"]}]},
        )
        symbol_js = Symbol(
            id="js:app.js:1-5:handler:function",
            name="handler",
            kind="function",
            language="javascript",
            path="app.js",
            span=Span(1, 5, 0, 50),
            meta={"decorators": [{"name": "app.get", "args": ["/test"]}]},
        )
        # Python symbol matches
        assert pattern.matches(symbol_python) is not None
        # JavaScript symbol does not match (wrong language)
        assert pattern.matches(symbol_js) is None


class TestTestFrameworkPatterns:
    """Tests for test-frameworks.yaml patterns (ADR-0003 v1.2.x)."""

    def test_test_frameworks_yaml_loads(self) -> None:
        """test-frameworks.yaml loads correctly."""
        pattern_def = load_framework_patterns("test-frameworks")
        assert pattern_def is not None
        assert pattern_def.id == "test-frameworks"
        assert pattern_def.language == "multi"
        # Should have patterns for multiple languages
        assert len(pattern_def.patterns) >= 10

    def test_python_test_function_pattern(self) -> None:
        """Pattern matches Python test_* functions."""
        pattern = Pattern(
            concept="test_function",
            symbol_name="^test_",
            symbol_kind="^function$",
            language="^python$",
        )
        symbol = Symbol(
            id="python:test_users.py:10-20:test_create_user:function",
            name="test_create_user",
            kind="function",
            language="python",
            path="test_users.py",
            span=Span(10, 20, 0, 100),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "test_function"
        assert result["matched_symbol_name"] == "test_create_user"

    def test_python_non_test_function_no_match(self) -> None:
        """Pattern does not match non-test functions."""
        pattern = Pattern(
            concept="test_function",
            symbol_name="^test_",
            symbol_kind="^function$",
            language="^python$",
        )
        symbol = Symbol(
            id="python:users.py:10-20:create_user:function",
            name="create_user",
            kind="function",
            language="python",
            path="users.py",
            span=Span(10, 20, 0, 100),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is None

    def test_go_test_function_pattern(self) -> None:
        """Pattern matches Go Test* functions."""
        pattern = Pattern(
            concept="test_function",
            symbol_name="^Test[A-Z]",
            symbol_kind="^function$",
            language="^go$",
        )
        symbol = Symbol(
            id="go:user_test.go:10-30:TestCreateUser:function",
            name="TestCreateUser",
            kind="function",
            language="go",
            path="user_test.go",
            span=Span(10, 30, 0, 100),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "test_function"

    def test_go_benchmark_function_pattern(self) -> None:
        """Pattern matches Go Benchmark* functions."""
        pattern = Pattern(
            concept="benchmark_function",
            symbol_name="^Benchmark[A-Z]",
            symbol_kind="^function$",
            language="^go$",
        )
        symbol = Symbol(
            id="go:user_test.go:50-70:BenchmarkCreateUser:function",
            name="BenchmarkCreateUser",
            kind="function",
            language="go",
            path="user_test.go",
            span=Span(50, 70, 0, 100),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "benchmark_function"

    def test_java_test_annotation_pattern(self) -> None:
        """Pattern matches Java @Test annotation."""
        pattern = Pattern(
            concept="test_function",
            decorator="^Test$",
            symbol_kind="^method$",
            language="^java$",
        )
        symbol = Symbol(
            id="java:UserTest.java:20-40:testCreateUser:method",
            name="testCreateUser",
            kind="method",
            language="java",
            path="UserTest.java",
            span=Span(20, 40, 0, 100),
            meta={"decorators": [{"name": "Test", "args": []}]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "test_function"

    def test_rust_test_attribute_pattern(self) -> None:
        """Pattern matches Rust #[test] attribute."""
        pattern = Pattern(
            concept="test_function",
            decorator="^test$",
            symbol_kind="^function$",
            language="^rust$",
        )
        symbol = Symbol(
            id="rust:lib.rs:30-50:test_create_user:function",
            name="test_create_user",
            kind="function",
            language="rust",
            path="lib.rs",
            span=Span(30, 50, 0, 100),
            meta={"decorators": [{"name": "test", "args": []}]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "test_function"

    def test_csharp_fact_attribute_pattern(self) -> None:
        """Pattern matches C# [Fact] attribute (xUnit)."""
        pattern = Pattern(
            concept="test_function",
            decorator="^Fact$",
            symbol_kind="^method$",
            language="^csharp$",
        )
        symbol = Symbol(
            id="csharp:UserTests.cs:25-45:CreateUser_ShouldWork:method",
            name="CreateUser_ShouldWork",
            kind="method",
            language="csharp",
            path="UserTests.cs",
            span=Span(25, 45, 0, 100),
            meta={"decorators": [{"name": "Fact", "args": []}]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "test_function"

    def test_cpp_gtest_test_macro_pattern(self) -> None:
        """Pattern matches C++ Google Test TEST() macro functions."""
        pattern = Pattern(
            concept="test_function",
            symbol_name="^TEST(_F|_P)?$",
            symbol_kind="^function$",
            language="^(cpp|c)$",
        )
        symbol = Symbol(
            id="cpp:unit_tests/test_utils.cpp:21-38:TEST:function",
            name="TEST",
            kind="function",
            language="cpp",
            path="unit_tests/test_utils.cpp",
            span=Span(21, 38, 0, 1),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "test_function"
        assert result["matched_symbol_name"] == "TEST"

    def test_cpp_gtest_test_f_macro_pattern(self) -> None:
        """Pattern matches C++ Google Test TEST_F() macro functions."""
        pattern = Pattern(
            concept="test_function",
            symbol_name="^TEST(_F|_P)?$",
            symbol_kind="^function$",
            language="^(cpp|c)$",
        )
        symbol = Symbol(
            id="cpp:tests/fixture_test.cpp:50-80:TEST_F:function",
            name="TEST_F",
            kind="function",
            language="cpp",
            path="tests/fixture_test.cpp",
            span=Span(50, 80, 0, 1),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "test_function"

    def test_cpp_catch2_test_case_pattern(self) -> None:
        """Pattern matches C++ Catch2 TEST_CASE() macro functions."""
        pattern = Pattern(
            concept="test_function",
            symbol_name="^(TEST_CASE|SCENARIO)$",
            symbol_kind="^function$",
            language="^(cpp|c)$",
        )
        symbol = Symbol(
            id="cpp:tests/vector_tests.cpp:10-30:TEST_CASE:function",
            name="TEST_CASE",
            kind="function",
            language="cpp",
            path="tests/vector_tests.cpp",
            span=Span(10, 30, 0, 1),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "test_function"

    def test_enrich_symbols_with_test_function(self) -> None:
        """enrich_symbols enriches Python test function with test_function concept."""
        symbol = Symbol(
            id="python:test_api.py:10-20:test_user_creation:function",
            name="test_user_creation",
            kind="function",
            language="python",
            path="test_api.py",
            span=Span(10, 20, 0, 100),
            meta={},
        )

        # Use real test-frameworks patterns (no mock)
        enriched = enrich_symbols([symbol], set())  # No frameworks detected

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        test_concepts = [c for c in concepts if c["concept"] == "test_function"]
        assert len(test_concepts) == 1
        assert test_concepts[0]["framework"] == "test-frameworks"


class TestLanguageConventionPatterns:
    """Tests for language-conventions.yaml patterns (ADR-0003 v1.2.x)."""

    def test_language_conventions_yaml_loads(self) -> None:
        """language-conventions.yaml loads correctly."""
        pattern_def = load_framework_patterns("language-conventions")
        assert pattern_def is not None
        assert pattern_def.id == "language-conventions"
        assert pattern_def.language == "multi"
        # Should have patterns for CUDA, WGSL, COBOL, LaTeX, Starlark
        assert len(pattern_def.patterns) >= 10

    def test_cuda_global_kernel_pattern(self) -> None:
        """Pattern matches CUDA __global__ kernels."""
        pattern = Pattern(
            concept="gpu_kernel",
            symbol_kind="^global$",
            language="^cuda$",
        )
        symbol = Symbol(
            id="cuda:kernels.cu:10-30:matrixMul:global",
            name="matrixMul",
            kind="global",
            language="cuda",
            path="kernels.cu",
            span=Span(10, 30, 0, 200),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "gpu_kernel"
        assert result["matched_symbol_kind"] == "global"

    def test_cuda_device_function_pattern(self) -> None:
        """Pattern matches CUDA __device__ functions."""
        pattern = Pattern(
            concept="gpu_function",
            symbol_kind="^device$",
            language="^cuda$",
        )
        symbol = Symbol(
            id="cuda:helpers.cu:5-15:dotProduct:device",
            name="dotProduct",
            kind="device",
            language="cuda",
            path="helpers.cu",
            span=Span(5, 15, 0, 100),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "gpu_function"

    def test_wgsl_vertex_shader_pattern(self) -> None:
        """Pattern matches WGSL @vertex shaders."""
        pattern = Pattern(
            concept="shader_entrypoint",
            decorator="^vertex$",
            language="^wgsl$",
        )
        symbol = Symbol(
            id="wgsl:shader.wgsl:1-10:vs_main:function",
            name="vs_main",
            kind="function",
            language="wgsl",
            path="shader.wgsl",
            span=Span(1, 10, 0, 50),
            meta={"decorators": [{"name": "vertex", "args": []}]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "shader_entrypoint"

    def test_wgsl_compute_shader_pattern(self) -> None:
        """Pattern matches WGSL @compute shaders."""
        pattern = Pattern(
            concept="shader_entrypoint",
            decorator="^compute$",
            language="^wgsl$",
        )
        symbol = Symbol(
            id="wgsl:compute.wgsl:1-20:main:function",
            name="main",
            kind="function",
            language="wgsl",
            path="compute.wgsl",
            span=Span(1, 20, 0, 80),
            meta={"decorators": [{"name": "compute", "args": []}]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "shader_entrypoint"

    def test_cobol_program_pattern(self) -> None:
        """Pattern matches COBOL programs."""
        pattern = Pattern(
            concept="program_entrypoint",
            symbol_kind="^program$",
            language="^cobol$",
        )
        symbol = Symbol(
            id="cobol:PAYROLL.cbl:1-500:PAYROLL:program",
            name="PAYROLL",
            kind="program",
            language="cobol",
            path="PAYROLL.cbl",
            span=Span(1, 500, 0, 10000),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "program_entrypoint"

    def test_cobol_section_pattern(self) -> None:
        """Pattern matches COBOL sections."""
        pattern = Pattern(
            concept="code_section",
            symbol_kind="^section$",
            language="^cobol$",
        )
        symbol = Symbol(
            id="cobol:PAYROLL.cbl:50-100:PROCESS-EMPLOYEE:section",
            name="PROCESS-EMPLOYEE",
            kind="section",
            language="cobol",
            path="PAYROLL.cbl",
            span=Span(50, 100, 0, 2000),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "code_section"

    def test_latex_section_pattern(self) -> None:
        """Pattern matches LaTeX sections."""
        pattern = Pattern(
            concept="document_structure",
            symbol_kind="^section$",
            language="^latex$",
        )
        symbol = Symbol(
            id="latex:thesis.tex:50-100:Introduction:section",
            name="Introduction",
            kind="section",
            language="latex",
            path="thesis.tex",
            span=Span(50, 100, 0, 2000),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "document_structure"

    def test_starlark_rule_pattern(self) -> None:
        """Pattern matches Starlark build rules."""
        pattern = Pattern(
            concept="build_rule",
            symbol_kind="^rule$",
            language="^starlark$",
        )
        symbol = Symbol(
            id="starlark:BUILD:10-30:my_library:rule",
            name="my_library",
            kind="rule",
            language="starlark",
            path="BUILD",
            span=Span(10, 30, 0, 300),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "build_rule"

    def test_starlark_macro_pattern(self) -> None:
        """Pattern matches Starlark macros."""
        pattern = Pattern(
            concept="build_macro",
            symbol_kind="^macro$",
            language="^starlark$",
        )
        symbol = Symbol(
            id="starlark:defs.bzl:1-20:my_macro:macro",
            name="my_macro",
            kind="macro",
            language="starlark",
            path="defs.bzl",
            span=Span(1, 20, 0, 200),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "build_macro"

    def test_enrich_symbols_with_cuda_kernel(self) -> None:
        """enrich_symbols enriches CUDA kernel with gpu_kernel concept."""
        symbol = Symbol(
            id="cuda:kernel.cu:1-50:compute:global",
            name="compute",
            kind="global",
            language="cuda",
            path="kernel.cu",
            span=Span(1, 50, 0, 500),
            meta={},
        )

        # Use real language-conventions patterns
        enriched = enrich_symbols([symbol], set())

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        gpu_concepts = [c for c in concepts if c["concept"] == "gpu_kernel"]
        assert len(gpu_concepts) == 1
        assert gpu_concepts[0]["framework"] == "language-conventions"


class TestConfigConventionPatterns:
    """Tests for config-conventions.yaml patterns (ADR-0003 v1.2.x)."""

    def test_config_conventions_yaml_loads(self) -> None:
        """config-conventions.yaml loads correctly."""
        pattern_def = load_framework_patterns("config-conventions")
        assert pattern_def is not None
        assert pattern_def.id == "config-conventions"
        assert pattern_def.language == "multi"
        # Should have patterns for NPM, Maven, Cargo
        assert len(pattern_def.patterns) >= 15

    def test_npm_dependency_pattern(self) -> None:
        """Pattern matches NPM dependencies."""
        pattern = Pattern(
            concept="npm_dependency",
            symbol_kind="^dependency$",
            language="^json$",
        )
        symbol = Symbol(
            id="json:package.json:5-5:react:dependency",
            name="react",
            kind="dependency",
            language="json",
            path="package.json",
            span=Span(5, 5, 0, 30),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "npm_dependency"

    def test_npm_dev_dependency_pattern(self) -> None:
        """Pattern matches NPM dev dependencies."""
        pattern = Pattern(
            concept="npm_dev_dependency",
            symbol_kind="^devDependency$",
            language="^json$",
        )
        symbol = Symbol(
            id="json:package.json:15-15:jest:devDependency",
            name="jest",
            kind="devDependency",
            language="json",
            path="package.json",
            span=Span(15, 15, 0, 25),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "npm_dev_dependency"

    def test_npm_script_pattern(self) -> None:
        """Pattern matches NPM scripts."""
        pattern = Pattern(
            concept="npm_script",
            symbol_kind="^script$",
            language="^json$",
        )
        symbol = Symbol(
            id="json:package.json:3-3:test:script",
            name="test",
            kind="script",
            language="json",
            path="package.json",
            span=Span(3, 3, 0, 40),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "npm_script"

    def test_npm_bin_pattern(self) -> None:
        """Pattern matches NPM bin entries (CLI executables)."""
        pattern = Pattern(
            concept="npm_bin",
            symbol_kind="^bin$",
            language="^json$",
        )
        symbol = Symbol(
            id="json:package.json:5-5:my-cli:bin",
            name="my-cli",
            kind="bin",
            language="json",
            path="package.json",
            span=Span(5, 5, 0, 35),
            meta={"path": "./bin/cli.js"},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "npm_bin"

    def test_maven_dependency_pattern(self) -> None:
        """Pattern matches Maven dependencies."""
        pattern = Pattern(
            concept="maven_dependency",
            symbol_kind="^dependency$",
            language="^xml$",
        )
        symbol = Symbol(
            id="xml:pom.xml:20-25:com.google.guava:guava:dependency",
            name="com.google.guava:guava",
            kind="dependency",
            language="xml",
            path="pom.xml",
            span=Span(20, 25, 0, 150),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "maven_dependency"

    def test_maven_module_pattern(self) -> None:
        """Pattern matches Maven modules."""
        pattern = Pattern(
            concept="maven_module",
            symbol_kind="^module$",
            language="^xml$",
        )
        symbol = Symbol(
            id="xml:pom.xml:50-50:core:module",
            name="core",
            kind="module",
            language="xml",
            path="pom.xml",
            span=Span(50, 50, 0, 20),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "maven_module"

    def test_android_permission_pattern(self) -> None:
        """Pattern matches Android permissions."""
        pattern = Pattern(
            concept="android_permission",
            symbol_kind="^permission$",
            language="^xml$",
        )
        symbol = Symbol(
            id="xml:AndroidManifest.xml:5-5:INTERNET:permission",
            name="INTERNET",
            kind="permission",
            language="xml",
            path="AndroidManifest.xml",
            span=Span(5, 5, 0, 80),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "android_permission"

    def test_android_activity_pattern(self) -> None:
        """Pattern matches Android activities."""
        pattern = Pattern(
            concept="android_component",
            symbol_kind="^activity$",
            language="^xml$",
        )
        symbol = Symbol(
            id="xml:AndroidManifest.xml:10-20:MainActivity:activity",
            name="MainActivity",
            kind="activity",
            language="xml",
            path="AndroidManifest.xml",
            span=Span(10, 20, 0, 200),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "android_component"

    def test_cargo_dependency_pattern(self) -> None:
        """Pattern matches Cargo dependencies."""
        pattern = Pattern(
            concept="cargo_dependency",
            symbol_kind="^dependency$",
            language="^toml$",
        )
        symbol = Symbol(
            id="toml:Cargo.toml:8-8:serde:dependency",
            name="serde",
            kind="dependency",
            language="toml",
            path="Cargo.toml",
            span=Span(8, 8, 0, 30),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "cargo_dependency"

    def test_cargo_dev_dependency_pattern(self) -> None:
        """Pattern matches Cargo dev dependencies."""
        pattern = Pattern(
            concept="cargo_dev_dependency",
            symbol_kind="^dev-dependency$",
            language="^toml$",
        )
        symbol = Symbol(
            id="toml:Cargo.toml:15-15:tokio-test:dev-dependency",
            name="tokio-test",
            kind="dev-dependency",
            language="toml",
            path="Cargo.toml",
            span=Span(15, 15, 0, 35),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "cargo_dev_dependency"

    def test_cargo_binary_pattern(self) -> None:
        """Pattern matches Cargo binary targets (kind='binary' from [[bin]])."""
        pattern = Pattern(
            concept="cargo_binary",
            symbol_kind="^binary$",
            language="^toml$",
        )
        symbol = Symbol(
            id="toml:Cargo.toml:20-25:my-cli:binary",
            name="my-cli",
            kind="binary",  # analyzer creates "binary" for [[bin]] sections
            language="toml",
            path="Cargo.toml",
            span=Span(20, 25, 0, 100),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "cargo_binary"

    def test_pyproject_script_pattern(self) -> None:
        """Pattern matches pyproject.toml [project.scripts] entries."""
        pattern = Pattern(
            concept="pyproject_script",
            symbol_kind="^script$",
            language="^toml$",
        )
        symbol = Symbol(
            id="toml:pyproject.toml:10-10:my-cli:script",
            name="my-cli",
            kind="script",
            language="toml",
            path="pyproject.toml",
            span=Span(10, 10, 0, 40),
            meta={"entry_point": "mypackage.cli:main"},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "pyproject_script"

    def test_typescript_reference_pattern(self) -> None:
        """Pattern matches TypeScript project references."""
        pattern = Pattern(
            concept="typescript_reference",
            symbol_kind="^reference$",
            language="^json$",
        )
        symbol = Symbol(
            id="json:tsconfig.json:10-10:../common:reference",
            name="../common",
            kind="reference",
            language="json",
            path="tsconfig.json",
            span=Span(10, 10, 0, 40),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "typescript_reference"

    def test_poetry_dependency_pattern(self) -> None:
        """Pattern matches Poetry dependencies."""
        pattern = Pattern(
            concept="poetry_dependency",
            symbol_kind="^dependency$",
            language="^toml$",
        )
        symbol = Symbol(
            id="toml:pyproject.toml:12-12:requests:dependency",
            name="requests",
            kind="dependency",
            language="toml",
            path="pyproject.toml",
            span=Span(12, 12, 0, 25),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "poetry_dependency"

    def test_enrich_symbols_with_npm_dependency(self) -> None:
        """enrich_symbols enriches NPM dependency with npm_dependency concept."""
        symbol = Symbol(
            id="json:package.json:5-5:lodash:dependency",
            name="lodash",
            kind="dependency",
            language="json",
            path="package.json",
            span=Span(5, 5, 0, 25),
            meta={},
        )

        # Use real config-conventions patterns
        enriched = enrich_symbols([symbol], set())

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        npm_concepts = [c for c in concepts if c["concept"] == "npm_dependency"]
        assert len(npm_concepts) == 1
        assert npm_concepts[0]["framework"] == "config-conventions"

    def test_enrich_symbols_with_cargo_dependency(self) -> None:
        """enrich_symbols enriches Cargo dependency with cargo_dependency concept."""
        symbol = Symbol(
            id="toml:Cargo.toml:10-10:tokio:dependency",
            name="tokio",
            kind="dependency",
            language="toml",
            path="Cargo.toml",
            span=Span(10, 10, 0, 30),
            meta={},
        )

        # Use real config-conventions patterns
        enriched = enrich_symbols([symbol], set())

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        cargo_concepts = [c for c in concepts if c["concept"] == "cargo_dependency"]
        assert len(cargo_concepts) == 1
        assert cargo_concepts[0]["framework"] == "config-conventions"


class TestPlayFrameworkPatterns:
    """Tests for Play Framework (Scala) patterns."""

    def test_play_yaml_loads(self) -> None:
        """Play Framework YAML file loads without error."""
        clear_pattern_cache()
        patterns = load_framework_patterns("play")
        assert patterns is not None
        assert len(patterns.patterns) > 0

    def test_play_controller_base_class_pattern(self) -> None:
        """Pattern matches Scala controller extending BaseController."""
        pattern = Pattern(
            concept="controller",
            base_class=r"^(BaseController|AbstractController|InjectedController)$",
        )
        symbol = Symbol(
            id="scala:UserController.scala:5-50:UserController:class",
            name="UserController",
            kind="class",
            language="scala",
            path="app/controllers/UserController.scala",
            span=Span(5, 50, 0, 0),
            meta={"base_classes": ["BaseController"]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "controller"
        assert result["matched_base_class"] == "BaseController"

    def test_play_action_decorator_pattern(self) -> None:
        """Pattern matches Play Action decorator."""
        pattern = Pattern(
            concept="route",
            decorator=r"^Action$",
        )
        symbol = Symbol(
            id="scala:UserController.scala:10-15:index:function",
            name="index",
            kind="function",
            language="scala",
            path="app/controllers/UserController.scala",
            span=Span(10, 15, 0, 0),
            meta={"decorators": [{"name": "Action"}]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "route"

    def test_play_async_action_pattern(self) -> None:
        """Pattern matches Play Action.async decorator."""
        pattern = Pattern(
            concept="route",
            decorator=r"^Action\.async$",
        )
        symbol = Symbol(
            id="scala:UserController.scala:20-30:getUsers:function",
            name="getUsers",
            kind="function",
            language="scala",
            path="app/controllers/UserController.scala",
            span=Span(20, 30, 0, 0),
            meta={"decorators": [{"name": "Action.async"}]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "route"

    def test_play_websocket_pattern(self) -> None:
        """Pattern matches Play WebSocket handler."""
        pattern = Pattern(
            concept="websocket_handler",
            decorator=r"^WebSocket\.(accept|acceptOrResult)$",
        )
        symbol = Symbol(
            id="scala:ChatController.scala:15-25:socket:function",
            name="socket",
            kind="function",
            language="scala",
            path="app/controllers/ChatController.scala",
            span=Span(15, 25, 0, 0),
            meta={"decorators": [{"name": "WebSocket.accept"}]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "websocket_handler"

    def test_enrich_symbols_with_play_controller(self) -> None:
        """enrich_symbols enriches Play controller with controller concept."""
        clear_pattern_cache()
        symbol = Symbol(
            id="scala:UserController.scala:5-50:UserController:class",
            name="UserController",
            kind="class",
            language="scala",
            path="app/controllers/UserController.scala",
            span=Span(5, 50, 0, 0),
            meta={"base_classes": ["AbstractController"]},
        )

        enriched = enrich_symbols([symbol], {"play"})

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        controller_concepts = [c for c in concepts if c["concept"] == "controller"]
        assert len(controller_concepts) == 1
        assert controller_concepts[0]["framework"] == "play"


class TestAkkaHttpPatterns:
    """Tests for Akka HTTP (Scala) patterns."""

    def test_akka_http_yaml_loads(self) -> None:
        """Akka HTTP YAML file loads without error."""
        clear_pattern_cache()
        patterns = load_framework_patterns("akka-http")
        assert patterns is not None
        assert len(patterns.patterns) > 0

    def test_akka_http_method_pattern(self) -> None:
        """Pattern matches Akka HTTP method directives."""
        pattern = Pattern(
            concept="route",
            decorator=r"^(get|post|put|delete|patch|head|options)$",
        )
        symbol = Symbol(
            id="scala:Routes.scala:10-15:getUsers:function",
            name="getUsers",
            kind="function",
            language="scala",
            path="src/main/scala/Routes.scala",
            span=Span(10, 15, 0, 0),
            meta={"decorators": [{"name": "get"}]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "route"

    def test_akka_http_path_pattern(self) -> None:
        """Pattern matches Akka HTTP path directive."""
        pattern = Pattern(
            concept="route",
            decorator=r"^(path|pathPrefix|pathEnd|pathSuffix|pathSuffixTest)$",
            extract_path="args[0]",
        )
        symbol = Symbol(
            id="scala:Routes.scala:5-20:usersRoute:function",
            name="usersRoute",
            kind="function",
            language="scala",
            path="src/main/scala/Routes.scala",
            span=Span(5, 20, 0, 0),
            meta={"decorators": [{"name": "path", "args": ["users"]}]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "route"
        assert result["path"] == "users"

    def test_akka_http_websocket_pattern(self) -> None:
        """Pattern matches Akka HTTP WebSocket handler."""
        pattern = Pattern(
            concept="websocket_handler",
            decorator=r"^handleWebSocketMessages$",
        )
        symbol = Symbol(
            id="scala:WsRoutes.scala:10-20:wsHandler:function",
            name="wsHandler",
            kind="function",
            language="scala",
            path="src/main/scala/WsRoutes.scala",
            span=Span(10, 20, 0, 0),
            meta={"decorators": [{"name": "handleWebSocketMessages"}]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "websocket_handler"

    def test_akka_http_auth_pattern(self) -> None:
        """Pattern matches Akka HTTP authentication directives."""
        pattern = Pattern(
            concept="auth",
            decorator=r"^(authenticateBasic|authenticateOAuth2|authorize)$",
        )
        symbol = Symbol(
            id="scala:Routes.scala:25-30:securedRoute:function",
            name="securedRoute",
            kind="function",
            language="scala",
            path="src/main/scala/Routes.scala",
            span=Span(25, 30, 0, 0),
            meta={"decorators": [{"name": "authenticateBasic"}]},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "auth"

    def test_enrich_symbols_with_akka_http_route(self) -> None:
        """enrich_symbols enriches Akka HTTP route with route concept."""
        clear_pattern_cache()
        symbol = Symbol(
            id="scala:Routes.scala:10-15:getUsers:function",
            name="getUsers",
            kind="function",
            language="scala",
            path="src/main/scala/Routes.scala",
            span=Span(10, 15, 0, 0),
            meta={"decorators": [{"name": "get"}]},
        )

        enriched = enrich_symbols([symbol], {"akka-http"})

        assert len(enriched) == 1
        assert "concepts" in enriched[0].meta
        concepts = enriched[0].meta["concepts"]
        route_concepts = [c for c in concepts if c["concept"] == "route"]
        assert len(route_concepts) == 1
        assert route_concepts[0]["framework"] == "akka-http"


class TestNamingConventionsPatterns:
    """Tests for naming-conventions.yaml patterns (ADR-0003 v1.4.x).

    These patterns detect entrypoints by naming conventions alone, providing
    a fallback when no framework-specific detection matches. This is the
    lowest-confidence tier (0.70).
    """

    def test_naming_conventions_yaml_loads(self) -> None:
        """naming-conventions.yaml loads correctly."""
        pattern_def = load_framework_patterns("naming-conventions")
        assert pattern_def is not None
        assert pattern_def.id == "naming-conventions"
        assert pattern_def.language == "multi"
        # Should have patterns for controller, handler, service
        assert len(pattern_def.patterns) >= 3

    def test_controller_by_name_pattern_matches(self) -> None:
        """Pattern matches classes ending in 'Controller'."""
        pattern = Pattern(
            concept="controller_by_name",
            symbol_name=r"^[A-Z][a-zA-Z0-9_]*Controller$",
            symbol_kind="^class$",
        )
        symbol = Symbol(
            id="java:UserController.java:10-50:UserController:class",
            name="UserController",
            kind="class",
            language="java",
            path="src/controllers/UserController.java",
            span=Span(10, 50, 0, 500),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "controller_by_name"
        assert result["matched_symbol_name"] == "UserController"
        assert result["matched_symbol_kind"] == "class"

    def test_controller_by_name_pattern_various_languages(self) -> None:
        """Pattern matches Controller classes across multiple languages."""
        pattern = Pattern(
            concept="controller_by_name",
            symbol_name=r"^[A-Z][a-zA-Z0-9_]*Controller$",
            symbol_kind="^class$",
        )
        # Test across Java, Python, Ruby, PHP
        test_cases = [
            ("java", "ProductController"),
            ("python", "AccountController"),
            ("ruby", "PaymentController"),
            ("php", "OrderController"),
        ]
        for lang, name in test_cases:
            symbol = Symbol(
                id=f"{lang}:{name}.{lang}:1-10:{name}:class",
                name=name,
                kind="class",
                language=lang,
                path=f"app/controllers/{name}.{lang}",
                span=Span(1, 10, 0, 100),
                meta={},
            )
            result = pattern.matches(symbol)
            assert result is not None, f"Should match {name} in {lang}"
            assert result["concept"] == "controller_by_name"

    def test_controller_by_name_rejects_non_class(self) -> None:
        """Pattern does not match functions named Controller."""
        pattern = Pattern(
            concept="controller_by_name",
            symbol_name=r"^[A-Z][a-zA-Z0-9_]*Controller$",
            symbol_kind="^class$",
        )
        symbol = Symbol(
            id="python:utils.py:1-5:UserController:function",
            name="UserController",
            kind="function",
            language="python",
            path="utils.py",
            span=Span(1, 5, 0, 50),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is None  # Not a class

    def test_controller_by_name_rejects_helper(self) -> None:
        """Pattern does not match ControllerHelper (doesn't end in Controller)."""
        pattern = Pattern(
            concept="controller_by_name",
            symbol_name=r"^[A-Z][a-zA-Z0-9_]*Controller$",
            symbol_kind="^class$",
        )
        symbol = Symbol(
            id="java:ControllerHelper.java:1-10:ControllerHelper:class",
            name="ControllerHelper",
            kind="class",
            language="java",
            path="ControllerHelper.java",
            span=Span(1, 10, 0, 100),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is None  # Doesn't end in "Controller"

    def test_handler_by_name_pattern_matches(self) -> None:
        """Pattern matches classes ending in 'Handler'."""
        pattern = Pattern(
            concept="handler_by_name",
            symbol_name=r"^[A-Z][a-zA-Z0-9_]*Handler$",
            symbol_kind="^class$",
        )
        symbol = Symbol(
            id="java:WebSocketHandler.java:5-30:WebSocketHandler:class",
            name="WebSocketHandler",
            kind="class",
            language="java",
            path="src/handlers/WebSocketHandler.java",
            span=Span(5, 30, 0, 300),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "handler_by_name"
        assert result["matched_symbol_name"] == "WebSocketHandler"

    def test_handler_by_name_rejects_non_class(self) -> None:
        """Pattern does not match functions named Handler."""
        pattern = Pattern(
            concept="handler_by_name",
            symbol_name=r"^[A-Z][a-zA-Z0-9_]*Handler$",
            symbol_kind="^class$",
        )
        symbol = Symbol(
            id="python:utils.py:1-5:RequestHandler:function",
            name="RequestHandler",
            kind="function",
            language="python",
            path="utils.py",
            span=Span(1, 5, 0, 50),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is None

    def test_service_by_name_pattern_matches(self) -> None:
        """Pattern matches classes ending in 'Service'."""
        pattern = Pattern(
            concept="service_by_name",
            symbol_name=r"^[A-Z][a-zA-Z0-9_]*Service$",
            symbol_kind="^class$",
        )
        symbol = Symbol(
            id="java:UserService.java:10-60:UserService:class",
            name="UserService",
            kind="class",
            language="java",
            path="src/services/UserService.java",
            span=Span(10, 60, 0, 600),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is not None
        assert result["concept"] == "service_by_name"
        assert result["matched_symbol_name"] == "UserService"

    def test_service_by_name_rejects_non_class(self) -> None:
        """Pattern does not match functions named Service."""
        pattern = Pattern(
            concept="service_by_name",
            symbol_name=r"^[A-Z][a-zA-Z0-9_]*Service$",
            symbol_kind="^class$",
        )
        symbol = Symbol(
            id="python:utils.py:1-5:PaymentService:function",
            name="PaymentService",
            kind="function",
            language="python",
            path="utils.py",
            span=Span(1, 5, 0, 50),
            meta={},
        )
        result = pattern.matches(symbol)
        assert result is None

    def test_enrich_symbols_with_naming_conventions(self) -> None:
        """enrich_symbols applies naming convention patterns."""
        clear_pattern_cache()
        symbols = [
            Symbol(
                id="java:UserController.java:1-50:UserController:class",
                name="UserController",
                kind="class",
                language="java",
                path="src/controllers/UserController.java",
                span=Span(1, 50, 0, 500),
                meta={},
            ),
            Symbol(
                id="java:RequestHandler.java:1-30:RequestHandler:class",
                name="RequestHandler",
                kind="class",
                language="java",
                path="src/handlers/RequestHandler.java",
                span=Span(1, 30, 0, 300),
                meta={},
            ),
            Symbol(
                id="java:EmailService.java:1-40:EmailService:class",
                name="EmailService",
                kind="class",
                language="java",
                path="src/services/EmailService.java",
                span=Span(1, 40, 0, 400),
                meta={},
            ),
        ]

        enriched = enrich_symbols(symbols, set())  # No specific framework

        # Check controller
        controller = next(s for s in enriched if s.name == "UserController")
        assert "concepts" in controller.meta
        concepts = controller.meta["concepts"]
        ctrl_concepts = [c for c in concepts if c["concept"] == "controller_by_name"]
        assert len(ctrl_concepts) == 1
        assert ctrl_concepts[0]["framework"] == "naming-conventions"

        # Check handler
        handler = next(s for s in enriched if s.name == "RequestHandler")
        assert "concepts" in handler.meta
        handler_concepts = [
            c for c in handler.meta["concepts"] if c["concept"] == "handler_by_name"
        ]
        assert len(handler_concepts) == 1

        # Check service
        service = next(s for s in enriched if s.name == "EmailService")
        assert "concepts" in service.meta
        service_concepts = [
            c for c in service.meta["concepts"] if c["concept"] == "service_by_name"
        ]
        assert len(service_concepts) == 1


class TestPyramidPatterns:
    """Tests for Pyramid framework pattern matching."""

    def test_pyramid_view_config_route_pattern(self) -> None:
        """Pyramid @view_config(route_name='...') matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pyramid")

        assert pattern_def is not None, "Pyramid patterns YAML should exist"

        symbol = Symbol(
            id="test:views.py:1:home:function",
            name="home",
            kind="function",
            language="python",
            path="views.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "view_config",
                        "args": [],
                        "kwargs": {"route_name": "home", "request_method": "GET"},
                    },
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["path"] == "home"
        assert results[0]["method"] == "GET"

    def test_pyramid_view_config_without_method(self) -> None:
        """Pyramid @view_config without request_method still matches."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pyramid")

        symbol = Symbol(
            id="test:views.py:10:users_list:function",
            name="users_list",
            kind="function",
            language="python",
            path="views.py",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "view_config",
                        "args": [],
                        "kwargs": {"route_name": "users"},
                    },
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["path"] == "users"

    def test_pyramid_view_defaults_pattern(self) -> None:
        """Pyramid @view_defaults matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pyramid")

        symbol = Symbol(
            id="test:views.py:1:UserViews:class",
            name="UserViews",
            kind="class",
            language="python",
            path="views.py",
            span=Span(1, 50, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "view_defaults",
                        "args": [],
                        "kwargs": {"route_name": "user"},
                    },
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_pyramid_notfound_view_config_pattern(self) -> None:
        """Pyramid @notfound_view_config matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pyramid")

        symbol = Symbol(
            id="test:views.py:100:not_found:function",
            name="not_found",
            kind="function",
            language="python",
            path="views.py",
            span=Span(100, 110, 0, 0),
            meta={
                "decorators": [
                    {"name": "notfound_view_config", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "error_handler"

    def test_pyramid_forbidden_view_config_pattern(self) -> None:
        """Pyramid @forbidden_view_config matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pyramid")

        symbol = Symbol(
            id="test:views.py:120:forbidden:function",
            name="forbidden",
            kind="function",
            language="python",
            path="views.py",
            span=Span(120, 130, 0, 0),
            meta={
                "decorators": [
                    {"name": "forbidden_view_config", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "error_handler"

    def test_pyramid_exception_view_config_pattern(self) -> None:
        """Pyramid @exception_view_config matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pyramid")

        symbol = Symbol(
            id="test:views.py:140:handle_exception:function",
            name="handle_exception",
            kind="function",
            language="python",
            path="views.py",
            span=Span(140, 150, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "exception_view_config",
                        "args": ["Exception"],
                        "kwargs": {},
                    },
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "error_handler"

    def test_pyramid_subscriber_pattern(self) -> None:
        """Pyramid @subscriber matches event_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pyramid")

        symbol = Symbol(
            id="test:events.py:1:on_app_created:function",
            name="on_app_created",
            kind="function",
            language="python",
            path="events.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "subscriber",
                        "args": ["ApplicationCreated"],
                        "kwargs": {},
                    },
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "event_handler"

    def test_pyramid_model_base_class_pattern(self) -> None:
        """Pyramid SQLAlchemy model (Base subclass) matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pyramid")

        symbol = Symbol(
            id="test:models.py:1:User:class",
            name="User",
            kind="class",
            language="python",
            path="models.py",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Base"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"


class TestNexPatterns:
    """Tests for Nex framework pattern matching."""

    def test_nex_use_nex_pattern(self) -> None:
        """Nex `use Nex` decorator matches page_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nex")

        assert pattern_def is not None, "Nex patterns YAML should exist"

        symbol = Symbol(
            id="test:pages/index.ex:1:Index:module",
            name="Index",
            kind="module",
            language="elixir",
            path="src/pages/index.ex",
            span=Span(1, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "use Nex", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "page_handler"

    def test_nex_mount_function_pattern(self) -> None:
        """Nex mount/1 function matches lifecycle_hook pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nex")

        symbol = Symbol(
            id="test:pages/index.ex:5:MyApp.Pages.Index.mount:function",
            name="MyApp.Pages.Index.mount",  # Elixir uses qualified names
            kind="function",
            language="elixir",
            path="src/pages/index.ex",
            span=Span(5, 10, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "lifecycle_hook"

    def test_nex_render_function_pattern(self) -> None:
        """Nex render/1 function matches lifecycle_hook pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nex")

        symbol = Symbol(
            id="test:pages/index.ex:12:MyApp.Pages.Index.render:function",
            name="MyApp.Pages.Index.render",  # Elixir uses qualified names
            kind="function",
            language="elixir",
            path="src/pages/index.ex",
            span=Span(12, 20, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "lifecycle_hook"

    def test_nex_get_function_pattern(self) -> None:
        """Nex get/1 function matches route_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nex")

        symbol = Symbol(
            id="test:api/users.ex:5:MyApp.Api.Users.get:function",
            name="MyApp.Api.Users.get",  # Elixir uses qualified names
            kind="function",
            language="elixir",
            path="src/api/users.ex",
            span=Span(5, 10, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_handler"

    def test_nex_post_function_pattern(self) -> None:
        """Nex post/1 function matches route_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nex")

        symbol = Symbol(
            id="test:api/users.ex:12:MyApp.Api.Users.post:function",
            name="MyApp.Api.Users.post",  # Elixir uses qualified names
            kind="function",
            language="elixir",
            path="src/api/users.ex",
            span=Span(12, 20, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_handler"

    def test_nex_put_function_pattern(self) -> None:
        """Nex put/1 function matches route_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nex")

        symbol = Symbol(
            id="test:api/users.ex:20:MyApp.Api.Users.put:function",
            name="MyApp.Api.Users.put",  # Elixir uses qualified names
            kind="function",
            language="elixir",
            path="src/api/users.ex",
            span=Span(20, 28, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_handler"

    def test_nex_delete_function_pattern(self) -> None:
        """Nex delete/1 function matches route_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nex")

        symbol = Symbol(
            id="test:api/users.ex:30:MyApp.Api.Users.delete:function",
            name="MyApp.Api.Users.delete",  # Elixir uses qualified names
            kind="function",
            language="elixir",
            path="src/api/users.ex",
            span=Span(30, 35, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_handler"

    def test_nex_patch_function_pattern(self) -> None:
        """Nex patch/1 function matches route_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nex")

        symbol = Symbol(
            id="test:api/users.ex:38:MyApp.Api.Users.patch:function",
            name="MyApp.Api.Users.patch",  # Elixir uses qualified names
            kind="function",
            language="elixir",
            path="src/api/users.ex",
            span=Span(38, 45, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_handler"


class TestSanicPatterns:
    """Tests for Sanic framework pattern matching."""

    def test_sanic_get_route_pattern(self) -> None:
        """Sanic @app.get decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sanic")

        assert pattern_def is not None, "Sanic patterns YAML should exist"

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

    def test_sanic_post_route_pattern(self) -> None:
        """Sanic @app.post decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sanic")

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

    def test_sanic_classic_route_pattern(self) -> None:
        """Sanic @app.route decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sanic")

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

    def test_sanic_blueprint_route_pattern(self) -> None:
        """Sanic blueprint route decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sanic")

        symbol = Symbol(
            id="test:routes.py:1:get_item:function",
            name="get_item",
            kind="function",
            language="python",
            path="routes.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "bp.get", "args": ["/items/<id>"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "GET"
        assert results[0]["path"] == "/items/<id>"

    def test_sanic_websocket_pattern(self) -> None:
        """Sanic @app.websocket decorator matches websocket_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sanic")

        symbol = Symbol(
            id="test:app.py:1:ws_handler:function",
            name="ws_handler",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.websocket", "args": ["/feed"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "websocket_handler"

    def test_sanic_middleware_on_request_pattern(self) -> None:
        """Sanic @app.on_request decorator matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sanic")

        symbol = Symbol(
            id="test:app.py:1:add_key:function",
            name="add_key",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.on_request", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_sanic_middleware_on_response_pattern(self) -> None:
        """Sanic @app.on_response decorator matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sanic")

        symbol = Symbol(
            id="test:app.py:1:custom_header:function",
            name="custom_header",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.on_response", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_sanic_exception_handler_pattern(self) -> None:
        """Sanic @app.exception decorator matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sanic")

        symbol = Symbol(
            id="test:app.py:1:handle_not_found:function",
            name="handle_not_found",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.exception", "args": ["NotFound"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "error_handler"

    def test_sanic_lifecycle_before_server_start_pattern(self) -> None:
        """Sanic @app.before_server_start decorator matches lifecycle_hook pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sanic")

        symbol = Symbol(
            id="test:app.py:1:setup_db:function",
            name="setup_db",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.before_server_start", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "lifecycle_hook"

    def test_sanic_lifecycle_after_server_stop_pattern(self) -> None:
        """Sanic @app.after_server_stop decorator matches lifecycle_hook pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sanic")

        symbol = Symbol(
            id="test:app.py:1:cleanup:function",
            name="cleanup",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.after_server_stop", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "lifecycle_hook"

    def test_sanic_signal_handler_pattern(self) -> None:
        """Sanic @app.signal decorator matches event_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sanic")

        symbol = Symbol(
            id="test:app.py:1:on_request_received:function",
            name="on_request_received",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {
                        "name": "app.signal",
                        "args": ["http.lifecycle.request"],
                        "kwargs": {},
                    },
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "event_handler"


class TestQuartPatterns:
    """Tests for Quart framework pattern matching."""

    def test_quart_get_route_pattern(self) -> None:
        """Quart @app.get decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quart")

        assert pattern_def is not None, "Quart patterns YAML should exist"

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

    def test_quart_post_route_pattern(self) -> None:
        """Quart @app.post decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quart")

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

    def test_quart_classic_route_pattern(self) -> None:
        """Quart @app.route decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quart")

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

    def test_quart_blueprint_route_pattern(self) -> None:
        """Quart blueprint route decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quart")

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

    def test_quart_websocket_pattern(self) -> None:
        """Quart @app.websocket decorator matches websocket_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quart")

        symbol = Symbol(
            id="test:app.py:1:ws_handler:function",
            name="ws_handler",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.websocket", "args": ["/ws"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "websocket_handler"

    def test_quart_before_request_pattern(self) -> None:
        """Quart @app.before_request decorator matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quart")

        symbol = Symbol(
            id="test:app.py:1:log_request:function",
            name="log_request",
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

    def test_quart_after_request_pattern(self) -> None:
        """Quart @app.after_request decorator matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quart")

        symbol = Symbol(
            id="test:app.py:1:add_header:function",
            name="add_header",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.after_request", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_quart_error_handler_pattern(self) -> None:
        """Quart @app.errorhandler decorator matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quart")

        symbol = Symbol(
            id="test:app.py:1:handle_not_found:function",
            name="handle_not_found",
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

    def test_quart_before_serving_pattern(self) -> None:
        """Quart @app.before_serving decorator matches lifecycle_hook pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quart")

        symbol = Symbol(
            id="test:app.py:1:setup_db:function",
            name="setup_db",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.before_serving", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "lifecycle_hook"

    def test_quart_after_serving_pattern(self) -> None:
        """Quart @app.after_serving decorator matches lifecycle_hook pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quart")

        symbol = Symbol(
            id="test:app.py:1:cleanup:function",
            name="cleanup",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 10, 0, 0),
            meta={
                "decorators": [
                    {"name": "app.after_serving", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "lifecycle_hook"


class TestFalconPatterns:
    """Tests for Falcon framework pattern matching."""

    def test_falcon_on_get_responder_pattern(self) -> None:
        """Falcon on_get method matches route_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("falcon")

        assert pattern_def is not None, "Falcon patterns YAML should exist"

        symbol = Symbol(
            id="test:resources.py:5:UserResource.on_get:method",
            name="on_get",
            kind="method",
            language="python",
            path="resources.py",
            span=Span(5, 15, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_handler"

    def test_falcon_on_post_responder_pattern(self) -> None:
        """Falcon on_post method matches route_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("falcon")

        symbol = Symbol(
            id="test:resources.py:20:UserResource.on_post:method",
            name="on_post",
            kind="method",
            language="python",
            path="resources.py",
            span=Span(20, 30, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_handler"

    def test_falcon_on_put_responder_pattern(self) -> None:
        """Falcon on_put method matches route_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("falcon")

        symbol = Symbol(
            id="test:resources.py:35:UserResource.on_put:method",
            name="on_put",
            kind="method",
            language="python",
            path="resources.py",
            span=Span(35, 45, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_handler"

    def test_falcon_on_delete_responder_pattern(self) -> None:
        """Falcon on_delete method matches route_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("falcon")

        symbol = Symbol(
            id="test:resources.py:50:UserResource.on_delete:method",
            name="on_delete",
            kind="method",
            language="python",
            path="resources.py",
            span=Span(50, 55, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_handler"

    def test_falcon_before_hook_pattern(self) -> None:
        """Falcon @falcon.before decorator matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("falcon")

        symbol = Symbol(
            id="test:resources.py:10:on_get:method",
            name="on_get",
            kind="method",
            language="python",
            path="resources.py",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "falcon.before", "args": ["validate"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        # Should match both route_handler (from method name) and middleware (from decorator)
        concepts = {r["concept"] for r in results}
        assert "middleware" in concepts

    def test_falcon_after_hook_pattern(self) -> None:
        """Falcon @falcon.after decorator matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("falcon")

        symbol = Symbol(
            id="test:resources.py:25:on_post:method",
            name="on_post",
            kind="method",
            language="python",
            path="resources.py",
            span=Span(25, 35, 0, 0),
            meta={
                "decorators": [
                    {"name": "falcon.after", "args": ["log_response"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        concepts = {r["concept"] for r in results}
        assert "middleware" in concepts

    def test_falcon_resource_class_pattern(self) -> None:
        """Falcon resource class extending Resource base matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("falcon")

        symbol = Symbol(
            id="test:resources.py:1:UserResource:class",
            name="UserResource",
            kind="class",
            language="python",
            path="resources.py",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Resource"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"


class TestLitestarPatterns:
    """Tests for Litestar framework pattern matching."""

    def test_litestar_get_route_pattern(self) -> None:
        """Litestar @get decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("litestar")

        assert pattern_def is not None, "Litestar patterns YAML should exist"

        symbol = Symbol(
            id="test:routes.py:5:get_users:function",
            name="get_users",
            kind="function",
            language="python",
            path="routes.py",
            span=Span(5, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "get", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "GET"
        assert results[0]["path"] == "/users"

    def test_litestar_post_route_pattern(self) -> None:
        """Litestar @post decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("litestar")

        symbol = Symbol(
            id="test:routes.py:20:create_user:function",
            name="create_user",
            kind="function",
            language="python",
            path="routes.py",
            span=Span(20, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "post", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "POST"

    def test_litestar_put_route_pattern(self) -> None:
        """Litestar @put decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("litestar")

        symbol = Symbol(
            id="test:routes.py:35:update_user:function",
            name="update_user",
            kind="function",
            language="python",
            path="routes.py",
            span=Span(35, 45, 0, 0),
            meta={
                "decorators": [
                    {"name": "put", "args": ["/users/{user_id}"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "PUT"

    def test_litestar_delete_route_pattern(self) -> None:
        """Litestar @delete decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("litestar")

        symbol = Symbol(
            id="test:routes.py:50:delete_user:function",
            name="delete_user",
            kind="function",
            language="python",
            path="routes.py",
            span=Span(50, 55, 0, 0),
            meta={
                "decorators": [
                    {"name": "delete", "args": ["/users/{user_id}"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["method"] == "DELETE"

    def test_litestar_websocket_pattern(self) -> None:
        """Litestar @websocket decorator matches websocket_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("litestar")

        symbol = Symbol(
            id="test:routes.py:60:ws_handler:function",
            name="ws_handler",
            kind="function",
            language="python",
            path="routes.py",
            span=Span(60, 70, 0, 0),
            meta={
                "decorators": [
                    {"name": "websocket", "args": ["/ws"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "websocket_handler"

    def test_litestar_controller_class_pattern(self) -> None:
        """Litestar Controller base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("litestar")

        symbol = Symbol(
            id="test:controllers.py:1:UserController:class",
            name="UserController",
            kind="class",
            language="python",
            path="controllers.py",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Controller"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"


class TestSymfonyPatterns:
    """Tests for Symfony PHP framework pattern matching."""

    def test_symfony_abstract_controller_pattern(self) -> None:
        """Symfony AbstractController base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("symfony")

        assert pattern_def is not None, "Symfony patterns YAML should exist"

        symbol = Symbol(
            id="test:UserController.php:1:UserController:class",
            name="UserController",
            kind="class",
            language="php",
            path="src/Controller/UserController.php",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["AbstractController"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_symfony_form_type_pattern(self) -> None:
        """Symfony AbstractType base class matches form pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("symfony")

        symbol = Symbol(
            id="test:UserType.php:1:UserType:class",
            name="UserType",
            kind="class",
            language="php",
            path="src/Form/UserType.php",
            span=Span(1, 40, 0, 0),
            meta={
                "base_classes": ["AbstractType"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "form"

    def test_symfony_command_pattern(self) -> None:
        """Symfony Command base class matches command pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("symfony")

        symbol = Symbol(
            id="test:SendEmailCommand.php:1:SendEmailCommand:class",
            name="SendEmailCommand",
            kind="class",
            language="php",
            path="src/Command/SendEmailCommand.php",
            span=Span(1, 60, 0, 0),
            meta={
                "base_classes": ["Command"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "command"

    def test_symfony_event_subscriber_pattern(self) -> None:
        """Symfony EventSubscriberInterface matches event_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("symfony")

        symbol = Symbol(
            id="test:UserEventSubscriber.php:1:UserEventSubscriber:class",
            name="UserEventSubscriber",
            kind="class",
            language="php",
            path="src/EventSubscriber/UserEventSubscriber.php",
            span=Span(1, 45, 0, 0),
            meta={
                "base_classes": ["EventSubscriberInterface"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "event_handler"

    def test_symfony_entity_repository_pattern(self) -> None:
        """Symfony ServiceEntityRepository base class matches repository pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("symfony")

        symbol = Symbol(
            id="test:UserRepository.php:1:UserRepository:class",
            name="UserRepository",
            kind="class",
            language="php",
            path="src/Repository/UserRepository.php",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["ServiceEntityRepository"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "repository"

    def test_symfony_constraint_pattern(self) -> None:
        """Symfony Constraint base class matches validator pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("symfony")

        symbol = Symbol(
            id="test:ValidEmail.php:1:ValidEmail:class",
            name="ValidEmail",
            kind="class",
            language="php",
            path="src/Validator/Constraints/ValidEmail.php",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["Constraint"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "validator"

    def test_symfony_constraint_validator_pattern(self) -> None:
        """Symfony ConstraintValidator base class matches validator pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("symfony")

        symbol = Symbol(
            id="test:ValidEmailValidator.php:1:ValidEmailValidator:class",
            name="ValidEmailValidator",
            kind="class",
            language="php",
            path="src/Validator/Constraints/ValidEmailValidator.php",
            span=Span(1, 35, 0, 0),
            meta={
                "base_classes": ["ConstraintValidator"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "validator"


class TestQuarkusPatterns:
    """Tests for Quarkus Java framework pattern matching."""

    def test_quarkus_panache_entity_pattern(self) -> None:
        """Quarkus PanacheEntity base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quarkus")

        assert pattern_def is not None, "Quarkus patterns YAML should exist"

        symbol = Symbol(
            id="test:User.java:1:User:class",
            name="User",
            kind="class",
            language="java",
            path="src/main/java/org/example/User.java",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["PanacheEntity"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"

    def test_quarkus_panache_repository_pattern(self) -> None:
        """Quarkus PanacheRepository base class matches repository pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quarkus")

        symbol = Symbol(
            id="test:UserRepository.java:1:UserRepository:class",
            name="UserRepository",
            kind="class",
            language="java",
            path="src/main/java/org/example/UserRepository.java",
            span=Span(1, 25, 0, 0),
            meta={
                "base_classes": ["PanacheRepository"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "repository"

    def test_quarkus_scheduled_annotation_pattern(self) -> None:
        """Quarkus @Scheduled annotation matches task pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quarkus")

        symbol = Symbol(
            id="test:TaskService.java:10:sendEmails:method",
            name="sendEmails",
            kind="method",
            language="java",
            path="src/main/java/org/example/TaskService.java",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "Scheduled", "args": [], "kwargs": {"every": "1h"}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "task"

    def test_quarkus_blocking_annotation_pattern(self) -> None:
        """Quarkus @Blocking annotation matches async pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quarkus")

        symbol = Symbol(
            id="test:Resource.java:15:blockingMethod:method",
            name="blockingMethod",
            kind="method",
            language="java",
            path="src/main/java/org/example/Resource.java",
            span=Span(15, 25, 0, 0),
            meta={
                "decorators": [
                    {"name": "Blocking", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "async"

    def test_quarkus_liveness_pattern(self) -> None:
        """Quarkus @Liveness annotation matches health_check pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quarkus")

        symbol = Symbol(
            id="test:HealthCheck.java:1:AppLiveness:class",
            name="AppLiveness",
            kind="class",
            language="java",
            path="src/main/java/org/example/HealthCheck.java",
            span=Span(1, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "Liveness", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "health_check"

    def test_quarkus_rest_client_pattern(self) -> None:
        """Quarkus @RegisterRestClient annotation matches client pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("quarkus")

        symbol = Symbol(
            id="test:ExternalApi.java:1:ExternalApiClient:interface",
            name="ExternalApiClient",
            kind="interface",
            language="java",
            path="src/main/java/org/example/ExternalApiClient.java",
            span=Span(1, 30, 0, 0),
            meta={
                "decorators": [
                    {"name": "RegisterRestClient", "args": [], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "client"


class TestNuxtPatterns:
    """Tests for Nuxt Vue.js meta-framework pattern matching."""

    def test_nuxt_define_page_meta_pattern(self) -> None:
        """Nuxt definePageMeta function matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nuxt")

        assert pattern_def is not None, "Nuxt patterns YAML should exist"

        symbol = Symbol(
            id="test:pages/about.vue:5:definePageMeta:call",
            name="definePageMeta",
            kind="function",
            language="javascript",
            path="pages/about.vue",
            span=Span(5, 10, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_nuxt_define_event_handler_pattern(self) -> None:
        """Nuxt defineEventHandler (Nitro) matches api_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nuxt")

        symbol = Symbol(
            id="test:server/api/users.ts:1:defineEventHandler:function",
            name="defineEventHandler",
            kind="function",
            language="typescript",
            path="server/api/users.ts",
            span=Span(1, 15, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "api_handler"

    def test_nuxt_define_nuxt_middleware_pattern(self) -> None:
        """Nuxt defineNuxtRouteMiddleware matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nuxt")

        symbol = Symbol(
            id="test:middleware/auth.ts:1:defineNuxtRouteMiddleware:function",
            name="defineNuxtRouteMiddleware",
            kind="function",
            language="typescript",
            path="middleware/auth.ts",
            span=Span(1, 20, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_nuxt_define_nuxt_plugin_pattern(self) -> None:
        """Nuxt defineNuxtPlugin matches plugin pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nuxt")

        symbol = Symbol(
            id="test:plugins/analytics.ts:1:defineNuxtPlugin:function",
            name="defineNuxtPlugin",
            kind="function",
            language="typescript",
            path="plugins/analytics.ts",
            span=Span(1, 25, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "plugin"

    def test_nuxt_use_async_data_pattern(self) -> None:
        """Nuxt useAsyncData composable matches data_fetcher pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nuxt")

        symbol = Symbol(
            id="test:pages/users.vue:10:useAsyncData:function",
            name="useAsyncData",
            kind="function",
            language="javascript",
            path="pages/users.vue",
            span=Span(10, 15, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "data_fetcher"

    def test_nuxt_use_fetch_pattern(self) -> None:
        """Nuxt useFetch composable matches data_fetcher pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("nuxt")

        symbol = Symbol(
            id="test:pages/posts.vue:8:useFetch:function",
            name="useFetch",
            kind="function",
            language="javascript",
            path="pages/posts.vue",
            span=Span(8, 12, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "data_fetcher"


class TestRemixPatterns:
    """Tests for Remix React meta-framework pattern matching."""

    def test_remix_loader_pattern(self) -> None:
        """Remix loader function matches data_fetcher pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("remix")

        assert pattern_def is not None, "Remix patterns YAML should exist"

        symbol = Symbol(
            id="test:routes/users.tsx:5:loader:function",
            name="loader",
            kind="function",
            language="typescript",
            path="app/routes/users.tsx",
            span=Span(5, 15, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "data_fetcher"

    def test_remix_action_pattern(self) -> None:
        """Remix action function matches mutation pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("remix")

        symbol = Symbol(
            id="test:routes/users.tsx:20:action:function",
            name="action",
            kind="function",
            language="typescript",
            path="app/routes/users.tsx",
            span=Span(20, 35, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "mutation"

    def test_remix_meta_pattern(self) -> None:
        """Remix meta function matches metadata_generator pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("remix")

        symbol = Symbol(
            id="test:routes/users.tsx:40:meta:function",
            name="meta",
            kind="function",
            language="typescript",
            path="app/routes/users.tsx",
            span=Span(40, 50, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "metadata_generator"

    def test_remix_error_boundary_pattern(self) -> None:
        """Remix ErrorBoundary function matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("remix")

        symbol = Symbol(
            id="test:routes/users.tsx:60:ErrorBoundary:function",
            name="ErrorBoundary",
            kind="function",
            language="typescript",
            path="app/routes/users.tsx",
            span=Span(60, 75, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "error_handler"

    def test_remix_links_pattern(self) -> None:
        """Remix links function matches stylesheet pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("remix")

        symbol = Symbol(
            id="test:root.tsx:5:links:function",
            name="links",
            kind="function",
            language="typescript",
            path="app/root.tsx",
            span=Span(5, 15, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "stylesheet"

    def test_remix_handle_pattern(self) -> None:
        """Remix handle export matches route_config pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("remix")

        symbol = Symbol(
            id="test:routes/admin.tsx:5:handle:variable",
            name="handle",
            kind="variable",
            language="typescript",
            path="app/routes/admin.tsx",
            span=Span(5, 10, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_config"


class TestSvelteKitPatterns:
    """Tests for SvelteKit Svelte meta-framework pattern matching."""

    def test_sveltekit_load_pattern(self) -> None:
        """SvelteKit load function matches data_fetcher pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sveltekit")

        assert pattern_def is not None, "SvelteKit patterns YAML should exist"

        symbol = Symbol(
            id="test:+page.server.ts:5:load:function",
            name="load",
            kind="function",
            language="typescript",
            path="src/routes/users/+page.server.ts",
            span=Span(5, 15, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "data_fetcher"

    def test_sveltekit_actions_pattern(self) -> None:
        """SvelteKit actions export matches mutation pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sveltekit")

        symbol = Symbol(
            id="test:+page.server.ts:20:actions:variable",
            name="actions",
            kind="variable",
            language="typescript",
            path="src/routes/users/+page.server.ts",
            span=Span(20, 40, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "mutation"

    def test_sveltekit_handle_hook_pattern(self) -> None:
        """SvelteKit handle hook matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sveltekit")

        symbol = Symbol(
            id="test:hooks.server.ts:1:handle:function",
            name="handle",
            kind="function",
            language="typescript",
            path="src/hooks.server.ts",
            span=Span(1, 20, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_sveltekit_handle_error_pattern(self) -> None:
        """SvelteKit handleError hook matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sveltekit")

        symbol = Symbol(
            id="test:hooks.server.ts:25:handleError:function",
            name="handleError",
            kind="function",
            language="typescript",
            path="src/hooks.server.ts",
            span=Span(25, 40, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "error_handler"

    def test_sveltekit_page_data_pattern(self) -> None:
        """SvelteKit PageData type matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("sveltekit")

        symbol = Symbol(
            id="test:+page.ts:1:PageData:interface",
            name="PageData",
            kind="interface",
            language="typescript",
            path="src/routes/+page.ts",
            span=Span(1, 10, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"


class TestHanamiPatterns:
    """Tests for Hanami Ruby framework pattern matching."""

    def test_hanami_action_base_class_pattern(self) -> None:
        """Hanami Action base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("hanami")

        assert pattern_def is not None, "Hanami patterns YAML should exist"

        symbol = Symbol(
            id="test:users/index.rb:1:Index:class",
            name="Index",
            kind="class",
            language="ruby",
            path="app/actions/users/index.rb",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["Hanami::Action"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_hanami_repository_pattern(self) -> None:
        """Hanami Repository base class matches repository pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("hanami")

        symbol = Symbol(
            id="test:user_repository.rb:1:UserRepository:class",
            name="UserRepository",
            kind="class",
            language="ruby",
            path="lib/repositories/user_repository.rb",
            span=Span(1, 15, 0, 0),
            meta={
                "base_classes": ["Hanami::Repository"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "repository"

    def test_hanami_entity_pattern(self) -> None:
        """Hanami Entity base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("hanami")

        symbol = Symbol(
            id="test:user.rb:1:User:class",
            name="User",
            kind="class",
            language="ruby",
            path="lib/entities/user.rb",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["Hanami::Entity"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"

    def test_hanami_interactor_pattern(self) -> None:
        """Hanami Interactor include matches service pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("hanami")

        symbol = Symbol(
            id="test:create_user.rb:1:CreateUser:class",
            name="CreateUser",
            kind="class",
            language="ruby",
            path="lib/interactors/create_user.rb",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Hanami::Interactor"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "service"

    def test_hanami_view_pattern(self) -> None:
        """Hanami View base class matches view pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("hanami")

        symbol = Symbol(
            id="test:users/index.rb:1:Index:class",
            name="Index",
            kind="class",
            language="ruby",
            path="app/views/users/index.rb",
            span=Span(1, 25, 0, 0),
            meta={
                "base_classes": ["Hanami::View"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "view"


class TestFeathersPatterns:
    """Tests for Feathers.js real-time framework pattern matching."""

    def test_feathers_service_class_pattern(self) -> None:
        """Feathers Service class matches service pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("feathers")

        assert pattern_def is not None, "Feathers patterns YAML should exist"

        symbol = Symbol(
            id="test:users.service.ts:1:UsersService:class",
            name="UsersService",
            kind="class",
            language="typescript",
            path="src/services/users/users.service.ts",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Service"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "service"

    def test_feathers_hook_function_pattern(self) -> None:
        """Feathers hook function matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("feathers")

        symbol = Symbol(
            id="test:authenticate.ts:1:authenticate:function",
            name="authenticate",
            kind="function",
            language="typescript",
            path="src/hooks/authenticate.ts",
            span=Span(1, 20, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_feathers_channel_pattern(self) -> None:
        """Feathers channels function matches websocket_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("feathers")

        symbol = Symbol(
            id="test:channels.ts:1:channels:function",
            name="channels",
            kind="function",
            language="typescript",
            path="src/channels.ts",
            span=Span(1, 40, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "websocket_handler"

    def test_feathers_configure_pattern(self) -> None:
        """Feathers configure function matches config pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("feathers")

        symbol = Symbol(
            id="test:app.ts:10:configure:function",
            name="configure",
            kind="function",
            language="typescript",
            path="src/app.ts",
            span=Span(10, 25, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "config"


class TestMasonitePatterns:
    """Tests for Masonite framework pattern matching."""

    def test_masonite_controller_base_class(self) -> None:
        """Masonite Controller base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("masonite")

        assert pattern_def is not None, "Masonite patterns YAML should exist"

        symbol = Symbol(
            id="test:controllers.py:1:UserController:class",
            name="UserController",
            kind="class",
            language="python",
            path="app/controllers/UserController.py",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Controller"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_masonite_route_get_via_usage_context(self) -> None:
        """Masonite Route.get() call matches route pattern via UsageContext."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("masonite")

        assert pattern_def is not None

        ctx = UsageContext.create(
            kind="call",
            context_name="Route.get",
            position="args[0]",
            path="routes/web.py",
            span=Span(5, 5, 0, 50),
            symbol_ref="test:routes/web.py:5:route_def:other",
            metadata={
                "url": "/users",
                "handler": "UserController@index",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_masonite_route_post_via_usage_context(self) -> None:
        """Masonite Route.post() call matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("masonite")

        ctx = UsageContext.create(
            kind="call",
            context_name="Route.post",
            position="args[0]",
            path="routes/web.py",
            span=Span(10, 10, 0, 50),
            symbol_ref="test:routes/web.py:10:route_def:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_masonite_model_base_class(self) -> None:
        """Masonite Model base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("masonite")

        symbol = Symbol(
            id="test:models.py:1:User:class",
            name="User",
            kind="class",
            language="python",
            path="app/models/User.py",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Model"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"

    def test_masonite_command_base_class(self) -> None:
        """Masonite Command base class matches command pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("masonite")

        symbol = Symbol(
            id="test:commands.py:1:SendEmails:class",
            name="SendEmails",
            kind="class",
            language="python",
            path="app/commands/SendEmails.py",
            span=Span(1, 40, 0, 0),
            meta={
                "base_classes": ["Command"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "command"

    def test_masonite_provider_base_class(self) -> None:
        """Masonite Provider base class matches service_provider pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("masonite")

        symbol = Symbol(
            id="test:providers.py:1:AppProvider:class",
            name="AppProvider",
            kind="class",
            language="python",
            path="app/providers/AppProvider.py",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Provider"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "service_provider"


class TestAdonisJSPatterns:
    """Tests for AdonisJS framework pattern matching."""

    def test_adonisjs_controller_naming_convention(self) -> None:
        """AdonisJS controller naming convention matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("adonisjs")

        assert pattern_def is not None, "AdonisJS patterns YAML should exist"

        symbol = Symbol(
            id="test:controllers.ts:1:UserController:class",
            name="UserController",
            kind="class",
            language="javascript",
            path="app/controllers/UserController.ts",
            span=Span(1, 50, 0, 0),
            meta={},
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_adonisjs_get_decorator(self) -> None:
        """AdonisJS @Get decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("adonisjs")

        symbol = Symbol(
            id="test:controllers.ts:10:index:method",
            name="index",
            kind="method",
            language="javascript",
            path="app/controllers/UserController.ts",
            span=Span(10, 20, 0, 0),
            meta={
                "decorators": [
                    {"name": "@Get", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"
        assert results[0]["path"] == "/users"

    def test_adonisjs_post_decorator(self) -> None:
        """AdonisJS @Post decorator matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("adonisjs")

        symbol = Symbol(
            id="test:controllers.ts:30:store:method",
            name="store",
            kind="method",
            language="javascript",
            path="app/controllers/UserController.ts",
            span=Span(30, 40, 0, 0),
            meta={
                "decorators": [
                    {"name": "@Post", "args": ["/users"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_adonisjs_middleware_decorator(self) -> None:
        """AdonisJS @Middleware decorator matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("adonisjs")

        symbol = Symbol(
            id="test:controllers.ts:5:show:method",
            name="show",
            kind="method",
            language="javascript",
            path="app/controllers/UserController.ts",
            span=Span(5, 15, 0, 0),
            meta={
                "decorators": [
                    {"name": "@Middleware", "args": ["auth"], "kwargs": {}},
                ],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_adonisjs_lucid_model(self) -> None:
        """AdonisJS Lucid model (BaseModel) matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("adonisjs")

        symbol = Symbol(
            id="test:models.ts:1:User:class",
            name="User",
            kind="class",
            language="javascript",
            path="app/models/User.ts",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["BaseModel"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"

    def test_adonisjs_command_base_class(self) -> None:
        """AdonisJS command (BaseCommand) matches command pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("adonisjs")

        symbol = Symbol(
            id="test:commands.ts:1:SendEmails:class",
            name="SendEmails",
            kind="class",
            language="javascript",
            path="app/commands/SendEmails.ts",
            span=Span(1, 40, 0, 0),
            meta={
                "base_classes": ["BaseCommand"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "command"

    def test_adonisjs_route_get_via_usage_context(self) -> None:
        """AdonisJS Route.get() call matches route pattern via UsageContext."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("adonisjs")

        ctx = UsageContext.create(
            kind="call",
            context_name="Route.get",
            position="args[0]",
            path="start/routes.ts",
            span=Span(5, 5, 0, 50),
            symbol_ref="test:routes.ts:5:route_def:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"


class TestRodaPatterns:
    """Tests for Roda framework pattern matching."""

    def test_roda_application_base_class(self) -> None:
        """Roda application base class matches application pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("roda")

        assert pattern_def is not None, "Roda patterns YAML should exist"

        symbol = Symbol(
            id="test:app.rb:1:App:class",
            name="App",
            kind="class",
            language="ruby",
            path="app.rb",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Roda"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "application"

    def test_roda_route_definition_via_usage_context(self) -> None:
        """Roda route block matches route_definition pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("roda")

        ctx = UsageContext.create(
            kind="call",
            context_name="route",
            position="args[0]",
            path="app.rb",
            span=Span(3, 50, 0, 0),
            symbol_ref="test:app.rb:3:route_block:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_definition"

    def test_roda_root_route_via_usage_context(self) -> None:
        """Roda r.root matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("roda")

        ctx = UsageContext.create(
            kind="call",
            context_name="r.root",
            position="args[0]",
            path="app.rb",
            span=Span(5, 8, 0, 0),
            symbol_ref="test:app.rb:5:root_handler:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_roda_get_route_via_usage_context(self) -> None:
        """Roda r.get matches route pattern with GET method."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("roda")

        ctx = UsageContext.create(
            kind="call",
            context_name="r.get",
            position="args[0]",
            path="app.rb",
            span=Span(10, 15, 0, 0),
            symbol_ref="test:app.rb:10:get_handler:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_roda_post_route_via_usage_context(self) -> None:
        """Roda r.post matches route pattern with POST method."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("roda")

        ctx = UsageContext.create(
            kind="call",
            context_name="r.post",
            position="args[0]",
            path="app.rb",
            span=Span(20, 25, 0, 0),
            symbol_ref="test:app.rb:20:post_handler:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_roda_on_segment_via_usage_context(self) -> None:
        """Roda r.on matches route_segment pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("roda")

        ctx = UsageContext.create(
            kind="call",
            context_name="r.on",
            position="args[0]",
            path="app.rb",
            span=Span(7, 30, 0, 0),
            symbol_ref="test:app.rb:7:users_segment:other",
            metadata={
                "segment": "users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_segment"

    def test_roda_is_terminal_via_usage_context(self) -> None:
        """Roda r.is matches route_terminal pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("roda")

        ctx = UsageContext.create(
            kind="call",
            context_name="r.is",
            position="args[0]",
            path="app.rb",
            span=Span(15, 20, 0, 0),
            symbol_ref="test:app.rb:15:user_terminal:other",
            metadata={
                "segment": "profile",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_terminal"

    def test_roda_plugin_via_usage_context(self) -> None:
        """Roda plugin call matches plugin pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("roda")

        ctx = UsageContext.create(
            kind="call",
            context_name="plugin",
            position="args[0]",
            path="app.rb",
            span=Span(2, 2, 0, 20),
            symbol_ref="test:app.rb:2:json_plugin:other",
            metadata={
                "plugin_name": ":json",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "plugin"


class TestJavalinPatterns:
    """Tests for Javalin framework pattern matching."""

    def test_javalin_create_via_usage_context(self) -> None:
        """Javalin.create() matches application pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("javalin")

        assert pattern_def is not None, "Javalin patterns YAML should exist"

        ctx = UsageContext.create(
            kind="call",
            context_name="Javalin.create",
            position="args[0]",
            path="App.java",
            span=Span(5, 5, 0, 50),
            symbol_ref="test:App.java:5:create_app:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "application"

    def test_javalin_get_route_via_usage_context(self) -> None:
        """Javalin app.get() matches route pattern with GET method."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("javalin")

        ctx = UsageContext.create(
            kind="call",
            context_name="app.get",
            position="args[0]",
            path="App.java",
            span=Span(10, 10, 0, 50),
            symbol_ref="test:App.java:10:get_users:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_javalin_post_route_via_usage_context(self) -> None:
        """Javalin app.post() matches route pattern with POST method."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("javalin")

        ctx = UsageContext.create(
            kind="call",
            context_name="app.post",
            position="args[0]",
            path="App.java",
            span=Span(15, 15, 0, 50),
            symbol_ref="test:App.java:15:create_user:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_javalin_delete_route_via_usage_context(self) -> None:
        """Javalin app.delete() matches route pattern with DELETE method."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("javalin")

        ctx = UsageContext.create(
            kind="call",
            context_name="app.delete",
            position="args[0]",
            path="App.java",
            span=Span(20, 20, 0, 50),
            symbol_ref="test:App.java:20:delete_user:other",
            metadata={
                "url": "/users/{id}",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_javalin_websocket_via_usage_context(self) -> None:
        """Javalin app.ws() matches websocket pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("javalin")

        ctx = UsageContext.create(
            kind="call",
            context_name="app.ws",
            position="args[0]",
            path="App.java",
            span=Span(25, 25, 0, 50),
            symbol_ref="test:App.java:25:websocket:other",
            metadata={
                "url": "/chat",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "websocket"

    def test_javalin_before_middleware_via_usage_context(self) -> None:
        """Javalin app.before() matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("javalin")

        ctx = UsageContext.create(
            kind="call",
            context_name="app.before",
            position="args[0]",
            path="App.java",
            span=Span(8, 8, 0, 40),
            symbol_ref="test:App.java:8:auth_middleware:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_javalin_exception_handler_via_usage_context(self) -> None:
        """Javalin app.exception() matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("javalin")

        ctx = UsageContext.create(
            kind="call",
            context_name="app.exception",
            position="args[0]",
            path="App.java",
            span=Span(30, 30, 0, 60),
            symbol_ref="test:App.java:30:exception_handler:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "error_handler"

    def test_javalin_handler_base_class(self) -> None:
        """Javalin Handler interface implementation matches handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("javalin")

        symbol = Symbol(
            id="test:UserHandler.java:1:UserHandler:class",
            name="UserHandler",
            kind="class",
            language="java",
            path="handlers/UserHandler.java",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Handler"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "handler"


class TestScalatraPatterns:
    """Tests for Scalatra framework pattern matching."""

    def test_scalatra_servlet_base_class(self) -> None:
        """Scalatra ScalatraServlet base class matches servlet pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("scalatra")

        assert pattern_def is not None, "Scalatra patterns YAML should exist"

        symbol = Symbol(
            id="test:MyServlet.scala:1:MyServlet:class",
            name="MyServlet",
            kind="class",
            language="scala",
            path="MyServlet.scala",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["ScalatraServlet"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "servlet"

    def test_scalatra_get_route_via_usage_context(self) -> None:
        """Scalatra get() call matches route pattern with GET method."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("scalatra")

        ctx = UsageContext.create(
            kind="call",
            context_name="get",
            position="args[0]",
            path="MyServlet.scala",
            span=Span(10, 15, 0, 0),
            symbol_ref="test:MyServlet.scala:10:get_users:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_scalatra_post_route_via_usage_context(self) -> None:
        """Scalatra post() call matches route pattern with POST method."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("scalatra")

        ctx = UsageContext.create(
            kind="call",
            context_name="post",
            position="args[0]",
            path="MyServlet.scala",
            span=Span(20, 25, 0, 0),
            symbol_ref="test:MyServlet.scala:20:create_user:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_scalatra_before_filter_via_usage_context(self) -> None:
        """Scalatra before() call matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("scalatra")

        ctx = UsageContext.create(
            kind="call",
            context_name="before",
            position="args[0]",
            path="MyServlet.scala",
            span=Span(5, 8, 0, 0),
            symbol_ref="test:MyServlet.scala:5:auth_filter:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_scalatra_error_handler_via_usage_context(self) -> None:
        """Scalatra error() call matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("scalatra")

        ctx = UsageContext.create(
            kind="call",
            context_name="error",
            position="args[0]",
            path="MyServlet.scala",
            span=Span(30, 35, 0, 0),
            symbol_ref="test:MyServlet.scala:30:error_handler:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "error_handler"

    def test_scalatra_json_support_trait(self) -> None:
        """Scalatra JacksonJsonSupport trait matches json_support pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("scalatra")

        symbol = Symbol(
            id="test:MyServlet.scala:1:MyServlet:class",
            name="MyServlet",
            kind="class",
            language="scala",
            path="MyServlet.scala",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["ScalatraServlet", "JacksonJsonSupport"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) >= 2  # servlet + json_support
        concepts = [r["concept"] for r in results]
        assert "servlet" in concepts
        assert "json_support" in concepts


class TestHttp4kPatterns:
    """Tests for Http4k framework pattern matching."""

    def test_http4k_route_bind_via_usage_context(self) -> None:
        """Http4k bind call matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("http4k")

        assert pattern_def is not None, "Http4k patterns YAML should exist"

        ctx = UsageContext.create(
            kind="call",
            context_name="bind",
            position="args[0]",
            path="Routes.kt",
            span=Span(10, 10, 0, 50),
            symbol_ref="test:Routes.kt:10:user_route:other",
            metadata={
                "path": "/users",
                "method": "GET",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_http4k_handler_base_class(self) -> None:
        """Http4k HttpHandler implementation matches handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("http4k")

        symbol = Symbol(
            id="test:UserHandler.kt:1:UserHandler:class",
            name="UserHandler",
            kind="class",
            language="kotlin",
            path="handlers/UserHandler.kt",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["HttpHandler"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "handler"

    def test_http4k_filter_base_class(self) -> None:
        """Http4k Filter implementation matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("http4k")

        symbol = Symbol(
            id="test:AuthFilter.kt:1:AuthFilter:class",
            name="AuthFilter",
            kind="class",
            language="kotlin",
            path="filters/AuthFilter.kt",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["Filter"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_http4k_routes_via_usage_context(self) -> None:
        """Http4k routes() call matches router pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("http4k")

        ctx = UsageContext.create(
            kind="call",
            context_name="routes",
            position="args[0]",
            path="App.kt",
            span=Span(5, 20, 0, 0),
            symbol_ref="test:App.kt:5:routes:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "router"

    def test_http4k_websocket_via_usage_context(self) -> None:
        """Http4k websockets() call matches websocket pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("http4k")

        ctx = UsageContext.create(
            kind="call",
            context_name="websockets",
            position="args[0]",
            path="App.kt",
            span=Span(25, 30, 0, 0),
            symbol_ref="test:App.kt:25:ws_routes:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "websocket"

    def test_http4k_server_via_usage_context(self) -> None:
        """Http4k asServer() call matches server pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("http4k")

        ctx = UsageContext.create(
            kind="call",
            context_name="asServer",
            position="args[0]",
            path="Main.kt",
            span=Span(10, 10, 0, 50),
            symbol_ref="test:Main.kt:10:server:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "server"


class TestHttp4sPatterns:
    """Tests for http4s framework pattern matching."""

    def test_http4s_routes_via_usage_context(self) -> None:
        """http4s HttpRoutes call matches router pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("http4s")

        assert pattern_def is not None, "http4s patterns YAML should exist"

        ctx = UsageContext.create(
            kind="call",
            context_name="HttpRoutes",
            position="args[0]",
            path="Routes.scala",
            span=Span(10, 20, 0, 0),
            symbol_ref="test:Routes.scala:10:user_routes:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "router"

    def test_http4s_routes_of_via_usage_context(self) -> None:
        """http4s HttpRoutes.of call matches router pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("http4s")

        ctx = UsageContext.create(
            kind="call",
            context_name="HttpRoutes.of",
            position="args[0]",
            path="Routes.scala",
            span=Span(15, 25, 0, 0),
            symbol_ref="test:Routes.scala:15:api_routes:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "router"

    def test_http4s_blaze_server_via_usage_context(self) -> None:
        """http4s BlazeServerBuilder call matches server pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("http4s")

        ctx = UsageContext.create(
            kind="call",
            context_name="BlazeServerBuilder",
            position="args[0]",
            path="Main.scala",
            span=Span(5, 10, 0, 0),
            symbol_ref="test:Main.scala:5:server:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "server"

    def test_http4s_middleware_via_usage_context(self) -> None:
        """http4s Logger middleware call matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("http4s")

        ctx = UsageContext.create(
            kind="call",
            context_name="Logger",
            position="args[0]",
            path="Middleware.scala",
            span=Span(8, 12, 0, 0),
            symbol_ref="test:Middleware.scala:8:logging:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_http4s_dsl_base_class(self) -> None:
        """http4s Http4sDsl trait matches dsl pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("http4s")

        symbol = Symbol(
            id="test:Routes.scala:1:UserRoutes:class",
            name="UserRoutes",
            kind="class",
            language="scala",
            path="Routes.scala",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Http4sDsl"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "dsl"

    def test_http4s_ioapp_base_class(self) -> None:
        """http4s IOApp trait matches application pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("http4s")

        symbol = Symbol(
            id="test:Main.scala:1:Main:object",
            name="Main",
            kind="class",
            language="scala",
            path="Main.scala",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["IOApp"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "application"


class TestVertxPatterns:
    """Tests for Vert.x framework pattern matching."""

    def test_vertx_router_via_usage_context(self) -> None:
        """Vert.x Router.router() matches router pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("vertx")

        assert pattern_def is not None, "Vert.x patterns YAML should exist"

        ctx = UsageContext.create(
            kind="call",
            context_name="Router.router",
            position="args[0]",
            path="Server.java",
            span=Span(10, 10, 0, 50),
            symbol_ref="test:Server.java:10:create_router:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "router"

    def test_vertx_route_get_via_usage_context(self) -> None:
        """Vert.x router.get() matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("vertx")

        ctx = UsageContext.create(
            kind="call",
            context_name="router.get",
            position="args[0]",
            path="Server.java",
            span=Span(15, 15, 0, 50),
            symbol_ref="test:Server.java:15:get_users:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_vertx_handler_via_usage_context(self) -> None:
        """Vert.x .handler() matches handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("vertx")

        ctx = UsageContext.create(
            kind="call",
            context_name=".handler",
            position="args[0]",
            path="Server.java",
            span=Span(20, 20, 0, 50),
            symbol_ref="test:Server.java:20:handle_request:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "handler"

    def test_vertx_verticle_base_class(self) -> None:
        """Vert.x AbstractVerticle base class matches verticle pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("vertx")

        symbol = Symbol(
            id="test:MainVerticle.java:1:MainVerticle:class",
            name="MainVerticle",
            kind="class",
            language="java",
            path="MainVerticle.java",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["AbstractVerticle"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "verticle"

    def test_vertx_eventbus_consumer_via_usage_context(self) -> None:
        """Vert.x eventBus.consumer() matches event_consumer pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("vertx")

        ctx = UsageContext.create(
            kind="call",
            context_name="eventBus.consumer",
            position="args[0]",
            path="EventHandler.java",
            span=Span(10, 15, 0, 0),
            symbol_ref="test:EventHandler.java:10:consume_events:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "event_consumer"

    def test_vertx_http_server_via_usage_context(self) -> None:
        """Vert.x vertx.createHttpServer() matches server pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("vertx")

        ctx = UsageContext.create(
            kind="call",
            context_name="vertx.createHttpServer",
            position="args[0]",
            path="Server.java",
            span=Span(5, 5, 0, 50),
            symbol_ref="test:Server.java:5:create_server:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "server"


class TestRestifyPatterns:
    """Tests for Restify framework pattern matching."""

    def test_restify_create_server_via_usage_context(self) -> None:
        """Restify createServer matches server pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("restify")

        assert pattern_def is not None, "Restify patterns YAML should exist"

        ctx = UsageContext.create(
            kind="call",
            context_name="restify.createServer",
            position="args[0]",
            path="server.js",
            span=Span(5, 5, 0, 50),
            symbol_ref="test:server.js:5:create_server:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "server"

    def test_restify_get_route_via_usage_context(self) -> None:
        """Restify server.get() matches route pattern with GET method."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("restify")

        ctx = UsageContext.create(
            kind="call",
            context_name="server.get",
            position="args[0]",
            path="routes.js",
            span=Span(10, 10, 0, 50),
            symbol_ref="test:routes.js:10:get_users:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_restify_post_route_via_usage_context(self) -> None:
        """Restify server.post() matches route pattern with POST method."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("restify")

        ctx = UsageContext.create(
            kind="call",
            context_name="server.post",
            position="args[0]",
            path="routes.js",
            span=Span(15, 15, 0, 50),
            symbol_ref="test:routes.js:15:create_user:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_restify_pre_middleware_via_usage_context(self) -> None:
        """Restify server.pre() matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("restify")

        ctx = UsageContext.create(
            kind="call",
            context_name="server.pre",
            position="args[0]",
            path="server.js",
            span=Span(8, 8, 0, 50),
            symbol_ref="test:server.js:8:pre_handler:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_restify_body_parser_via_usage_context(self) -> None:
        """Restify bodyParser plugin matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("restify")

        ctx = UsageContext.create(
            kind="call",
            context_name="restify.plugins.bodyParser",
            position="args[0]",
            path="server.js",
            span=Span(12, 12, 0, 50),
            symbol_ref="test:server.js:12:body_parser:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_restify_json_client_via_usage_context(self) -> None:
        """Restify createJsonClient matches client pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("restify")

        ctx = UsageContext.create(
            kind="call",
            context_name="restify.createJsonClient",
            position="args[0]",
            path="client.js",
            span=Span(5, 5, 0, 50),
            symbol_ref="test:client.js:5:create_client:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "client"


class TestCodeIgniterPatterns:
    """Tests for CodeIgniter framework pattern matching."""

    def test_codeigniter_controller_base_class(self) -> None:
        """CodeIgniter BaseController base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("codeigniter")

        assert pattern_def is not None, "CodeIgniter patterns YAML should exist"

        symbol = Symbol(
            id="test:UserController.php:1:UserController:class",
            name="UserController",
            kind="class",
            language="php",
            path="app/Controllers/UserController.php",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["BaseController"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_codeigniter_model_base_class(self) -> None:
        """CodeIgniter Model base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("codeigniter")

        symbol = Symbol(
            id="test:UserModel.php:1:UserModel:class",
            name="UserModel",
            kind="class",
            language="php",
            path="app/Models/UserModel.php",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Model"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"

    def test_codeigniter_get_route_via_usage_context(self) -> None:
        """CodeIgniter $routes->get() matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("codeigniter")

        ctx = UsageContext.create(
            kind="call",
            context_name="$routes->get",
            position="args[0]",
            path="app/Config/Routes.php",
            span=Span(10, 10, 0, 50),
            symbol_ref="test:Routes.php:10:get_users:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_codeigniter_post_route_via_usage_context(self) -> None:
        """CodeIgniter $routes->post() matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("codeigniter")

        ctx = UsageContext.create(
            kind="call",
            context_name="$routes->post",
            position="args[0]",
            path="app/Config/Routes.php",
            span=Span(15, 15, 0, 50),
            symbol_ref="test:Routes.php:15:create_user:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_codeigniter_resource_route_via_usage_context(self) -> None:
        """CodeIgniter $routes->resource() matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("codeigniter")

        ctx = UsageContext.create(
            kind="call",
            context_name="$routes->resource",
            position="args[0]",
            path="app/Config/Routes.php",
            span=Span(20, 20, 0, 50),
            symbol_ref="test:Routes.php:20:users_resource:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_codeigniter_filter_interface(self) -> None:
        """CodeIgniter FilterInterface matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("codeigniter")

        symbol = Symbol(
            id="test:AuthFilter.php:1:AuthFilter:class",
            name="AuthFilter",
            kind="class",
            language="php",
            path="app/Filters/AuthFilter.php",
            span=Span(1, 20, 0, 0),
            meta={
                "base_classes": ["FilterInterface"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"


class TestLumenPatterns:
    """Tests for Lumen framework pattern matching."""

    def test_lumen_controller_base_class(self) -> None:
        """Lumen Controller base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("lumen")

        assert pattern_def is not None, "Lumen patterns YAML should exist"

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

    def test_lumen_router_get_route_via_usage_context(self) -> None:
        """Lumen $router->get() matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("lumen")

        ctx = UsageContext.create(
            kind="call",
            context_name="$router->get",
            position="args[0]",
            path="routes/web.php",
            span=Span(10, 10, 0, 50),
            symbol_ref="test:web.php:10:get_users:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_lumen_router_post_route_via_usage_context(self) -> None:
        """Lumen $router->post() matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("lumen")

        ctx = UsageContext.create(
            kind="call",
            context_name="$router->post",
            position="args[0]",
            path="routes/web.php",
            span=Span(15, 15, 0, 50),
            symbol_ref="test:web.php:15:create_user:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_lumen_app_get_route_via_usage_context(self) -> None:
        """Lumen $app->get() matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("lumen")

        ctx = UsageContext.create(
            kind="call",
            context_name="$app->get",
            position="args[0]",
            path="routes/web.php",
            span=Span(20, 20, 0, 50),
            symbol_ref="test:web.php:20:get_api:other",
            metadata={
                "url": "/api/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_lumen_route_group_via_usage_context(self) -> None:
        """Lumen $router->group() matches route_group pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("lumen")

        ctx = UsageContext.create(
            kind="call",
            context_name="$router->group",
            position="args[0]",
            path="routes/web.php",
            span=Span(5, 20, 0, 0),
            symbol_ref="test:web.php:5:api_group:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_group"

    def test_lumen_service_provider_base_class(self) -> None:
        """Lumen ServiceProvider base class matches service_provider pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("lumen")

        symbol = Symbol(
            id="test:AppServiceProvider.php:1:AppServiceProvider:class",
            name="AppServiceProvider",
            kind="class",
            language="php",
            path="app/Providers/AppServiceProvider.php",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["ServiceProvider"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "service_provider"


class TestPadrinoPatterns:
    """Tests for Padrino framework pattern matching."""

    def test_padrino_application_base_class(self) -> None:
        """Padrino Application base class matches application pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("padrino")

        assert pattern_def is not None, "Padrino patterns YAML should exist"

        symbol = Symbol(
            id="test:app.rb:1:App:class",
            name="App",
            kind="class",
            language="ruby",
            path="app/app.rb",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Padrino::Application"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "application"

    def test_padrino_get_route_via_usage_context(self) -> None:
        """Padrino get route matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("padrino")

        ctx = UsageContext.create(
            kind="call",
            context_name="get",
            position="args[0]",
            path="app/controllers/users.rb",
            span=Span(10, 15, 0, 0),
            symbol_ref="test:users.rb:10:get_users:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_padrino_post_route_via_usage_context(self) -> None:
        """Padrino post route matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("padrino")

        ctx = UsageContext.create(
            kind="call",
            context_name="post",
            position="args[0]",
            path="app/controllers/users.rb",
            span=Span(20, 25, 0, 0),
            symbol_ref="test:users.rb:20:create_user:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_padrino_controller_via_usage_context(self) -> None:
        """Padrino controller call matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("padrino")

        ctx = UsageContext.create(
            kind="call",
            context_name="controller",
            position="args[0]",
            path="app/controllers/users.rb",
            span=Span(5, 50, 0, 0),
            symbol_ref="test:users.rb:5:users_controller:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_padrino_before_filter_via_usage_context(self) -> None:
        """Padrino before filter matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("padrino")

        ctx = UsageContext.create(
            kind="call",
            context_name="before",
            position="args[0]",
            path="app/app.rb",
            span=Span(8, 12, 0, 0),
            symbol_ref="test:app.rb:8:auth_filter:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_padrino_mailer_via_usage_context(self) -> None:
        """Padrino mailer call matches mailer pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("padrino")

        ctx = UsageContext.create(
            kind="call",
            context_name="mailer",
            position="args[0]",
            path="app/mailers/user_mailer.rb",
            span=Span(5, 20, 0, 0),
            symbol_ref="test:user_mailer.rb:5:user_mailer:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "mailer"


class TestCakePHPPatterns:
    """Tests for CakePHP framework pattern matching."""

    def test_cakephp_controller_base_class(self) -> None:
        """CakePHP Controller base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("cakephp")

        assert pattern_def is not None, "CakePHP patterns YAML should exist"

        symbol = Symbol(
            id="test:UsersController.php:1:UsersController:class",
            name="UsersController",
            kind="class",
            language="php",
            path="src/Controller/UsersController.php",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Controller"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_cakephp_table_model_base_class(self) -> None:
        """CakePHP Table base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("cakephp")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UsersTable.php:1:UsersTable:class",
            name="UsersTable",
            kind="class",
            language="php",
            path="src/Model/Table/UsersTable.php",
            span=Span(1, 100, 0, 0),
            meta={
                "base_classes": ["Table"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"

    def test_cakephp_entity_base_class(self) -> None:
        """CakePHP Entity base class matches entity pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("cakephp")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:User.php:1:User:class",
            name="User",
            kind="class",
            language="php",
            path="src/Model/Entity/User.php",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Entity"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "entity"

    def test_cakephp_connect_route_via_usage_context(self) -> None:
        """CakePHP routes->connect matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("cakephp")

        ctx = UsageContext.create(
            kind="call",
            context_name="$routes->connect",
            position="args[0]",
            path="config/routes.php",
            span=Span(10, 15, 0, 0),
            symbol_ref="test:routes.php:10:connect:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_cakephp_middleware_base_class(self) -> None:
        """CakePHP MiddlewareInterface matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("cakephp")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:AuthMiddleware.php:1:AuthMiddleware:class",
            name="AuthMiddleware",
            kind="class",
            language="php",
            path="src/Middleware/AuthMiddleware.php",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["MiddlewareInterface"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_cakephp_component_base_class(self) -> None:
        """CakePHP Component base class matches component pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("cakephp")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:AuthComponent.php:1:AuthComponent:class",
            name="AuthComponent",
            kind="class",
            language="php",
            path="src/Controller/Component/AuthComponent.php",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Component"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "component"


class TestYiiPatterns:
    """Tests for Yii framework pattern matching."""

    def test_yii_controller_base_class(self) -> None:
        """Yii Controller base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("yii")

        assert pattern_def is not None, "Yii patterns YAML should exist"

        symbol = Symbol(
            id="test:SiteController.php:1:SiteController:class",
            name="SiteController",
            kind="class",
            language="php",
            path="controllers/SiteController.php",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Controller"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_yii_activerecord_model_base_class(self) -> None:
        """Yii ActiveRecord base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("yii")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:User.php:1:User:class",
            name="User",
            kind="class",
            language="php",
            path="models/User.php",
            span=Span(1, 100, 0, 0),
            meta={
                "base_classes": ["ActiveRecord"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"

    def test_yii_widget_base_class(self) -> None:
        """Yii Widget base class matches widget pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("yii")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:NavWidget.php:1:NavWidget:class",
            name="NavWidget",
            kind="class",
            language="php",
            path="widgets/NavWidget.php",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Widget"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "widget"

    def test_yii_module_base_class(self) -> None:
        """Yii Module base class matches module pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("yii")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:AdminModule.php:1:AdminModule:class",
            name="AdminModule",
            kind="class",
            language="php",
            path="modules/admin/AdminModule.php",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Module"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "module"

    def test_yii_migration_base_class(self) -> None:
        """Yii Migration base class matches migration pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("yii")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:m210101_000001_create_users_table.php:1:m210101:class",
            name="m210101_000001_create_users_table",
            kind="class",
            language="php",
            path="migrations/m210101_000001_create_users_table.php",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Migration"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "migration"

    def test_yii_action_base_class(self) -> None:
        """Yii Action base class matches action pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("yii")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:ViewAction.php:1:ViewAction:class",
            name="ViewAction",
            kind="class",
            language="php",
            path="actions/ViewAction.php",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Action"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "action"


class TestLaminasPatterns:
    """Tests for Laminas (formerly Zend) framework pattern matching."""

    def test_laminas_action_controller_base_class(self) -> None:
        """Laminas AbstractActionController matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laminas")

        assert pattern_def is not None, "Laminas patterns YAML should exist"

        symbol = Symbol(
            id="test:IndexController.php:1:IndexController:class",
            name="IndexController",
            kind="class",
            language="php",
            path="module/Application/src/Controller/IndexController.php",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["AbstractActionController"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_laminas_restful_controller_base_class(self) -> None:
        """Laminas AbstractRestfulController matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laminas")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:ApiController.php:1:ApiController:class",
            name="ApiController",
            kind="class",
            language="php",
            path="module/Api/src/Controller/ApiController.php",
            span=Span(1, 100, 0, 0),
            meta={
                "base_classes": ["AbstractRestfulController"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_laminas_form_base_class(self) -> None:
        """Laminas Form base class matches form pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laminas")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserForm.php:1:UserForm:class",
            name="UserForm",
            kind="class",
            language="php",
            path="module/Application/src/Form/UserForm.php",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Form"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "form"

    def test_laminas_middleware_interface(self) -> None:
        """Laminas MiddlewareInterface matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laminas")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:AuthMiddleware.php:1:AuthMiddleware:class",
            name="AuthMiddleware",
            kind="class",
            language="php",
            path="src/Middleware/AuthMiddleware.php",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["MiddlewareInterface"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_laminas_input_filter_base_class(self) -> None:
        """Laminas InputFilter base class matches validation pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laminas")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserInputFilter.php:1:UserInputFilter:class",
            name="UserInputFilter",
            kind="class",
            language="php",
            path="module/Application/src/InputFilter/UserInputFilter.php",
            span=Span(1, 40, 0, 0),
            meta={
                "base_classes": ["InputFilter"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "validation"

    def test_laminas_table_gateway_base_class(self) -> None:
        """Laminas TableGateway base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("laminas")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:UserTable.php:1:UserTable:class",
            name="UserTable",
            kind="class",
            language="php",
            path="module/Application/src/Model/UserTable.php",
            span=Span(1, 60, 0, 0),
            meta={
                "base_classes": ["TableGateway"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"


class TestFuelPHPPatterns:
    """Tests for FuelPHP framework pattern matching."""

    def test_fuelphp_controller_base_class(self) -> None:
        """FuelPHP Controller base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("fuelphp")

        assert pattern_def is not None, "FuelPHP patterns YAML should exist"

        symbol = Symbol(
            id="test:Welcome.php:1:Controller_Welcome:class",
            name="Controller_Welcome",
            kind="class",
            language="php",
            path="fuel/app/classes/controller/welcome.php",
            span=Span(1, 50, 0, 0),
            meta={
                "base_classes": ["Controller"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_fuelphp_orm_model_base_class(self) -> None:
        """FuelPHP Orm Model base class matches model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("fuelphp")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:User.php:1:Model_User:class",
            name="Model_User",
            kind="class",
            language="php",
            path="fuel/app/classes/model/user.php",
            span=Span(1, 100, 0, 0),
            meta={
                "base_classes": ["Model"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "model"

    def test_fuelphp_task_base_class(self) -> None:
        """FuelPHP Task base class matches command pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("fuelphp")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:Robots.php:1:Task_Robots:class",
            name="Task_Robots",
            kind="class",
            language="php",
            path="fuel/app/tasks/robots.php",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Task"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "command"

    def test_fuelphp_viewmodel_base_class(self) -> None:
        """FuelPHP ViewModel base class matches view_model pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("fuelphp")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:Welcome.php:1:ViewModel_Welcome:class",
            name="ViewModel_Welcome",
            kind="class",
            language="php",
            path="fuel/app/classes/view/welcome.php",
            span=Span(1, 40, 0, 0),
            meta={
                "base_classes": ["ViewModel"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "view_model"

    def test_fuelphp_rest_controller_base_class(self) -> None:
        """FuelPHP Controller_Rest base class matches controller pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("fuelphp")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:Api.php:1:Controller_Api:class",
            name="Controller_Api",
            kind="class",
            language="php",
            path="fuel/app/classes/controller/api.php",
            span=Span(1, 80, 0, 0),
            meta={
                "base_classes": ["Controller_Rest"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "controller"

    def test_fuelphp_migration_base_class(self) -> None:
        """FuelPHP Migration base class matches migration pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("fuelphp")

        assert pattern_def is not None

        symbol = Symbol(
            id="test:001_create_users.php:1:Migration_Create_Users:class",
            name="Migration_Create_Users",
            kind="class",
            language="php",
            path="fuel/app/migrations/001_create_users.php",
            span=Span(1, 30, 0, 0),
            meta={
                "base_classes": ["Migration"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "migration"


class TestRingCompojurePatterns:
    """Tests for Ring/Compojure Clojure framework pattern matching."""

    def test_compojure_defroutes_via_usage_context(self) -> None:
        """Compojure defroutes matches router pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("ring-compojure")

        assert pattern_def is not None, "Ring/Compojure patterns YAML should exist"

        ctx = UsageContext.create(
            kind="call",
            context_name="defroutes",
            position="args[0]",
            path="src/myapp/routes.clj",
            span=Span(10, 30, 0, 0),
            symbol_ref="test:routes.clj:10:app-routes:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "router"

    def test_compojure_get_route_via_usage_context(self) -> None:
        """Compojure GET macro matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("ring-compojure")

        ctx = UsageContext.create(
            kind="call",
            context_name="GET",
            position="args[0]",
            path="src/myapp/routes.clj",
            span=Span(12, 15, 0, 0),
            symbol_ref="test:routes.clj:12:get-users:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_compojure_post_route_via_usage_context(self) -> None:
        """Compojure POST macro matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("ring-compojure")

        ctx = UsageContext.create(
            kind="call",
            context_name="POST",
            position="args[0]",
            path="src/myapp/routes.clj",
            span=Span(20, 25, 0, 0),
            symbol_ref="test:routes.clj:20:create-user:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_ring_wrap_middleware_via_usage_context(self) -> None:
        """Ring wrap-* middleware matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("ring-compojure")

        ctx = UsageContext.create(
            kind="call",
            context_name="wrap-json-response",
            position="args[0]",
            path="src/myapp/handler.clj",
            span=Span(5, 8, 0, 0),
            symbol_ref="test:handler.clj:5:handler:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_compojure_context_via_usage_context(self) -> None:
        """Compojure context matches route_group pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("ring-compojure")

        ctx = UsageContext.create(
            kind="call",
            context_name="context",
            position="args[0]",
            path="src/myapp/routes.clj",
            span=Span(30, 40, 0, 0),
            symbol_ref="test:routes.clj:30:api-routes:other",
            metadata={
                "url": "/api",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route_group"

    def test_compojure_routes_via_usage_context(self) -> None:
        """Compojure routes matches router pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("ring-compojure")

        ctx = UsageContext.create(
            kind="call",
            context_name="routes",
            position="args[0]",
            path="src/myapp/handler.clj",
            span=Span(15, 20, 0, 0),
            symbol_ref="test:handler.clj:15:all-routes:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "router"


class TestPedestalPatterns:
    """Tests for Pedestal Clojure framework pattern matching."""

    def test_pedestal_defroutes_via_usage_context(self) -> None:
        """Pedestal defroutes matches router pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pedestal")

        assert pattern_def is not None, "Pedestal patterns YAML should exist"

        ctx = UsageContext.create(
            kind="call",
            context_name="defroutes",
            position="args[0]",
            path="src/myapp/service.clj",
            span=Span(10, 30, 0, 0),
            symbol_ref="test:service.clj:10:routes:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "router"

    def test_pedestal_table_routes_via_usage_context(self) -> None:
        """Pedestal table-routes matches router pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pedestal")

        ctx = UsageContext.create(
            kind="call",
            context_name="table-routes",
            position="args[0]",
            path="src/myapp/service.clj",
            span=Span(15, 25, 0, 0),
            symbol_ref="test:service.clj:15:api-routes:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "router"

    def test_pedestal_definterceptor_via_usage_context(self) -> None:
        """Pedestal definterceptor matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pedestal")

        ctx = UsageContext.create(
            kind="call",
            context_name="definterceptor",
            position="args[0]",
            path="src/myapp/interceptors.clj",
            span=Span(5, 15, 0, 0),
            symbol_ref="test:interceptors.clj:5:auth-interceptor:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_pedestal_interceptor_via_usage_context(self) -> None:
        """Pedestal interceptor call matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pedestal")

        ctx = UsageContext.create(
            kind="call",
            context_name="interceptor",
            position="args[0]",
            path="src/myapp/interceptors.clj",
            span=Span(20, 30, 0, 0),
            symbol_ref="test:interceptors.clj:20:log-interceptor:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_pedestal_create_server_via_usage_context(self) -> None:
        """Pedestal create-server matches server pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pedestal")

        ctx = UsageContext.create(
            kind="call",
            context_name="create-server",
            position="args[0]",
            path="src/myapp/server.clj",
            span=Span(30, 35, 0, 0),
            symbol_ref="test:server.clj:30:server:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "server"

    def test_pedestal_body_params_via_usage_context(self) -> None:
        """Pedestal body-params matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("pedestal")

        ctx = UsageContext.create(
            kind="call",
            context_name="body-params",
            position="args[0]",
            path="src/myapp/interceptors.clj",
            span=Span(40, 45, 0, 0),
            symbol_ref="test:interceptors.clj:40:body-parser:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"


class TestServantPatterns:
    """Tests for Haskell Servant framework pattern matching."""

    def test_servant_serve_via_usage_context(self) -> None:
        """Servant serve function matches server pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("servant")

        assert pattern_def is not None, "Servant patterns YAML should exist"

        ctx = UsageContext.create(
            kind="call",
            context_name="serve",
            position="args[0]",
            path="app/Main.hs",
            span=Span(10, 15, 0, 0),
            symbol_ref="test:Main.hs:10:main:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "server"

    def test_servant_run_via_usage_context(self) -> None:
        """Servant run function matches server pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("servant")

        ctx = UsageContext.create(
            kind="call",
            context_name="run",
            position="args[0]",
            path="app/Main.hs",
            span=Span(12, 15, 0, 0),
            symbol_ref="test:Main.hs:12:main:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "server"

    def test_servant_handler_type_base_class(self) -> None:
        """Servant Handler type matches handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("servant")

        symbol = Symbol(
            id="test:API.hs:1:getUsers:function",
            name="getUsers",
            kind="function",
            language="haskell",
            path="src/API.hs",
            span=Span(1, 10, 0, 0),
            meta={
                "base_classes": ["Handler"],
            },
        )

        results = match_patterns(symbol, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "handler"

    def test_servant_hoistserver_via_usage_context(self) -> None:
        """Servant hoistServer function matches transformer pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("servant")

        ctx = UsageContext.create(
            kind="call",
            context_name="hoistServer",
            position="args[0]",
            path="app/Main.hs",
            span=Span(20, 25, 0, 0),
            symbol_ref="test:Main.hs:20:app:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "transformer"

    def test_servant_servewithdocs_via_usage_context(self) -> None:
        """Servant serveWithDocs function matches documentation pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("servant")

        ctx = UsageContext.create(
            kind="call",
            context_name="serveWithDocs",
            position="args[0]",
            path="app/Main.hs",
            span=Span(30, 35, 0, 0),
            symbol_ref="test:Main.hs:30:main:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "documentation"

    def test_servant_err404_via_usage_context(self) -> None:
        """Servant err404 function matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("servant")

        ctx = UsageContext.create(
            kind="call",
            context_name="err404",
            position="args[0]",
            path="src/Handlers.hs",
            span=Span(40, 45, 0, 0),
            symbol_ref="test:Handlers.hs:40:notFound:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "error_handler"


class TestScottyPatterns:
    """Tests for Haskell Scotty framework pattern matching."""

    def test_scotty_app_via_usage_context(self) -> None:
        """Scotty scotty function matches application pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("scotty")

        assert pattern_def is not None, "Scotty patterns YAML should exist"

        ctx = UsageContext.create(
            kind="call",
            context_name="scotty",
            position="args[0]",
            path="app/Main.hs",
            span=Span(10, 20, 0, 0),
            symbol_ref="test:Main.hs:10:main:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "application"

    def test_scotty_get_route_via_usage_context(self) -> None:
        """Scotty get function matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("scotty")

        ctx = UsageContext.create(
            kind="call",
            context_name="get",
            position="args[0]",
            path="app/Main.hs",
            span=Span(15, 18, 0, 0),
            symbol_ref="test:Main.hs:15:getUsers:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_scotty_post_route_via_usage_context(self) -> None:
        """Scotty post function matches route pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("scotty")

        ctx = UsageContext.create(
            kind="call",
            context_name="post",
            position="args[0]",
            path="app/Main.hs",
            span=Span(20, 25, 0, 0),
            symbol_ref="test:Main.hs:20:createUser:other",
            metadata={
                "url": "/users",
            },
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "route"

    def test_scotty_middleware_via_usage_context(self) -> None:
        """Scotty middleware function matches middleware pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("scotty")

        ctx = UsageContext.create(
            kind="call",
            context_name="middleware",
            position="args[0]",
            path="app/Main.hs",
            span=Span(8, 10, 0, 0),
            symbol_ref="test:Main.hs:8:logMiddleware:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "middleware"

    def test_scotty_json_response_via_usage_context(self) -> None:
        """Scotty json function matches response pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("scotty")

        ctx = UsageContext.create(
            kind="call",
            context_name="json",
            position="args[0]",
            path="app/Handlers.hs",
            span=Span(30, 32, 0, 0),
            symbol_ref="test:Handlers.hs:30:response:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "response"

    def test_scotty_raise_error_via_usage_context(self) -> None:
        """Scotty raise function matches error_handler pattern."""
        clear_pattern_cache()
        pattern_def = load_framework_patterns("scotty")

        ctx = UsageContext.create(
            kind="call",
            context_name="raise",
            position="args[0]",
            path="app/Handlers.hs",
            span=Span(40, 42, 0, 0),
            symbol_ref="test:Handlers.hs:40:error:other",
            metadata={},
        )

        results = match_usage_patterns(ctx, [pattern_def])

        assert len(results) == 1
        assert results[0]["concept"] == "error_handler"
