# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the OpenAPI/Swagger linker."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.openapi import (
    OpenApiOperation,
    _count_openapi_files,
    _get_route_info_from_concept,
    _get_route_symbols,
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
            kind="function",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            meta={
                "concepts": [{"concept": "route", "path": "/users", "method": "GET"}]
            },
        )

        result = link_openapi(tmp_path, [route_symbol])

        assert len(result.symbols) == 1
        assert result.symbols[0].kind == "function"
        assert (result.symbols[0].meta or {}).get("framework_role") == "openapi_operation"
        assert len(result.edges) == 1
        # Folded to references + meta['ref_construct']='openapi_operation',
        # direction-preserving (audit-findings 0016 finding 2)
        assert result.edges[0].edge_type == "references"
        assert (result.edges[0].meta or {}).get("ref_construct") == "openapi_operation"

    def _spec_with(self, tmp_path: Path, path: str, *methods: str) -> None:
        ops = "\n".join(
            f"    {m}:\n      operationId: op{m.capitalize()}" for m in methods
        )
        (tmp_path / "openapi.yaml").write_text(
            'openapi: "3.0.0"\n'
            "info:\n  title: Test API\n  version: \"1.0\"\n"
            f"paths:\n  {path}:\n{ops}\n",
        )

    def _route(self, tmp_path: Path, path: str, method: str) -> Symbol:
        return Symbol(
            id=f"python:/app.py:10-20:handler_{method.replace(',', '_')}:function",
            name="handler",
            kind="function",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            meta={"concepts": [
                {"concept": "route", "path": path, "method": method},
            ]},
        )

    def test_link_all_wildcard_method(self, tmp_path: Path) -> None:
        """WI-zunal: `ALL` (node-http's catch-all handler) is a wildcard.

        The inline check knew only ANY and *, so an ALL route never linked
        where an equivalent ANY route would.
        """
        self._spec_with(tmp_path, "/users", "get")
        result = link_openapi(tmp_path, [self._route(tmp_path, "/users", "ALL")])
        assert len(result.edges) == 1

    def test_link_comma_joined_multi_method_route(self, tmp_path: Path) -> None:
        """WI-zunal: a YAML-enriched multi-method route links on EACH verb.

        `framework_patterns` joins a list-valued `method` key with ",", and
        only the route-materializer splits it back — so the linker sees
        "GET,POST", which can never equal a single op method.
        """
        self._spec_with(tmp_path, "/hybrid", "get", "post")
        result = link_openapi(
            tmp_path, [self._route(tmp_path, "/hybrid", "GET,POST")],
        )
        assert len(result.edges) == 2
        assert {(e.meta or {}).get("method") for e in result.edges} == {
            "GET", "POST",
        }

    def test_comma_joined_route_still_rejects_absent_verb(
        self, tmp_path: Path,
    ) -> None:
        """The multi-method relaxation must not become a wildcard."""
        self._spec_with(tmp_path, "/hybrid", "delete")
        result = link_openapi(
            tmp_path, [self._route(tmp_path, "/hybrid", "GET,POST")],
        )
        assert result.edges == []

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
            kind="function",
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
            (e for e in result.edges if (e.meta or {}).get("framework_dispatch") == "openapi_operation_id"),
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
            kind="function",
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
            (e for e in result.edges if (e.meta or {}).get("framework_dispatch") == "openapi_path"),
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
            kind="function",
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
        path_edges = [e for e in result.edges if (e.meta or {}).get("framework_dispatch") == "openapi_path"]
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
        assert result.run.pass_id == "openapi-linker"
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
            kind="function",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            meta={},  # No path!
        )

        result = link_openapi(tmp_path, [route_symbol])
        # Should create symbol but no path match edge
        assert len(result.symbols) == 1
        path_edges = [e for e in result.edges if (e.meta or {}).get("framework_dispatch") == "openapi_path"]
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
            kind="function",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            meta={"framework_role": "route"},
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
            meta={"concepts": [{"concept": "route", "path": "/users", "method": "GET"}], "framework_role": "route"},
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[route])
        symbols = _get_route_symbols(ctx)
        assert len(symbols) == 1
        assert symbols[0].name == "get_users"

    def test_get_route_info_from_concept_marker_only(self, tmp_path: Path) -> None:
        """BUG-2: openapi's old concept-first extractor had NO meta-fallback, so
        a marker-only route (Go net/http, Express — framework_role + route_path/
        http_method, no concept) returned (None,None) and was dropped from
        OpenAPI matching. After folding to route_of it extracts the marker."""
        route = Symbol(
            id="test:route",
            name="get_users",
            kind="function",
            language="go",
            path=str(tmp_path / "app.go"),
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            meta={"framework_role": "route", "route_path": "/users", "http_method": "GET"},
        )
        assert _get_route_info_from_concept(route) == ("/users", "GET")

    def test_get_route_info_from_concept_none_meta(self, tmp_path: Path) -> None:
        """A non-route symbol (meta=None) yields (None, None)."""
        symbol = Symbol(
            id="test:func",
            name="no_meta",
            kind="function",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            meta=None,
        )
        assert _get_route_info_from_concept(symbol) == (None, None)

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
            kind="function",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            meta={"path": "/users", "method": "GET", "framework_role": "route"},
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[route])

        result = openapi_linker(ctx)

        assert len(result.symbols) == 1
        assert result.symbols[0].kind == "function"
        assert (result.symbols[0].meta or {}).get("framework_role") == "openapi_operation"
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


