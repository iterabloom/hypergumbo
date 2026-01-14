"""Tests for the hypergumbo explain command."""
import json
from pathlib import Path

from hypergumbo.schema import SCHEMA_VERSION
from hypergumbo.cli import cmd_explain, main, _extract_path_from_symbol_id


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


def test_cmd_explain_prints_output_summary(tmp_path: Path, capsys) -> None:
    """Explain prints output summary to stdout."""
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
    args.symbol = "test"
    args.path = str(tmp_path)
    args.input = None

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "[hypergumbo explain] Generated 0 artifact(s)" in out
    assert "Output: stdout" in out


def test_cmd_explain_formats_file_level_callers(tmp_path: Path, capsys) -> None:
    """File-level symbols (kind=file) are shown as '<module level>' not raw ID."""
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
            },
            {
                "id": "python:tests/test_foo.py:1-1:file:file",
                "name": "file",
                "kind": "file",
                "language": "python",
                "path": "tests/test_foo.py",
                "span": {"start_line": 1, "end_line": 1, "start_col": 0, "end_col": 0},
            },
        ],
        "edges": [
            {
                "id": "edge1",
                "src": "python:tests/test_foo.py:1-1:file:file",
                "dst": "python:src/main.py:1-10:foo:function",
                "type": "calls",
                "line": 5,
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
    # Should show "<module level>" instead of "file" or the raw ID
    assert "<module level>" in out
    # Should NOT show the raw symbol ID format
    assert ":file:file" not in out


def test_cmd_explain_formats_missing_file_level_callers(tmp_path: Path, capsys) -> None:
    """Edge referencing file-level symbol NOT in nodes shows path from ID."""
    # This tests the case where an edge references a symbol that's not in the
    # nodes list but ends with ":file:file" - we extract the path from the ID.
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:bar:function",
                "name": "bar",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            # Note: the file-level symbol is NOT included in nodes
        ],
        "edges": [
            {
                "id": "edge1",
                "src": "python:tests/test_bar.py:1-1:file:file",
                "dst": "python:src/main.py:1-10:bar:function",
                "type": "calls",
                "line": 10,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "bar"
    args.path = str(tmp_path)
    args.input = None

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show "<module level>" via fallback ID detection
    assert "<module level>" in out
    # Should show the path extracted from the symbol ID
    assert "tests/test_bar.py" in out
    # Should NOT show the raw symbol ID format
    assert ":file:file" not in out


def test_extract_path_from_symbol_id() -> None:
    """Test path extraction from symbol IDs."""
    # Standard case
    assert _extract_path_from_symbol_id(
        "python:/home/user/project/src/main.py:1-10:foo:function"
    ) == "/home/user/project/src/main.py"

    # File-level symbol
    assert _extract_path_from_symbol_id(
        "python:tests/test_foo.py:1-1:file:file"
    ) == "tests/test_foo.py"

    # Windows-style path (with drive letter containing colon)
    assert _extract_path_from_symbol_id(
        "python:C:/Users/dev/project/main.py:5-20:bar:function"
    ) == "C:/Users/dev/project/main.py"

    # Empty string
    assert _extract_path_from_symbol_id("") == ""

    # Invalid format (no colon)
    assert _extract_path_from_symbol_id("invalid") == ""

    # Invalid format (no line range pattern)
    assert _extract_path_from_symbol_id("python:path/only") == ""


def test_cmd_explain_exclude_tests(tmp_path: Path, capsys) -> None:
    """--exclude-tests hides callers/callees from test files."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/core.py:1-10:process:function",
                "name": "process",
                "kind": "function",
                "language": "python",
                "path": "src/core.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:1-10:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:tests/test_core.py:1-10:test_process:function",
                "name": "test_process",
                "kind": "function",
                "language": "python",
                "path": "tests/test_core.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            {
                "id": "edge1",
                "src": "python:src/main.py:1-10:main:function",
                "dst": "python:src/core.py:1-10:process:function",
                "type": "calls",
                "line": 5,
            },
            {
                "id": "edge2",
                "src": "python:tests/test_core.py:1-10:test_process:function",
                "dst": "python:src/core.py:1-10:process:function",
                "type": "calls",
                "line": 8,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    # Without --exclude-tests: both callers shown
    args = FakeArgs()
    args.symbol = "process"
    args.path = str(tmp_path)
    args.input = None
    args.exclude_tests = False

    cmd_explain(args)
    out, _ = capsys.readouterr()
    assert "main" in out
    assert "test_process" in out
    assert "Called by (2)" in out

    # With --exclude-tests: only production caller shown
    args.exclude_tests = True
    cmd_explain(args)
    out, _ = capsys.readouterr()
    assert "main" in out
    assert "test_process" not in out
    assert "Called by (1)" in out


def test_cmd_explain_exclude_tests_for_callees(tmp_path: Path, capsys) -> None:
    """--exclude-tests also hides test callees (what the symbol calls)."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/core.py:1-10:process:function",
                "name": "process",
                "kind": "function",
                "language": "python",
                "path": "src/core.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/utils.py:1-10:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:tests/conftest.py:1-10:fixture:function",
                "name": "fixture",
                "kind": "function",
                "language": "python",
                "path": "tests/conftest.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            # process calls helper (production)
            {
                "id": "edge1",
                "src": "python:src/core.py:1-10:process:function",
                "dst": "python:src/utils.py:1-10:helper:function",
                "type": "calls",
                "line": 5,
            },
            # process calls fixture (test code)
            {
                "id": "edge2",
                "src": "python:src/core.py:1-10:process:function",
                "dst": "python:tests/conftest.py:1-10:fixture:function",
                "type": "calls",
                "line": 8,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    # With --exclude-tests: only production callee shown
    args = FakeArgs()
    args.symbol = "process"
    args.path = str(tmp_path)
    args.input = None
    args.exclude_tests = True

    cmd_explain(args)
    out, _ = capsys.readouterr()
    assert "helper" in out
    assert "fixture" not in out
    assert "Calls (1)" in out


def test_cmd_explain_formats_missing_non_file_symbol(tmp_path: Path, capsys) -> None:
    """Edge referencing a symbol NOT in nodes shows raw ID as fallback."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:baz:function",
                "name": "baz",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            # Note: the external_lib symbol is NOT included in nodes
        ],
        "edges": [
            {
                "id": "edge1",
                "src": "python:external/lib.py:1-10:external_func:function",
                "dst": "python:src/main.py:1-10:baz:function",
                "type": "calls",
                "line": 15,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "baz"
    args.path = str(tmp_path)
    args.input = None
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show the raw symbol ID since node is not found and it's not :file:file
    assert "python:external/lib.py:1-10:external_func:function" in out


def test_cmd_explain_sorts_by_in_degree(tmp_path: Path, capsys) -> None:
    """Callers/callees are sorted by in-degree (most called first)."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/api.py:1-10:api_handler:function",
                "name": "api_handler",
                "kind": "function",
                "language": "python",
                "path": "src/api.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/utils.py:1-10:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:1-10:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            # main calls api_handler
            {
                "id": "edge1",
                "src": "python:src/main.py:1-10:main:function",
                "dst": "python:src/api.py:1-10:api_handler:function",
                "type": "calls",
                "line": 5,
            },
            # helper calls api_handler
            {
                "id": "edge2",
                "src": "python:src/utils.py:1-10:helper:function",
                "dst": "python:src/api.py:1-10:api_handler:function",
                "type": "calls",
                "line": 3,
            },
            # 5 things call helper (making it high in-degree)
            {
                "id": "edge3",
                "src": "python:src/api.py:1-10:api_handler:function",
                "dst": "python:src/utils.py:1-10:helper:function",
                "type": "calls",
                "line": 7,
            },
            {
                "id": "edge4",
                "src": "python:src/main.py:1-10:main:function",
                "dst": "python:src/utils.py:1-10:helper:function",
                "type": "calls",
                "line": 8,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "api_handler"
    args.path = str(tmp_path)
    args.input = None
    args.exclude_tests = False

    cmd_explain(args)
    out, _ = capsys.readouterr()

    # helper has in-degree 2 (called by api_handler and main)
    # main has in-degree 0 (nothing calls it)
    # So helper should appear before main in the "Called by" list
    helper_pos = out.find("helper")
    main_pos = out.find("main")
    assert helper_pos < main_pos, "helper (higher in-degree) should appear before main"
