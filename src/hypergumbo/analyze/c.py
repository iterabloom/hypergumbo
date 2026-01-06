"""C analysis pass using tree-sitter-c.

This analyzer uses tree-sitter-c to parse C files and extract:
- Function definitions and declarations (symbols)
- Struct declarations (symbols)
- Typedef declarations (symbols)
- Enum declarations (symbols)
- Function call relationships (edges)
- JNI export patterns (Java_ClassName_methodName)

If tree-sitter-c is not installed, the analyzer gracefully degrades
and returns an empty result.

How It Works
------------
1. Check if tree-sitter and tree-sitter-c are available
2. If not available, return empty result (not an error, just no C analysis)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and JNI export patterns

Why This Design
---------------
- Optional dependency keeps base install lightweight
- C support is separate from other languages to keep modules focused
- Two-pass allows cross-file call resolution
- Same pattern as PHP/JS analyzers for consistency
"""
from __future__ import annotations

import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "c-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_c_files(repo_root: Path) -> Iterator[Path]:
    """Yield all C files in the repository."""
    yield from find_files(repo_root, ["*.c", "*.h"])


def is_c_tree_sitter_available() -> bool:
    """Check if tree-sitter and C grammar are available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_c") is None:
        return False
    return True


@dataclass
class CAnalysisResult:
    """Result of analyzing C files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"c:{path}:{start_line}-{end_line}:{name}:{kind}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_identifier_in_children(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find identifier name in node's children."""
    for child in node.children:
        if child.type in ("identifier", "type_identifier"):
            return _node_text(child, source)
    return None


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from function_definition or declaration."""
    # Look for declarator which contains the function name
    for child in node.children:
        if child.type == "function_declarator":
            # Function declarator contains identifier
            return _find_identifier_in_children(child, source)
        elif child.type == "pointer_declarator":
            # Handle pointer return types: int* func()
            for subchild in child.children:
                if subchild.type == "function_declarator":
                    return _find_identifier_in_children(subchild, source)
    return None


def _find_function_declarator(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """Find the function_declarator node within a function definition or declaration."""
    for child in node.children:
        if child.type == "function_declarator":
            return child
        elif child.type == "pointer_declarator":
            # Pointer return type: int* func()
            for subchild in child.children:
                if subchild.type == "function_declarator":
                    return subchild
    return None  # pragma: no cover


def _extract_c_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a C function definition or declaration.

    Returns signature like "(int x, char* name) int" or "(void) void".
    """
    if node.type not in ("function_definition", "declaration"):
        return None  # pragma: no cover

    # Find function_declarator
    func_decl = _find_function_declarator(node)
    if not func_decl:
        return None  # pragma: no cover

    # Find parameter_list
    param_list = None
    for child in func_decl.children:
        if child.type == "parameter_list":
            param_list = child
            break

    if not param_list:
        return None  # pragma: no cover

    # Extract parameters
    param_strs: list[str] = []
    for child in param_list.children:
        if child.type == "parameter_declaration":
            # Get full text of parameter and clean it
            param_text = _node_text(child, source).strip()
            param_strs.append(param_text)

    # Build signature with parameters
    sig = "(" + ", ".join(param_strs) + ")"

    # Extract return type (before the function_declarator)
    return_type_parts: list[str] = []
    for child in node.children:
        if child.type in ("function_declarator", "pointer_declarator"):
            break
        if child.type in ("primitive_type", "type_identifier", "sized_type_specifier",
                          "storage_class_specifier", "type_qualifier"):
            return_type_parts.append(_node_text(child, source))

    if return_type_parts:
        return_type = " ".join(return_type_parts)
        # Add pointer indicator if function_declarator is wrapped in pointer_declarator
        for child in node.children:
            if child.type == "pointer_declarator":
                return_type += "*"
                break
        if return_type and return_type != "void":
            sig += f" {return_type}"

    return sig


def _get_c_parser() -> Optional["tree_sitter.Parser"]:
    """Get tree-sitter parser for C."""
    try:
        import tree_sitter
        import tree_sitter_c
    except ImportError:
        return None

    parser = tree_sitter.Parser()
    lang_ptr = tree_sitter_c.language()
    parser.language = tree_sitter.Language(lang_ptr)
    return parser


@dataclass
class _ParsedFile:
    """Holds parsed file data for two-pass analysis."""

    path: Path
    tree: "tree_sitter.Tree"
    source: bytes


