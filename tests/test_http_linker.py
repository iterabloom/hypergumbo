"""Tests for HTTP client-server linker."""

from pathlib import Path
from textwrap import dedent

from hypergumbo.ir import Span, Symbol
from hypergumbo.linkers.http import (
    _extract_path_from_url,
    _match_route_pattern,
    _scan_javascript_file,
    _scan_python_file,
    link_http,
)


class TestExtractPathFromUrl:
    """Tests for URL path extraction."""

    def test_simple_path(self):
        assert _extract_path_from_url("/api/users") == "/api/users"

    def test_full_url(self):
        assert _extract_path_from_url("http://localhost:8000/api/users") == "/api/users"

    def test_https_url(self):
        assert _extract_path_from_url("https://example.com/api/users") == "/api/users"

    def test_url_with_query_params(self):
        assert _extract_path_from_url("/api/users?page=1") == "/api/users"

    def test_root_path(self):
        assert _extract_path_from_url("/") == "/"

    def test_empty_string(self):
        assert _extract_path_from_url("") is None

    def test_variable_in_url(self):
        # URLs with template variables should still extract base path
        assert _extract_path_from_url("/api/users/123") == "/api/users/123"


class TestMatchRoutePattern:
    """Tests for route pattern matching."""

    def test_exact_match(self):
        assert _match_route_pattern("/api/users", "/api/users") is True

    def test_no_match(self):
        assert _match_route_pattern("/api/users", "/api/posts") is False

    def test_colon_param(self):
        # Flask/Express style: /users/:id
        assert _match_route_pattern("/api/users/123", "/api/users/:id") is True

    def test_bracket_param(self):
        # FastAPI style: /users/{id}
        assert _match_route_pattern("/api/users/123", "/api/users/{id}") is True

    def test_angle_param(self):
        # Flask style: /users/<id>
        assert _match_route_pattern("/api/users/123", "/api/users/<id>") is True

    def test_multiple_params(self):
        assert _match_route_pattern(
            "/api/users/123/posts/456", "/api/users/:userId/posts/:postId"
        ) is True

    def test_partial_match_fails(self):
        assert _match_route_pattern("/api/users/123/extra", "/api/users/:id") is False

    def test_trailing_slash_normalization(self):
        assert _match_route_pattern("/api/users/", "/api/users") is True
        assert _match_route_pattern("/api/users", "/api/users/") is True


