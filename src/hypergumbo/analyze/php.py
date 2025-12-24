"""PHP analysis pass using tree-sitter-php.

This analyzer uses tree-sitter-php to parse PHP files and extract:
- Function declarations (symbols)
- Class declarations (symbols)
- Method declarations (symbols)
- Function call relationships (edges)

If tree-sitter-php is not installed, the analyzer gracefully degrades
and returns an empty result.

How It Works
------------
1. Check if tree-sitter and tree-sitter-php are available
2. If not available, return empty result (not an error, just no PHP analysis)
3. If available, parse each file and extract symbols/edges
4. Use tree-sitter to identify functions, classes, and method calls

Why This Design
---------------
- Optional dependency keeps base install lightweight
- PHP support is separate from JS/TS to keep modules focused
- Same pattern as JS/TS analyzer for consistency
"""
from __future__ import annotations

import importlib.util
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "php-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_php_files(repo_root: Path) -> Iterator[Path]:
    """Yield all PHP files in the repository."""
    yield from find_files(repo_root, ["*.php"])


def is_php_tree_sitter_available() -> bool:
    """Check if tree-sitter and PHP grammar are available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_php") is None:
        return False
    return True


@dataclass
class PhpAnalysisResult:
    """Result of analyzing PHP files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"php:{path}:{start_line}-{end_line}:{name}:{kind}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_name_in_children(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find identifier name in node's children."""
    for child in node.children:
        if child.type == "name":
            return _node_text(child, source)
    return None


def _get_php_parser() -> Optional["tree_sitter.Parser"]:
    """Get tree-sitter parser for PHP."""
    try:
        import tree_sitter
        import tree_sitter_php
    except ImportError:
        return None

    parser = tree_sitter.Parser()
    # PHP has two grammars: php and php_only. We use php which includes HTML.
    lang_ptr = tree_sitter_php.language_php()
    parser.language = tree_sitter.Language(lang_ptr)
    return parser


def _extract_symbols_and_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge]]:
    """Extract symbols and edges from a parsed PHP tree."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    symbol_by_name: dict[str, Symbol] = {}
    current_class: Optional[Symbol] = None

    def visit(node: "tree_sitter.Node", current_function: Optional[Symbol] = None) -> None:
        nonlocal current_class

        # Function declarations
        if node.type == "function_definition":
            name = _find_name_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function"),
                    name=name,
                    kind="function",
                    language="php",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)
                symbol_by_name[name] = symbol
                # Visit body with this as current function
                for child in node.children:
                    visit(child, symbol)
                return

        # Class declarations
        if node.type == "class_declaration":
            name = _find_name_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "class"),
                    name=name,
                    kind="class",
                    language="php",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)
                symbol_by_name[name] = symbol
                old_class = current_class
                current_class = symbol
                for child in node.children:
                    visit(child, current_function)
                current_class = old_class
                return

        # Method declarations (inside classes)
        if node.type == "method_declaration":
            name = _find_name_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                full_name = f"{current_class.name}.{name}" if current_class else name
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="php",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)
                symbol_by_name[name] = symbol
                for child in node.children:
                    visit(child, symbol)
                return

        # Function calls
        if node.type == "function_call_expression":
            # Get the function name
            func_node = node.child_by_field_name("function")
            if func_node and func_node.type == "name":
                callee_name = _node_text(func_node, source)
                if current_function and callee_name in symbol_by_name:
                    target_sym = symbol_by_name[callee_name]
                    edge = Edge(
                        id=f"call:{current_function.id}->{target_sym.id}",
                        src=current_function.id,
                        dst=target_sym.id,
                        edge_type="calls",
                        line=node.start_point[0] + 1,
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    )
                    edges.append(edge)

        # Recurse into children
        for child in node.children:
            visit(child, current_function)

    visit(tree.root_node)
    return symbols, edges


def _analyze_php_file(
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge], bool]:
    """Analyze a single PHP file.

    Returns (symbols, edges, success).
    """
    parser = _get_php_parser()
    if parser is None:
        return [], [], False

    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return [], [], False

    symbols, edges = _extract_symbols_and_edges(tree, source, file_path, run)
    return symbols, edges, True


def analyze_php(repo_root: Path) -> PhpAnalysisResult:
    """Analyze all PHP files in a repository.

    Returns a PhpAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-php is not available, returns empty result (silently skipped).
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Check for tree-sitter-php availability
    if not is_php_tree_sitter_available():
        run.duration_ms = int((time.time() - start_time) * 1000)
        return PhpAnalysisResult(
            run=run,
            skipped=True,
            skip_reason="requires tree-sitter-php: pip install tree-sitter-php",
        )

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    files_analyzed = 0
    files_skipped = 0

    for file_path in find_php_files(repo_root):
        symbols, edges, success = _analyze_php_file(file_path, run)
        if success:
            files_analyzed += 1
            all_symbols.extend(symbols)
            all_edges.extend(edges)
        else:
            files_skipped += 1

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return PhpAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
