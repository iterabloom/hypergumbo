"""Tests for the hypergumbo symbols command."""
import json
from pathlib import Path

from hypergumbo.schema import SCHEMA_VERSION
from hypergumbo.cli import cmd_symbols, main


class FakeArgs:
    """Minimal namespace for testing command functions."""

    pass


def test_cmd_symbols_shows_tabular_output(tmp_path: Path, capsys) -> None:
    """Symbols command shows tabular output with degrees."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:11-20:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 11, "end_line": 20, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            {
                "id": "edge:1",
                "src": "python:src/main.py:1-10:main:function",
                "dst": "python:src/main.py:11-20:helper:function",
                "type": "calls",
                "line": 5,
                "confidence": 0.9,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Check table headers
    assert "Symbol" in out
    assert "Kind" in out
    assert "In" in out
    assert "Out" in out
    assert "Deg" in out
    assert "File" in out
    # Check data
    assert "main" in out
    assert "helper" in out
    assert "function" in out
    assert "src/main.py" in out


def test_cmd_symbols_sorts_by_file_degree(tmp_path: Path, capsys) -> None:
    """Symbols are sorted by total file degree (descending), then filename."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/cold.py:1-5:cold_func:function",
                "name": "cold_func",
                "kind": "function",
                "language": "python",
                "path": "src/cold.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/hot.py:1-10:hot_main:function",
                "name": "hot_main",
                "kind": "function",
                "language": "python",
                "path": "src/hot.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/hot.py:11-20:hot_helper:function",
                "name": "hot_helper",
                "kind": "function",
                "language": "python",
                "path": "src/hot.py",
                "span": {"start_line": 11, "end_line": 20, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            # hot_main calls hot_helper - makes hot.py have higher total degree
            {
                "id": "edge:1",
                "src": "python:src/hot.py:1-10:hot_main:function",
                "dst": "python:src/hot.py:11-20:hot_helper:function",
                "type": "calls",
                "line": 5,
                "confidence": 0.9,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # hot.py should come before cold.py because it has more total degree
    hot_pos = out.find("hot_main")
    cold_pos = out.find("cold_func")
    assert hot_pos < cold_pos, "Hot file symbols should appear before cold file symbols"


def test_cmd_symbols_truncates_with_message(tmp_path: Path, capsys) -> None:
    """Symbols truncates at --limit and shows message."""
    # Create many symbols
    nodes = []
    for i in range(50):
        nodes.append({
            "id": f"python:src/file{i}.py:1-5:func{i}:function",
            "name": f"func{i}",
            "kind": "function",
            "language": "python",
            "path": f"src/file{i}.py",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        })

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 10  # Only show 10
    args.all = False

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show truncation message
    assert "40 additional symbols omitted for brevity" in out
    assert "--all" in out


def test_cmd_symbols_all_flag(tmp_path: Path, capsys) -> None:
    """--all flag shows all symbols regardless of limit."""
    nodes = []
    for i in range(50):
        nodes.append({
            "id": f"python:src/file{i}.py:1-5:func{i}:function",
            "name": f"func{i}",
            "kind": "function",
            "language": "python",
            "path": f"src/file{i}.py",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        })

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 10  # Would truncate normally
    args.all = True  # But --all overrides

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should NOT show truncation message
    assert "additional symbols omitted" not in out
    # Should show all 50 symbols
    assert "func49" in out


def test_cmd_symbols_filter_by_kind(tmp_path: Path, capsys) -> None:
    """Symbols can be filtered by kind."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:MyClass:class",
                "name": "MyClass",
                "kind": "class",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:11-15:my_func:function",
                "name": "my_func",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 11, "end_line": 15, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = "function"
    args.language = None
    args.limit = 200
    args.all = False

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "my_func" in out
    assert "MyClass" not in out


def test_cmd_symbols_filter_by_language(tmp_path: Path, capsys) -> None:
    """Symbols can be filtered by language."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:py_func:function",
                "name": "py_func",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "javascript:src/main.js:1-10:jsFunc:function",
                "name": "jsFunc",
                "kind": "function",
                "language": "javascript",
                "path": "src/main.js",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = "python"
    args.limit = 200
    args.all = False

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "py_func" in out
    assert "jsFunc" not in out


def test_cmd_symbols_no_symbols_found(tmp_path: Path, capsys) -> None:
    """Symbols command handles no symbols gracefully."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "No symbols found" in out


def test_cmd_symbols_with_input_file(tmp_path: Path, capsys) -> None:
    """Symbols can read from specified input file."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:custom_func:function",
                "name": "custom_func",
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
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "custom_func" in out


def test_cmd_symbols_input_not_found(tmp_path: Path) -> None:
    """Symbols fails if input file doesn't exist."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(tmp_path / "nonexistent.json")
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False

    result = cmd_symbols(args)

    assert result == 1


def test_cmd_symbols_no_results_file(tmp_path: Path) -> None:
    """Symbols fails if no results file exists."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False

    result = cmd_symbols(args)

    assert result == 1


def test_cmd_symbols_prints_output_summary(tmp_path: Path, capsys) -> None:
    """Symbols prints output summary to stdout."""
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

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "[hypergumbo symbols] Generated 0 artifact(s)" in out
    assert "Output: stdout" in out


def test_cmd_symbols_filter_by_kind_and_language(tmp_path: Path, capsys) -> None:
    """Symbols can be filtered by both kind and language."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:py_func:function",
                "name": "py_func",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:11-20:PyClass:class",
                "name": "PyClass",
                "kind": "class",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 11, "end_line": 20, "start_col": 0, "end_col": 10},
            },
            {
                "id": "javascript:src/main.js:1-10:jsFunc:function",
                "name": "jsFunc",
                "kind": "function",
                "language": "javascript",
                "path": "src/main.js",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = "function"  # Filter to functions only
    args.language = "python"  # Filter to python only
    args.limit = 200
    args.all = False

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show Python function only
    assert "py_func" in out
    # Should not show Python class (wrong kind)
    assert "PyClass" not in out
    # Should not show JS function (wrong language)
    assert "jsFunc" not in out


def test_main_with_symbols(tmp_path: Path, capsys) -> None:
    """Main with symbols command."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:main:function",
                "name": "main",
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

    result = main(["symbols", "--path", str(tmp_path)])

    assert result == 0

    out, _ = capsys.readouterr()
    assert "main" in out
