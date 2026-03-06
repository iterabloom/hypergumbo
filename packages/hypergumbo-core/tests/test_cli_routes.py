"""Tests for the hypergumbo routes command.

Covers cmd_routes CLI command: listing API routes, filtering by language,
test path exclusion, and output formatting.
"""
import json
from pathlib import Path

from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.cli import cmd_routes, main


class FakeArgs:
    """Minimal namespace for testing command functions."""

    pass


def test_cmd_routes_shows_http_routes(tmp_path: Path, capsys) -> None:
    """Routes command shows HTTP API endpoints."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/api.py:1-5:get_user:function",
                "name": "get_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:abc123",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/users/{id}", "method": "GET"}]
                },
            },
            {
                "id": "python:src/api.py:6-10:create_user:function",
                "name": "create_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 6, "end_line": 10, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:def456",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/users", "method": "POST"}]
                },
            },
            {
                "id": "python:src/utils.py:1-5:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                # No concepts - not a route
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None

    result = cmd_routes(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "get_user" in out
    assert "create_user" in out
    assert "GET" in out.upper() or "get" in out.lower()
    assert "POST" in out.upper() or "post" in out.lower()
    assert "helper" not in out  # Non-route should not appear


def test_cmd_routes_filter_by_language(tmp_path: Path, capsys) -> None:
    """Routes can be filtered by language."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/api.py:1-5:get_data:function",
                "name": "get_data",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:abc123",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/data", "method": "GET"}]
                },
            },
            {
                "id": "javascript:src/api.js:1-5:getData:function",
                "name": "getData",
                "kind": "function",
                "language": "javascript",
                "path": "src/api.js",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:def456",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/data", "method": "GET"}]
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = "python"

    result = cmd_routes(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "get_data" in out
    assert "getData" not in out


def test_cmd_routes_no_routes_found(tmp_path: Path, capsys) -> None:
    """Routes command reports when no routes found."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/utils.py:1-5:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None

    result = cmd_routes(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "No API routes" in out


def test_cmd_routes_with_input_file(tmp_path: Path, capsys) -> None:
    """Routes can read from specified input file."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/api.py:1-5:delete_user:function",
                "name": "delete_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:abc123",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/users/{id}", "method": "DELETE"}]
                },
            },
        ],
        "edges": [],
    }
    input_file = tmp_path / "custom_results.json"
    input_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.language = None

    result = cmd_routes(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "delete_user" in out


def test_cmd_routes_input_not_found(tmp_path: Path) -> None:
    """Routes fails if input file doesn't exist."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(tmp_path / "nonexistent.json")
    args.language = None

    result = cmd_routes(args)

    assert result == 1


def test_cmd_routes_auto_runs_analysis(tmp_path: Path, capsys) -> None:
    """Routes auto-runs analysis if no results file exists."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None

    result = cmd_routes(args)

    # Auto-runs analysis and succeeds (even if no routes found)
    assert result == 0
    _, err = capsys.readouterr()
    assert "No cached results found, running analysis" in err


def test_cmd_routes_groups_by_path(tmp_path: Path, capsys) -> None:
    """Routes are grouped by file path."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/users.py:1-5:get_user:function",
                "name": "get_user",
                "kind": "function",
                "language": "python",
                "path": "src/users.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:abc123",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/users/{id}", "method": "GET"}]
                },
            },
            {
                "id": "python:src/users.py:6-10:create_user:function",
                "name": "create_user",
                "kind": "function",
                "language": "python",
                "path": "src/users.py",
                "span": {"start_line": 6, "end_line": 10, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:def456",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/users", "method": "POST"}]
                },
            },
            {
                "id": "python:src/posts.py:1-5:get_posts:function",
                "name": "get_posts",
                "kind": "function",
                "language": "python",
                "path": "src/posts.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:ghi789",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/posts", "method": "GET"}]
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None

    result = cmd_routes(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "src/users.py" in out
    assert "src/posts.py" in out


def test_cmd_routes_with_route_path(tmp_path: Path, capsys) -> None:
    """Routes with meta.concepts display the route path."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/api.py:1-5:get_user:function",
                "name": "get_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:abc123",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/users/{id}", "method": "GET"}]
                },
            },
            {
                "id": "python:src/api.py:6-10:create_user:function",
                "name": "create_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 6, "end_line": 10, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:def456",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/users", "method": "POST"}]
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None

    result = cmd_routes(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Route paths should be displayed with arrow notation
    assert "/users/{id}" in out
    assert "/users" in out
    assert "get_user" in out
    assert "create_user" in out
    assert "->" in out  # Route path arrow


def test_cmd_routes_with_concept_metadata(tmp_path: Path, capsys) -> None:
    """Routes with meta.concepts (FRAMEWORK_PATTERNS phase) display correctly."""
    behavior_map = {
        "schema_version": "0.1.0",
        "nodes": [
            {
                "id": "python:src/api.py:1-5:get_users:function",
                "name": "get_users",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:abc123",  # Hash stable_id, not HTTP method
                "meta": {
                    "concepts": [{"concept": "route", "path": "/users", "method": "GET"}]
                },
            },
            {
                "id": "python:src/api.py:6-10:create_user:function",
                "name": "create_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 6, "end_line": 10, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:def456",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/users", "method": "POST"}]
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None

    result = cmd_routes(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Route paths should be extracted from concept metadata
    assert "/users" in out
    assert "get_users" in out
    assert "create_user" in out
    assert "GET" in out
    assert "POST" in out
    assert "->" in out  # Route path arrow


def test_cmd_routes_concept_without_path(tmp_path: Path, capsys) -> None:
    """Routes with concept but no path display method and name only."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/api.py:1-5:get_users:function",
                "name": "get_users",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:abc123",
                "meta": {
                    # Route concept with method but no path
                    "concepts": [{"concept": "route", "method": "GET"}]
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None

    result = cmd_routes(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Route without path should display method and name (no arrow)
    assert "get_users" in out
    assert "GET" in out
    assert "->" not in out  # No route path arrow since there's no path


def test_main_with_routes(tmp_path: Path, capsys) -> None:
    """Main with routes command."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/api.py:1-5:update_item:function",
                "name": "update_item",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:abc123",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/items/{id}", "method": "PUT"}]
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    result = main(["routes", "--path", str(tmp_path)])

    assert result == 0

    out, _ = capsys.readouterr()
    assert "update_item" in out


def test_cmd_routes_prints_output_summary(tmp_path: Path, capsys) -> None:
    """Routes prints output summary to stdout."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/api.py:1-5:get_items:function",
                "name": "get_items",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:abc123",
                "meta": {
                    "concepts": [{"concept": "route", "path": "/items", "method": "GET"}]
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None

    result = cmd_routes(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "[hypergumbo routes] Using 1 cached" in out
    assert "Output: stdout" in out


def test_cmd_routes_includes_test_routes_by_default(tmp_path: Path, capsys) -> None:
    """Routes from test files are included by default (consistent with other commands).

    All subcommands use --exclude-tests (opt-in exclusion). Routes is no exception.
    """
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/api.py:1-5:get_user:function",
                "name": "get_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5},
                "meta": {
                    "concepts": [{"concept": "route", "path": "/users/{id}", "method": "GET"}]
                },
            },
            {
                "id": "python:tests/test_views.py:1-5:test_get_user:function",
                "name": "test_get_user",
                "kind": "function",
                "language": "python",
                "path": "tests/test_views.py",
                "span": {"start_line": 1, "end_line": 5},
                "meta": {
                    "concepts": [{"concept": "route", "path": "/test-endpoint", "method": "GET"}]
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None
    args.exclude_tests = False

    result = cmd_routes(args)
    assert result == 0

    out, _ = capsys.readouterr()
    # Both routes should be present by default
    assert "get_user" in out
    assert "test_get_user" in out
    assert "Found 2 API route" in out


def test_cmd_routes_shows_kind_route_symbols(tmp_path: Path, capsys) -> None:
    """Route symbols (kind='route') are shown even without concept enrichment.

    Go analyzers create route symbols with kind='route' and route metadata
    in meta (route_path, http_method, handler_name) without concept enrichment.
    The routes command should detect these based on symbol kind.
    """
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "go:cmd/webui.go:10-10:ANY /graphql:route",
                "name": "graphqlHandler",
                "kind": "route",
                "language": "go",
                "path": "cmd/webui.go",
                "span": {"start_line": 10, "end_line": 10, "start_col": 0, "end_col": 50},
                "stable_id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                "meta": {
                    "route_path": "/graphql",
                    "http_method": "ANY",
                    "handler_name": "graphqlHandler",
                },
            },
            {
                "id": "go:cmd/webui.go:12-12:POST /upload:route",
                "name": "uploadHandler",
                "kind": "route",
                "language": "go",
                "path": "cmd/webui.go",
                "span": {"start_line": 12, "end_line": 12, "start_col": 0, "end_col": 60},
                "stable_id": "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3",
                "meta": {
                    "route_path": "/upload/{repo}",
                    "http_method": "POST",
                    "handler_name": "uploadHandler",
                },
            },
            {
                "id": "go:cmd/server.go:5-20:main:function",
                "name": "main",
                "kind": "function",
                "language": "go",
                "path": "cmd/server.go",
                "span": {"start_line": 5, "end_line": 20},
                # Not a route - no concept, not kind=route
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None

    result = cmd_routes(args)
    assert result == 0

    out, _ = capsys.readouterr()
    assert "graphqlHandler" in out
    assert "uploadHandler" in out
    assert "/graphql" in out
    assert "/upload/{repo}" in out
    assert "ANY" in out
    assert "POST" in out
    assert "main" not in out  # Non-route should not appear
    assert "Found 2 API route" in out


def test_cmd_routes_shows_controller_action(tmp_path: Path, capsys) -> None:
    """Routes with controller_action in meta display it instead of symbol name.

    Rails route symbols have controller_action populated (e.g. 'users#index').
    The output should show this mapping so developers can navigate from route
    to handler: [GET] /users -> users#index (line 5).
    """
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "ruby:config/routes.rb:5-5:GET /users:route",
                "name": "GET /users",
                "kind": "route",
                "language": "ruby",
                "path": "config/routes.rb",
                "span": {"start_line": 5, "end_line": 5, "start_col": 0, "end_col": 30},
                "stable_id": "sha256:abc123",
                "meta": {
                    "http_method": "GET",
                    "route_path": "/users",
                    "controller_action": "users#index",
                },
            },
            {
                "id": "ruby:config/routes.rb:6-6:POST /users:route",
                "name": "POST /users",
                "kind": "route",
                "language": "ruby",
                "path": "config/routes.rb",
                "span": {"start_line": 6, "end_line": 6, "start_col": 0, "end_col": 30},
                "stable_id": "sha256:def456",
                "meta": {
                    "http_method": "POST",
                    "route_path": "/users",
                    "controller_action": "users#create",
                },
            },
            {
                "id": "ruby:config/routes.rb:10-10:GET /health:route",
                "name": "GET /health",
                "kind": "route",
                "language": "ruby",
                "path": "config/routes.rb",
                "span": {"start_line": 10, "end_line": 10, "start_col": 0, "end_col": 30},
                "stable_id": "sha256:ghi789",
                "meta": {
                    "http_method": "GET",
                    "route_path": "/health",
                    # No controller_action - should fall back to name
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None

    result = cmd_routes(args)
    assert result == 0

    out, _ = capsys.readouterr()
    # Routes with controller_action should show it
    assert "users#index" in out
    assert "users#create" in out
    # Route without controller_action falls back to name
    assert "GET /health" in out
    # controller_action should appear after the arrow
    assert "[GET] /users -> users#index" in out
    assert "[POST] /users -> users#create" in out


def test_cmd_routes_controller_action_from_concept(tmp_path: Path, capsys) -> None:
    """Routes with controller_action in concept metadata display it.

    Concept-enriched routes (from YAML pattern matching) may also carry
    controller_action in the concept dict.
    """
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/api.py:1-5:get_user:function",
                "name": "get_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "sha256:abc123",
                "meta": {
                    "concepts": [
                        {
                            "concept": "route",
                            "path": "/users/{id}",
                            "method": "GET",
                            "controller_action": "UserController.get_user",
                        }
                    ]
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None

    result = cmd_routes(args)
    assert result == 0

    out, _ = capsys.readouterr()
    assert "UserController.get_user" in out
    assert "[GET] /users/{id} -> UserController.get_user" in out


def test_cmd_routes_excludes_test_routes_with_flag(tmp_path: Path, capsys) -> None:
    """Routes from test files are excluded when --exclude-tests is used.

    Django bakeoff showed 73% of route source files were from test
    directories. Use -x/--exclude-tests to filter them out.
    """
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/api.py:1-5:get_user:function",
                "name": "get_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 5},
                "meta": {
                    "concepts": [{"concept": "route", "path": "/users/{id}", "method": "GET"}]
                },
            },
            {
                "id": "python:tests/test_views.py:1-5:test_get_user:function",
                "name": "test_get_user",
                "kind": "function",
                "language": "python",
                "path": "tests/test_views.py",
                "span": {"start_line": 1, "end_line": 5},
                "meta": {
                    "concepts": [{"concept": "route", "path": "/test-endpoint", "method": "GET"}]
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None
    args.exclude_tests = True

    result = cmd_routes(args)
    assert result == 0

    out, _ = capsys.readouterr()
    # Production route should be present
    assert "get_user" in out
    # Test route should be excluded
    assert "test_get_user" not in out
    assert "Found 1 API route" in out


def test_cmd_routes_deduplicates_within_same_file(tmp_path: Path, capsys) -> None:
    """Route symbols with same (method, path, file) are deduplicated.

    When a materialized route symbol and a concept-enriched handler both
    exist in the same file for the same endpoint, only the first is shown.
    """
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "go:routes.go:10-15:GET /api/users:route",
                "name": "GET /api/users",
                "kind": "route",
                "language": "go",
                "path": "routes.go",
                "span": {"start_line": 10, "end_line": 15},
                "meta": {"route_path": "/api/users", "http_method": "GET"},
            },
            {
                "id": "go:routes.go:20-25:listUsers:function",
                "name": "listUsers",
                "kind": "function",
                "language": "go",
                "path": "routes.go",
                "span": {"start_line": 20, "end_line": 25},
                "meta": {
                    "concepts": [
                        {"concept": "route", "path": "/api/users", "method": "GET"}
                    ]
                },
            },
            {
                "id": "go:routes.go:30-35:createUser:function",
                "name": "createUser",
                "kind": "function",
                "language": "go",
                "path": "routes.go",
                "span": {"start_line": 30, "end_line": 35},
                "meta": {
                    "concepts": [
                        {"concept": "route", "path": "/api/users", "method": "POST"}
                    ]
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None
    args.exclude_tests = False

    result = cmd_routes(args)
    assert result == 0

    out, _ = capsys.readouterr()
    # GET /api/users appears in two nodes in the same file, show once
    assert out.count("[GET] /api/users") == 1
    # POST /api/users is unique, should appear once
    assert out.count("[POST] /api/users") == 1
    # Total should be 2 unique routes, not 3
    assert "Found 2 API route" in out


def test_cmd_routes_keeps_same_method_path_from_different_files(
    tmp_path: Path, capsys
) -> None:
    """Route symbols with same (method, path) from different files are NOT deduped.

    The go-swagger handler cache pattern creates kind=route nodes in
    api/v2/restapi/operations/alertmanager_api.go while the v1 deprecation
    router creates kind=route nodes in api/v1_deprecation_router.go.
    Both register GET /alerts but they are different API versions and
    should both appear in the output.

    Deduplication only applies when a materialized route symbol and a
    concept-enriched handler node represent the same registration.
    """
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "go:v1_deprecation_router.go:48-48:GET /alerts:route",
                "name": "dr.deprecationHandler",
                "kind": "route",
                "language": "go",
                "path": "api/v1_deprecation_router.go",
                "span": {"start_line": 48, "end_line": 48},
                "meta": {"route_path": "/alerts", "http_method": "GET"},
            },
            {
                "id": "go:alertmanager_api.go:377-377:GET /alerts:route",
                "name": "alert.NewGetAlerts",
                "kind": "route",
                "language": "go",
                "path": "api/v2/restapi/operations/alertmanager_api.go",
                "span": {"start_line": 377, "end_line": 377},
                "meta": {"route_path": "/alerts", "http_method": "GET"},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None
    args.exclude_tests = False

    result = cmd_routes(args)
    assert result == 0

    out, _ = capsys.readouterr()
    # Both routes should appear — they are different API versions
    assert out.count("[GET] /alerts") == 2
    assert "Found 2 API route" in out


def test_cmd_routes_dedup_duplicate_concept_routes_same_file(
    tmp_path: Path, capsys
) -> None:
    """Two concept-enriched handlers in same file with same (method, path) are deduped."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "go:routes.go:10-15:handlerA:function",
                "name": "handlerA",
                "kind": "function",
                "language": "go",
                "path": "routes.go",
                "span": {"start_line": 10, "end_line": 15},
                "meta": {
                    "concepts": [
                        {"concept": "route", "path": "/api/users", "method": "GET"}
                    ]
                },
            },
            {
                "id": "go:routes.go:20-25:handlerB:function",
                "name": "handlerB",
                "kind": "function",
                "language": "go",
                "path": "routes.go",
                "span": {"start_line": 20, "end_line": 25},
                "meta": {
                    "concepts": [
                        {"concept": "route", "path": "/api/users", "method": "GET"}
                    ]
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None
    args.exclude_tests = False

    result = cmd_routes(args)
    assert result == 0

    out, _ = capsys.readouterr()
    assert out.count("[GET] /api/users") == 1
    assert "Found 1 API route" in out


def test_cmd_routes_dedup_same_route_same_file(
    tmp_path: Path, capsys
) -> None:
    """Two kind=route nodes with same (method, path, file) are deduped."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "go:routes.go:10-10:GET /x:route",
                "name": "handler1",
                "kind": "route",
                "language": "go",
                "path": "routes.go",
                "span": {"start_line": 10, "end_line": 10},
                "meta": {"route_path": "/x", "http_method": "GET"},
            },
            {
                "id": "go:routes.go:20-20:GET /x:route",
                "name": "handler2",
                "kind": "route",
                "language": "go",
                "path": "routes.go",
                "span": {"start_line": 20, "end_line": 20},
                "meta": {"route_path": "/x", "http_method": "GET"},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None
    args.exclude_tests = False

    result = cmd_routes(args)
    assert result == 0

    out, _ = capsys.readouterr()
    assert out.count("[GET] /x") == 1
    assert "Found 1 API route" in out


def test_cmd_routes_concept_before_materialized_same_file_dedup(
    tmp_path: Path, capsys
) -> None:
    """Concept-enriched route and materialized route in same file are deduped."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "go:routes.go:10-15:listUsers:function",
                "name": "listUsers",
                "kind": "function",
                "language": "go",
                "path": "routes.go",
                "span": {"start_line": 10, "end_line": 15},
                "meta": {
                    "concepts": [
                        {"concept": "route", "path": "/api/users", "method": "GET"}
                    ]
                },
            },
            {
                "id": "go:routes.go:20-20:GET /api/users:route",
                "name": "GET /api/users",
                "kind": "route",
                "language": "go",
                "path": "routes.go",
                "span": {"start_line": 20, "end_line": 20},
                "meta": {"route_path": "/api/users", "http_method": "GET"},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None
    args.exclude_tests = False

    result = cmd_routes(args)
    assert result == 0

    out, _ = capsys.readouterr()
    assert out.count("[GET] /api/users") == 1
    assert "Found 1 API route" in out
