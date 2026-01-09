"""Tests for the hypergumbo explain command."""
import json
from pathlib import Path

from hypergumbo.schema import SCHEMA_VERSION
from hypergumbo.cli import cmd_explain, main


class FakeArgs:
    """Minimal namespace for testing command functions."""

    pass


def test_cmd_explain_shows_symbol_details(tmp_path: Path, capsys) -> None:
    """Explain shows detailed info about a symbol."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
                "cyclomatic_complexity": 5,
                "lines_of_code": 10,
                "supply_chain": {
                    "tier": 1,
                    "tier_name": "first_party",
                    "reason": "matches ^src/",
                },
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "foo"
    args.path = str(tmp_path)
    args.input = None

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "foo" in out
    assert "function" in out
    assert "src/main.py" in out
    assert "complexity" in out.lower() or "5" in out
    assert "lines" in out.lower() or "10" in out


def test_cmd_explain_shows_callers_and_callees(tmp_path: Path, capsys) -> None:
    """Explain shows callers (who calls this) and callees (what this calls)."""
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
            {
                "id": "python:src/main.py:10-15:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 10, "end_line": 15, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/utils.py:1-5:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            {
                "id": "edge:1",
                "src": "python:src/main.py:1-5:main:function",
                "dst": "python:src/main.py:10-15:foo:function",
                "type": "calls",
                "line": 3,
                "confidence": 0.9,
            },
            {
                "id": "edge:2",
                "src": "python:src/main.py:10-15:foo:function",
                "dst": "python:src/utils.py:1-5:helper:function",
                "type": "calls",
                "line": 12,
                "confidence": 0.85,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "foo"
    args.path = str(tmp_path)
    args.input = None

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show caller (main) and callee (helper)
    assert "main" in out
    assert "helper" in out
    # Should indicate direction (called by / calls)
    assert "call" in out.lower()


def test_cmd_explain_symbol_not_found(tmp_path: Path, capsys) -> None:
    """Explain reports error when symbol not found."""
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
    args.symbol = "nonexistent"
    args.path = str(tmp_path)
    args.input = None

    result = cmd_explain(args)

    assert result == 1

    _, err = capsys.readouterr()
    assert "not found" in err.lower() or "No symbol" in err


def test_cmd_explain_multiple_matches(tmp_path: Path, capsys) -> None:
    """Explain lists all matches when multiple symbols match."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:process:function",
                "name": "process",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/utils.py:1-5:process:function",
                "name": "process",
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
    args.symbol = "process"
    args.path = str(tmp_path)
    args.input = None

    result = cmd_explain(args)

    # Should succeed but show disambiguation or all matches
    assert result == 0

    out, _ = capsys.readouterr()
    # Should mention both locations
    assert "src/main.py" in out
    assert "src/utils.py" in out


def test_cmd_explain_with_input_file(tmp_path: Path, capsys) -> None:
    """Explain can read from specified input file."""
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
    args.symbol = "bar"
    args.path = str(tmp_path)
    args.input = str(input_file)

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "bar" in out


def test_cmd_explain_input_not_found(tmp_path: Path) -> None:
    """Explain fails if input file doesn't exist."""
    args = FakeArgs()
    args.symbol = "foo"
    args.path = str(tmp_path)
    args.input = str(tmp_path / "nonexistent.json")

    result = cmd_explain(args)

    assert result == 1


def test_cmd_explain_no_results_file(tmp_path: Path) -> None:
    """Explain fails if no results file exists."""
    args = FakeArgs()
    args.symbol = "foo"
    args.path = str(tmp_path)
    args.input = None

    result = cmd_explain(args)

    assert result == 1


def test_main_with_explain(tmp_path: Path, capsys) -> None:
    """Main with explain command."""
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

    result = main(["explain", "test", "--path", str(tmp_path)])

    assert result == 0

    out, _ = capsys.readouterr()
    assert "test" in out


def test_cmd_explain_shows_no_callers_callees(tmp_path: Path, capsys) -> None:
    """Explain shows appropriate message when no callers or callees exist."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:isolated:function",
                "name": "isolated",
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
    args.symbol = "isolated"
    args.path = str(tmp_path)
    args.input = None

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "isolated" in out
    # Should indicate no callers/callees (or just not crash)
