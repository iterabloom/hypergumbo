# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for HTTP client-server linker."""

from pathlib import Path
from textwrap import dedent

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.http import (
    HttpClientCall,
    _create_client_symbol,
    _extract_module_constants,
    _extract_path_from_url,
    _find_source_files,
    _fold_template_literal,
    _match_route_pattern,
    _scan_elm_file,
    _scan_go_file,
    _scan_java_file,
    _scan_javascript_file,
    _scan_python_file,
    _scan_ruby_file,
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

    def test_angularjs_http_get(self):
        """Detects AngularJS $http.get('/api/users')."""
        code = dedent('''
            $http.get('/api/users')
        ''')
        calls = _scan_javascript_file(Path("service.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"
        assert calls[0].url_type == "literal"

    def test_angularjs_http_post(self):
        """Detects AngularJS $http.post('/api/users', data)."""
        code = dedent('''
            $http.post('/api/users', userData)
        ''')
        calls = _scan_javascript_file(Path("service.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "/api/users"

    def test_angularjs_http_patch(self):
        """Detects AngularJS $http.patch('/api/permissions', patch)."""
        code = dedent('''
            $http.patch('api/session/data/' + dataSource + '/permissions', permissionPatch)
        ''')
        calls = _scan_javascript_file(Path("permissionService.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "PATCH"
        # Only captures the first string fragment before concatenation
        assert calls[0].url == "api/session/data/"
        assert calls[0].url_type == "literal"

    def test_angularjs_http_delete(self):
        """Detects AngularJS $http.delete('/api/users/1')."""
        code = dedent('''
            $http.delete('/api/users/' + userId)
        ''')
        calls = _scan_javascript_file(Path("service.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "DELETE"

    def test_angularjs_http_put(self):
        """Detects AngularJS $http.put('/api/users/1', data)."""
        code = dedent('''
            $http.put('/api/users/1', userData)
        ''')
        calls = _scan_javascript_file(Path("service.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "PUT"
        assert calls[0].url == "/api/users/1"

    def test_angularjs_http_with_variable_url(self):
        """Detects AngularJS $http.get(apiUrl) with variable URL."""
        code = dedent('''
            $http.get(apiUrl)
        ''')
        calls = _scan_javascript_file(Path("service.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "apiUrl"
        assert calls[0].url_type == "variable"

    def test_angularjs_http_config_object(self):
        """Detects AngularJS $http({method: 'GET', url: '/api/users'})."""
        code = dedent('''
            $http({
                method: 'GET',
                url: '/api/users'
            })
        ''')
        calls = _scan_javascript_file(Path("service.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"

    def test_angularjs_http_config_url_first(self):
        """Detects AngularJS $http({url: '/api/users', method: 'POST'})."""
        code = dedent('''
            $http({
                url: '/api/users',
                method: 'POST',
                data: userData
            })
        ''')
        calls = _scan_javascript_file(Path("service.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "/api/users"

    def test_angularjs_http_no_false_positive_on_variable(self):
        """Does not false-positive on $http as a variable name in non-HTTP contexts."""
        code = dedent('''
            var $httpProvider = injector.get('$httpProvider');
            $httpProvider.interceptors.push('authInterceptor');
        ''')
        calls = _scan_javascript_file(Path("config.js"), code)
        assert len(calls) == 0

    def test_angularjs_multiple_calls(self):
        """Detects multiple AngularJS $http calls in one file."""
        code = dedent('''
            $http.get('/api/users')
            $http.post('/api/users', data)
            $http.delete('/api/users/1')
        ''')
        calls = _scan_javascript_file(Path("service.js"), code)
        assert len(calls) == 3

    def test_jquery_ajax_get(self):
        """Detects jQuery $.ajax({url: '/api/users', type: 'GET'})."""
        code = dedent('''
            $.ajax({
                url: '/api/users',
                type: 'GET',
                success: function(data) {}
            })
        ''')
        calls = _scan_javascript_file(Path("app.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"

    def test_jquery_get_shorthand(self):
        """Detects jQuery $.get('/api/users')."""
        code = dedent('''
            $.get('/api/users', function(data) {})
        ''')
        calls = _scan_javascript_file(Path("app.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"

    def test_jquery_post_shorthand(self):
        """Detects jQuery $.post('/api/users', data)."""
        code = dedent('''
            $.post('/api/users', userData, function(result) {})
        ''')
        calls = _scan_javascript_file(Path("app.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "/api/users"

    def test_jquery_ajax_method_variant(self):
        """Detects jQuery $.ajax with 'method' instead of 'type'."""
        code = dedent('''
            $.ajax({
                url: '/api/items',
                method: 'PUT',
                data: payload
            })
        ''')
        calls = _scan_javascript_file(Path("app.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "PUT"
        assert calls[0].url == "/api/items"

    def test_jquery_ajax_type_before_url(self):
        """Detects jQuery $.ajax with type/method before url."""
        code = dedent('''
            $.ajax({
                type: 'DELETE',
                url: '/api/users/1',
                success: callback
            })
        ''')
        calls = _scan_javascript_file(Path("app.js"), code)
        assert len(calls) == 1
        assert calls[0].method == "DELETE"
        assert calls[0].url == "/api/users/1"


class TestLinkHttp:
    """Tests for the main HTTP linking function."""

    def test_links_fetch_to_express_route(self, tmp_path):
        # Create a JS file with fetch call
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users")')

        # Create a route symbol (as if from Express analyzer with concepts)
        route_symbol = Symbol(
            id="server.js::getUsers",
            name="getUsers",
            kind="route",
            path=str(tmp_path / "server.js"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="javascript",
            stable_id="sha256:abc123",
            meta={
                "concepts": [{"concept": "route", "path": "/api/users", "method": "GET"}]
            },
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

        # Create a route symbol (as if from Flask analyzer with concepts)
        route_symbol = Symbol(
            id="server.py::get_users",
            name="get_users",
            kind="route",
            path=str(tmp_path / "server.py"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="python",
            stable_id="sha256:abc123",
            meta={
                "concepts": [{"concept": "route", "path": "/api/users", "method": "GET"}]
            },
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
            stable_id="sha256:abc123",
            meta={
                "concepts": [{"concept": "route", "path": "/api/users/:id", "method": "GET"}]
            },
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
            stable_id="sha256:abc123",
            meta={
                "concepts": [{"concept": "route", "path": "/api/users", "method": "GET"}]
            },
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
            stable_id="sha256:abc123",
            meta={
                "concepts": [{"concept": "route", "path": "/api/users", "method": "GET"}]
            },
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
            stable_id="sha256:abc123",
            meta={
                "concepts": [{"concept": "route", "path": "/api/users", "method": "GET"}]
            },
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

    def test_matches_wildcard_method_route(self, tmp_path):
        """Routes with no HTTP method match any client method."""
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/data", { method: "DELETE" })')

        # Route with no method (wildcard)
        route_symbol = Symbol(
            id="server.js::handleData",
            name="handleData",
            kind="route",
            path=str(tmp_path / "server.js"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="javascript",
            stable_id="sha256:abc123",
            meta={
                "concepts": [{"concept": "route", "path": "/api/data", "method": ""}]
            },
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].dst == route_symbol.id


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
            stable_id="sha256:abc123",
            meta={
                "concepts": [{"concept": "route", "path": "API_URL", "method": "GET"}]
            },
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
            stable_id="sha256:abc123",
            meta={
                "concepts": [{"concept": "route", "path": "/api/users", "method": "GET"}]
            },
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
        from hypergumbo_core.linkers.http import _get_route_info_from_concept

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
        from hypergumbo_core.linkers.http import _get_route_symbols
        from hypergumbo_core.linkers.registry import LinkerContext

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

    def test_links_to_route_symbol_with_direct_metadata(self, tmp_path):
        """Links HTTP calls to route symbols with direct route_path/http_method metadata.

        Route symbols from analyzers (Ruby, PHP, Elixir, JS) store route info
        in meta.route_path and meta.http_method, not meta.concepts.
        """
        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/users")')

        # Route symbol with direct metadata (from analyzer, not FRAMEWORK_PATTERNS)
        route_symbol = Symbol(
            id="routes.rb::GET /users::route",
            name="GET /users",
            kind="route",
            path=str(tmp_path / "routes.rb"),
            span=Span(start_line=5, start_col=0, end_line=5, end_col=30),
            language="ruby",
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "controller_action": "users#index",
            },
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.dst == route_symbol.id
        assert edge.meta["http_method"] == "GET"
        assert edge.meta["cross_language"] is True  # JS client -> Ruby route

    def test_get_route_info_direct_metadata_fallback(self):
        """_get_route_info_from_concept falls back to direct meta fields."""
        from hypergumbo_core.linkers.http import _get_route_info_from_concept

        # Symbol with direct metadata, no concepts
        symbol = Symbol(
            id="routes.rb::GET /api::route",
            name="GET /api",
            kind="route",
            path="routes.rb",
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="ruby",
            meta={
                "route_path": "/api/users",
                "http_method": "POST",
            },
        )

        path, method = _get_route_info_from_concept(symbol)
        assert path == "/api/users"
        assert method == "POST"


class TestHttpLinkerEntryPoint:
    """Tests for HTTP linker registry integration."""

    def test_count_route_symbols(self, tmp_path):
        """_count_route_symbols counts routes for requirement check."""
        from hypergumbo_core.linkers.http import _count_route_symbols
        from hypergumbo_core.linkers.registry import LinkerContext

        route = Symbol(
            id="server.py::get_users",
            name="get_users",
            kind="route",
            path="server.py",
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="python",
        )

        non_route = Symbol(
            id="utils.py::helper",
            name="helper",
            kind="function",
            path="utils.py",
            span=Span(start_line=1, start_col=0, end_line=5, end_col=0),
            language="python",
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[route, non_route])
        assert _count_route_symbols(ctx) == 1

    def test_http_linker_entry_point(self, tmp_path):
        """http_linker entry point works via LinkerContext."""
        from hypergumbo_core.linkers.http import http_linker
        from hypergumbo_core.linkers.registry import LinkerContext

        client_file = tmp_path / "client.js"
        client_file.write_text('fetch("/api/users")')

        route = Symbol(
            id="server.py::get_users",
            name="get_users",
            kind="route",
            path=str(tmp_path / "server.py"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="python",
            meta={
                "concepts": [{"concept": "route", "path": "/api/users", "method": "GET"}]
            },
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[route])
        result = http_linker(ctx)

        assert len(result.edges) == 1
        assert result.run is not None


class TestScanGoFile:
    """Tests for Go HTTP client call detection."""

    def test_http_get(self):
        """Detects http.Get("url")."""
        code = dedent('''
            package main
            import "net/http"
            func main() {
                resp, _ := http.Get("/api/users")
            }
        ''')
        calls = _scan_go_file(Path("main.go"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"
        assert calls[0].language == "go"

    def test_http_post(self):
        """Detects http.Post("url", contentType, body)."""
        code = dedent('''
            resp, _ := http.Post("/api/users", "application/json", body)
        ''')
        calls = _scan_go_file(Path("main.go"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "/api/users"

    def test_http_head(self):
        """Detects http.Head("url")."""
        code = dedent('''
            resp, _ := http.Head("/api/health")
        ''')
        calls = _scan_go_file(Path("main.go"), code)
        assert len(calls) == 1
        assert calls[0].method == "HEAD"
        assert calls[0].url == "/api/health"

    def test_http_new_request(self):
        """Detects http.NewRequest("METHOD", "url", body)."""
        code = dedent('''
            req, _ := http.NewRequest("DELETE", "/api/users/1", nil)
        ''')
        calls = _scan_go_file(Path("main.go"), code)
        assert len(calls) == 1
        assert calls[0].method == "DELETE"
        assert calls[0].url == "/api/users/1"

    def test_http_new_request_put(self):
        """Detects http.NewRequest with PUT method."""
        code = dedent('''
            req, _ := http.NewRequest("PUT", "/api/users/1", bytes.NewReader(data))
        ''')
        calls = _scan_go_file(Path("main.go"), code)
        assert len(calls) == 1
        assert calls[0].method == "PUT"

    def test_http_new_request_with_context(self):
        """Detects http.NewRequestWithContext(ctx, "METHOD", "url", body)."""
        code = dedent('''
            req, _ := http.NewRequestWithContext(ctx, "PATCH", "/api/users/1", body)
        ''')
        calls = _scan_go_file(Path("main.go"), code)
        assert len(calls) == 1
        assert calls[0].method == "PATCH"
        assert calls[0].url == "/api/users/1"

    def test_http_get_with_variable(self):
        """Detects http.Get(apiURL) with variable URL."""
        code = dedent('''
            apiURL := "/api/users"
            resp, _ := http.Get(apiURL)
        ''')
        calls = _scan_go_file(Path("main.go"), code)
        assert len(calls) == 1
        assert calls[0].url == "apiURL"
        assert calls[0].url_type == "variable"

    def test_http_get_with_literal(self):
        """Verifies literal URLs have url_type='literal'."""
        code = dedent('''
            resp, _ := http.Get("/api/users")
        ''')
        calls = _scan_go_file(Path("main.go"), code)
        assert len(calls) == 1
        assert calls[0].url_type == "literal"

    def test_multiple_calls(self):
        """Detects multiple Go HTTP calls in one file."""
        code = dedent('''
            resp1, _ := http.Get("/api/users")
            resp2, _ := http.Post("/api/users", "application/json", body)
            req, _ := http.NewRequest("DELETE", "/api/users/1", nil)
        ''')
        calls = _scan_go_file(Path("main.go"), code)
        assert len(calls) == 3

    def test_no_http_calls(self):
        """Go file without HTTP calls returns empty list."""
        code = dedent('''
            package main
            func add(a, b int) int { return a + b }
        ''')
        calls = _scan_go_file(Path("main.go"), code)
        assert len(calls) == 0

    def test_full_url(self):
        """Detects http.Get with full URL."""
        code = dedent('''
            resp, _ := http.Get("http://localhost:8080/api/users")
        ''')
        calls = _scan_go_file(Path("main.go"), code)
        assert len(calls) == 1
        assert calls[0].url == "http://localhost:8080/api/users"


class TestGoHttpLinking:
    """Integration tests for Go HTTP client linking to routes."""

    def test_links_go_http_get_to_route(self, tmp_path):
        """Go http.Get calls link to route symbols."""
        client_file = tmp_path / "client.go"
        client_file.write_text('package main\nresp, _ := http.Get("/api/users")')

        route_symbol = Symbol(
            id="server.py::get_users",
            name="get_users",
            kind="route",
            path=str(tmp_path / "server.py"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="python",
            stable_id="sha256:abc123",
            meta={
                "concepts": [{"concept": "route", "path": "/api/users", "method": "GET"}]
            },
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "http_calls"
        assert result.edges[0].dst == route_symbol.id
        assert result.edges[0].meta["cross_language"] is True


class TestScanRubyFile:
    """Tests for Ruby HTTP client call detection."""

    def test_rest_client_get(self):
        """Detects RestClient.get("/api/users")."""
        code = dedent('''
            require 'rest-client'
            response = RestClient.get("/api/users")
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"
        assert calls[0].language == "ruby"
        assert calls[0].url_type == "literal"

    def test_rest_client_post(self):
        """Detects RestClient.post("/api/users", payload)."""
        code = dedent('''
            RestClient.post("/api/users", {name: "Alice"}.to_json)
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "/api/users"

    def test_rest_client_put(self):
        """Detects RestClient.put("/api/users/1", payload)."""
        code = dedent('''
            RestClient.put("/api/users/1", data)
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "PUT"

    def test_rest_client_delete(self):
        """Detects RestClient.delete("/api/users/1")."""
        code = dedent('''
            RestClient.delete("/api/users/1")
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "DELETE"

    def test_rest_client_patch(self):
        """Detects RestClient.patch("/api/users/1", payload)."""
        code = dedent('''
            RestClient.patch("/api/users/1", data)
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "PATCH"

    def test_httparty_get(self):
        """Detects HTTParty.get("/api/users")."""
        code = dedent('''
            require 'httparty'
            response = HTTParty.get("/api/users")
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"

    def test_httparty_post(self):
        """Detects HTTParty.post("/api/users", body: data)."""
        code = dedent('''
            HTTParty.post("/api/users", body: data.to_json)
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"

    def test_faraday_get(self):
        """Detects Faraday.get("/api/users")."""
        code = dedent('''
            require 'faraday'
            response = Faraday.get("/api/users")
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"

    def test_faraday_post(self):
        """Detects Faraday.post("/api/users", body)."""
        code = dedent('''
            Faraday.post("/api/users", body)
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"

    def test_net_http_get_uri(self):
        """Detects Net::HTTP.get(URI("/api/users"))."""
        code = dedent('''
            require 'net/http'
            response = Net::HTTP.get(URI("/api/users"))
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"

    def test_net_http_post_form_uri(self):
        """Detects Net::HTTP.post_form(URI("/api/users"), data)."""
        code = dedent('''
            Net::HTTP.post_form(URI("/api/users"), data)
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "/api/users"

    def test_net_http_get_response_uri(self):
        """Detects Net::HTTP.get_response(URI("/api/users"))."""
        code = dedent('''
            Net::HTTP.get_response(URI("/api/users"))
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"

    def test_net_http_uri_parse(self):
        """Detects Net::HTTP.get(URI.parse("/api/users"))."""
        code = dedent('''
            Net::HTTP.get(URI.parse("/api/users"))
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"

    def test_rest_client_with_variable(self):
        """Detects RestClient.get(api_url) with variable URL."""
        code = dedent('''
            api_url = "/api/users"
            RestClient.get(api_url)
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].url == "api_url"
        assert calls[0].url_type == "variable"

    def test_multiple_calls(self):
        """Detects multiple Ruby HTTP calls in one file."""
        code = dedent('''
            RestClient.get("/api/users")
            HTTParty.post("/api/items")
            Faraday.delete("/api/items/1")
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 3

    def test_no_http_calls(self):
        """Ruby file without HTTP calls returns empty list."""
        code = dedent('''
            class User
              def name
                @name
              end
            end
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 0

    def test_full_url(self):
        """Detects RestClient.get with full URL."""
        code = dedent('''
            RestClient.get("http://localhost:3000/api/users")
        ''')
        calls = _scan_ruby_file(Path("client.rb"), code)
        assert len(calls) == 1
        assert calls[0].url == "http://localhost:3000/api/users"


class TestScanJavaFile:
    """Tests for Java HTTP client call detection."""

    def test_rest_template_get_for_object(self):
        """Detects restTemplate.getForObject("/api/users", ...)."""
        code = dedent('''
            String result = restTemplate.getForObject("/api/users", String.class);
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"
        assert calls[0].language == "java"
        assert calls[0].url_type == "literal"

    def test_rest_template_get_for_entity(self):
        """Detects restTemplate.getForEntity("/api/users", ...)."""
        code = dedent('''
            ResponseEntity<String> resp = restTemplate.getForEntity("/api/users", String.class);
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"

    def test_rest_template_post_for_object(self):
        """Detects restTemplate.postForObject("/api/users", ...)."""
        code = dedent('''
            User user = restTemplate.postForObject("/api/users", request, User.class);
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "/api/users"

    def test_rest_template_post_for_entity(self):
        """Detects restTemplate.postForEntity("/api/users", ...)."""
        code = dedent('''
            restTemplate.postForEntity("/api/users", entity, String.class);
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"

    def test_rest_template_delete_not_detected(self):
        """restTemplate.delete() is NOT detected — too ambiguous with generic .delete().

        Use exchange(url, HttpMethod.DELETE, ...) instead.
        """
        code = dedent('''
            restTemplate.delete("/api/users/1");
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 0

    def test_rest_template_put_not_detected(self):
        """restTemplate.put() is NOT detected — too ambiguous with HashMap.put().

        Use exchange(url, HttpMethod.PUT, ...) instead.
        """
        code = dedent('''
            restTemplate.put("/api/users/1", entity);
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 0

    def test_hashmap_put_not_http_client(self):
        """HashMap.put() must not be detected as an HTTP client call."""
        code = dedent('''
            states.put(InstallState.CONFIGURE_INSTANCE, InstallState.INITIAL_SETUP_COMPLETED);
            errors.put("username", Messages.HudsonPrivateSecurityRealm_CreateAccount_UserNameRequired());
            map.put("key", value);
        ''')
        calls = _scan_java_file(Path("InstallUtil.java"), code)
        assert len(calls) == 0

    def test_rest_template_exchange(self):
        """Detects restTemplate.exchange("/api/users", HttpMethod.GET, ...)."""
        code = dedent('''
            restTemplate.exchange("/api/users", HttpMethod.GET, entity, String.class);
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"

    def test_rest_template_exchange_post(self):
        """Detects restTemplate.exchange with POST method."""
        code = dedent('''
            restTemplate.exchange("/api/items", HttpMethod.POST, entity, Item.class);
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"

    def test_rest_template_patch_for_object(self):
        """Detects restTemplate.patchForObject("/api/users/1", ...)."""
        code = dedent('''
            restTemplate.patchForObject("/api/users/1", patch, User.class);
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 1
        assert calls[0].method == "PATCH"

    def test_retrofit_get_annotation(self):
        """Detects @GET("/api/users") annotation."""
        code = dedent('''
            @GET("/api/users")
            Call<List<User>> getUsers();
        ''')
        calls = _scan_java_file(Path("ApiService.java"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"
        assert calls[0].url_type == "literal"

    def test_retrofit_post_annotation(self):
        """Detects @POST("/api/users") annotation."""
        code = dedent('''
            @POST("/api/users")
            Call<User> createUser(@Body User user);
        ''')
        calls = _scan_java_file(Path("ApiService.java"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"

    def test_retrofit_put_annotation(self):
        """Detects @PUT("/api/users/{id}") annotation."""
        code = dedent('''
            @PUT("/api/users/{id}")
            Call<User> updateUser(@Path("id") long id, @Body User user);
        ''')
        calls = _scan_java_file(Path("ApiService.java"), code)
        assert len(calls) == 1
        assert calls[0].method == "PUT"
        assert calls[0].url == "/api/users/{id}"

    def test_retrofit_delete_annotation(self):
        """Detects @DELETE("/api/users/{id}") annotation."""
        code = dedent('''
            @DELETE("/api/users/{id}")
            Call<Void> deleteUser(@Path("id") long id);
        ''')
        calls = _scan_java_file(Path("ApiService.java"), code)
        assert len(calls) == 1
        assert calls[0].method == "DELETE"

    def test_retrofit_patch_annotation(self):
        """Detects @PATCH("/api/users/{id}") annotation."""
        code = dedent('''
            @PATCH("/api/users/{id}")
            Call<User> patchUser(@Path("id") long id, @Body Map<String, Object> fields);
        ''')
        calls = _scan_java_file(Path("ApiService.java"), code)
        assert len(calls) == 1
        assert calls[0].method == "PATCH"

    def test_rest_template_with_variable(self):
        """Detects restTemplate.getForObject(apiUrl, ...) with variable URL."""
        code = dedent('''
            String apiUrl = "/api/users";
            restTemplate.getForObject(apiUrl, String.class);
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 1
        assert calls[0].url == "apiUrl"
        assert calls[0].url_type == "variable"

    def test_multiple_calls(self):
        """Detects multiple Java HTTP calls in one file."""
        code = dedent('''
            restTemplate.getForObject("/api/users", String.class);
            restTemplate.postForObject("/api/items", body, Item.class);

            @GET("/api/health")
            Call<String> healthCheck();
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 3

    def test_no_http_calls(self):
        """Java file without HTTP calls returns empty list."""
        code = dedent('''
            public class User {
                private String name;
                public String getName() { return name; }
            }
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 0

    def test_full_url(self):
        """Detects RestTemplate with full URL."""
        code = dedent('''
            restTemplate.getForObject("http://localhost:8080/api/users", String.class);
        ''')
        calls = _scan_java_file(Path("Client.java"), code)
        assert len(calls) == 1
        assert calls[0].url == "http://localhost:8080/api/users"


class TestRubyHttpLinking:
    """Integration tests for Ruby HTTP client linking to routes."""

    def test_links_ruby_rest_client_to_route(self, tmp_path):
        """Ruby RestClient.get calls link to route symbols."""
        client_file = tmp_path / "client.rb"
        client_file.write_text('RestClient.get("/api/users")')

        route_symbol = Symbol(
            id="server.py::get_users",
            name="get_users",
            kind="route",
            path=str(tmp_path / "server.py"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="python",
            stable_id="sha256:abc123",
            meta={
                "concepts": [{"concept": "route", "path": "/api/users", "method": "GET"}]
            },
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "http_calls"
        assert result.edges[0].dst == route_symbol.id
        assert result.edges[0].meta["cross_language"] is True

    def test_links_ruby_httparty_to_js_route(self, tmp_path):
        """Ruby HTTParty calls link to JS route symbols (cross-language)."""
        client_file = tmp_path / "service.rb"
        client_file.write_text('HTTParty.post("/api/items")')

        route_symbol = Symbol(
            id="routes.ts::createItem",
            name="createItem",
            kind="function",
            path=str(tmp_path / "routes.ts"),
            span=Span(start_line=10, start_col=0, end_line=20, end_col=0),
            language="javascript",
            meta={
                "concepts": [{"concept": "route", "path": "/api/items", "method": "POST"}]
            },
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].meta["cross_language"] is True
        assert result.edges[0].confidence == 0.8


class TestJavaHttpLinking:
    """Integration tests for Java HTTP client linking to routes."""

    def test_links_java_rest_template_to_route(self, tmp_path):
        """Java RestTemplate calls link to route symbols."""
        client_file = tmp_path / "Client.java"
        client_file.write_text(
            'restTemplate.getForObject("/api/users", String.class);'
        )

        route_symbol = Symbol(
            id="server.py::get_users",
            name="get_users",
            kind="route",
            path=str(tmp_path / "server.py"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=20),
            language="python",
            stable_id="sha256:abc123",
            meta={
                "concepts": [{"concept": "route", "path": "/api/users", "method": "GET"}]
            },
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "http_calls"
        assert result.edges[0].dst == route_symbol.id
        assert result.edges[0].meta["cross_language"] is True

    def test_links_java_retrofit_to_route(self, tmp_path):
        """Java Retrofit annotation calls link to route symbols."""
        client_file = tmp_path / "ApiService.java"
        client_file.write_text(dedent('''
            @GET("/api/users")
            Call<List<User>> getUsers();
        '''))

        route_symbol = Symbol(
            id="routes.rb::GET /api/users::route",
            name="GET /api/users",
            kind="route",
            path=str(tmp_path / "routes.rb"),
            span=Span(start_line=5, start_col=0, end_line=5, end_col=30),
            language="ruby",
            meta={
                "http_method": "GET",
                "route_path": "/api/users",
            },
        )

        result = link_http(tmp_path, [route_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].meta["cross_language"] is True


class TestFindSourceFiles:
    """Tests for _find_source_files minified file filtering."""

    def test_skips_minified_js(self, tmp_path: Path):
        """Minified .min.js files are excluded from HTTP scanning."""
        normal = tmp_path / "api.js"
        normal.write_text("fetch('/api/users');")
        minified = tmp_path / "jquery.min.js"
        minified.write_text("fetch('/api/users');")

        found = [p.name for p in _find_source_files(tmp_path)]
        assert "api.js" in found
        assert "jquery.min.js" not in found

    def test_skips_minified_ts(self, tmp_path: Path):
        """Minified .min.ts files are excluded from HTTP scanning."""
        normal = tmp_path / "client.ts"
        normal.write_text("fetch('/api/users');")
        minified = tmp_path / "bundle.min.ts"
        minified.write_text("fetch('/api/users');")

        found = [p.name for p in _find_source_files(tmp_path)]
        assert "client.ts" in found
        assert "bundle.min.ts" not in found

    def test_keeps_non_minified(self, tmp_path: Path):
        """Non-minified files with 'min' in name are kept."""
        admin = tmp_path / "admin.js"
        admin.write_text("fetch('/api/users');")
        mining = tmp_path / "mining.ts"
        mining.write_text("fetch('/api/users');")

        found = [p.name for p in _find_source_files(tmp_path)]
        assert "admin.js" in found
        assert "mining.ts" in found

    def test_non_js_ts_unaffected(self, tmp_path: Path):
        """Python, Go, Ruby, Java files are unaffected by minified filter."""
        (tmp_path / "app.py").write_text("requests.get('/api')")
        (tmp_path / "main.go").write_text("http.Get('/api')")
        (tmp_path / "client.rb").write_text("RestClient.get('/api')")
        (tmp_path / "Api.java").write_text("restTemplate.getForObject('/api')")

        found = [p.name for p in _find_source_files(tmp_path)]
        assert "app.py" in found
        assert "main.go" in found
        assert "client.rb" in found
        assert "Api.java" in found


class TestCreateClientSymbolStableId:
    """Tests for _create_client_symbol stable_id collision fix (INV-popop)."""

    def test_different_urls_produce_different_stable_ids(self, tmp_path: Path):
        """GET /users and GET /posts must not collide on stable_id."""
        call_a = HttpClientCall(
            method="GET", url="/api/users", line=10,
            file_path=str(tmp_path / "app.py"), language="python",
        )
        call_b = HttpClientCall(
            method="GET", url="/api/posts", line=20,
            file_path=str(tmp_path / "app.py"), language="python",
        )
        sym_a = _create_client_symbol(call_a, tmp_path)
        sym_b = _create_client_symbol(call_b, tmp_path)
        assert sym_a.stable_id != sym_b.stable_id

    def test_same_url_produces_same_stable_id(self, tmp_path: Path):
        """Same method+URL from different lines should have the same stable_id."""
        call_a = HttpClientCall(
            method="GET", url="/api/users", line=10,
            file_path=str(tmp_path / "a.py"), language="python",
        )
        call_b = HttpClientCall(
            method="GET", url="/api/users", line=99,
            file_path=str(tmp_path / "b.py"), language="python",
        )
        sym_a = _create_client_symbol(call_a, tmp_path)
        sym_b = _create_client_symbol(call_b, tmp_path)
        assert sym_a.stable_id == sym_b.stable_id

    def test_stable_id_is_not_bare_method(self, tmp_path: Path):
        """stable_id must not be a bare HTTP method string."""
        call = HttpClientCall(
            method="POST", url="/api/users", line=5,
            file_path=str(tmp_path / "app.js"), language="javascript",
        )
        sym = _create_client_symbol(call, tmp_path)
        assert sym.stable_id != "POST"
        assert sym.stable_id != "post"
        # Should be a hash digest (64 hex chars for sha256)
        assert len(sym.stable_id) == 64

    def test_different_methods_same_url_different_stable_ids(self, tmp_path: Path):
        """GET /api/users and POST /api/users are distinct."""
        call_get = HttpClientCall(
            method="GET", url="/api/users", line=10,
            file_path=str(tmp_path / "app.py"), language="python",
        )
        call_post = HttpClientCall(
            method="POST", url="/api/users", line=11,
            file_path=str(tmp_path / "app.py"), language="python",
        )
        sym_get = _create_client_symbol(call_get, tmp_path)
        sym_post = _create_client_symbol(call_post, tmp_path)
        assert sym_get.stable_id != sym_post.stable_id

    def test_full_url_extracts_path(self, tmp_path: Path):
        """Full URLs should have path extracted for stable_id."""
        call_full = HttpClientCall(
            method="GET", url="http://localhost:8080/api/users", line=10,
            file_path=str(tmp_path / "app.py"), language="python",
        )
        call_path = HttpClientCall(
            method="GET", url="/api/users", line=20,
            file_path=str(tmp_path / "app.py"), language="python",
        )
        sym_full = _create_client_symbol(call_full, tmp_path)
        sym_path = _create_client_symbol(call_path, tmp_path)
        # Both should resolve to the same stable_id since paths match
        assert sym_full.stable_id == sym_path.stable_id


# ---------------------------------------------------------------------------
# Tests: Elm HTTP scanner (WI-tinip)
# ---------------------------------------------------------------------------


class TestScanElmFile:
    """Tests for Elm HTTP client call detection."""

    def test_utils_api_get(self) -> None:
        """``Utils.Api.get (apiUrl ++ "/receivers") ...`` → GET /receivers."""
        code = dedent('''
            module Alerts.Api exposing (fetchReceivers)

            fetchReceivers apiUrl =
                Utils.Api.send
                    (Utils.Api.get
                        (apiUrl ++ "/receivers")
                        decoder
                    )
        ''')
        calls = _scan_elm_file(Path("Alerts/Api.elm"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/receivers"
        assert calls[0].language == "elm"
        assert calls[0].url_type == "literal"

    def test_utils_api_post(self) -> None:
        """``Utils.Api.post (apiUrl ++ "/silences") body ...`` → POST /silences."""
        code = dedent('''
            createSilence apiUrl body =
                Utils.Api.post (apiUrl ++ "/silences") body
        ''')
        calls = _scan_elm_file(Path("Silences/Api.elm"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "/silences"

    def test_utils_api_delete(self) -> None:
        """``Utils.Api.delete`` is supported."""
        code = dedent('''
            deleteSilence apiUrl id =
                Utils.Api.delete (apiUrl ++ "/silence/abc-123")
        ''')
        calls = _scan_elm_file(Path("Silences/Api.elm"), code)
        assert len(calls) == 1
        assert calls[0].method == "DELETE"
        assert calls[0].url == "/silence/abc-123"

    def test_http_get_record_form(self) -> None:
        """``Http.get { url = "..." }`` from the core library."""
        code = dedent('''
            getStatus =
                Http.get
                    { url = "/api/status"
                    , expect = Http.expectJson GotStatus decoder
                    }
        ''')
        calls = _scan_elm_file(Path("Status.elm"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/status"

    def test_http_post_record_form(self) -> None:
        """``Http.post { url = "..." }`` POST variant."""
        code = dedent('''
            submitForm body =
                Http.post
                    { url = "/api/v1/submit"
                    , body = Http.jsonBody body
                    , expect = Http.expectJson Submitted decoder
                    }
        ''')
        calls = _scan_elm_file(Path("Form.elm"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "/api/v1/submit"

    def test_http_request_method_first(self) -> None:
        """``Http.request { method = "PUT", url = "...", ... }`` explicit method."""
        code = dedent('''
            updateUser id data =
                Http.request
                    { method = "PUT"
                    , headers = []
                    , url = "/api/users/1"
                    , body = Http.jsonBody data
                    , expect = Http.expectJson Updated decoder
                    , timeout = Nothing
                    , tracker = Nothing
                    }
        ''')
        calls = _scan_elm_file(Path("Users.elm"), code)
        assert len(calls) == 1
        assert calls[0].method == "PUT"
        assert calls[0].url == "/api/users/1"

    def test_http_request_url_first(self) -> None:
        """``Http.request { url = "...", method = "DELETE", ... }`` — url before method."""
        code = dedent('''
            removeUser id =
                Http.request
                    { url = "/api/users/1"
                    , method = "DELETE"
                    , headers = []
                    , body = Http.emptyBody
                    , expect = Http.expectWhatever Removed
                    , timeout = Nothing
                    , tracker = Nothing
                    }
        ''')
        calls = _scan_elm_file(Path("Users.elm"), code)
        assert len(calls) == 1
        assert calls[0].method == "DELETE"
        assert calls[0].url == "/api/users/1"

    def test_multiple_calls_in_module(self) -> None:
        """A single Elm module can define many endpoint functions."""
        code = dedent('''
            module Alerts.Api exposing (fetchAlerts, fetchReceivers)

            fetchAlerts apiUrl =
                Utils.Api.get (apiUrl ++ "/alerts") decoder

            fetchReceivers apiUrl =
                Utils.Api.get (apiUrl ++ "/receivers") decoder

            postAlert apiUrl body =
                Utils.Api.post (apiUrl ++ "/alerts") body
        ''')
        calls = _scan_elm_file(Path("Api.elm"), code)
        assert len(calls) == 3
        methods = sorted((c.method, c.url) for c in calls)
        assert methods == [
            ("GET", "/alerts"),
            ("GET", "/receivers"),
            ("POST", "/alerts"),
        ]

    def test_no_http_calls_returns_empty(self) -> None:
        """An Elm module with no HTTP calls returns no calls."""
        code = dedent('''
            module Types exposing (Foo, bar)

            type Foo = Bar | Baz

            bar : Int -> Int
            bar x = x + 1
        ''')
        calls = _scan_elm_file(Path("Types.elm"), code)
        assert calls == []

    def test_line_numbers_recorded(self) -> None:
        """Each detected call records its line number correctly."""
        code = dedent('''
            module Api exposing (..)




            fetchA apiUrl =
                Utils.Api.get (apiUrl ++ "/a") decoder
        ''').lstrip("\n")
        calls = _scan_elm_file(Path("Api.elm"), code)
        assert len(calls) == 1
        # The Utils.Api.get line is line 7 (1-indexed) after the blank lines
        assert calls[0].line == 7


class TestElmFilesAreScanned:
    """Integration: _find_source_files should include .elm files so the
    scanner is reached by link_http."""

    def test_elm_file_listed(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Alerts.elm").write_text(
            "module Alerts exposing (fetch)\n"
            "fetch apiUrl = Utils.Api.get (apiUrl ++ \"/alerts\")"
        )
        files = list(_find_source_files(tmp_path))
        assert any(f.suffix == ".elm" for f in files), (
            f"Expected .elm file in discovery set; got {[str(f) for f in files]}"
        )

    def test_link_http_routes_elm_to_go(self, tmp_path: Path) -> None:
        """End-to-end: an Elm client calling /api/alerts should be linked
        to a Go route symbol declared with that same path.
        """
        (tmp_path / "ui").mkdir()
        (tmp_path / "ui" / "Alerts.elm").write_text(dedent('''
            module Alerts exposing (..)

            fetchAlerts apiUrl =
                Utils.Api.get (apiUrl ++ "/api/v2/alerts") decoder
        ''').lstrip("\n"))

        # Mock a Go route symbol with route info in meta
        # (format mirrors what Go/Ruby/PHP analyzers emit — see
        # _get_route_info_from_concept).
        route = Symbol(
            id="api/alerts.go::handleAlerts",
            name="handleAlerts",
            kind="route",
            path=str(tmp_path / "api" / "alerts.go"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=0),
            language="go",
            meta={
                "route_path": "/api/v2/alerts",
                "http_method": "GET",
            },
        )

        result = link_http(tmp_path, [route])

        # Elm symbol should have been created
        elm_syms = [s for s in result.symbols if s.language == "elm"]
        assert len(elm_syms) == 1
        # And a cross-language edge should exist
        assert any(
            e.src == elm_syms[0].id and e.dst == route.id
            and e.edge_type == "http_calls"
            for e in result.edges
        ), "Elm → Go route edge missing from link_http result"


class TestExtractModuleConstants:
    """Tests for JS/TS module-scope const assignment extraction (WI-sijoh)."""

    def test_extracts_const_with_double_quotes(self):
        code = dedent('''
            const API_PATH = "api/v1";
        ''')
        assert _extract_module_constants(code) == {"API_PATH": "api/v1"}

    def test_extracts_const_with_single_quotes(self):
        code = dedent('''
            const API_URL = '/api';
        ''')
        assert _extract_module_constants(code) == {"API_URL": "/api"}

    def test_extracts_exported_const(self):
        code = dedent('''
            export const BASE = "/v2";
        ''')
        assert _extract_module_constants(code) == {"BASE": "/v2"}

    def test_extracts_let_and_var(self):
        code = dedent('''
            let FOO = "/foo";
            var BAR = "/bar";
        ''')
        assert _extract_module_constants(code) == {"FOO": "/foo", "BAR": "/bar"}

    def test_extracts_multiple_consts(self):
        code = dedent('''
            const A = "/a";
            const B = "/b";
            export const C = "/c";
        ''')
        assert _extract_module_constants(code) == {"A": "/a", "B": "/b", "C": "/c"}

    def test_skips_non_string_consts(self):
        """Numeric / object / array consts are not URL candidates."""
        code = dedent('''
            const COUNT = 42;
            const CONFIG = { url: "/api" };
            const TAGS = ["a", "b"];
        ''')
        # COUNT is skipped (numeric), CONFIG/TAGS skipped (not plain strings).
        assert _extract_module_constants(code) == {}

    def test_skips_function_scope_const(self):
        """Only module-scope (top-level) consts should be folded."""
        code = dedent('''
            function makeUrl() {
                const LOCAL = "/local";
                return LOCAL;
            }
        ''')
        # Indented const inside a function is not a module-level binding.
        assert _extract_module_constants(code) == {}


class TestFoldTemplateLiteral:
    """Tests for template-literal folding (WI-sijoh)."""

    def test_no_interpolation_yields_literal(self):
        url, url_type = _fold_template_literal("/api/users", {})
        assert url == "/api/users"
        assert url_type == "literal"

    def test_all_interpolations_resolved_yields_literal(self):
        url, url_type = _fold_template_literal(
            "${BASE}/users", {"BASE": "/api/v1"},
        )
        assert url == "/api/v1/users"
        assert url_type == "literal"

    def test_unresolved_leading_prefix_is_stripped(self):
        """A leading ${pathPrefix}/ is treated as host/base per Elm semantics."""
        url, url_type = _fold_template_literal("${pathPrefix}/api/users", {})
        assert url == "/api/users"
        # Still "literal" because the remaining path has no unresolved slots.
        assert url_type == "literal"

    def test_middle_unresolved_slot_becomes_param(self):
        """A middle ${id} slot becomes {id} so route-pattern matching works."""
        url, url_type = _fold_template_literal("/api/users/${id}", {})
        assert url == "/api/users/{id}"
        assert url_type == "literal"

    def test_trailing_non_path_slot_is_stripped_as_variable(self):
        """A trailing ${queryString} slot is stripped; marks url_type=variable."""
        url, url_type = _fold_template_literal(
            "/api/items${queryString}", {},
        )
        # Prefix remains for route-matching via startswith (WI-sijoh).
        assert url == "/api/items"
        assert url_type == "variable"

    def test_mixed_folded_const_and_param(self):
        url, url_type = _fold_template_literal(
            "${BASE}/users/${id}", {"BASE": "/api/v1"},
        )
        assert url == "/api/v1/users/{id}"
        assert url_type == "literal"

    def test_fully_unresolved_stays_variable(self):
        """With no literal anchor, result is empty and url_type is variable."""
        url, url_type = _fold_template_literal("${base}${path}", {})
        # Truncation at first non-'/'-preceded placeholder leaves nothing.
        assert url == ""
        assert url_type == "variable"

    def test_mantine_ui_case_folds_prefix_and_strips_leading_host(self):
        """The motivating case from WI-sijoh — alertmanager mantine-ui api.ts.

        After host-prefix strip and const fold, the URL becomes
        /api/v1{path}{queryString}. Because {path} is preceded by a
        non-'/' char ('1'), it is template continuation — the URL is
        truncated there and url_type becomes 'variable'. The remaining
        '/api/v1' is a usable route-matching prefix.
        """
        url, url_type = _fold_template_literal(
            "${pathPrefix}/${API_PATH}${path}${queryString}",
            {"API_PATH": "api/v1"},
        )
        assert url == "/api/v1"
        assert url_type == "variable"


class TestJavaScriptTemplateLiteralFetch:
    """Tests for fetch(\\`...\\`) backtick template literal scanning (WI-sijoh)."""

    def test_fetch_with_literal_template(self):
        code = dedent('''
            fetch(`/api/users`);
        ''')
        calls = _scan_javascript_file(Path("test.ts"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/users"
        assert calls[0].url_type == "literal"

    def test_fetch_with_const_interpolation(self):
        code = dedent('''
            const API_PATH = "api/v1";
            fetch(`/${API_PATH}/alerts`);
        ''')
        calls = _scan_javascript_file(Path("test.ts"), code)
        assert len(calls) == 1
        assert calls[0].url == "/api/v1/alerts"
        assert calls[0].url_type == "literal"

    def test_fetch_with_path_param(self):
        code = dedent('''
            fetch(`/api/users/${id}`);
        ''')
        calls = _scan_javascript_file(Path("test.ts"), code)
        assert len(calls) == 1
        assert calls[0].url == "/api/users/{id}"
        assert calls[0].url_type == "literal"

    def test_fetch_mantine_ui_case(self):
        """The motivating WI-sijoh case from alertmanager mantine-ui/src/api/api.ts."""
        code = dedent('''
            export const API_PATH = "api/v1";

            const res = await fetch(
                `${pathPrefix}/${API_PATH}${path}${queryString}`,
                { cache: "no-store" }
            );
        ''')
        calls = _scan_javascript_file(Path("api.ts"), code)
        # pathPrefix host-stripped, API_PATH folded, {path} truncates because
        # it continues inline with 'v1'. Prefix '/api/v1' is usable for
        # startswith-style route matching; url_type='variable' flags it.
        fetch_calls = [c for c in calls if c.url.startswith("/api/v1")]
        assert len(fetch_calls) == 1
        assert fetch_calls[0].url == "/api/v1"
        assert fetch_calls[0].url_type == "variable"

    def test_fetch_fully_variable_template_emits_variable(self):
        """When nothing can be folded, emit url_type='variable' for the raw template."""
        code = dedent('''
            fetch(`${base}${path}`);
        ''')
        calls = _scan_javascript_file(Path("test.ts"), code)
        assert len(calls) == 1
        assert calls[0].url_type == "variable"

    def test_axios_get_with_template(self):
        code = dedent('''
            const BASE = "/api/v1";
            axios.get(`${BASE}/alerts`);
        ''')
        calls = _scan_javascript_file(Path("test.ts"), code)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "/api/v1/alerts"
        assert calls[0].url_type == "literal"

    def test_axios_post_with_template(self):
        code = dedent('''
            axios.post(`/api/items/${id}`, body);
        ''')
        calls = _scan_javascript_file(Path("test.ts"), code)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].url == "/api/items/{id}"

    def test_template_literal_does_not_double_match_as_fetch_variable(self):
        """A single template fetch must not be detected both as template and variable."""
        code = dedent('''
            fetch(`/api/users`);
        ''')
        calls = _scan_javascript_file(Path("test.ts"), code)
        assert len(calls) == 1


class TestJsTemplateLiteralEndToEnd:
    """Integration: template-literal fetch → Go route should produce routes_to edge."""

    def test_mantine_ui_template_fetch_links_to_go_route(self, tmp_path: Path):
        (tmp_path / "ui").mkdir()
        (tmp_path / "ui" / "api.ts").write_text(dedent('''
            export const API_PATH = "api/v1";
            async function fetchAlerts(pathPrefix: string, path: string) {
                return fetch(`${pathPrefix}/${API_PATH}${path}`);
            }
        ''').lstrip("\n"))

        route = Symbol(
            id="api/alerts.go::handleAlerts",
            name="handleAlerts",
            kind="route",
            path=str(tmp_path / "api" / "alerts.go"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=0),
            language="go",
            meta={"route_path": "/api/v1/alerts", "http_method": "GET"},
        )

        result = link_http(tmp_path, [route])

        ts_syms = [s for s in result.symbols if s.language == "javascript"]
        assert len(ts_syms) == 1
        assert any(
            e.src == ts_syms[0].id
            and e.dst == route.id
            and e.edge_type == "http_calls"
            for e in result.edges
        ), "TS → Go route edge missing from link_http result"

    def test_prefix_candidate_gate(self):
        """_is_prefix_candidate requires >=2 non-empty path segments."""
        from hypergumbo_core.linkers.http import _is_prefix_candidate

        assert _is_prefix_candidate("/api/v1") is True
        assert _is_prefix_candidate("/api/v1/users") is True
        assert _is_prefix_candidate("/") is False
        assert _is_prefix_candidate("/api") is False
        assert _is_prefix_candidate("") is False

    def test_variable_url_wildcard_route_prefix_match(self, tmp_path: Path):
        """Variable-URL prefix match also walks the wildcard (ANY) route bucket."""
        (tmp_path / "ui").mkdir()
        (tmp_path / "ui" / "api.ts").write_text(dedent('''
            export const API_PATH = "api/v1";
            async function fetchAny(path: string) {
                return fetch(`/${API_PATH}${path}`);
            }
        ''').lstrip("\n"))

        # Route registered without an explicit method ("ANY"/wildcard bucket).
        route = Symbol(
            id="api/any.py::any_handler",
            name="any_handler",
            kind="route",
            path=str(tmp_path / "api" / "any.py"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=0),
            language="python",
            meta={"route_path": "/api/v1/catchall"},
        )

        result = link_http(tmp_path, [route])

        assert any(
            e.dst == route.id and e.edge_type == "http_calls"
            for e in result.edges
        ), "Wildcard-method route should be reachable via variable-URL prefix match"

    def test_variable_url_below_prefix_threshold_no_match(self, tmp_path: Path):
        """A shallow prefix (/api) must NOT trigger prefix fallback — too broad."""
        (tmp_path / "ui").mkdir()
        (tmp_path / "ui" / "api.ts").write_text(dedent('''
            async function fetchShallow(path: string) {
                return fetch(`/api${path}`);
            }
        ''').lstrip("\n"))

        route = Symbol(
            id="api/foo.go::foo",
            name="foo",
            kind="route",
            path=str(tmp_path / "api" / "foo.go"),
            span=Span(start_line=1, start_col=0, end_line=1, end_col=0),
            language="go",
            meta={"route_path": "/api/foo", "http_method": "GET"},
        )

        result = link_http(tmp_path, [route])

        # Client symbol is still created, but no edge emitted.
        assert not any(
            e.edge_type == "http_calls" for e in result.edges
        ), "Shallow '/api' prefix should not match any route"
