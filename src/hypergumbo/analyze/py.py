"""Python AST analysis pass."""
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..ir import Edge, Symbol


def find_python_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Python files in the repository."""
    yield from repo_root.rglob("*.py")


def _make_symbol_id(path: str, line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID in format {lang}:{file}:{start}-{end}:{name}:{kind}."""
    return f"python:{path}:{line}-{end_line}:{name}:{kind}"


@dataclass
class AnalysisResult:
    """Result of analyzing a Python file."""

    symbols: list[Symbol]
    edges: list[Edge]


def extract_nodes(py_file: Path) -> AnalysisResult:
    """
    Extract function/class definitions and call edges from a Python file.

    Returns an AnalysisResult with symbols and edges.
    Gracefully handles syntax errors and encoding issues.
    """
    try:
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return AnalysisResult(symbols=[], edges=[])

    symbols = []
    symbol_by_name: dict[str, Symbol] = {}

    # First pass: collect all function and class definitions
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            end_line = node.end_lineno or node.lineno
            symbol = Symbol(
                id=_make_symbol_id(str(py_file), node.lineno, end_line, node.name, "function"),
                name=node.name,
                kind="function",
                language="python",
                path=str(py_file),
                line=node.lineno,
                end_line=end_line,
            )
            symbols.append(symbol)
            symbol_by_name[node.name] = symbol
        elif isinstance(node, ast.ClassDef):
            end_line = node.end_lineno or node.lineno
            symbol = Symbol(
                id=_make_symbol_id(str(py_file), node.lineno, end_line, node.name, "class"),
                name=node.name,
                kind="class",
                language="python",
                path=str(py_file),
                line=node.lineno,
                end_line=end_line,
            )
            symbols.append(symbol)
            symbol_by_name[node.name] = symbol

    # Second pass: find call edges within functions
    edges = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            caller_symbol = symbol_by_name.get(node.name)
            if caller_symbol:
                # Walk the function body to find calls
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        # Handle simple name calls like helper()
                        if isinstance(child.func, ast.Name):
                            callee_name = child.func.id
                            callee_symbol = symbol_by_name.get(callee_name)
                            if callee_symbol:
                                edges.append(Edge(
                                    source=caller_symbol.id,
                                    target=callee_symbol.id,
                                    kind="calls",
                                    line=child.lineno,
                                ))

    return AnalysisResult(symbols=symbols, edges=edges)


def analyze_python(repo_root: Path) -> AnalysisResult:
    """
    Analyze all Python files in a repository.

    Returns an AnalysisResult with all detected symbols and edges.
    """
    all_symbols = []
    all_edges = []
    for py_file in find_python_files(repo_root):
        result = extract_nodes(py_file)
        all_symbols.extend(result.symbols)
        all_edges.extend(result.edges)
    return AnalysisResult(symbols=all_symbols, edges=all_edges)