class TestScanPythonFile:
    """Tests for Python HTTP client call detection."""

    def test_requests_get(self):
        code = dedent('''
            import requests
            response = requests.get("/api/users")
        ''')
        calls = _scan_python_file(Path("test.py"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"

    def test_requests_post(self):
        code = dedent('''
            import requests
            response = requests.post("/api/users", json=data)
        ''')
        calls = _scan_python_file(Path("test.py"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "/api/users"

    def test_requests_with_full_url(self):
        code = dedent('''
            import requests
            response = requests.get("http://localhost:8000/api/users")
        ''')
        calls = _scan_python_file(Path("test.py"), code)
        assert len(calls) == 1
        assert calls[0].url == "http://localhost:8000/api/users"

    def test_httpx_get(self):
        code = dedent('''
            import httpx
            response = httpx.get("/api/users")
        ''')
        calls = _scan_python_file(Path("test.py"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"

    def test_multiple_calls(self):
        code = dedent('''
            import requests
            r1 = requests.get("/api/users")
            r2 = requests.post("/api/users")
            r3 = requests.delete("/api/users/1")
        ''')
        calls = _scan_python_file(Path("test.py"), code)
        assert len(calls) == 3

    def test_no_http_calls(self):
        code = dedent('''
            def get_users():
                return []
        ''')
        calls = _scan_python_file(Path("test.py"), code)
        assert len(calls) == 0


class TestScanJavaScriptFile:
    """Tests for JavaScript HTTP client call detection."""

    def test_fetch_simple(self):
        code = dedent('''
            fetch("/api/users")
        ''')
        calls = _scan_javascript_file(Path("test.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"  # Default method
        assert calls[0].url == "/api/users"

    def test_fetch_with_method(self):
        code = dedent('''
            fetch("/api/users", { method: "POST" })
        ''')
        calls = _scan_javascript_file(Path("test.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"

    def test_fetch_with_method_lowercase(self):
        code = dedent('''
            fetch("/api/users", { method: 'post' })
        ''')
        calls = _scan_javascript_file(Path("test.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"

    def test_axios_get(self):
        code = dedent('''
            axios.get("/api/users")
        ''')
        calls = _scan_javascript_file(Path("test.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"

    def test_axios_post(self):
        code = dedent('''
            axios.post("/api/users", data)
        ''')
        calls = _scan_javascript_file(Path("test.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"

    def test_multiple_calls(self):
        code = dedent('''
            fetch("/api/users")
            axios.get("/api/posts")
            axios.delete("/api/users/1")
        ''')
        calls = _scan_javascript_file(Path("test.js"), code)
        assert len(calls) == 3

    def test_no_http_calls(self):
        code = dedent('''
            function getUsers() {
                return [];
            }
        ''')
        calls = _scan_javascript_file(Path("test.js"), code)
        assert len(calls) == 0

    def test_openapi_request_get(self):
        """Detects OpenAPI-generated __request() calls with GET method."""
        code = dedent('''
            return __request(OpenAPI, {
                method: 'GET',
                url: '/api/v1/items/'
            });
        ''')
        calls = _scan_javascript_file(Path("sdk.gen.ts"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/v1/items/"

    def test_openapi_request_post(self):
        """Detects OpenAPI-generated __request() calls with POST method."""
        code = dedent('''
            return __request(OpenAPI, {
                method: 'POST',
                url: '/api/v1/users/',
                body: data.requestBody
            });
        ''')
        calls = _scan_javascript_file(Path("sdk.gen.ts"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "/api/v1/users/"

    def test_openapi_request_with_path_params(self):
        """Detects OpenAPI requests with path parameters."""
        code = dedent('''
            return __request(OpenAPI, {
                method: 'PUT',
                url: '/api/v1/items/{id}',
                path: { id: data.id }
            });
        ''')
        calls = _scan_javascript_file(Path("sdk.gen.ts"), code)
        assert len(calls) == 1
        assert calls[0].method == "PUT"
        assert calls[0].url == "/api/v1/items/{id}"

    def test_openapi_request_multiple(self):
        """Detects multiple OpenAPI request calls."""
        code = dedent('''
            export class ItemsService {
                public static readItems(): CancelablePromise<ItemsResponse> {
                    return __request(OpenAPI, {
                        method: 'GET',
                        url: '/api/v1/items/'
                    });
                }

                public static createItem(): CancelablePromise<ItemResponse> {
                    return __request(OpenAPI, {
                        method: 'POST',
                        url: '/api/v1/items/'
                    });
                }
            }
        ''')
        calls = _scan_javascript_file(Path("sdk.gen.ts"), code)
        assert len(calls) == 2
        assert calls[0].method == "GET"
        assert calls[1].method == "POST"

    def test_openapi_request_url_before_method(self):
        """Detects OpenAPI request with url before method."""
        code = dedent('''
            return __request(OpenAPI, {
                url: '/api/v1/users/',
                method: 'DELETE',
                errors: { 422: 'Validation Error' }
            });
        ''')
        calls = _scan_javascript_file(Path("sdk.gen.ts"), code)
        assert len(calls) == 1
        assert calls[0].method == "DELETE"
        assert calls[0].url == "/api/v1/users/"


class TestLinkHttp:
    """Tests for the main HTTP linking function."""

    def test_links_fetch_to_express_route(self, tmp_path):
        # Create a JS file with fetch call
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users")')

        # Create a route symbol (as if from Express analyzer)
        route_symbol = Symbol(
            id="server.js::getUsers",
            name="getUsers",
            kind="route",
            path=str(tmp_path / "server.js"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="javascript",
            stable_id="GET",
            meta={"route_path": "/api/users", "http_method": "GET"},
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "http_calls"
        assert result.edges[0].dst == route_symbol.id
        assert result.edges[0].meta["http_method"] == "GET"
        assert result.edges[0].meta["url_path"] == "/api/users"

    def test_links_requests_to_flask_route(self, tmp_path):
        # Create a Python file with requests call
        client_file = tmp_path / "client.py"
        client_file.write_text('import requests\nrequests.get("/api/users")')

        # Create a route symbol (as if from Flask analyzer)
        route_symbol = Symbol(
            id="server.py::get_users",
            name="get_users",
            kind="route",
            path=str(tmp_path / "server.py"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="python",
            stable_id="GET",
            meta={"route_path": "/api/users", "http_method": "GET"},
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "http_calls"
        assert result.edges[0].dst == route_symbol.id

    def test_matches_parameterized_route(self, tmp_path):
        # Create a JS file with fetch call
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users/123")')

        # Create a route symbol with parameter
        route_symbol = Symbol(
            id="server.js::getUser",
            name="getUser",
            kind="route",
            path=str(tmp_path / "server.js"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="javascript",
            stable_id="GET",
            meta={"route_path": "/api/users/:id", "http_method": "GET"},
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].dst == route_symbol.id

    def test_method_must_match(self, tmp_path):
        # Create a JS file with POST fetch
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users", { method: "POST" })')

        # Create a GET route symbol
        route_symbol = Symbol(
            id="server.js::getUsers",
            name="getUsers",
            kind="route",
            path=str(tmp_path / "server.js"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="javascript",
            stable_id="GET",
            meta={"route_path": "/api/users", "http_method": "GET"},
        )

        result = link_http(tmp_path, [route_symbol])

        # Should not match because methods differ
        assert len(result.edges) == 0

    def test_cross_language_linking(self, tmp_path):
        # JavaScript client calling Python server
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users")')

        # Python route symbol
        route_symbol = Symbol(
            id="server.py::get_users",
            name="get_users",
            kind="route",
            path=str(tmp_path / "server.py"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="python",
            stable_id="GET",
            meta={"route_path": "/api/users", "http_method": "GET"},
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].meta["cross_language"] is True

    def test_creates_client_symbols(self, tmp_path):
        # Create a JS file with fetch call
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users")')

        # Create a route symbol
        route_symbol = Symbol(
            id="server.js::getUsers",
            name="getUsers",
            kind="route",
            path=str(tmp_path / "server.js"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="javascript",
            stable_id="GET",
            meta={"route_path": "/api/users", "http_method": "GET"},
        )

        result = link_http(tmp_path, [route_symbol])

        # Should create an http_client symbol for the fetch call
        assert len(result.symbols) >= 1
        client_sym = result.symbols[0]
        assert client_sym.kind == "http_client"
        assert client_sym.meta["url_path"] == "/api/users"

    def test_empty_when_no_routes(self, tmp_path):
        # Create a JS file with fetch call but no route symbols
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users")')

        result = link_http(tmp_path, [])

        # Should still create client symbol but no edges
        assert len(result.symbols) >= 1
        assert len(result.edges) == 0

    def test_has_analysis_run(self, tmp_path):
        result = link_http(tmp_path, [])

        assert result.run is not None
        assert result.run.pass_id == "http-linker-v1"
