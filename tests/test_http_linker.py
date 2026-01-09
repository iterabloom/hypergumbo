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


class TestVariableUrlPatterns:
    """Tests for variable URL detection in HTTP calls."""

    def test_python_requests_with_variable(self):
        """Detects requests.get(API_URL) with variable URL."""
        code = dedent('''
            import requests
            API_URL = "/api/users"
            response = requests.get(API_URL)
        ''')
        calls = _scan_python_file(Path("test.py"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "API_URL"
        assert calls[0].url_type == "variable"

    def test_python_requests_with_literal(self):
        """Verifies literal URLs have url_type='literal'."""
        code = dedent('''
            import requests
            response = requests.get("/api/users")
        ''')
        calls = _scan_python_file(Path("test.py"), code)
        assert len(calls) == 1
        assert calls[0].url == "/api/users"
        assert calls[0].url_type == "literal"

    def test_python_requests_with_dotted_variable(self):
        """Detects requests.post(config.api_url) with dotted variable."""
        code = dedent('''
            import requests
            response = requests.post(config.api_url)
        ''')
        calls = _scan_python_file(Path("test.py"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "config.api_url"
        assert calls[0].url_type == "variable"

    def test_js_fetch_with_variable(self):
        """Detects fetch(API_URL) with variable URL."""
        code = dedent('''
            const API_URL = '/api/users';
            fetch(API_URL);
        ''')
        calls = _scan_javascript_file(Path("test.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "API_URL"
        assert calls[0].url_type == "variable"

    def test_js_fetch_with_literal(self):
        """Verifies literal URLs have url_type='literal'."""
        code = dedent('''
            fetch('/api/users');
        ''')
        calls = _scan_javascript_file(Path("test.js"), code)
        assert len(calls) == 1
        assert calls[0].url == "/api/users"
        assert calls[0].url_type == "literal"

    def test_js_axios_with_variable(self):
        """Detects axios.get(API_ENDPOINT) with variable URL."""
        code = dedent('''
            const API_ENDPOINT = '/api/users';
            axios.get(API_ENDPOINT);
        ''')
        calls = _scan_javascript_file(Path("test.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "API_ENDPOINT"
        assert calls[0].url_type == "variable"

    def test_js_axios_with_dotted_variable(self):
        """Detects axios.post(config.apiUrl) with dotted variable."""
        code = dedent('''
            axios.post(config.apiUrl, data);
        ''')
        calls = _scan_javascript_file(Path("test.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "config.apiUrl"
        assert calls[0].url_type == "variable"

    def test_symbol_includes_url_type(self, tmp_path):
        """Verifies url_type is included in symbol meta."""
        client_file = tmp_path / "client.js"
        client_file.write_text("fetch(API_URL);")

        result = link_http(tmp_path, [])

        assert len(result.symbols) == 1
        assert result.symbols[0].meta["url_type"] == "variable"

    def test_edge_includes_url_type(self, tmp_path):
        """Verifies url_type is included in edge meta."""
        client_file = tmp_path / "client.js"
        client_file.write_text("fetch(API_URL);")

        route_symbol = Symbol(
            id="server.js::getUsers",
            name="getUsers",
            kind="route",
            path=str(tmp_path / "server.js"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="javascript",
            stable_id="GET",
            meta={"route_path": "API_URL", "http_method": "GET"},
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].meta["url_type"] == "variable"
        assert result.edges[0].confidence == 0.65

    def test_literal_url_higher_confidence(self, tmp_path):
        """Verifies literal URLs have higher confidence than variables."""
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users");')

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
        assert result.edges[0].meta["url_type"] == "literal"
        assert result.edges[0].confidence == 0.9  # Same language, literal URL


class TestConceptMetadataSupport:
    """Tests for concept metadata support from FRAMEWORK_PATTERNS phase."""

    def test_links_to_symbol_with_route_concept(self, tmp_path):
        """Links HTTP calls to symbols with route concept in meta.concepts."""
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users")')

        # Symbol with concept metadata (from FRAMEWORK_PATTERNS phase)
        route_symbol = Symbol(
            id="main.py::get_users::function",
            name="get_users",
            kind="function",  # Not "route" - detected via concept
            path=str(tmp_path / "main.py"),
            span=Span(start_line=10, start_col=0, end_line=20, end_col=0),
            language="python",
            meta={
                "decorators": [
                    {"name": "app.get", "args": ["/api/users"], "kwargs": {}},
                ],
                "concepts": [
                    {"concept": "route", "path": "/api/users", "method": "GET"},
                ],
            },
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].dst == route_symbol.id
        assert result.edges[0].meta["http_method"] == "GET"

    def test_links_to_symbol_with_post_route_concept(self, tmp_path):
        """Links POST calls to symbols with POST route concept."""
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/items", { method: "POST" })')

        route_symbol = Symbol(
            id="routes.py::create_item::function",
            name="create_item",
            kind="function",
            path=str(tmp_path / "routes.py"),
            span=Span(start_line=5, start_col=0, end_line=15, end_col=0),
            language="python",
            meta={
                "concepts": [
                    {"concept": "route", "path": "/api/items", "method": "POST"},
                ],
            },
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].meta["http_method"] == "POST"

    def test_concept_method_must_match(self, tmp_path):
        """HTTP method in concept must match call method."""
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users", { method: "DELETE" })')

        route_symbol = Symbol(
            id="main.py::get_users::function",
            name="get_users",
            kind="function",
            path=str(tmp_path / "main.py"),
            span=Span(start_line=10, start_col=0, end_line=20, end_col=0),
            language="python",
            meta={
                "concepts": [
                    {"concept": "route", "path": "/api/users", "method": "GET"},
                ],
            },
        )

        result = link_http(tmp_path, [route_symbol])

        # Should not match - DELETE != GET
        assert len(result.edges) == 0

    def test_concept_path_with_parameters(self, tmp_path):
        """Matches parameterized paths in concept metadata."""
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/items/123")')

        route_symbol = Symbol(
            id="main.py::delete_item::function",
            name="delete_item",
            kind="function",
            path=str(tmp_path / "main.py"),
            span=Span(start_line=30, start_col=0, end_line=40, end_col=0),
            language="python",
            meta={
                "concepts": [
                    {"concept": "route", "path": "/api/items/{id}", "method": "GET"},
                ],
            },
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1

    def test_prefers_concept_over_legacy_meta(self, tmp_path):
        """When both concept and legacy meta exist, uses concept."""
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/new-path")')

        # Symbol has both concept and legacy metadata
        route_symbol = Symbol(
            id="main.py::handler::function",
            name="handler",
            kind="function",
            path=str(tmp_path / "main.py"),
            span=Span(start_line=10, start_col=0, end_line=20, end_col=0),
            language="python",
            meta={
                # Legacy meta (should be ignored when concept present)
                "route_path": "/api/old-path",
                "http_method": "POST",
                # Concept meta (takes precedence)
                "concepts": [
                    {"concept": "route", "path": "/api/new-path", "method": "GET"},
                ],
            },
        )

        result = link_http(tmp_path, [route_symbol])

        # Should match new-path from concept, not old-path from legacy
        assert len(result.edges) == 1
        assert result.edges[0].meta["url_path"] == "/api/new-path"

    def test_falls_back_to_legacy_meta(self, tmp_path):
        """Falls back to legacy meta when no route concept exists."""
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users")')

        # Symbol with only legacy metadata (no concepts)
        route_symbol = Symbol(
            id="main.py::get_users::function",
            name="get_users",
            kind="route",
            path=str(tmp_path / "main.py"),
            span=Span(start_line=10, start_col=0, end_line=20, end_col=0),
            language="python",
            stable_id="GET",
            meta={
                "route_path": "/api/users",
                "http_method": "GET",
            },
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1

    def test_ignores_non_route_concepts(self, tmp_path):
        """Symbols with non-route concepts don't match as routes."""
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users")')

        # Symbol with model concept (not route)
        model_symbol = Symbol(
            id="models.py::User::class",
            name="User",
            kind="class",
            path=str(tmp_path / "models.py"),
            span=Span(start_line=1, start_col=0, end_line=10, end_col=0),
            language="python",
            meta={
                "concepts": [
                    {"concept": "model", "matched_base_class": "BaseModel"},
                ],
            },
        )

        result = link_http(tmp_path, [model_symbol])

        # No route concept, shouldn't match
        assert len(result.edges) == 0

    def test_get_route_info_from_symbol_without_meta(self):
        """_get_route_info_from_concept handles symbols with meta=None."""
        from hypergumbo.linkers.http import _get_route_info_from_concept

        symbol = Symbol(
            id="test::func",
            name="func",
            kind="function",
            path="test.py",
            span=Span(start_line=1, start_col=0, end_line=5, end_col=0),
            language="python",
            meta=None,  # No metadata
        )

        path, method = _get_route_info_from_concept(symbol)
        assert path is None
        assert method is None

    def test_get_route_symbols_includes_concept_routes(self, tmp_path):
        """_get_route_symbols finds symbols with route concepts."""
        from hypergumbo.linkers.http import _get_route_symbols
        from hypergumbo.linkers.registry import LinkerContext

        concept_route = Symbol(
            id="main.py::handler::function",
            name="handler",
            kind="function",  # Not "route"
            path="main.py",
            span=Span(start_line=10, start_col=0, end_line=20, end_col=0),
            language="python",
            meta={
                "concepts": [
                    {"concept": "route", "path": "/api/test", "method": "GET"},
                ],
            },
        )

        legacy_route = Symbol(
            id="old.py::get_users::function",
            name="get_users",
            kind="route",
            path="old.py",
            span=Span(start_line=1, start_col=0, end_line=10, end_col=0),
            language="python",
            meta={"route_path": "/api/users", "http_method": "GET"},
        )

        non_route = Symbol(
            id="utils.py::helper::function",
            name="helper",
            kind="function",
            path="utils.py",
            span=Span(start_line=1, start_col=0, end_line=5, end_col=0),
            language="python",
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[concept_route, legacy_route, non_route],
        )

        routes = _get_route_symbols(ctx)

        assert len(routes) == 2
        assert concept_route in routes
        assert legacy_route in routes
        assert non_route not in routes