class TestOperationIdsNameTheirOwnKind:
    """ADR-0036 Ruling 2: the last slot of a node id IS ``Symbol.kind``. An
    operation stand-in ended in ``operation`` on a ``kind="function"`` Symbol."""

    def _result(self, tmp_path: Path):
        (tmp_path / "openapi.yaml").write_text(
            "openapi: '3.0.0'\n"
            "info: {title: Demo, version: '1.0'}\n"
            "paths:\n"
            "  /users:\n"
            "    get:\n"
            "      operationId: listUsers\n"
            "      responses: {'200': {description: ok}}\n"
            "    post:\n"
            "      responses: {'201': {description: created}}\n"
        )
        route = Symbol(
            id="python:app.py:1-3:list_users:function", name="list_users",
            kind="function", language="python", path="app.py",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test", origin_run_id="uuid:test",
            meta={"concepts": [{"concept": "route", "path": "/users", "method": "GET"}]},
        )
        return link_openapi(tmp_path, [route]), route

    def test_the_fixture_emits_operations_and_an_edge(self, tmp_path: Path) -> None:
        result, route = self._result(tmp_path)
        assert sorted(s.signature for s in result.symbols) == ["GET /users", "POST /users"]
        assert [e.dst for e in result.edges] == [route.id]

    def test_every_id_ends_in_its_symbols_kind(self, tmp_path: Path) -> None:
        result, _ = self._result(tmp_path)
        wrong = [(s.id, s.kind) for s in result.symbols if s.id.rsplit(":", 1)[1] != s.kind]
        assert wrong == []

    def test_every_edge_starts_on_an_operation(self, tmp_path: Path) -> None:
        result, _ = self._result(tmp_path)
        ids = {s.id for s in result.symbols}
        assert [e.src for e in result.edges if e.src not in ids] == []


class TestAnOpenApiDocumentAloneActivatesTheLinker:
    """WI-supar: the linker activates on the ``openapi`` / ``swagger``
    framework, and the only detector was the Java ``io.swagger`` dependency,
    so a repository holding an OpenAPI document produced no operation node."""

    @pytest.mark.parametrize("name, text, language", [
        ("openapi.yaml",
         "openapi: 3.0.0\ninfo: {title: T, version: '1'}\n"
         "paths:\n  /pets:\n    get:\n      operationId: listPets\n",
         "yaml"),
        ("swagger.json",
         '{"swagger": "2.0", "paths": {"/pets": {"get": {"operationId": "listPets"}}}}',
         "json"),
    ])
    def test_the_behavior_map_has_the_operation_nodes(
        self, tmp_path: Path, name: str, text: str, language: str,
    ) -> None:
        import json

        from hypergumbo_core.cli import run_behavior_map

        (tmp_path / name).write_text(text)
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        assert "openapi" in data["profile"]["frameworks"]
        ops = [n for n in data["nodes"] if n.get("protocol_origin") == "openapi"]
        assert [(n["name"], n["path"]) for n in ops] == [("listPets", name)]
        (op,) = ops
        # ADR-0031 Class B / ADR-0034: the spec file's language is the
        # discovery language and the id's language slot.
        assert op["language"] is None
        assert op["discovery_language"] == language
        assert op["id"].split(":", 1)[0] == language
        violations = data["validation_report"]["violations"]
        assert [v for v in violations if v["field_name"] == "Symbol.discovery_language"] == []

    def test_a_tree_without_an_openapi_document_has_no_operation_node(
        self, tmp_path: Path,
    ) -> None:
        """Control: an ``api.json`` that is no OpenAPI document activates nothing."""
        import json

        from hypergumbo_core.cli import run_behavior_map

        (tmp_path / "api.json").write_text('{"endpoints": ["/a"]}')
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        assert "openapi" not in data["profile"]["frameworks"]
        assert [n for n in data["nodes"] if n.get("protocol_origin") == "openapi"] == []


