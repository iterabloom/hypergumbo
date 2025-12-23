"""Tests for Python AST analysis - detecting functions and classes."""
import json
from pathlib import Path

from hypergumbo.cli import run_behavior_map
from hypergumbo.analyze.py import extract_nodes, _module_name_from_path, _resolve_relative_import


def test_run_detects_python_function(tmp_path: Path) -> None:
    """Running analysis on a Python file should detect function definitions."""
    # Create a Python file with a function
    py_file = tmp_path / "hello.py"
    py_file.write_text("def greet():\n    pass\n")

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    # Load results
    data = json.loads(out_path.read_text())

    # Expect a node in the output
    assert len(data["nodes"]) == 1
    node = data["nodes"][0]
    assert node["name"] == "greet"
    assert node["kind"] == "function"
    assert node["language"] == "python"
    assert "hello.py" in node["path"]


def test_run_skips_syntax_error_files(tmp_path: Path) -> None:
    """Files with syntax errors should be skipped, not crash analysis."""
    # Create a valid Python file
    good_file = tmp_path / "good.py"
    good_file.write_text("def works():\n    pass\n")

    # Create an invalid Python file
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def broken(\n")  # SyntaxError

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    # Should still find the good function
    data = json.loads(out_path.read_text())
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["name"] == "works"


def test_run_skips_unicode_error_files(tmp_path: Path) -> None:
    """Files with encoding errors should be skipped, not crash analysis."""
    # Create a valid Python file
    good_file = tmp_path / "good.py"
    good_file.write_text("def works():\n    pass\n")

    # Create a file with invalid UTF-8 bytes
    bad_file = tmp_path / "bad.py"
    bad_file.write_bytes(b"\x80\x81\x82 invalid utf-8")

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    # Should still find the good function
    data = json.loads(out_path.read_text())
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["name"] == "works"


def test_run_detects_python_class(tmp_path: Path) -> None:
    """Running analysis on a Python file should detect class definitions."""
    # Create a Python file with a class
    py_file = tmp_path / "models.py"
    py_file.write_text("class User:\n    pass\n")

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    # Load results
    data = json.loads(out_path.read_text())

    # Expect a class node in the output
    assert len(data["nodes"]) == 1
    node = data["nodes"][0]
    assert node["name"] == "User"
    assert node["kind"] == "class"
    assert node["language"] == "python"
    assert "models.py" in node["path"]


def test_run_detects_call_edges(tmp_path: Path) -> None:
    """Running analysis should detect when one function calls another."""
    # Create a Python file with two functions where one calls the other
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "def helper():\n"
        "    pass\n"
        "\n"
        "def main():\n"
        "    helper()\n"
    )

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    # Load results
    data = json.loads(out_path.read_text())

    # Should have two function nodes
    assert len(data["nodes"]) == 2

    # Should have one edge showing main calls helper
    assert len(data["edges"]) == 1
    edge = data["edges"][0]
    assert edge["kind"] == "calls"
    assert "main" in edge["source"]
    assert "helper" in edge["target"]


def test_run_detects_cross_file_call_edges(tmp_path: Path) -> None:
    """Running analysis should detect calls across files via imports."""
    # Create a utility module with a helper function
    utils_file = tmp_path / "utils.py"
    utils_file.write_text("def helper():\n    pass\n")

    # Create a main module that imports and calls the helper
    main_file = tmp_path / "main.py"
    main_file.write_text(
        "from utils import helper\n"
        "\n"
        "def run():\n"
        "    helper()\n"
    )

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    # Load results
    data = json.loads(out_path.read_text())

    # Should have two function nodes (helper in utils, run in main)
    assert len(data["nodes"]) == 2

    # Should have one cross-file edge: run -> helper
    assert len(data["edges"]) == 1
    edge = data["edges"][0]
    assert edge["kind"] == "calls"
    assert "run" in edge["source"]
    assert "helper" in edge["target"]
    # The target should reference utils.py, not main.py
    assert "utils.py" in edge["target"]


def test_extract_nodes_detects_local_calls(tmp_path: Path) -> None:
    """extract_nodes should detect intra-file calls."""
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "def helper():\n"
        "    pass\n"
        "\n"
        "def main():\n"
        "    helper()\n"
    )

    result = extract_nodes(py_file)

    assert len(result.symbols) == 2
    assert len(result.edges) == 1
    assert "main" in result.edges[0].source
    assert "helper" in result.edges[0].target


def test_extract_nodes_handles_syntax_error(tmp_path: Path) -> None:
    """extract_nodes should return empty result for syntax errors."""
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def broken(\n")

    result = extract_nodes(bad_file)

    assert result.symbols == []
    assert result.edges == []


def test_module_name_from_path_basic(tmp_path: Path) -> None:
    """_module_name_from_path should convert paths to module names."""
    py_file = tmp_path / "utils.py"
    assert _module_name_from_path(py_file, tmp_path) == "utils"


def test_module_name_from_path_nested(tmp_path: Path) -> None:
    """_module_name_from_path should handle nested packages."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    py_file = pkg / "mod.py"
    assert _module_name_from_path(py_file, tmp_path) == "pkg.mod"


def test_module_name_from_path_outside_repo(tmp_path: Path) -> None:
    """_module_name_from_path should handle files outside repo root."""
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    py_file = other_dir / "external.py"
    # When file is outside repo_root, falls back to using the path as-is
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    result = _module_name_from_path(py_file, repo_root)
    assert "external" in result


def test_resolve_relative_import_too_high() -> None:
    """_resolve_relative_import should handle going up too many levels gracefully."""
    # Trying to go up 5 levels from 'pkg.mod' (only 2 levels) should return module as-is
    result = _resolve_relative_import("utils", 5, "pkg.mod")
    assert result == "utils"

    # With no module part, should return empty string
    result = _resolve_relative_import(None, 5, "pkg.mod")
    assert result == ""


def test_run_detects_relative_import_calls(tmp_path: Path) -> None:
    """Running analysis should detect calls via relative imports (from ..X import Y)."""
    # Create a package structure:
    # pkg/
    #   __init__.py
    #   utils.py      -> def helper(): pass
    #   sub/
    #     __init__.py
    #     main.py     -> from ..utils import helper; def run(): helper()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")

    utils_file = pkg / "utils.py"
    utils_file.write_text("def helper():\n    pass\n")

    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")

    main_file = sub / "main.py"
    main_file.write_text(
        "from ..utils import helper\n"
        "\n"
        "def run():\n"
        "    helper()\n"
    )

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    # Load results
    data = json.loads(out_path.read_text())

    # Should have two function nodes (helper in utils, run in main)
    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 2

    # Should have one cross-file edge: run -> helper
    assert len(data["edges"]) == 1
    edge = data["edges"][0]
    assert edge["kind"] == "calls"
    assert "run" in edge["source"]
    assert "helper" in edge["target"]
    # The target should reference utils.py, not main.py
    assert "utils.py" in edge["target"]
