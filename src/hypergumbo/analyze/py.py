"""Python AST analysis pass."""
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..ir import AnalysisRun, Edge, Span, Symbol


def find_python_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Python files in the repository."""
    yield from repo_root.rglob("*.py")


def _make_symbol_id(path: str, line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID in format {lang}:{file}:{start}-{end}:{name}:{kind}."""
    return f"python:{path}:{line}-{end_line}:{name}:{kind}"


PASS_ID = "python-ast-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class AnalysisResult:
    """Result of analyzing Python files."""

    symbols: list[Symbol]
    edges: list[Edge]
    run: AnalysisRun | None = None


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol]
    symbol_by_name: dict[str, Symbol]
    # Maps imported name -> (module_name, original_name)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    # The parsed AST tree (kept to avoid re-parsing)
    tree: ast.AST | None = None


def _module_name_from_path(py_file: Path, repo_root: Path) -> str:
    """Convert a file path to a module name.

    E.g., /repo/utils.py -> 'utils', /repo/pkg/mod.py -> 'pkg.mod'
    """
    try:
        rel_path = py_file.relative_to(repo_root)
    except ValueError:
        rel_path = py_file
    # Remove .py extension and convert path separators to dots
    return str(rel_path.with_suffix("")).replace("/", ".").replace("\\", ".")


def _resolve_relative_import(
    module: str | None, level: int, importing_module: str
) -> str:
    """Resolve a relative import to an absolute module name.

    Args:
        module: The module part of the import (e.g., 'utils' in 'from ..utils import X')
        level: The number of dots (0 for absolute, 1 for '.', 2 for '..', etc.)
        importing_module: The fully qualified name of the importing module

    Returns:
        The resolved absolute module name.

    Example:
        _resolve_relative_import('utils', 2, 'pkg.sub.main') -> 'pkg.utils'
    """
    if level == 0:
        # Absolute import
        return module or ""

    # Split the importing module into parts
    parts = importing_module.split(".")

    # Go up 'level' levels (level=1 means same package, level=2 means parent, etc.)
    # We go up (level) levels from the module's package (excluding the module name itself)
    # So for 'pkg.sub.main' with level=2, we go up 2 from 'pkg.sub' -> 'pkg'
    if level > len(parts):
        # Can't go up that many levels, return as-is
        return module or ""

    base_parts = parts[:-level] if level <= len(parts) else []
    if module:
        base_parts.append(module)

    return ".".join(base_parts)


def _extract_imports(
    tree: ast.AST, importing_module: str
) -> dict[str, tuple[str, str]]:
    """Extract import mappings from AST with relative import resolution.

    Args:
        tree: The parsed AST
        importing_module: The fully qualified name of the importing module

    Returns a dict mapping local name -> (resolved_module_name, original_name).
    For 'from utils import helper', returns {'helper': ('utils', 'helper')}.
    For 'from ..utils import helper' in 'pkg.sub.main', returns {'helper': ('pkg.utils', 'helper')}.
    """
    imports: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            resolved_module = _resolve_relative_import(
                node.module, node.level, importing_module
            )
            if resolved_module:  # Skip if we couldn't resolve
                for alias in node.names:
                    local_name = alias.asname if alias.asname else alias.name
                    imports[local_name] = (resolved_module, alias.name)
    return imports


def _extract_file_analysis(py_file: Path, repo_root: Path | None = None) -> FileAnalysis | None:
    """Extract symbols and imports from a single file.

    Args:
        py_file: Path to the Python file
        repo_root: Repository root for resolving relative imports. If None,
                   relative imports won't be fully resolved.

    Returns None if the file cannot be parsed.
    """
    try:
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return None

    symbols = []
    symbol_by_name: dict[str, Symbol] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            end_line = node.end_lineno or node.lineno
            end_col = node.end_col_offset or 0
            span = Span(
                start_line=node.lineno,
                end_line=end_line,
                start_col=node.col_offset,
                end_col=end_col,
            )
            symbol = Symbol(
                id=_make_symbol_id(str(py_file), node.lineno, end_line, node.name, "function"),
                name=node.name,
                kind="function",
                language="python",
                path=str(py_file),
                span=span,
            )
            symbols.append(symbol)
            symbol_by_name[node.name] = symbol
        elif isinstance(node, ast.ClassDef):
            end_line = node.end_lineno or node.lineno
            end_col = node.end_col_offset or 0
            span = Span(
                start_line=node.lineno,
                end_line=end_line,
                start_col=node.col_offset,
                end_col=end_col,
            )
            symbol = Symbol(
                id=_make_symbol_id(str(py_file), node.lineno, end_line, node.name, "class"),
                name=node.name,
                kind="class",
                language="python",
                path=str(py_file),
                span=span,
            )
            symbols.append(symbol)
            symbol_by_name[node.name] = symbol

    # Compute module name for import resolution
    if repo_root is not None:
        importing_module = _module_name_from_path(py_file, repo_root)
    else:
        importing_module = py_file.stem  # Fallback to just filename
    imports = _extract_imports(tree, importing_module)
    return FileAnalysis(symbols=symbols, symbol_by_name=symbol_by_name, imports=imports, tree=tree)


