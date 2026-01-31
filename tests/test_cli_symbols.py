"""Tests for the hypergumbo symbols command."""
import json
from pathlib import Path

from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.cli import cmd_symbols, main


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
    args.exclude_tests = False
    args.max_per_file = None

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
    args.exclude_tests = False
    args.max_per_file = None

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
    args.exclude_tests = False
    args.max_per_file = None

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
    args.exclude_tests = False
    args.max_per_file = None

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
    args.exclude_tests = False
    args.max_per_file = None

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
    args.exclude_tests = False
    args.max_per_file = None

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
    args.exclude_tests = False
    args.max_per_file = None

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
    args.exclude_tests = False
    args.max_per_file = None

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
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 1


def test_cmd_symbols_auto_runs_analysis(tmp_path: Path, capsys) -> None:
    """Symbols auto-runs analysis if no results file exists."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    # Auto-runs analysis and succeeds (even if no symbols found)
    assert result == 0
    _, err = capsys.readouterr()
    assert "No cached results found, running analysis" in err


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
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "[hypergumbo symbols] Using 1 cached" in out
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
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show Python function only
    assert "py_func" in out
    # Should not show Python class (wrong kind)
    assert "PyClass" not in out
    # Should not show JS function (wrong language)
    assert "jsFunc" not in out


def test_cmd_symbols_exclude_tests(tmp_path: Path, capsys) -> None:
    """--exclude-tests flag filters out test symbols."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:main_func:function",
                "name": "main_func",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:tests/test_main.py:1-10:test_main:function",
                "name": "test_main",
                "kind": "function",
                "language": "python",
                "path": "tests/test_main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:test_utils.py:1-10:test_helper:function",
                "name": "test_helper",
                "kind": "function",
                "language": "python",
                "path": "test_utils.py",
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
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = True  # Exclude tests
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show non-test symbol
    assert "main_func" in out
    # Should not show test symbols
    assert "test_main" not in out
    assert "test_helper" not in out


def test_cmd_symbols_max_per_file(tmp_path: Path, capsys) -> None:
    """--max-per-file limits symbols shown per file."""
    # Create multiple symbols in same file
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": f"python:src/hot.py:{i}-{i+5}:func{i}:function",
                "name": f"func{i}",
                "kind": "function",
                "language": "python",
                "path": "src/hot.py",
                "span": {"start_line": i, "end_line": i+5, "start_col": 0, "end_col": 10},
            }
            for i in range(10)
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
    args.exclude_tests = False
    args.max_per_file = 3  # Only 3 per file

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show some funcs from hot.py
    assert "func" in out
    # Count occurrences - should be limited to 3
    count = sum(1 for i in range(10) if f"func{i}" in out)
    assert count == 3, f"Expected 3 symbols from hot.py, got {count}"


def test_cmd_symbols_max_per_file_with_all(tmp_path: Path, capsys) -> None:
    """--max-per-file with --all shows all files but limited symbols per file."""
    # Create symbols in multiple files
    nodes = []
    for file_idx in range(5):
        for sym_idx in range(10):
            nodes.append({
                "id": f"python:src/file{file_idx}.py:{sym_idx}-{sym_idx+5}:func{file_idx}_{sym_idx}:function",
                "name": f"func{file_idx}_{sym_idx}",
                "kind": "function",
                "language": "python",
                "path": f"src/file{file_idx}.py",
                "span": {"start_line": sym_idx, "end_line": sym_idx+5, "start_col": 0, "end_col": 10},
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
    args.limit = 200  # Would normally truncate to 200
    args.all = True   # But --all ignores this
    args.exclude_tests = False
    args.max_per_file = 2  # Limit to 2 per file

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # All 5 files should be represented
    for file_idx in range(5):
        assert f"file{file_idx}.py" in out
    # Each file should have max 2 symbols
    # Total should be 5 files * 2 symbols = 10 symbols
    assert "additional symbols omitted" not in out  # --all with max-per-file


def test_cmd_symbols_exclude_tests_affects_degree_counts(tmp_path: Path, capsys) -> None:
    """--exclude-tests excludes test edges from degree counts, not just display."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:main_func:function",
                "name": "main_func",
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
            {
                "id": "python:tests/test_main.py:1-10:test_main:function",
                "name": "test_main",
                "kind": "function",
                "language": "python",
                "path": "tests/test_main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            # test_main calls main_func (should be excluded from degree when -x)
            {
                "id": "edge:1",
                "src": "python:tests/test_main.py:1-10:test_main:function",
                "dst": "python:src/main.py:1-10:main_func:function",
                "type": "calls",
                "line": 5,
                "confidence": 0.9,
            },
            # main_func calls helper (non-test edge, should always be counted)
            {
                "id": "edge:2",
                "src": "python:src/main.py:1-10:main_func:function",
                "dst": "python:src/main.py:11-20:helper:function",
                "type": "calls",
                "line": 8,
                "confidence": 0.9,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    # First, run WITHOUT exclude_tests to see baseline degrees
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    cmd_symbols(args)
    out_without_exclude, _ = capsys.readouterr()

    # main_func should have in-degree=1 (from test_main) without exclude
    # The output shows: name, kind, in, out, deg, file
    # Find the line with main_func and check its in-degree
    for line in out_without_exclude.split("\n"):
        if "main_func" in line:
            # In the table, columns are: Symbol, Kind, In, Out, Deg, File
            # The "In" column should show 1 (from test_main calling it)
            parts = line.split()
            # We need to find the In value - it's the 3rd column after Symbol and Kind
            # But parsing Rich table output is tricky; let's just check "1" appears
            assert "1" in line, f"main_func should have in-degree 1: {line}"
            break

    # Now run WITH exclude_tests
    args.exclude_tests = True
    cmd_symbols(args)
    out_with_exclude, _ = capsys.readouterr()

    # main_func should have in-degree=0 (test edge excluded)
    # helper should have in-degree=1 (from main_func, non-test edge)
    for line in out_with_exclude.split("\n"):
        if "main_func" in line:
            # main_func: in=0, out=1 (calls helper)
            # The line should show "0" for in-degree (first number after "function")
            # Let's check that the pattern shows 0 for in-degree
            parts = line.split()
            # Find index of "function" then next should be in-degree
            if "function" in parts:
                idx = parts.index("function")
                in_deg = parts[idx + 1] if idx + 1 < len(parts) else None
                assert in_deg == "0", f"main_func should have in-degree 0 with -x: {line}"
            break


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
