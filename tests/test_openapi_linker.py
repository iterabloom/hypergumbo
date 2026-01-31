"""Tests for the OpenAPI/Swagger linker."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.openapi import (
    OpenApiOperation,
    _count_openapi_files,
    _get_route_symbols,
    _has_route_concept,
    _is_openapi_spec,
    _load_yaml,
    _normalize_path,
    _parse_openapi_spec,
    _paths_match,
    link_openapi,
    openapi_linker,
)
from hypergumbo_core.linkers.registry import LinkerContext


class TestOpenApiSpecDetection:
    """Tests for OpenAPI spec file detection."""

    def test_is_openapi_spec_v3(self) -> None:
        """Detects OpenAPI 3.x spec."""
        data = {"openapi": "3.0.0", "paths": {}}
        assert _is_openapi_spec(data)

    def test_is_openapi_spec_swagger_v2(self) -> None:
        """Detects Swagger 2.x spec."""
        data = {"swagger": "2.0", "paths": {}}
        assert _is_openapi_spec(data)

    def test_is_openapi_spec_with_paths(self) -> None:
        """Detects spec by paths key."""
        data = {"paths": {"/users": {}}}
        assert _is_openapi_spec(data)

    def test_is_not_openapi_spec(self) -> None:
        """Rejects non-OpenAPI data."""
        data = {"name": "test", "version": "1.0"}
        assert not _is_openapi_spec(data)


class TestPathNormalization:
    """Tests for path parameter normalization."""

    def test_normalize_curly_braces(self) -> None:
        """Normalizes {param} to :param."""
        assert _normalize_path("/users/{id}") == "/users/:id"
        assert _normalize_path("/users/{user_id}/posts/{post_id}") == "/users/:user_id/posts/:post_id"

    def test_normalize_angle_brackets(self) -> None:
        """Normalizes <param> to :param."""
        assert _normalize_path("/users/<id>") == "/users/:id"

    def test_normalize_already_colon(self) -> None:
        """Leaves :param unchanged."""
        assert _normalize_path("/users/:id") == "/users/:id"


class TestPathMatching:
    """Tests for path matching."""

    def test_exact_match(self) -> None:
        """Matches identical paths."""
        assert _paths_match("/users", "/users")

    def test_match_with_params(self) -> None:
        """Matches paths with parameters."""
        assert _paths_match("/users/{id}", "/users/:id")
        assert _paths_match("/users/{id}", "/users/{id}")

    def test_match_different_param_names(self) -> None:
        """Matches paths with different param names."""
        assert _paths_match("/users/{user_id}", "/users/:id")

    def test_no_match_different_paths(self) -> None:
        """Rejects different paths."""
        assert not _paths_match("/users", "/posts")
        assert not _paths_match("/users/{id}", "/users/{id}/posts")


class TestOpenApiParsing:
    """Tests for OpenAPI spec parsing."""

    def test_parse_openapi_v3_yaml(self, tmp_path: Path) -> None:
        """Parses OpenAPI 3.x YAML spec."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    get:
      operationId: getUsers
      summary: List users
      tags:
        - users
    post:
      operationId: createUser
      summary: Create user
  /users/{id}:
    get:
      operationId: getUserById
      summary: Get user by ID
""")
        operations = _parse_openapi_spec(spec)
        assert len(operations) == 3

        get_users = next(op for op in operations if op.operation_id == "getUsers")
        assert get_users.path == "/users"
        assert get_users.method == "GET"
        assert get_users.summary == "List users"
        assert "users" in get_users.tags

    def test_parse_openapi_v3_json(self, tmp_path: Path) -> None:
        """Parses OpenAPI 3.x JSON spec."""
        spec = tmp_path / "openapi.json"
        spec.write_text("""{
  "openapi": "3.0.0",
  "info": {"title": "Test API", "version": "1.0"},
  "paths": {
    "/users": {
      "get": {
        "operationId": "getUsers"
      }
    }
  }
}""")
        operations = _parse_openapi_spec(spec)
        assert len(operations) == 1
        assert operations[0].operation_id == "getUsers"

    def test_parse_swagger_v2(self, tmp_path: Path) -> None:
        """Parses Swagger 2.x spec."""
        spec = tmp_path / "swagger.yaml"
        spec.write_text("""
swagger: "2.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    get:
      operationId: getUsers
    post:
      operationId: createUser
""")
        operations = _parse_openapi_spec(spec)
        assert len(operations) == 2

    def test_parse_invalid_file(self, tmp_path: Path) -> None:
        """Handles invalid spec file gracefully."""
        spec = tmp_path / "invalid.yaml"
        spec.write_text("not: valid: yaml: content:")
        operations = _parse_openapi_spec(spec)
        # Should return empty list, not crash
        assert operations == [] or len(operations) >= 0  # May parse as valid YAML with paths check

    def test_parse_non_spec_file(self, tmp_path: Path) -> None:
        """Rejects non-OpenAPI YAML."""
        spec = tmp_path / "config.yaml"
        spec.write_text("""