def _extract_edges(
    tree: ast.AST,
    local_symbols: dict[str, Symbol],
    imports: dict[str, tuple[str, str]],
    global_symbols: dict[tuple[str, str], Symbol],
) -> list[Edge]:
    """Extract call edges from an AST, resolving both local and cross-file calls."""
    edges = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            caller_symbol = local_symbols.get(node.name)
            if caller_symbol:
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        callee_name = None
                        callee_symbol = None

                        # Handle simple name calls: helper()
                        if isinstance(child.func, ast.Name):
                            callee_name = child.func.id
                            # First check local symbols
                            callee_symbol = local_symbols.get(callee_name)
                            # Then check imports for cross-file resolution
                            if not callee_symbol and callee_name in imports:
                                module_name, original_name = imports[callee_name]
                                callee_symbol = global_symbols.get((module_name, original_name))

                        # Handle method calls: self.helper() or obj.method()
                        elif isinstance(child.func, ast.Attribute):
                            callee_name = child.func.attr
                            # For self.method() calls, look up method in local symbols
                            if (isinstance(child.func.value, ast.Name)
                                    and child.func.value.id == "self"):
                                callee_symbol = local_symbols.get(callee_name)

                        if callee_symbol:
                            # Determine evidence type based on call pattern
                            if isinstance(child.func, ast.Attribute):
                                evidence_type = "ast_call_method"
                            else:
                                evidence_type = "ast_call_direct"

                            edges.append(Edge.create(
                                src=caller_symbol.id,
                                dst=callee_symbol.id,
                                edge_type="calls",
                                line=child.lineno,
                                evidence_type=evidence_type,
                            ))
    return edges


def extract_nodes(py_file: Path, global_symbols: dict[str, Symbol] | None = None) -> AnalysisResult:
    """
    Extract function/class definitions and call edges from a Python file.

    Returns an AnalysisResult with symbols and edges.
    Gracefully handles syntax errors and encoding issues.

    Note: For cross-file call detection, use analyze_python() instead.
    This function only detects intra-file calls for backwards compatibility.
    """
    file_analysis = _extract_file_analysis(py_file)
    if file_analysis is None:
        return AnalysisResult(symbols=[], edges=[])

    # For single-file analysis, only detect local calls
    edges = _extract_edges(file_analysis.tree, file_analysis.symbol_by_name, {}, {})
    return AnalysisResult(symbols=file_analysis.symbols, edges=edges)


def analyze_python(repo_root: Path) -> AnalysisResult:
    """
    Analyze all Python files in a repository.

    Returns an AnalysisResult with all detected symbols, edges, and provenance.
    Supports cross-file call detection via import resolution.
    """
    import time

    start_time = time.time()

    # Create analysis run for provenance tracking
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # First pass: collect all symbols and imports from all files
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0
    for py_file in find_python_files(repo_root):
        analysis = _extract_file_analysis(py_file, repo_root)
        if analysis is not None:
            file_analyses[py_file] = analysis
        else:
            files_skipped += 1

    # Build global symbol table: (module_name, symbol_name) -> Symbol
    global_symbols: dict[tuple[str, str], Symbol] = {}
    for py_file, analysis in file_analyses.items():
        module_name = _module_name_from_path(py_file, repo_root)
        for symbol in analysis.symbols:
            global_symbols[(module_name, symbol.name)] = symbol

    # Second pass: extract edges with cross-file resolution
    all_symbols = []
    all_edges = []
    for py_file, analysis in file_analyses.items():
        # Set origin on symbols
        for symbol in analysis.symbols:
            symbol.origin = PASS_ID
            symbol.origin_run_id = run.execution_id
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges(
            analysis.tree, analysis.symbol_by_name, analysis.imports, global_symbols
        )
        # Set origin on edges
        for edge in edges:
            edge.origin = PASS_ID
            edge.origin_run_id = run.execution_id
        all_edges.extend(edges)

    # Update run metadata
    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return AnalysisResult(symbols=all_symbols, edges=all_edges, run=run)
