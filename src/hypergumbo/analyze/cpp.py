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
- Uses iterative traversal to avoid RecursionError on deeply nested code
"""
from __future__ import annotations

import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import (
    AnalysisResult,
    FileAnalysis,
    find_child_by_type as _find_child_by_type,
    is_grammar_available,
    iter_tree,
    make_file_id as _base_make_file_id,
    make_symbol_id as _base_make_symbol_id,
    node_text as _node_text,
)

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "cpp-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Backwards compatibility alias
CppAnalysisResult = AnalysisResult


def find_cpp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all C++ files in the repository."""
    yield from find_files(repo_root, ["*.cpp", "*.cc", "*.cxx", "*.hpp", "*.hxx", "*.h"])


def is_cpp_tree_sitter_available() -> bool:
    """Check if tree-sitter with C++ grammar is available."""
    return is_grammar_available("tree_sitter_cpp")


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return _base_make_symbol_id("cpp", path, start_line, end_line, name, kind)


def _make_file_id(path: str) -> str:
    """Generate ID for a C++ file node (used as include edge source)."""
    return _base_make_file_id("cpp", path)


def _extract_cpp_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a C++ function definition or declaration.

    Returns signature like "(int x, std::string& name) int" or "(void)".
    """
    if node.type not in ("function_definition", "declaration"):
        return None  # pragma: no cover

    # Find function_declarator
    declarator = _find_child_by_type(node, "function_declarator")
    if not declarator:
        return None  # pragma: no cover

    # Find parameter_list
    param_list = _find_child_by_type(declarator, "parameter_list")
    if not param_list:
        return None  # pragma: no cover

    # Extract parameters
    param_strs: list[str] = []
    for child in param_list.children:
        if child.type == "parameter_declaration":
            param_text = _node_text(child, source).strip()
            param_strs.append(param_text)

    # Build signature with parameters
    sig = "(" + ", ".join(param_strs) + ")"

    # Extract return type (collect nodes before function_declarator)
    return_type_parts: list[str] = []
    for child in node.children:
        if child.type == "function_declarator":
            break
        if child.type in (
            "primitive_type", "type_identifier", "qualified_identifier",
            "sized_type_specifier", "template_type", "auto",
            "storage_class_specifier", "type_qualifier",
        ):
            return_type_parts.append(_node_text(child, source))

    if return_type_parts:
        return_type = " ".join(return_type_parts)
        if return_type and return_type != "void":
            sig += f" {return_type}"

    return sig


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

    # Use iterative traversal to avoid RecursionError on deeply nested code
    for node in iter_tree(tree.root_node):
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
                signature = _extract_cpp_signature(node, source)

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
                    signature=signature,
                )
                analysis.symbols.append(symbol)
                # Store by both full name and short name
                analysis.symbol_by_name[name] = symbol
                short_name = name.split("::")[-1] if "::" in name else name
                if short_name != name:
                    analysis.symbol_by_name[short_name] = symbol

    return analysis


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract include, call, and instantiation edges from a file.

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):  # pragma: no cover - IO errors hard to trigger in tests
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))

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

    # Stack entries: (node, current_function_context)
    stack: list[tuple["tree_sitter.Node", Optional[Symbol]]] = [
        (tree.root_node, None)
    ]

    while stack:
        node, current_function = stack.pop()

        new_function = current_function

        # Track current function for call edges
        if node.type == "function_definition":
            result = _extract_function_name(node, source)
            if result:
                name, _ = result
                short_name = name.split("::")[-1] if "::" in name else name
                if short_name in local_symbols:
                    new_function = local_symbols[short_name]

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

        # Add children to stack with updated context
        for child in reversed(node.children):
            stack.append((child, new_function))

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
