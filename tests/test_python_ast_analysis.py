"""Tests for Python AST analysis - detecting functions and classes."""
import json
from pathlib import Path

from hypergumbo.cli import run_behavior_map


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