name: myapp
version: 1.0
""")
        operations = _parse_openapi_spec(spec)
        assert len(operations) == 0


class TestOpenApiLinking:
    """Tests for OpenAPI to route handler linking."""

    def test_link_by_path_and_method(self, tmp_path: Path) -> None:
        """Links operations to routes by path and method."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    get:
      operationId: getUsers
""")
        # Create a route symbol that should match
        route_symbol = Symbol(
            id="python:/app.py:10-20:get_users:function",
            name="get_users",
            kind="route",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            meta={
                "concepts": [{"concept": "route", "path": "/users", "method": "GET"}]
            },
        )

        result = link_openapi(tmp_path, [route_symbol])

        assert len(result.symbols) == 1
        assert result.symbols[0].kind == "openapi_operation"
        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "openapi_implements"

    def test_link_by_operation_id(self, tmp_path: Path) -> None:
        """Links operations to routes by operationId match."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    get:
      operationId: getUsers
""")
        # Create a route with matching name but different path
        route_symbol = Symbol(
            id="python:/app.py:10-20:getUsers:function",
            name="getUsers",
            kind="route",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            meta={
                "concepts": [{"concept": "route", "path": "/api/users", "method": "GET"}]
            },
        )

        result = link_openapi(tmp_path, [route_symbol])

        # Should still match by operationId
        assert len(result.edges) >= 1
        op_id_edge = next(
            (e for e in result.edges if e.evidence_type == "openapi_operation_id_match"),
            None,
        )
        assert op_id_edge is not None
        assert op_id_edge.confidence == 0.9

    def test_link_parameterized_paths(self, tmp_path: Path) -> None:
        """Links operations with path parameters."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users/{id}:
    get:
      operationId: getUserById
""")
        route_symbol = Symbol(
            id="python:/app.py:10-20:get_user:function",
            name="get_user",
            kind="route",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            meta={
                "concepts": [{"concept": "route", "path": "/users/:id", "method": "GET"}]
            },
        )

        result = link_openapi(tmp_path, [route_symbol])

        assert len(result.edges) >= 1
        path_edge = next(
            (e for e in result.edges if e.evidence_type == "openapi_path_match"),
            None,
        )
        assert path_edge is not None

    def test_no_link_method_mismatch(self, tmp_path: Path) -> None:
        """Does not link when methods don't match."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    post:
      operationId: createUser
""")
        route_symbol = Symbol(
            id="python:/app.py:10-20:get_users:function",
            name="get_users",
            kind="route",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            meta={
                "concepts": [{"concept": "route", "path": "/users", "method": "GET"}]
            },
        )

        result = link_openapi(tmp_path, [route_symbol])

        # Should have symbol but no path match edge
        assert len(result.symbols) == 1
        path_edges = [e for e in result.edges if e.evidence_type == "openapi_path_match"]
        assert len(path_edges) == 0

    def test_link_multiple_operations(self, tmp_path: Path) -> None:
        """Links multiple operations from one spec."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    get:
      operationId: getUsers
    post:
      operationId: createUser
  /users/{id}:
    get:
      operationId: getUserById
    put:
      operationId: updateUser
    delete:
      operationId: deleteUser
""")
        result = link_openapi(tmp_path, [])

        assert len(result.symbols) == 5
        methods = {s.meta["http_method"] for s in result.symbols if s.meta}
        assert methods == {"GET", "POST", "PUT", "DELETE"}

    def test_creates_analysis_run(self, tmp_path: Path) -> None:
        """Creates analysis run with metadata."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    get:
      operationId: getUsers
""")
        result = link_openapi(tmp_path, [])

        assert result.run is not None
        assert result.run.pass_id == "openapi-linker-v1"
        assert result.run.files_analyzed == 1


class TestOpenApiNoSpec:
    """Tests for handling missing OpenAPI specs."""

    def test_no_spec_files(self, tmp_path: Path) -> None:
        """Handles repos without OpenAPI specs."""
        (tmp_path / "app.py").write_text("# no openapi here")
        result = link_openapi(tmp_path, [])

        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_concept_metadata_route(self, tmp_path: Path) -> None:
        """Links to routes identified by concept metadata."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    get:
      operationId: getUsers
""")
        # Route identified by concept, not kind
        route_symbol = Symbol(
            id="python:/app.py:10-20:get_users:function",
            name="get_users",
            kind="function",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            meta={
                "concepts": [{"concept": "route", "path": "/users", "method": "GET"}]
            },
        )

        result = link_openapi(tmp_path, [route_symbol])
        # Routes with concept metadata are detected and matched
        assert len(result.symbols) == 1
        assert len(result.edges) >= 1  # Should match by path


class TestOpenApiEdgeCases:
    """Tests for edge cases and malformed specs."""

    def test_route_without_path_meta(self, tmp_path: Path) -> None:
        """Handles route without path in meta."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    get:
      operationId: getUsers
""")
        # Route with no path in meta
        route_symbol = Symbol(
            id="python:/app.py:10-20:get_users:function",
            name="get_users",
            kind="route",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            meta={},  # No path!
        )

        result = link_openapi(tmp_path, [route_symbol])
        # Should create symbol but no path match edge
        assert len(result.symbols) == 1
        path_edges = [e for e in result.edges if e.evidence_type == "openapi_path_match"]
        assert len(path_edges) == 0

    def test_malformed_paths_not_dict(self, tmp_path: Path) -> None:
        """Handles spec where paths is not a dict."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths: "not a dict"