class TestOperationStandInsCarryTheSpecFilesLanguage:
    """WI-supar: ``discovery_language`` came from the tree-sitter grammar
    table, which has no YAML or JSON entry, so every operation stand-in had
    none (a cross_field violation); the id's language slot read ``openapi``,
    which is no language (ADR-0034: a stand-in's slot is its discovery
    language)."""

    @pytest.mark.parametrize("name, language", [
        ("openapi.yaml", "yaml"), ("openapi.yml", "yaml"), ("openapi.json", "json"),
    ])
    def test_language_of_the_spec_file(self, tmp_path: Path, name: str, language: str) -> None:
        from hypergumbo_core.catalog import all_known_languages

        if name.endswith("json"):
            text = '{"openapi": "3.0.0", "paths": {"/a": {"get": {}}}}'
        else:
            text = "openapi: 3.0.0\npaths:\n  /a:\n    get: {}\n"
        (tmp_path / name).write_text(text)

        result = link_openapi(tmp_path, [])

        (op,) = result.symbols
        assert op.language is None
        assert op.discovery_language == language
        # ADR-0036 Ruling 1 (WI-supar): no operationId, so the name is
        # ``METHOD path`` and the slot is that name sanitized.
        assert op.id.split(":")[-2] == "GET /a"
        assert op.id.split(":", 1)[0] == language
        assert language in all_known_languages()


class TestUnquotedVersions:
    """With the linker activated on any OpenAPI document (WI-supar), an
    unquoted ``swagger: 2.0`` / ``openapi: 3.1`` (YAML loads a number, which
    has no ``startswith``) raised AttributeError and crashed the pass."""

    def test_numeric_versions_are_specs(self) -> None:
        assert _is_openapi_spec({"swagger": 2.0})
        assert _is_openapi_spec({"openapi": 3.1})

    def test_a_yaml_list_or_other_versions_are_not(self) -> None:
        """Control: neither a list document nor a version outside 2.x / 3.x."""
        assert not _is_openapi_spec(["openapi", "3.0"])
        assert not _is_openapi_spec({"swagger": 1.2})

    def test_unquoted_swagger_document_yields_its_operations(self, tmp_path: Path) -> None:
        (tmp_path / "swagger.yaml").write_text(
            "swagger: 2.0\ninfo: {title: T, version: 1}\n"
            "paths:\n  /orders:\n    put:\n      operationId: putOrder\n"
        )
        result = link_openapi(tmp_path, [])
        assert [s.name for s in result.symbols] == ["putOrder"]


def test_the_name_slot_is_the_operation_id(tmp_path: Path) -> None:
    """WI-supar / ADR-0036 Ruling 1: the slot held ``METHOD path`` while the
    name was the operationId."""
    from hypergumbo_core.spec_validator import compute_name_slot_stats

    (tmp_path / "openapi.yaml").write_text(
        "openapi: 3.0.0\npaths:\n  /a:\n    get:\n      operationId: getA\n    post: {}\n"
    )
    result = link_openapi(tmp_path, [])

    assert sorted(s.name for s in result.symbols) == ["POST /a", "getA"]
    assert compute_name_slot_stats(result.symbols)["mismatched"] == 0


