"""Python AST analysis pass."""
import ast
from pathlib import Path
from typing import Iterator


def find_python_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Python files in the repository."""
    yield from repo_root.rglob("*.py")


def extract_nodes(py_file: Path) -> list[dict]:
    """
    Extract function and class definitions from a Python file.

    Returns a list of node dicts with name, kind, language, path, line.
    Gracefully handles syntax errors and encoding issues.
    """
    try:
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return []

    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            nodes.append({
                "name": node.name,
                "kind": "function",
                "language": "python",
                "path": str(py_file),
                "line": node.lineno,
            })
        elif isinstance(node, ast.ClassDef):
            nodes.append({
                "name": node.name,
                "kind": "class",
                "language": "python",
                "path": str(py_file),
                "line": node.lineno,
            })
    return nodes


def analyze_python(repo_root: Path) -> list[dict]:
    """
    Analyze all Python files in a repository.

    Returns a list of all detected nodes (functions, classes, etc.).
    """
    nodes = []
    for py_file in find_python_files(repo_root):
        nodes.extend(extract_nodes(py_file))
    return nodes