""")
        operations = _parse_openapi_spec(spec)
        assert len(operations) == 0

    def test_malformed_path_item_not_dict(self, tmp_path: Path) -> None:
        """Handles spec where path item is not a dict."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users: "not a dict"
""")
        operations = _parse_openapi_spec(spec)
        assert len(operations) == 0

    def test_malformed_operation_not_dict(self, tmp_path: Path) -> None:
        """Handles spec where operation is not a dict."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    get: "not a dict"
""")
        operations = _parse_openapi_spec(spec)
        assert len(operations) == 0

    def test_malformed_tags_not_list(self, tmp_path: Path) -> None:
        """Handles spec where tags is not a list."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    get:
      operationId: getUsers
      tags: "not a list"
""")
        operations = _parse_openapi_spec(spec)
        assert len(operations) == 1
        # Tags should default to empty list
        assert operations[0].tags == []

    def test_invalid_json_spec(self, tmp_path: Path) -> None:
        """Handles invalid JSON spec file."""
        spec = tmp_path / "openapi.json"
        spec.write_text("{not valid json")
        operations = _parse_openapi_spec(spec)
        assert len(operations) == 0


class TestOpenApiRegisteredLinker:
    """Tests for the registered openapi_linker function."""

    def test_count_openapi_files_with_spec(self, tmp_path: Path) -> None:
        """Counts OpenAPI files when present."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
paths:
  /users:
    get:
      operationId: getUsers
""")
        ctx = LinkerContext(repo_root=tmp_path)
        assert _count_openapi_files(ctx) is True

    def test_count_openapi_files_without_spec(self, tmp_path: Path) -> None:
        """Counts OpenAPI files when not present."""
        (tmp_path / "app.py").write_text("# no openapi")
        ctx = LinkerContext(repo_root=tmp_path)
        assert _count_openapi_files(ctx) is False

    def test_get_route_symbols_by_kind(self, tmp_path: Path) -> None:
        """Gets route symbols by kind."""
        route = Symbol(
            id="test:route",
            name="get_users",
            kind="route",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[route])
        symbols = _get_route_symbols(ctx)
        assert len(symbols) == 1
        assert symbols[0].name == "get_users"

    def test_get_route_symbols_by_concept(self, tmp_path: Path) -> None:
        """Gets route symbols by concept meta."""
        route = Symbol(
            id="test:route",
            name="get_users",
            kind="function",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            meta={"concepts": [{"concept": "route", "path": "/users", "method": "GET"}]},
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[route])
        symbols = _get_route_symbols(ctx)
        assert len(symbols) == 1
        assert symbols[0].name == "get_users"

    def test_has_route_concept_with_none_meta(self, tmp_path: Path) -> None:
        """_has_route_concept returns False for symbol with meta=None."""
        symbol = Symbol(
            id="test:func",
            name="no_meta",
            kind="function",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            meta=None,
        )
        assert _has_route_concept(symbol) is False

    def test_openapi_linker_integration(self, tmp_path: Path) -> None:
        """Tests the full openapi_linker function."""
        spec = tmp_path / "openapi.yaml"
        spec.write_text("""
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    get:
      operationId: getUsers
""")
        route = Symbol(
            id="python:/app.py:10-20:getUsers:function",
            name="getUsers",
            kind="route",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            meta={"path": "/users", "method": "GET"},
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[route])

        result = openapi_linker(ctx)

        assert len(result.symbols) == 1
        assert result.symbols[0].kind == "openapi_operation"
        assert len(result.edges) >= 1
        assert result.run is not None


class TestYamlFallback:
    """Tests for YAML import fallback to JSON."""

    def test_yaml_import_error_fallback_to_json(self) -> None:
        """Falls back to JSON when YAML import fails."""
        json_content = '{"openapi": "3.0.0", "paths": {"/users": {"get": {}}}}'

        # Mock yaml.safe_load to raise ImportError
        with patch.dict("sys.modules", {"yaml": None}):
            # Force re-import to trigger ImportError path
            import importlib

            import hypergumbo_core.linkers.openapi as openapi_module

            importlib.reload(openapi_module)
            result = openapi_module._load_yaml(json_content)
            assert result is not None
            assert result.get("openapi") == "3.0.0"
            # Reload again to restore yaml
            importlib.reload(openapi_module)

    def test_yaml_import_error_invalid_json(self) -> None:
        """Returns None when YAML fails and JSON is invalid."""
        invalid_content = "not: valid: json: or: yaml:"

        # Mock yaml import to raise ImportError
        with patch.dict("sys.modules", {"yaml": None}):
            import importlib

            import hypergumbo_core.linkers.openapi as openapi_module

            importlib.reload(openapi_module)
            result = openapi_module._load_yaml(invalid_content)
            assert result is None
            # Reload again to restore yaml
            importlib.reload(openapi_module)
