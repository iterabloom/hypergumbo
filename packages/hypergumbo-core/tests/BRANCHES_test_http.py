"""Branch coverage tests for http.py linker.

Tests specific branch paths that may not be covered by the main test suite.
"""
from pathlib import Path

import pytest

from hypergumbo_core.linkers.http import (
    link_http,
    _match_route_pattern,
)
from hypergumbo_core.ir import Symbol, Span


def make_route_symbol(
    id_: str,
    name: str,
    path: str = "routes.py",
    language: str = "python",
    route_path: str = "/api/users",
    method: str = "GET",
) -> Symbol:
    """Create a test route symbol."""
    return Symbol(
        id=id_,
        name=name,
        kind="route",
        path=path,
        language=language,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        meta={
            "concepts": [
                {"concept": "route", "path": route_path, "method": method}
            ]
        },
    )


class TestMatchRoutePattern:
    """Branch coverage for _match_route_pattern function."""

    def test_no_match_different_paths(self) -> None:
        """Test when paths don't match at all."""
        assert _match_route_pattern("/api/users", "/api/products") is False

    def test_match_exact_path(self) -> None:
        """Test exact path match."""
        assert _match_route_pattern("/api/users", "/api/users") is True

    def test_match_with_param(self) -> None:
        """Test path with parameter placeholder."""
        assert _match_route_pattern("/api/users/123", "/api/users/:id") is True

    def test_no_match_different_segment_count(self) -> None:
        """Test paths with different segment counts."""
        assert _match_route_pattern("/api/users", "/api/users/all") is False


class TestLinkHttp:
    """Branch coverage for link_http function."""

    def test_call_path_no_match(self, tmp_path: Path) -> None:
        """Test when HTTP call path doesn't match route (branch 457->439).

        When the request path doesn't match the route pattern,
        no edge should be created.
        """
        # Create a Python file with HTTP client call
        py_file = tmp_path / "client.py"
        py_file.write_text('''
import requests

response = requests.get("http://localhost/api/products")
''')

        # Route that doesn't match the request path
        route_sym = make_route_symbol(
            "route1",
            "get_users",
            route_path="/api/users",  # Doesn't match /api/products
            method="GET",
        )

        result = link_http(tmp_path, [route_sym])
        # Call found but no matching route - no edges
        assert result is not None
        assert len(result.edges) == 0

    def test_method_mismatch(self, tmp_path: Path) -> None:
        """Test when HTTP method doesn't match route method."""
        py_file = tmp_path / "client.py"
        py_file.write_text('''
import requests

response = requests.post("http://localhost/api/users")
''')

        route_sym = make_route_symbol(
            "route1",
            "get_users",
            route_path="/api/users",
            method="GET",  # POST call won't match GET route
        )

        result = link_http(tmp_path, [route_sym])
        # Call found but method mismatch - no edges
        assert result is not None
        assert len(result.edges) == 0

    def test_matching_call_and_route(self, tmp_path: Path) -> None:
        """Test successful matching creates edge."""
        py_file = tmp_path / "client.py"
        py_file.write_text('''
import requests

response = requests.get("http://localhost/api/users")
''')

        route_sym = make_route_symbol(
            "route1",
            "get_users",
            route_path="/api/users",
            method="GET",
        )

        result = link_http(tmp_path, [route_sym])
        # Should create edge linking call to route
        assert result is not None
        assert len(result.edges) == 1
        assert result.edges[0].dst == "route1"

    def test_multiple_routes_one_match(self, tmp_path: Path) -> None:
        """Test with multiple routes where only one matches."""
        py_file = tmp_path / "client.py"
        py_file.write_text('''
import requests

response = requests.get("http://localhost/api/orders")
''')

        routes = [
            make_route_symbol("route1", "get_users", route_path="/api/users", method="GET"),
            make_route_symbol("route2", "get_orders", route_path="/api/orders", method="GET"),
            make_route_symbol("route3", "get_products", route_path="/api/products", method="GET"),
        ]

        result = link_http(tmp_path, routes)
        # Only route2 should match
        assert result is not None
        assert len(result.edges) == 1
        assert result.edges[0].dst == "route2"
