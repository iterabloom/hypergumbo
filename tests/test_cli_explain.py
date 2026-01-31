"""Tests for the hypergumbo explain command."""
import json
from pathlib import Path

from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.cli import cmd_explain, main, _extract_path_from_symbol_id


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


def test_cmd_explain_auto_runs_analysis(tmp_path: Path, capsys) -> None:
    """Explain auto-runs analysis if no results file exists."""
    args = FakeArgs()
    args.symbol = "foo"
    args.path = str(tmp_path)
    args.input = None
    args.exclude_tests = False
    args.with_source = False
    args.tokens = None

    result = cmd_explain(args)

    # Auto-runs analysis, but returns error since symbol not found
    # (explain still requires finding the symbol)
    assert result == 1
    _, err = capsys.readouterr()
    # Verify analysis was auto-run before failing to find symbol
    assert "No cached results found, running analysis" in err
    assert "No symbol found matching 'foo'" in err


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
    assert "[hypergumbo explain] Using 1 cached" in out
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


# =============================================================================
# Tests for explain --with-source mode
# =============================================================================


def test_cmd_explain_with_source_shows_source(tmp_path: Path, capsys) -> None:
    """--with-source shows source code for queried symbol."""
    # Create actual source file
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    main_py.write_text(
        """\
def foo():
    print("hello")
    return 42
"""
    )

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-3:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 3, "start_col": 0, "end_col": 13},
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
    args.with_source = True
    args.tokens = None
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show the source code
    assert 'def foo():' in out
    assert 'print("hello")' in out
    assert 'return 42' in out


def test_cmd_explain_with_source_shows_caller_source(tmp_path: Path, capsys) -> None:
    """--with-source shows source code for callers."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    main_py.write_text(
        """\
def main():
    result = foo()
    return result

def foo():
    return 42
