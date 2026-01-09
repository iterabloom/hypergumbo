"""Tests for the hypergumbo routes command."""
import json
from pathlib import Path

from hypergumbo.schema import SCHEMA_VERSION
from hypergumbo.cli import cmd_routes, main


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
                "stable_id": "get",  # HTTP GET marker
            },
            {
                "id": "python:src/api.py:6-10:create_user:function",
                "name": "create_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 6, "end_line": 10, "start_col": 0, "end_col": 10},
                "stable_id": "post",  # HTTP POST marker
            },
            {
                "id": "python:src/utils.py:1-5:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                # No stable_id - not a route
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
                "stable_id": "get",
            },
            {
                "id": "javascript:src/api.js:1-5:getData:function",
                "name": "getData",
                "kind": "function",
                "language": "javascript",
                "path": "src/api.js",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "get",
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
                "stable_id": "delete",
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


def test_cmd_routes_no_results_file(tmp_path: Path) -> None:
    """Routes fails if no results file exists."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.language = None

    result = cmd_routes(args)

    assert result == 1


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
                "stable_id": "get",
            },
            {
                "id": "python:src/users.py:6-10:create_user:function",
                "name": "create_user",
                "kind": "function",
                "language": "python",
                "path": "src/users.py",
                "span": {"start_line": 6, "end_line": 10, "start_col": 0, "end_col": 10},
                "stable_id": "post",
            },
            {
                "id": "python:src/posts.py:1-5:get_posts:function",
                "name": "get_posts",
                "kind": "function",
                "language": "python",
                "path": "src/posts.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
                "stable_id": "get",
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
    """Routes with meta.route_path display the route path."""
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
                "stable_id": "get",
                "meta": {"route_path": "/users/{id}"},
            },
            {
                "id": "python:src/api.py:6-10:create_user:function",
                "name": "create_user",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 6, "end_line": 10, "start_col": 0, "end_col": 10},
                "stable_id": "post",
                "meta": {"route_path": "/users"},
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
                "stable_id": "put",
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