def _extract_symbols(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> list[Symbol]:
    """Extract symbols from a parsed C tree (pass 1).

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        # Function definitions
        if node.type == "function_definition":
            name = _get_function_name(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                signature = _extract_c_signature(node, source)
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function"),
                    name=name,
                    kind="function",
                    language="c",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=signature,
                )
                symbols.append(symbol)

        # Function declarations (prototypes)
        elif node.type == "declaration":
            # Check if this is a function declaration
            for child in node.children:
                if child.type == "function_declarator":
                    name = _find_identifier_in_children(child, source)
                    if name:
                        span = Span(
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        )
                        signature = _extract_c_signature(node, source)
                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function"),
                            name=name,
                            kind="function",
                            language="c",
                            path=str(file_path),
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            signature=signature,
                        )
                        symbols.append(symbol)

        # Struct declarations
        elif node.type == "struct_specifier":
            name = _find_identifier_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "struct"),
                    name=name,
                    kind="struct",
                    language="c",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)

        # Enum declarations
        elif node.type == "enum_specifier":
            name = _find_identifier_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "enum"),
                    name=name,
                    kind="enum",
                    language="c",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)

        # Typedef declarations
        elif node.type == "type_definition":
            # Find the typedef name (last identifier usually)
            name = None
            for child in node.children:
                if child.type == "type_identifier":
                    name = _node_text(child, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "typedef"),
                    name=name,
                    kind="typedef",
                    language="c",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)

    return symbols


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    global_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up to find the enclosing function definition."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            name = _get_function_name(current, source)
            if name and name in global_symbols:
                func_sym = global_symbols[name]
                if func_sym.path == str(file_path):
                    return func_sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
    global_symbols: dict[str, Symbol],
) -> list[Edge]:
    """Extract edges from a parsed C tree (pass 2).

    Uses global symbol registry to resolve cross-file references.
    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        # Function calls: func_name(...)
        if node.type == "call_expression":
            current_function = _get_enclosing_function(node, source, file_path, global_symbols)
            if current_function:
                # Get the function being called
                func_node = node.child_by_field_name("function")
                if func_node and func_node.type == "identifier":
                    callee_name = _node_text(func_node, source)
                    if callee_name in global_symbols:
                        target_sym = global_symbols[callee_name]
                        edge = Edge.create(
                            src=current_function.id,
                            dst=target_sym.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=0.95,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="ast_call_direct",
                        )
                        edges.append(edge)

    return edges


def _analyze_c_file(
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge], bool]:
    """Analyze a single C file (legacy single-pass, used for testing).

    Returns (symbols, edges, success).
    """
    parser = _get_c_parser()
    if parser is None:
        return [], [], False

    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return [], [], False

    symbols = _extract_symbols(tree, source, file_path, run)

    # Build symbol registry for edge extraction
    global_symbols: dict[str, Symbol] = {}

    for sym in symbols:
        global_symbols[sym.name] = sym

    edges = _extract_edges(tree, source, file_path, run, global_symbols)
    return symbols, edges, True


def analyze_c(repo_root: Path) -> CAnalysisResult:
    """Analyze all C files in a repository.

    Uses a two-pass approach:
    1. Parse all files and extract symbols into global registry
    2. Detect calls and resolve against global symbol registry

    Returns a CAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-c is not available, returns empty result (silently skipped).
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Check for tree-sitter-c availability
    if not is_c_tree_sitter_available():
        skip_reason = "C analysis skipped: requires tree-sitter-c (pip install tree-sitter-c)"
        warnings.warn(skip_reason, stacklevel=2)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return CAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    parser = _get_c_parser()
    if parser is None:
        skip_reason = "C analysis skipped: requires tree-sitter-c (pip install tree-sitter-c)"
        warnings.warn(skip_reason, stacklevel=2)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return CAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    # Pass 1: Parse all files and extract symbols
    parsed_files: list[_ParsedFile] = []
    all_symbols: list[Symbol] = []
    files_analyzed = 0
    files_skipped = 0

    for file_path in find_c_files(repo_root):
        try:
            source = file_path.read_bytes()
            tree = parser.parse(source)
            parsed_files.append(_ParsedFile(path=file_path, tree=tree, source=source))
            symbols = _extract_symbols(tree, source, file_path, run)
            all_symbols.extend(symbols)
            files_analyzed += 1
        except (OSError, IOError):
            files_skipped += 1

    # Build global symbol registry
    global_symbols: dict[str, Symbol] = {}

    for sym in all_symbols:
        global_symbols[sym.name] = sym

    # Pass 2: Extract edges using global symbol registry
    all_edges: list[Edge] = []
    for pf in parsed_files:
        edges = _extract_edges(
            pf.tree, pf.source, pf.path, run,
            global_symbols
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return CAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