"""
    )

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:5-6:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 5, "end_line": 6, "start_col": 0, "end_col": 13},
            },
            {
                "id": "python:src/main.py:1-3:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 3, "start_col": 0, "end_col": 17},
            },
        ],
        "edges": [
            {
                "id": "edge1",
                "src": "python:src/main.py:1-3:main:function",
                "dst": "python:src/main.py:5-6:foo:function",
                "type": "calls",
                "line": 2,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "foo"
    args.path = str(tmp_path)
    args.input = None
    args.with_source = True
    args.tokens = None
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show caller source code
    assert 'def main():' in out
    assert 'result = foo()' in out


def test_cmd_explain_with_source_module_level_shows_single_line(tmp_path: Path, capsys) -> None:
    """Module-level calls show only the single line of the call, not entire file."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    main_py.write_text(
        """\
def foo():
    return 42
"""
    )

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_main = tests_dir / "test_main.py"
    test_main.write_text(
        """\
import pytest
from src.main import foo

# Module-level call
result = foo()

def test_foo():
    assert foo() == 42
"""
    )

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-2:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 13},
            },
            {
                # Module-level file node
                "id": "python:tests/test_main.py:1-1:file:file",
                "name": "file",
                "kind": "file",
                "language": "python",
                "path": "tests/test_main.py",
                "span": {"start_line": 1, "end_line": 1, "start_col": 0, "end_col": 0},
            },
        ],
        "edges": [
            {
                # Module-level call at line 5
                "id": "edge1",
                "src": "python:tests/test_main.py:1-1:file:file",
                "dst": "python:src/main.py:1-2:foo:function",
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
    args.with_source = True
    args.tokens = None
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show only the call line, not the entire file
    assert "result = foo()" in out
    # Should NOT show other lines from the test file
    assert "import pytest" not in out
    assert "def test_foo" not in out


def test_cmd_explain_with_source_token_budget_omits_low_priority(tmp_path: Path, capsys) -> None:
    """--tokens budget causes omission of low-priority sources (bottom-up by in-degree)."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    # Create a long source file
    main_py.write_text(
        """\
def foo():
    return 42

def caller_important():
    # This is a very long function
    x = 1
    y = 2
    z = 3
    return foo()

def caller_unimportant():
    # This is also a very long function
    a = 1
    b = 2
    c = 3
    return foo()
"""
    )

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-2:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 13},
            },
            {
                "id": "python:src/main.py:4-9:caller_important:function",
                "name": "caller_important",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 4, "end_line": 9, "start_col": 0, "end_col": 17},
            },
            {
                "id": "python:src/main.py:11-17:caller_unimportant:function",
                "name": "caller_unimportant",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 11, "end_line": 17, "start_col": 0, "end_col": 17},
            },
        ],
        "edges": [
            {
                "id": "edge1",
                "src": "python:src/main.py:4-9:caller_important:function",
                "dst": "python:src/main.py:1-2:foo:function",
                "type": "calls",
                "line": 9,
            },
            {
                "id": "edge2",
                "src": "python:src/main.py:11-17:caller_unimportant:function",
                "dst": "python:src/main.py:1-2:foo:function",
                "type": "calls",
                "line": 17,
            },
            # Make caller_important have higher in-degree (more important)
            {
                "id": "edge3",
                "src": "python:external:1-1:ext:function",
                "dst": "python:src/main.py:4-9:caller_important:function",
                "type": "calls",
                "line": 1,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "foo"
    args.path = str(tmp_path)
    args.input = None
    args.with_source = True
    args.tokens = 40  # Small budget: only fits foo + one caller
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show the queried symbol
    assert "def foo():" in out
    # caller_important has higher in-degree, should be shown first
    # With tight budget, lower-priority caller should be omitted
    assert "caller_important" in out
    # Should indicate sources were omitted
    assert "omitted" in out.lower()


def test_cmd_explain_with_source_shows_callee_source(tmp_path: Path, capsys) -> None:
    """--with-source shows source code for callees (what the symbol calls)."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    main_py.write_text(
        """\
def main():
    result = helper()
    return result

def helper():
    return 42
"""
    )

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-3:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 3, "start_col": 0, "end_col": 17},
            },
            {
                "id": "python:src/main.py:5-6:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 5, "end_line": 6, "start_col": 0, "end_col": 13},
            },
        ],
        "edges": [
            {
                "id": "edge1",
                "src": "python:src/main.py:1-3:main:function",
                "dst": "python:src/main.py:5-6:helper:function",
                "type": "calls",
                "line": 2,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "main"
    args.path = str(tmp_path)
    args.input = None
    args.with_source = True
    args.tokens = None
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show callee source code
    assert 'def helper():' in out
    assert 'return 42' in out


def test_cmd_explain_with_source_deduplicates_source(tmp_path: Path, capsys) -> None:
    """--with-source deduplicates source when same symbol is both caller and callee."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    main_py.write_text(
        """\
def foo():
    bar()
    return 42

def bar():
    foo()
    return 0
"""
    )

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-3:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 3, "start_col": 0, "end_col": 13},
            },
            {
                "id": "python:src/main.py:5-7:bar:function",
                "name": "bar",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 5, "end_line": 7, "start_col": 0, "end_col": 12},
            },
        ],
        "edges": [
            # foo calls bar
            {
                "id": "edge1",
                "src": "python:src/main.py:1-3:foo:function",
                "dst": "python:src/main.py:5-7:bar:function",
                "type": "calls",
                "line": 2,
            },
            # bar calls foo (mutual recursion)
            {
                "id": "edge2",
                "src": "python:src/main.py:5-7:bar:function",
                "dst": "python:src/main.py:1-3:foo:function",
                "type": "calls",
                "line": 6,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "foo"
    args.path = str(tmp_path)
    args.input = None
    args.with_source = True
    args.tokens = None
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # bar appears in both callers and callees, but source should only be shown once
    # Count occurrences of "def bar():"
    bar_count = out.count("def bar():")
    assert bar_count == 1, f"bar source shown {bar_count} times, expected 1 (deduplicated)"


def test_cmd_explain_with_source_missing_source_file(tmp_path: Path, capsys) -> None:
    """--with-source handles missing source files gracefully."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/missing.py:1-10:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/missing.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
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
    args.with_source = True
    args.tokens = None
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0  # Should not crash

    out, _ = capsys.readouterr()
    # Should mention the symbol even without source
    assert "foo" in out
    # Should indicate source is unavailable
    assert "unavailable" in out.lower() or "not found" in out.lower() or "[source" in out.lower()


def test_cmd_explain_with_source_queried_symbol_exceeds_budget(tmp_path: Path, capsys) -> None:
    """Queried symbol is always shown even if it exceeds token budget."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    # Create a longer source that exceeds tiny budget
    main_py.write_text(
        """\
def large_function():
    # This is a longer function that exceeds the tiny budget
    x = 1
    y = 2
    z = 3
    a = 4
    b = 5
    return x + y + z + a + b
"""
    )

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-9:large_function:function",
                "name": "large_function",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 9, "start_col": 0, "end_col": 26},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "large_function"
    args.path = str(tmp_path)
    args.input = None
    args.with_source = True
    args.tokens = 5  # Very tiny budget, smaller than the symbol itself
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should still show the queried symbol even over budget
    assert "def large_function():" in out
    assert "return x + y + z + a + b" in out


def test_cmd_explain_with_source_self_recursion(tmp_path: Path, capsys) -> None:
    """Self-recursive function source is not duplicated."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    main_py.write_text(
        """\
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)
"""
    )

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-4:factorial:function",
                "name": "factorial",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 4, "start_col": 0, "end_col": 30},
            },
        ],
        "edges": [
            # Self-recursive call
            {
                "id": "edge1",
                "src": "python:src/main.py:1-4:factorial:function",
                "dst": "python:src/main.py:1-4:factorial:function",
                "type": "calls",
                "line": 4,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "factorial"
    args.path = str(tmp_path)
    args.input = None
    args.with_source = True
    args.tokens = None
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show factorial source only once (not duplicated as caller and callee)
    factorial_count = out.count("def factorial(n):")
    assert factorial_count == 1, f"factorial source shown {factorial_count} times, expected 1"


def test_cmd_explain_with_source_module_level_callee(tmp_path: Path, capsys) -> None:
    """Module-level callee shows only the single call line."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    main_py.write_text(
        """\
def main():
    initialize()
    return 0

initialize()
"""
    )

    # File node representing module-level code
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-3:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 3, "start_col": 0, "end_col": 12},
            },
            {
                "id": "python:src/main.py:1-1:file:file",
                "name": "file",
                "kind": "file",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 1, "start_col": 0, "end_col": 0},
            },
        ],
        "edges": [
            # main calls initialize (external, not shown)
            {
                "id": "edge1",
                "src": "python:src/main.py:1-3:main:function",
                "dst": "python:src/main.py:1-1:file:file",
                "type": "calls",
                "line": 5,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "main"
    args.path = str(tmp_path)
    args.input = None
    args.with_source = True
    args.tokens = None
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show the module-level line
    assert "initialize()" in out



def test_cmd_explain_with_source_budget_all_fit(tmp_path: Path, capsys) -> None:
    """--tokens budget shows all sources when they fit within budget."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    main_py.write_text(
        """\
def foo():
    return 42

def bar():
    return foo()
"""
    )

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-2:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 13},
            },
            {
                "id": "python:src/main.py:4-5:bar:function",
                "name": "bar",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 4, "end_line": 5, "start_col": 0, "end_col": 17},
            },
        ],
        "edges": [
            {
                "id": "edge1",
                "src": "python:src/main.py:4-5:bar:function",
                "dst": "python:src/main.py:1-2:foo:function",
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
    args.with_source = True
    args.tokens = 10000  # Large budget - everything fits
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Both foo and bar sources shown (nothing omitted)
    assert "def foo():" in out
    assert "def bar():" in out
    assert "omitted" not in out.lower()


def test_cmd_explain_with_source_module_level_caller_omitted(tmp_path: Path, capsys) -> None:
    """Module-level callers are omitted first when budget is tight."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    main_py.write_text(
        """\
def foo():
    return 42

def bar():
    return foo()

foo()
"""
    )

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-2:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 2, "start_col": 0, "end_col": 13},
            },
            {
                "id": "python:src/main.py:4-5:bar:function",
                "name": "bar",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 4, "end_line": 5, "start_col": 0, "end_col": 17},
            },
            {
                "id": "python:src/main.py:file:file",
                "name": "file",
                "kind": "file",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 7, "end_line": 7, "start_col": 0, "end_col": 5},
            },
        ],
        "edges": [
            # bar calls foo (regular caller)
            {
                "id": "edge1",
                "src": "python:src/main.py:4-5:bar:function",
                "dst": "python:src/main.py:1-2:foo:function",
                "type": "calls",
                "line": 5,
            },
            # Module-level calls foo
            {
                "id": "edge2",
                "src": "python:src/main.py:file:file",
                "dst": "python:src/main.py:1-2:foo:function",
                "type": "calls",
                "line": 7,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "foo"
    args.path = str(tmp_path)
    args.input = None
    args.with_source = True
    args.tokens = 13  # Tight budget: foo (~7) + bar (~7) = 14, leaving 6 - only bar fits, module-level omitted
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # foo source shown (queried symbol always shown)
    assert "def foo():" in out
    # Module-level call should be omitted first
    assert "module-level call(s) omitted" in out


def test_cmd_explain_with_source_callee_omitted(tmp_path: Path, capsys) -> None:
    """Callee sources are omitted when budget is tight."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    main_py.write_text(
        """\
def foo():
    bar()
    baz()
    return 42

def bar():
    return 1

def baz():
    return 2
"""
    )

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-4:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 4, "start_col": 0, "end_col": 13},
            },
            {
                "id": "python:src/main.py:6-7:bar:function",
                "name": "bar",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 6, "end_line": 7, "start_col": 0, "end_col": 12},
            },
            {
                "id": "python:src/main.py:9-10:baz:function",
                "name": "baz",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 9, "end_line": 10, "start_col": 0, "end_col": 12},
            },
        ],
        "edges": [
            # foo calls bar
            {
                "id": "edge1",
                "src": "python:src/main.py:1-4:foo:function",
                "dst": "python:src/main.py:6-7:bar:function",
                "type": "calls",
                "line": 2,
            },
            # foo calls baz
            {
                "id": "edge2",
                "src": "python:src/main.py:1-4:foo:function",
                "dst": "python:src/main.py:9-10:baz:function",
                "type": "calls",
                "line": 3,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "foo"
    args.path = str(tmp_path)
    args.input = None
    args.with_source = True
    args.tokens = 22  # Budget: foo (~14) + one callee (~6) fits, other callee (~6) omitted
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # foo source shown (queried symbol always shown)
    assert "def foo():" in out
    # At least one callee should be omitted
    assert "callee source(s) omitted" in out


def test_cmd_explain_with_source_module_level_callee_omitted(tmp_path: Path, capsys) -> None:
    """Module-level callees are omitted first when budget is tight."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    main_py.write_text(
        """\
def foo():
    bar()
    return init_value

def bar():
    return 1

init_value = 42
"""
    )

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-3:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 3, "start_col": 0, "end_col": 21},
            },
            {
                "id": "python:src/main.py:5-6:bar:function",
                "name": "bar",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 5, "end_line": 6, "start_col": 0, "end_col": 12},
            },
            {
                "id": "python:src/main.py:file:file",
                "name": "file",
                "kind": "file",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 8, "end_line": 8, "start_col": 0, "end_col": 16},
            },
        ],
        "edges": [
            # foo calls bar (regular callee)
            {
                "id": "edge1",
                "src": "python:src/main.py:1-3:foo:function",
                "dst": "python:src/main.py:5-6:bar:function",
                "type": "calls",
                "line": 2,
            },
            # foo references module-level variable
            {
                "id": "edge2",
                "src": "python:src/main.py:1-3:foo:function",
                "dst": "python:src/main.py:file:file",
                "type": "calls",
                "line": 3,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.symbol = "foo"
    args.path = str(tmp_path)
    args.input = None
    args.with_source = True
    args.tokens = 18  # Budget: foo (~11) + bar (~6) fits, module-level callee (~5) omitted
    args.exclude_tests = False

    result = cmd_explain(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # foo source shown (queried symbol always shown)
    assert "def foo():" in out
    # Module-level callee should be omitted first
    assert "module-level call(s) omitted" in out
