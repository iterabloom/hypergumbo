"""Python AST analysis pass."""
import ast
from pathlib import Path
from typing import Iterator

from ..ir import Symbol


def find_python_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Python files in the repository."""
    yield from repo_root.rglob("*.py")


def _make_symbol_id(path: str, line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID in format {lang}:{file}:{start}-{end}:{name}:{kind}."""
    return f"python:{path}:{line}-{end_line}:{name}:{kind}"


def extract_nodes(py_file: Path) -> list[Symbol]:
    """
    Extract function and class definitions from a Python file.

    Returns a list of Symbol objects.
    Gracefully handles syntax errors and encoding issues.
    """
    try:
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return []

    symbols = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            end_line = node.end_lineno or node.lineno
            symbols.append(Symbol(
                id=_make_symbol_id(str(py_file), node.lineno, end_line, node.name, "function"),
                name=node.name,
                kind="function",
                language="python",
                path=str(py_file),
                line=node.lineno,
                end_line=end_line,
            ))
        elif isinstance(node, ast.ClassDef):
            end_line = node.end_lineno or node.lineno
            symbols.append(Symbol(
                id=_make_symbol_id(str(py_file), node.lineno, end_line, node.name, "class"),
                name=node.name,
                kind="class",
                language="python",
                path=str(py_file),
                line=node.lineno,
                end_line=end_line,
            ))
    return symbols


def analyze_python(repo_root: Path) -> list[Symbol]:
    """
    Analyze all Python files in a repository.

    Returns a list of all detected Symbol objects (functions, classes, etc.).
    """
    symbols = []
    for py_file in find_python_files(repo_root):
        symbols.extend(extract_nodes(py_file))
    return symbols
