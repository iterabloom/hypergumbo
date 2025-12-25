"""C++ analysis pass using tree-sitter-cpp.

This analyzer uses tree-sitter to parse C++ files and extract:
- Class declarations
- Struct declarations
- Enum declarations
- Function definitions (standalone and class methods)
- Namespace declarations
- Function call relationships
- Include directives
- Object instantiation (new expressions)

If tree-sitter with C++ support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-cpp is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls, instantiations, and resolve against global symbol registry
4. Detect include directives and new expressions

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-cpp package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other language analyzers for consistency
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

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "cpp-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_cpp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all C++ files in the repository."""
    yield from find_files(repo_root, ["*.cpp", "*.cc", "*.cxx", "*.hpp", "*.hxx", "*.h"])


def is_cpp_tree_sitter_available() -> bool:
    """Check if tree-sitter with C++ grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_cpp") is None:
        return False
    return True


@dataclass
class CppAnalysisResult:
    """Result of analyzing C++ files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"cpp:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a C++ file node (used as include edge source)."""
    return f"cpp:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)


def _extract_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[tuple[str, str]]:
    """Extract function name and kind from function_definition or field_declaration.

    Returns (name, kind) tuple where kind is 'function' or 'method'.
    """
    declarator = _find_child_by_type(node, "function_declarator")
    if not declarator:
        return None  # pragma: no cover - defensive

    # Check for qualified name (Class::method)
    qualified = _find_child_by_type(declarator, "qualified_identifier")
    if qualified:
        # It's a class method implementation
        # Format: namespace::class::method or class::method
        full_name = _node_text(qualified, source)
        return (full_name, "method")

    # Check for simple identifier (standalone function)
    ident = _find_child_by_type(declarator, "identifier")
    if ident:
        name = _node_text(ident, source)
        return (name, "function")

    # Check for field_identifier (method declaration in class)
    field_ident = _find_child_by_type(declarator, "field_identifier")
    if field_ident:
        name = _node_text(field_ident, source)
        return (name, "method")

    return None  # pragma: no cover - defensive


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single C++ file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):  # pragma: no cover - IO errors hard to trigger in tests
        return FileAnalysis()

    analysis = FileAnalysis()

    def visit(node: "tree_sitter.Node") -> None:
        # Class declaration
        if node.type == "class_specifier":
            name_node = _find_child_by_type(node, "type_identifier")
            if name_node:
                name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "class"),
                    name=name,
                    kind="class",
                    language="cpp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

        # Struct declaration
        elif node.type == "struct_specifier":
            name_node = _find_child_by_type(node, "type_identifier")
            if name_node:
                name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "struct"),
                    name=name,
                    kind="struct",
                    language="cpp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

        # Enum declaration
        elif node.type == "enum_specifier":
            name_node = _find_child_by_type(node, "type_identifier")
            if name_node:
                name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "enum"),
                    name=name,
                    kind="enum",
                    language="cpp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

        # Function definition
        elif node.type == "function_definition":
            result = _extract_function_name(node, source)
            if result:
                name, kind = result
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, kind),
                    name=name,
                    kind=kind,
                    language="cpp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                # Store by both full name and short name
                analysis.symbol_by_name[name] = symbol
                short_name = name.split("::")[-1] if "::" in name else name
                if short_name != name:
                    analysis.symbol_by_name[short_name] = symbol

        # Recurse into children
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return analysis


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract include, call, and instantiation edges from a file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):  # pragma: no cover - IO errors hard to trigger in tests
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))
    current_function: Optional[Symbol] = None

    def get_callee_name(node: "tree_sitter.Node") -> Optional[str]:
        """Extract the function name being called from a call_expression."""
        # Check for field_expression (obj.method())
        field_expr = _find_child_by_type(node, "field_expression")
        if field_expr:
            field_ident = _find_child_by_type(field_expr, "field_identifier")
            if field_ident:
                return _node_text(field_ident, source)

        # Check for qualified_identifier (Class::method())
        qualified = _find_child_by_type(node, "qualified_identifier")
        if qualified:
            return _node_text(qualified, source)

        # Check for simple identifier (function())
        ident = _find_child_by_type(node, "identifier")
        if ident:
            return _node_text(ident, source)

        return None  # pragma: no cover - defensive

    def visit(node: "tree_sitter.Node") -> None:
        nonlocal current_function

        # Track current function for call edges
        if node.type == "function_definition":
            result = _extract_function_name(node, source)
            if result:
                name, _ = result
                short_name = name.split("::")[-1] if "::" in name else name
                if short_name in local_symbols:
                    old_function = current_function
                    current_function = local_symbols[short_name]

                    # Process function body
                    for child in node.children:
                        visit(child)

                    current_function = old_function
                    return

        # Include directive
        elif node.type == "preproc_include":
            # Get the included file
            path_node = _find_child_by_type(node, "string_literal")
            if path_node:
                # Local include: #include "header.h"
                content = _find_child_by_type(path_node, "string_content")
                if content:
                    include_path = _node_text(content, source)
                    edges.append(Edge.create(
                        src=file_id,
                        dst=f"cpp:{include_path}:0-0:header:header",
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        evidence_type="include_directive",
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))
            else:
                # System include: #include <header>
                sys_lib = _find_child_by_type(node, "system_lib_string")
                if sys_lib:
                    include_path = _node_text(sys_lib, source)
                    edges.append(Edge.create(
                        src=file_id,
                        dst=f"cpp:{include_path}:0-0:header:header",
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        evidence_type="include_directive",
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))

        # Function call
        elif node.type == "call_expression":
            if current_function is not None:
                callee_name = get_callee_name(node)
                if callee_name:
                    # Try to resolve: look for short name first
                    short_name = callee_name.split("::")[-1] if "::" in callee_name else callee_name

                    # Check local symbols first
                    if short_name in local_symbols:
                        callee = local_symbols[short_name]
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="function_call",
                            confidence=0.85,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                    # Check global symbols
                    elif short_name in global_symbols:
                        callee = global_symbols[short_name]
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="function_call",
                            confidence=0.80,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

        # new expression
        elif node.type == "new_expression":
            if current_function is not None:
                type_name = None
                type_node = _find_child_by_type(node, "type_identifier")
                if type_node:
                    type_name = _node_text(type_node, source)
                else:
                    # Check for qualified_identifier (new Namespace::Class())
                    qualified = _find_child_by_type(node, "qualified_identifier")
                    if qualified:
                        # Get the type_identifier from within the qualified name
                        inner_type = _find_child_by_type(qualified, "type_identifier")
                        if inner_type:
                            type_name = _node_text(inner_type, source)
                if type_name:
                    # Check if it's a known class
                    if type_name in local_symbols:
                        target = local_symbols[type_name]
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=target.id,
                            edge_type="instantiates",
                            line=node.start_point[0] + 1,
                            evidence_type="new_expression",
                            confidence=0.90,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                    elif type_name in global_symbols:
                        target = global_symbols[type_name]
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=target.id,
                            edge_type="instantiates",
                            line=node.start_point[0] + 1,
                            evidence_type="new_expression",
                            confidence=0.85,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

        # Recurse
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return edges


