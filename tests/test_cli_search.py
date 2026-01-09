"""Tests for the hypergumbo search command."""
import json
from pathlib import Path

from hypergumbo.schema import SCHEMA_VERSION
from hypergumbo.cli import cmd_search, main


class FakeArgs:
    """Minimal namespace for testing command functions."""

    pass


def test_cmd_search_finds_exact_match(tmp_path: Path, capsys) -> None:
    """Search finds symbols by exact name match."""
    # Create a behavior map with some symbols
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/utils.py:1-5:bar:function",
                "name": "bar",
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
    args.pattern = "foo"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "foo" in out
    assert "src/main.py" in out


def test_cmd_search_fuzzy_match(tmp_path: Path, capsys) -> None:
    """Search finds symbols by fuzzy/partial match."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:getUserById:function",
                "name": "getUserById",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:6-10:getPostById:function",
                "name": "getPostById",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 6, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "ById"  # Partial match
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "getUserById" in out
    assert "getPostById" in out


def test_cmd_search_filter_by_kind(tmp_path: Path, capsys) -> None:
    """Search can filter by symbol kind."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:User:class",
                "name": "User",
                "kind": "class",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:6-10:user:function",
                "name": "user",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 6, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "user"
    args.path = str(tmp_path)
    args.input = None
    args.kind = "class"
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "User" in out
    assert "class" in out


def test_cmd_search_filter_by_language(tmp_path: Path, capsys) -> None:
    """Search can filter by language."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:getData:function",
                "name": "getData",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "javascript:src/main.js:1-5:getData:function",
                "name": "getData",
                "kind": "function",
                "language": "javascript",
                "path": "src/main.js",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "getData"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = "python"
    args.limit = 20

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "python" in out
    assert "javascript" not in out


def test_cmd_search_no_results(tmp_path: Path, capsys) -> None:
    """Search reports no results when nothing matches."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "nonexistent"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "No symbols found" in out


def test_cmd_search_with_input_file(tmp_path: Path, capsys) -> None:
    """Search can read from specified input file."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:bar:function",
                "name": "bar",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    input_file = tmp_path / "custom_results.json"
    input_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "bar"
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "bar" in out


def test_cmd_search_input_not_found(tmp_path: Path) -> None:
    """Search fails if input file doesn't exist."""
    args = FakeArgs()
    args.pattern = "foo"
    args.path = str(tmp_path)
    args.input = str(tmp_path / "nonexistent.json")
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 1


def test_cmd_search_no_results_file(tmp_path: Path) -> None:
    """Search fails if no results file exists."""
    args = FakeArgs()
    args.pattern = "foo"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 1


def test_cmd_search_respects_limit(tmp_path: Path, capsys) -> None:
    """Search respects the --limit option."""
    nodes = [
        {
            "id": f"python:src/file{i}.py:1-5:func{i}:function",
            "name": f"func{i}",
            "kind": "function",
            "language": "python",
            "path": f"src/file{i}.py",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        }
        for i in range(10)
    ]
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "func"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 3

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show only 3 results
    assert out.count("function") <= 3


def test_main_with_search(tmp_path: Path, capsys) -> None:
    """Main with search command."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:test:function",
                "name": "test",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    result = main(["search", "test", "--path", str(tmp_path)])

    assert result == 0

    out, _ = capsys.readouterr()
    assert "test" in out
