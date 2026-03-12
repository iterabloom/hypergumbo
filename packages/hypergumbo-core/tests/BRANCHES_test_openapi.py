# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for openapi.py linker.

Tests specific branch paths that may not be covered by the main test suite.
"""
from pathlib import Path

import pytest

from hypergumbo_core.linkers.openapi import (
    _get_route_info_from_concept,
    link_openapi,
)
from hypergumbo_core.ir import Symbol, Span


def make_symbol(
    id_: str,
    name: str,
    kind: str = "function",
    path: str = "app.py",
    language: str = "python",
    meta: dict | None = None,
) -> Symbol:
    """Create a test symbol."""
    return Symbol(
        id=id_,
        name=name,
        kind=kind,
        path=path,
        language=language,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        meta=meta,
    )


class TestGetRouteInfoFromConcept:
    """Branch coverage for _get_route_info_from_concept function."""

    def test_concept_not_dict(self) -> None:
        """Test when concept is not a dict (branch 272->271).

        The concepts list might contain non-dict items which should be skipped.
        """
        sym = make_symbol(
            "id1",
            "my_handler",
            meta={"concepts": ["string_concept", None, 123]},
        )
        path, method = _get_route_info_from_concept(sym)
        # Should return None, None since no valid route concept
        assert path is None
        assert method is None

    def test_concept_dict_but_not_route(self) -> None:
        """Test when concept is dict but not a route concept (branch 272->271)."""
        sym = make_symbol(
            "id1",
            "my_handler",
            meta={"concepts": [
                {"concept": "handler"},  # Not a route
                {"concept": "middleware"},  # Not a route
            ]},
        )
        path, method = _get_route_info_from_concept(sym)
        # Should return None, None since no route concept
        assert path is None
        assert method is None

    def test_mixed_concepts_with_route(self) -> None:
        """Test with mixed concepts including a route."""
        sym = make_symbol(
            "id1",
            "my_handler",
            meta={"concepts": [
                {"concept": "handler"},  # Not a route
                "string_item",  # Not a dict
                {"concept": "route", "path": "/api/users", "method": "GET"},  # Route
            ]},
        )
        path, method = _get_route_info_from_concept(sym)
        # Should find the route concept after iterating past others
        assert path == "/api/users"
        assert method == "GET"

    def test_empty_concepts_list(self) -> None:
        """Test with empty concepts list."""
        sym = make_symbol("id1", "my_handler", meta={"concepts": []})
        path, method = _get_route_info_from_concept(sym)
        assert path is None
        assert method is None

    def test_no_meta(self) -> None:
        """Test symbol with no meta."""
        sym = make_symbol("id1", "my_handler", meta=None)
        path, method = _get_route_info_from_concept(sym)
        assert path is None
        assert method is None


class TestLinkOpenapi:
    """Branch coverage for link_openapi function."""

    def test_spec_file_with_no_operations(self, tmp_path: Path) -> None:
        """Test when OpenAPI file has no operations (branch 316->309).

        A valid OpenAPI spec that has no paths should be parsed
        but contribute no operations.
        """
        # Create a minimal OpenAPI spec with no paths
        spec_file = tmp_path / "openapi.yaml"
        spec_file.write_text('''
openapi: "3.0.0"
info:
  title: Empty API
  version: "1.0.0"
paths: {}
''')

        result = link_openapi(tmp_path, [])
        # File was found and parsed, but no operations
        assert result is not None
        assert len(result.symbols) == 0

    def test_spec_with_operations(self, tmp_path: Path) -> None:
        """Test OpenAPI spec with operations creates symbols."""
        spec_file = tmp_path / "openapi.yaml"
        spec_file.write_text('''
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0.0"
paths:
  /users:
    get:
      operationId: listUsers
      responses:
        "200":
          description: Success
''')

        result = link_openapi(tmp_path, [])
        # Should have one symbol for the GET /users operation
        assert result is not None
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "listUsers"

    def test_invalid_yaml_file(self, tmp_path: Path) -> None:
        """Test with invalid YAML file."""
        spec_file = tmp_path / "openapi.yaml"
        spec_file.write_text('''
this is not: valid: yaml:
  - broken
''')

        result = link_openapi(tmp_path, [])
        # Should return empty result (file failed to parse)
        assert result is not None
        assert len(result.symbols) == 0

    def test_yaml_not_openapi(self, tmp_path: Path) -> None:
        """Test with valid YAML that's not OpenAPI spec."""
        spec_file = tmp_path / "openapi.yaml"
        spec_file.write_text('''
# Just some random YAML config
database:
  host: localhost
  port: 5432
''')

        result = link_openapi(tmp_path, [])
        # Should return empty result (not recognized as OpenAPI)
        assert result is not None
        assert len(result.symbols) == 0