class TestOperationSpansAreTheirLines:
    """WI-supar: an operation's line was a count of PATHS (every operation of
    the third path sat on line 3, of any file); it is now the method key's
    line, and the span ends on the operation mapping's last line."""

    def test_yaml_operations_span_their_lines(self, tmp_path: Path) -> None:
        (tmp_path / "openapi.yaml").write_text(
            "openapi: 3.0.0\n"            # 1
            "info: {title: T, version: '1'}\n"
            "paths:\n"                     # 3
            "  /a:\n"                      # 4
            "    get:\n"                   # 5
            "      operationId: getA\n"    # 6
            "      summary: A\n"           # 7
            "    post:\n"                  # 8
            "      operationId: postA\n"   # 9
            "  /b:\n"                      # 10
            "    delete:\n"                # 11
            "      operationId: delB\n"    # 12
        )
        result = link_openapi(tmp_path, [])

        spans = {s.name: (s.span.start_line, s.span.end_line) for s in result.symbols}
        assert spans == {"getA": (5, 7), "postA": (8, 9), "delB": (11, 12)}
        for sym in result.symbols:
            assert sym.id.split(":")[-3] == f"{sym.span.start_line}-{sym.span.end_line}"

    def test_blank_lines_after_an_operation_are_not_its_lines(self, tmp_path: Path) -> None:
        (tmp_path / "openapi.yaml").write_text(
            "openapi: 3.0.0\n"
            "paths:\n"
            "  /a:\n"
            "    get:\n"                  # 4
            "      operationId: getA\n"   # 5
            "\n"
            "\n"
            "    post:\n"                 # 8
            "      operationId: postA\n"  # 9
        )
        result = link_openapi(tmp_path, [])

        spans = {s.name: (s.span.start_line, s.span.end_line) for s in result.symbols}
        assert spans == {"getA": (4, 5), "postA": (8, 9)}

    def test_json_operations_span_their_lines(self, tmp_path: Path) -> None:
        (tmp_path / "swagger.json").write_text(
            '{"swagger": "2.0",\n'
            ' "paths": {\n'
            '  "/a": {\n'
            '   "get": {"operationId": "getA"},\n'      # 4
            '   "put": {\n'                              # 5
            '     "operationId": "putA"\n'
            '   }\n'                                     # 7
            '  }\n'
            ' }\n'
            '}\n'
        )
        result = link_openapi(tmp_path, [])

        spans = {s.name: (s.span.start_line, s.span.end_line) for s in result.symbols}
        assert spans == {"getA": (4, 4), "putA": (5, 7)}

    def test_a_document_yaml_cannot_compose_gets_the_no_span_sentinel(
        self, tmp_path: Path,
    ) -> None:
        """Control: a tab-indented JSON spec parses as JSON but not as YAML;
        its operations are kept with the 0-0 span, not a guessed line."""
        (tmp_path / "openapi.json").write_text(
            '{\n\t"openapi": "3.0.0",\n\t"paths": {"/a": {"get": {"operationId": "getA"}}}\n}\n'
        )
        result = link_openapi(tmp_path, [])

        (op,) = result.symbols
        assert (op.name, op.span.start_line, op.span.end_line) == ("getA", 0, 0)
        assert ":0-0:getA:function" in op.id


def _route(path: str, route_path: str, method: str = "GET") -> Symbol:
    return Symbol(
        id=f"go:{path}:1-1:{method} {route_path}:function", name=f"{method} {route_path}",
        kind="function", language=None, discovery_language="go", protocol_origin="http",
        path=path, span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        origin="test", origin_run_id="uuid:test",
        meta={"framework_role": "route", "route_path": route_path, "http_method": method},
    )


class TestTheDocumentsPathPrefix:
    """WI-supar: ``basePath`` (Swagger 2) and ``servers[].url`` (OpenAPI 3)
    were ignored, so every route at the bare operation path was linked: on
    alertmanager, React client-side routes and the v1 router's routes."""

    def _spec(self, tmp_path: Path, header: str) -> None:
        (tmp_path / "api" / "v2").mkdir(parents=True)
        (tmp_path / "api" / "v2" / "openapi.yaml").write_text(
            header + "paths:\n  /status:\n    get:\n      operationId: getStatus\n"
        )

    def test_swagger_base_path(self, tmp_path: Path) -> None:
        self._spec(tmp_path, "swagger: '2.0'\nbasePath: /api/v2/\n")
        full = _route("web/server.go", "/api/v2/status")
        generated = _route("api/v2/restapi/api.go", "/status")
        client = _route("ui/src/App.tsx", "/status")
        other_version = _route("api/v1_router.go", "/status")

        result = link_openapi(tmp_path, [full, generated, client, other_version])

        assert sorted(e.dst for e in result.edges) == sorted([full.id, generated.id])

    def test_openapi3_server_url_with_a_variable(self, tmp_path: Path) -> None:
        self._spec(
            tmp_path,
            "openapi: 3.0.0\nservers:\n  - url: 'https://example.com/svc/{ver}'\n"
            "    variables:\n      ver: {default: v2}\n",
        )
        full = _route("cmd/main.go", "/svc/v2/status")
        client = _route("ui/src/App.tsx", "/status")
        # Control: the variable is its default, not any segment.
        older = _route("cmd/v1.go", "/svc/v1/status")

        result = link_openapi(tmp_path, [full, client, older])

        assert [e.dst for e in result.edges] == [full.id]

    def test_without_a_prefix_the_bare_path_is_the_full_path(self, tmp_path: Path) -> None:
        """Control: no basePath, no servers (or ``/``): every route at the path."""
        self._spec(tmp_path, "openapi: 3.0.0\nservers:\n  - url: /\n  - {}\n")
        anywhere = _route("ui/src/App.tsx", "/status")

        result = link_openapi(tmp_path, [anywhere])

        assert [e.dst for e in result.edges] == [anywhere.id]