def analyze_cpp(repo_root: Path) -> CppAnalysisResult:
    """Analyze all C++ files in a repository.

    Returns a CppAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-cpp is not available, returns a skipped result.
    """
    if not is_cpp_tree_sitter_available():
        warnings.warn(
            "tree-sitter-cpp not available. Install with: pip install hypergumbo[cpp]",
            stacklevel=2,
        )
        return CppAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-cpp not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-cpp
    try:
        import tree_sitter_cpp
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_cpp.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:  # pragma: no cover - parser load failure hard to trigger
        run.duration_ms = int((time.time() - start_time) * 1000)
        return CppAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load C++ parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for cpp_file in find_cpp_files(repo_root):
        analysis = _extract_symbols_from_file(cpp_file, parser, run)
        if analysis.symbols:
            file_analyses[cpp_file] = analysis
        else:
            files_skipped += 1

    # Build global symbol registry
    global_symbols: dict[str, Symbol] = {}
    for analysis in file_analyses.values():
        for symbol in analysis.symbols:
            # Store by short name for cross-file resolution
            short_name = symbol.name.split("::")[-1] if "::" in symbol.name else symbol.name
            global_symbols[short_name] = symbol
            global_symbols[symbol.name] = symbol

    # Pass 2: Extract edges
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for cpp_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            cpp_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return CppAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
