"""Tests for the hypergumbo test-coverage command and --help --all feature."""

import json
from pathlib import Path

from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.cli import cmd_test_coverage, main


class FakeArgs:
    """Minimal namespace for testing command functions."""

    pass


def test_cmd_test_coverage_basic_hot_cold_spots(tmp_path: Path, capsys) -> None:
    """Test coverage command identifies hot and cold spots correctly."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            # Test functions (in test file)
            {
                "id": "python:tests/test_utils.py:1-5:test_helper:function",
                "name": "test_helper",
                "kind": "function",
                "language": "python",
                "path": "tests/test_utils.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:tests/test_utils.py:10-15:test_helper2:function",
                "name": "test_helper2",
                "kind": "function",
                "language": "python",
                "path": "tests/test_utils.py",
                "span": {"start_line": 10, "end_line": 15},
            },
            {
                "id": "python:tests/test_core.py:1-10:test_process:function",
                "name": "test_process",
                "kind": "function",
                "language": "python",
                "path": "tests/test_core.py",
                "span": {"start_line": 1, "end_line": 10},
            },
            # Production functions
            {
                "id": "python:src/utils.py:1-10:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 10},
            },
            {
                "id": "python:src/core.py:1-50:process:function",
                "name": "process",
                "kind": "function",
                "language": "python",
                "path": "src/core.py",
                "span": {"start_line": 1, "end_line": 50},
                "lines_of_code": 50,
                "cyclomatic_complexity": 8,
            },
        ],
        "edges": [
            # test_helper calls helper
            {
                "type": "calls",
                "src": "python:tests/test_utils.py:1-5:test_helper:function",
                "dst": "python:src/utils.py:1-10:helper:function",
            },
            # test_helper2 also calls helper (making it a test-dense)
            {
                "type": "calls",
                "src": "python:tests/test_utils.py:10-15:test_helper2:function",
                "dst": "python:src/utils.py:1-10:helper:function",
            },
            # test_process calls helper too
            {
                "type": "calls",
                "src": "python:tests/test_core.py:1-10:test_process:function",
                "dst": "python:src/utils.py:1-10:helper:function",
            },
            # process() is never called by any test - cold spot
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "text"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Check summary
    assert "Total functions: 2" in out
    assert "Tested: 1" in out
    assert "Untested: 1" in out
    # Hot spot: helper is called by 3 tests
    assert "helper()" in out
    assert "3 tests" in out
    # Cold spot: process has 0 tests
    assert "process()" in out
    assert "50 LOC" in out
    assert "complexity: 8" in out


def test_cmd_test_coverage_json_output(tmp_path: Path, capsys) -> None:
    """Test coverage command outputs valid JSON format."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:tests/test_foo.py:1-5:test_foo:function",
                "name": "test_foo",
                "kind": "function",
                "language": "python",
                "path": "tests/test_foo.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:src/foo.py:1-10:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/foo.py",
                "span": {"start_line": 1, "end_line": 10},
            },
            {
                "id": "python:src/bar.py:1-20:bar:function",
                "name": "bar",
                "kind": "function",
                "language": "python",
                "path": "src/bar.py",
                "span": {"start_line": 1, "end_line": 20},
                "lines_of_code": 20,
            },
        ],
        "edges": [
            {
                "type": "calls",
                "src": "python:tests/test_foo.py:1-5:test_foo:function",
                "dst": "python:src/foo.py:1-10:foo:function",
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "json"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0

    out, _ = capsys.readouterr()
    output = json.loads(out)

    assert output["schema_version"] == "0.1.0"
    assert output["view"] == "test-coverage"
    assert output["summary"]["total_functions"] == 2
    assert output["summary"]["tested_functions"] == 1
    assert output["summary"]["untested_functions"] == 1
    assert output["summary"]["coverage_percent"] == 50.0
    assert output["summary"]["total_tests"] == 1

    # Hot spot
    assert len(output["test_dense"]) == 1
    assert output["test_dense"][0]["name"] == "foo"
    assert output["test_dense"][0]["test_count"] == 1
    assert "test_foo" in output["test_dense"][0]["tests"]

    # Cold spot
    assert len(output["cold_spots"]) == 1
    assert output["cold_spots"][0]["name"] == "bar"
    assert output["cold_spots"][0]["test_count"] == 0
    assert output["cold_spots"][0]["lines_of_code"] == 20


def test_cmd_test_coverage_no_functions(tmp_path: Path, capsys) -> None:
    """Test coverage command handles empty behavior map."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            # Only a class, no functions
            {
                "id": "python:src/foo.py:1-10:Foo:class",
                "name": "Foo",
                "kind": "class",
                "language": "python",
                "path": "src/foo.py",
                "span": {"start_line": 1, "end_line": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "text"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0
    _, err = capsys.readouterr()
    assert "No functions found" in err


def test_cmd_test_coverage_no_tests(tmp_path: Path, capsys) -> None:
    """Test coverage when there are no test functions."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            # Production function only
            {
                "id": "python:src/foo.py:1-10:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/foo.py",
                "span": {"start_line": 1, "end_line": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "text"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0
    out, _ = capsys.readouterr()
    # 0% coverage when no tests
    assert "Tested: 0 (0.0%)" in out
    assert "Untested: 1" in out
    assert "Total test functions: 0" in out


def test_cmd_test_coverage_all_tested(tmp_path: Path, capsys) -> None:
    """Test coverage when all functions are tested."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:tests/test_foo.py:1-5:test_foo:function",
                "name": "test_foo",
                "kind": "function",
                "language": "python",
                "path": "tests/test_foo.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:src/foo.py:1-10:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/foo.py",
                "span": {"start_line": 1, "end_line": 10},
            },
        ],
        "edges": [
            {
                "type": "calls",
                "src": "python:tests/test_foo.py:1-5:test_foo:function",
                "dst": "python:src/foo.py:1-10:foo:function",
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "text"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0
    out, _ = capsys.readouterr()
    assert "Tested: 1 (100.0%)" in out
    assert "Untested: 0" in out


def test_cmd_test_coverage_min_tests_filter(tmp_path: Path, capsys) -> None:
    """Test --min-tests filter only shows functions with enough tests."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:tests/test_a.py:1-5:test_a:function",
                "name": "test_a",
                "kind": "function",
                "language": "python",
                "path": "tests/test_a.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:tests/test_b.py:1-5:test_b:function",
                "name": "test_b",
                "kind": "function",
                "language": "python",
                "path": "tests/test_b.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:src/foo.py:1-10:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/foo.py",
                "span": {"start_line": 1, "end_line": 10},
            },
            {
                "id": "python:src/bar.py:1-10:bar:function",
                "name": "bar",
                "kind": "function",
                "language": "python",
                "path": "src/bar.py",
                "span": {"start_line": 1, "end_line": 10},
            },
        ],
        "edges": [
            # foo is called by 2 tests
            {
                "type": "calls",
                "src": "python:tests/test_a.py:1-5:test_a:function",
                "dst": "python:src/foo.py:1-10:foo:function",
            },
            {
                "type": "calls",
                "src": "python:tests/test_b.py:1-5:test_b:function",
                "dst": "python:src/foo.py:1-10:foo:function",
            },
            # bar is called by 1 test
            {
                "type": "calls",
                "src": "python:tests/test_a.py:1-5:test_a:function",
                "dst": "python:src/bar.py:1-10:bar:function",
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "text"
    args.min_tests = 2  # Only show functions with 2+ tests
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0
    out, _ = capsys.readouterr()
    # foo has 2 tests, should appear
    assert "foo()" in out
    # bar has 1 test, should not appear in test-denses
    # But summary still shows total counts
    assert "Total functions: 2" in out


def test_cmd_test_coverage_max_tests_filter(tmp_path: Path, capsys) -> None:
    """Test --max-tests filter shows functions with at most N tests."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:tests/test_a.py:1-5:test_a:function",
                "name": "test_a",
                "kind": "function",
                "language": "python",
                "path": "tests/test_a.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:tests/test_b.py:1-5:test_b:function",
                "name": "test_b",
                "kind": "function",
                "language": "python",
                "path": "tests/test_b.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:src/foo.py:1-10:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/foo.py",
                "span": {"start_line": 1, "end_line": 10},
            },
        ],
        "edges": [
            {
                "type": "calls",
                "src": "python:tests/test_a.py:1-5:test_a:function",
                "dst": "python:src/foo.py:1-10:foo:function",
            },
            {
                "type": "calls",
                "src": "python:tests/test_b.py:1-5:test_b:function",
                "dst": "python:src/foo.py:1-10:foo:function",
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "json"
    args.min_tests = None
    args.max_tests = 1  # Only show functions with <=1 tests
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0
    out, _ = capsys.readouterr()
    output = json.loads(out)
    # foo has 2 tests, should be filtered out
    assert len(output["test_dense"]) == 0


def test_cmd_test_coverage_top_n(tmp_path: Path, capsys) -> None:
    """Test --top limits number of results shown."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:tests/test.py:1-5:test_all:function",
                "name": "test_all",
                "kind": "function",
                "language": "python",
                "path": "tests/test.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:src/a.py:1-10:a:function",
                "name": "a",
                "kind": "function",
                "language": "python",
                "path": "src/a.py",
                "span": {"start_line": 1, "end_line": 10},
            },
            {
                "id": "python:src/b.py:1-10:b:function",
                "name": "b",
                "kind": "function",
                "language": "python",
                "path": "src/b.py",
                "span": {"start_line": 1, "end_line": 10},
            },
            {
                "id": "python:src/c.py:1-10:c:function",
                "name": "c",
                "kind": "function",
                "language": "python",
                "path": "src/c.py",
                "span": {"start_line": 1, "end_line": 10},
            },
        ],
        "edges": [
            {
                "type": "calls",
                "src": "python:tests/test.py:1-5:test_all:function",
                "dst": "python:src/a.py:1-10:a:function",
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "json"
    args.min_tests = None
    args.max_tests = None
    args.top = 1  # Only top 1

    result = cmd_test_coverage(args)

    assert result == 0
    out, _ = capsys.readouterr()
    output = json.loads(out)
    # Only 1 test-dense and 1 cold spot
    assert len(output["test_dense"]) == 1
    assert len(output["cold_spots"]) == 1


def test_cmd_test_coverage_custom_input(tmp_path: Path, capsys) -> None:
    """Test --input allows specifying custom results file."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:tests/test.py:1-5:test_fn:function",
                "name": "test_fn",
                "kind": "function",
                "language": "python",
                "path": "tests/test.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:src/fn.py:1-10:fn:function",
                "name": "fn",
                "kind": "function",
                "language": "python",
                "path": "src/fn.py",
                "span": {"start_line": 1, "end_line": 10},
            },
        ],
        "edges": [
            {
                "type": "calls",
                "src": "python:tests/test.py:1-5:test_fn:function",
                "dst": "python:src/fn.py:1-10:fn:function",
            },
        ],
    }
    # Use a custom filename
    custom_file = tmp_path / "custom-results.json"
    custom_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(custom_file)
    args.format = "text"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0
    out, _ = capsys.readouterr()
    assert "Tested: 1 (100.0%)" in out


def test_cmd_test_coverage_input_not_found(tmp_path: Path, capsys) -> None:
    """Test error when specified input file doesn't exist."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(tmp_path / "nonexistent.json")
    args.format = "text"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 1
    _, err = capsys.readouterr()
    assert "Input file not found" in err


def test_cmd_test_coverage_auto_runs_analysis(tmp_path: Path, capsys) -> None:
    """Test auto-runs analysis when no hypergumbo.results.json exists."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "text"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    # Auto-runs analysis and succeeds
    assert result == 0
    _, err = capsys.readouterr()
    # Should indicate analysis was auto-run
    assert "No cached results found, running analysis" in err


def test_cmd_test_coverage_ignores_non_call_edges(tmp_path: Path, capsys) -> None:
    """Test that only 'calls' edges are considered for coverage."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:tests/test.py:1-5:test_fn:function",
                "name": "test_fn",
                "kind": "function",
                "language": "python",
                "path": "tests/test.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:src/fn.py:1-10:fn:function",
                "name": "fn",
                "kind": "function",
                "language": "python",
                "path": "src/fn.py",
                "span": {"start_line": 1, "end_line": 10},
            },
        ],
        "edges": [
            # This is an imports edge, not calls - should be ignored
            {
                "type": "imports",
                "src": "python:tests/test.py:1-5:test_fn:function",
                "dst": "python:src/fn.py:1-10:fn:function",
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "text"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0
    out, _ = capsys.readouterr()
    # fn should be untested since imports edge doesn't count
    assert "Untested: 1" in out


def test_cmd_test_coverage_methods_also_counted(tmp_path: Path, capsys) -> None:
    """Test that methods (not just functions) are included in coverage."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:tests/test.py:1-10:TestClass.test_method:method",
                "name": "test_method",
                "kind": "method",
                "language": "python",
                "path": "tests/test.py",
                "span": {"start_line": 1, "end_line": 10},
            },
            {
                "id": "python:src/service.py:1-20:Service.do_work:method",
                "name": "do_work",
                "kind": "method",
                "language": "python",
                "path": "src/service.py",
                "span": {"start_line": 1, "end_line": 20},
            },
        ],
        "edges": [
            {
                "type": "calls",
                "src": "python:tests/test.py:1-10:TestClass.test_method:method",
                "dst": "python:src/service.py:1-20:Service.do_work:method",
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "json"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0
    out, _ = capsys.readouterr()
    output = json.loads(out)
    assert output["summary"]["total_functions"] == 1
    assert output["summary"]["tested_functions"] == 1
    assert output["summary"]["coverage_percent"] == 100.0


def test_cmd_test_coverage_truncation_message(tmp_path: Path, capsys) -> None:
    """Test that truncation message shows when using --top."""
    # Create many cold spots
    nodes = []
    for i in range(25):
        nodes.append({
            "id": f"python:src/fn{i}.py:1-10:fn{i}:function",
            "name": f"fn{i}",
            "kind": "function",
            "language": "python",
            "path": f"src/fn{i}.py",
            "span": {"start_line": 1, "end_line": 10},
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
    args.format = "text"
    args.min_tests = None
    args.max_tests = None
    args.top = 5

    result = cmd_test_coverage(args)

    assert result == 0
    out, _ = capsys.readouterr()
    assert "Showing top 5" in out


def test_cmd_test_coverage_cold_spot_without_metrics(tmp_path: Path, capsys) -> None:
    """Test cold spots work when LOC/complexity not available (defaults to 1 LOC)."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/foo.py:1-10:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/foo.py",
                "span": {"start_line": 1, "end_line": 10},
                # No lines_of_code or cyclomatic_complexity
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "text"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0
    out, _ = capsys.readouterr()
    # foo should appear (LOC defaults to 1 for density calculation)
    assert "foo()" in out
    # No complexity shown since it wasn't provided
    assert "complexity" not in out


def test_cmd_test_coverage_json_includes_test_density(tmp_path: Path, capsys) -> None:
    """Test JSON output includes test_density (tests per LOC) for test-denses."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:tests/test.py:1-5:test_fn:function",
                "name": "test_fn",
                "kind": "function",
                "language": "python",
                "path": "tests/test.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:tests/test2.py:1-5:test_fn2:function",
                "name": "test_fn2",
                "kind": "function",
                "language": "python",
                "path": "tests/test2.py",
                "span": {"start_line": 1, "end_line": 5},
            },
            {
                "id": "python:src/util.py:1-10:util:function",
                "name": "util",
                "kind": "function",
                "language": "python",
                "path": "src/util.py",
                "span": {"start_line": 1, "end_line": 10},
                "lines_of_code": 10,
            },
        ],
        "edges": [
            {
                "type": "calls",
                "src": "python:tests/test.py:1-5:test_fn:function",
                "dst": "python:src/util.py:1-10:util:function",
            },
            {
                "type": "calls",
                "src": "python:tests/test2.py:1-5:test_fn2:function",
                "dst": "python:src/util.py:1-10:util:function",
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "json"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0
    out, _ = capsys.readouterr()
    output = json.loads(out)
    assert len(output["test_dense"]) == 1
    hot_spot = output["test_dense"][0]
    assert hot_spot["test_count"] == 2
    assert hot_spot["lines_of_code"] == 10
    # test_density = 2 tests / 10 LOC = 0.2
    assert hot_spot["test_density"] == 0.2


def test_cmd_test_coverage_json_cold_spot_with_complexity(tmp_path: Path, capsys) -> None:
    """Test JSON output includes cyclomatic_complexity for cold spots."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/complex.py:1-50:complex_fn:function",
                "name": "complex_fn",
                "kind": "function",
                "language": "python",
                "path": "src/complex.py",
                "span": {"start_line": 1, "end_line": 50},
                "cyclomatic_complexity": 15,
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "json"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0
    out, _ = capsys.readouterr()
    output = json.loads(out)
    assert len(output["cold_spots"]) == 1
    assert output["cold_spots"][0]["cyclomatic_complexity"] == 15


def test_cmd_test_coverage_prints_output_summary(tmp_path: Path, capsys) -> None:
    """Test-coverage prints output summary to stdout for text mode."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.format = "text"
    args.min_tests = None
    args.max_tests = None
    args.top = None

    result = cmd_test_coverage(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "[hypergumbo test-coverage] Using 1 cached" in out
    assert "Output: stdout" in out


def test_help_all_shows_all_subcommands(capsys) -> None:
    """Test that --help --all shows help for all subcommands."""
    result = main(["--help", "--all"])
    assert result == 0

    out, _ = capsys.readouterr()
    # Main help should appear
    assert "Generate codebase summaries" in out
    # Detailed subcommand header should appear
    assert "DETAILED SUBCOMMAND HELP" in out
    # Each subcommand should have its own section
    assert "hypergumbo sketch" in out
    assert "hypergumbo run" in out
    assert "hypergumbo test-coverage" in out
    assert "hypergumbo slice" in out
    assert "hypergumbo catalog" in out
    # Should include option details for subcommands
    assert "--format {text,json}" in out  # From test-coverage
    assert "--entry" in out  # From slice


def test_help_all_with_short_flag(capsys) -> None:
    """Test that -h --all also works."""
    result = main(["-h", "--all"])
    assert result == 0

    out, _ = capsys.readouterr()
    assert "DETAILED SUBCOMMAND HELP" in out
    assert "hypergumbo test-coverage" in out
