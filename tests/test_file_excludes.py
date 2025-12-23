"""Tests for file exclude logic."""
import json
from pathlib import Path

from hypergumbo.cli import run_behavior_map


def test_excludes_node_modules(tmp_path: Path) -> None:
    """Should not analyze files in node_modules directory."""
    # Create a Python file in root
    root_file = tmp_path / "app.py"
    root_file.write_text("def main(): pass")

    # Create a Python file in node_modules (should be excluded)
    node_modules = tmp_path / "node_modules" / "some-package"
    node_modules.mkdir(parents=True)
    excluded_file = node_modules / "index.py"
    excluded_file.write_text("def excluded(): pass")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Should only have the root file's function
    function_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(function_nodes) == 1
    assert function_nodes[0]["name"] == "main"


def test_excludes_venv(tmp_path: Path) -> None:
    """Should not analyze files in venv directory."""
    # Create a Python file in root
    root_file = tmp_path / "app.py"
    root_file.write_text("def main(): pass")

    # Create a Python file in venv (should be excluded)
    venv_dir = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    venv_dir.mkdir(parents=True)
    excluded_file = venv_dir / "some_package.py"
    excluded_file.write_text("def excluded(): pass")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    function_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(function_nodes) == 1
    assert function_nodes[0]["name"] == "main"


def test_excludes_dot_venv(tmp_path: Path) -> None:
    """Should not analyze files in .venv directory."""
    root_file = tmp_path / "app.py"
    root_file.write_text("def main(): pass")

    dot_venv = tmp_path / ".venv" / "lib"
    dot_venv.mkdir(parents=True)
    excluded_file = dot_venv / "excluded.py"
    excluded_file.write_text("def excluded(): pass")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    function_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(function_nodes) == 1


def test_excludes_dist(tmp_path: Path) -> None:
    """Should not analyze files in dist directory."""
    root_file = tmp_path / "app.py"
    root_file.write_text("def main(): pass")

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    excluded_file = dist_dir / "bundle.py"
    excluded_file.write_text("def excluded(): pass")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    function_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(function_nodes) == 1


def test_excludes_build(tmp_path: Path) -> None:
    """Should not analyze files in build directory."""
    root_file = tmp_path / "app.py"
    root_file.write_text("def main(): pass")

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    excluded_file = build_dir / "output.py"
    excluded_file.write_text("def excluded(): pass")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    function_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(function_nodes) == 1


def test_excludes_dot_git(tmp_path: Path) -> None:
    """Should not analyze files in .git directory."""
    root_file = tmp_path / "app.py"
    root_file.write_text("def main(): pass")

    git_dir = tmp_path / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    excluded_file = git_dir / "pre-commit.py"
    excluded_file.write_text("def excluded(): pass")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    function_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(function_nodes) == 1


def test_excludes_pycache(tmp_path: Path) -> None:
    """Should not analyze files in __pycache__ directory."""
    root_file = tmp_path / "app.py"
    root_file.write_text("def main(): pass")

    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    excluded_file = pycache / "app.cpython-312.py"
    excluded_file.write_text("def excluded(): pass")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    function_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(function_nodes) == 1


def test_excludes_nested_node_modules(tmp_path: Path) -> None:
    """Should exclude node_modules even when deeply nested."""
    root_file = tmp_path / "app.py"
    root_file.write_text("def main(): pass")

    # Nested node_modules (common in monorepos)
    nested = tmp_path / "packages" / "web" / "node_modules" / "react"
    nested.mkdir(parents=True)
    excluded_file = nested / "index.py"
    excluded_file.write_text("def excluded(): pass")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    function_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(function_nodes) == 1


def test_excludes_html_in_node_modules(tmp_path: Path) -> None:
    """Should also exclude HTML files in node_modules."""
    # Create a valid HTML file in root
    html_file = tmp_path / "index.html"
    html_file.write_text('<html><script src="app.js"></script></html>')

    # Create HTML in node_modules (should be excluded)
    node_modules = tmp_path / "node_modules" / "some-lib"
    node_modules.mkdir(parents=True)
    excluded_html = node_modules / "demo.html"
    excluded_html.write_text('<html><script src="demo.js"></script></html>')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Should only have one HTML file node
    html_nodes = [n for n in data["nodes"] if n["kind"] == "file" and "html" in n["path"]]
    assert len(html_nodes) == 1
    assert "index.html" in html_nodes[0]["path"]
