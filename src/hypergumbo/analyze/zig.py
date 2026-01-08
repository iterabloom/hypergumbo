"""Zig language analyzer using tree-sitter.

This module provides static analysis for Zig source code, extracting symbols
(functions, structs, enums, unions, error sets, tests) and edges (imports, calls).

Implementation approach:
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
  with cross-file resolution using the symbol table from pass 1.
- Uses tree-sitter-zig grammar for parsing
- Handles Zig-specific constructs like comptime, error sets, test blocks, etc.

Zig grammar key patterns:
- function_declaration: fn name(params) return_type { body }
- variable_declaration: const/var name = value (used for structs, enums, etc.)
- struct_declaration: struct { fields... methods... }
- enum_declaration: enum { variants... }
- union_declaration: union(enum) { fields... }
- error_set_declaration: error { errors... }
- test_declaration: test "name" { body }
- builtin_function: @import("module") for imports
- call_expression: func(args) or obj.method(args)
"""

import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "zig.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class ZigAnalysisResult:
    """Result of analyzing Zig files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_zig_tree_sitter_available() -> bool:
    """Check if tree-sitter and tree-sitter-zig are available."""
    ts_spec = importlib.util.find_spec("tree_sitter")
    if ts_spec is None:
        return False

    zig_spec = importlib.util.find_spec("tree_sitter_zig")
    if zig_spec is None:
        return False

    return True


def find_zig_files(root: Path) -> Iterator[Path]:
    """Find all Zig files in the given directory."""
    for path in root.rglob("*.zig"):
        if path.is_file():
            yield path


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text content from a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(
    node: "tree_sitter.Node", type_name: str
) -> Optional["tree_sitter.Node"]:
    """Find the first child node of a specific type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from a function_declaration node."""
    # Look for identifier child that is the function name
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
    return None  # pragma: no cover - defensive


def _get_struct_name_from_variable(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract name from a variable_declaration that defines a struct/enum/union."""
    # Pattern: const Name = struct { ... }
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
    return None


def _get_import_module(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract module name from @import builtin call."""
    # Pattern: @import("module")
    args_node = _find_child_by_type(node, "arguments")
    if args_node is None:
        return None  # pragma: no cover - defensive

    for child in args_node.children:
        if child.type == "string":
            text = _node_text(child, source)
            # Remove quotes
            return text.strip('"')
    return None  # pragma: no cover - defensive


def _get_call_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function/method name from a call_expression."""
    # Can be: identifier(args) or field_expression(args)
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
        elif child.type == "field_expression":
            # obj.method() - get the method name
            for fc in child.children:
                if fc.type == "identifier":
                    # Get the last identifier (method name)
                    pass
            # Find the last identifier which is the method name
            last_id = None
            for fc in child.children:
                if fc.type == "identifier":
                    last_id = _node_text(fc, source)
            return last_id
    return None  # pragma: no cover - defensive


def _make_symbol_id(
    path: str, start_line: int, end_line: int, name: str, kind: str
) -> str:
    """Generate location-based ID for a symbol."""
    return f"zig:{path}:{start_line}-{end_line}:{name}:{kind}"


def _extract_zig_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a function_declaration node.

    Returns signature in format: (param: Type, param2: Type2) ReturnType
    Omits void return types.
    """
    params: list[str] = []
    return_type: Optional[str] = None

    # Find parameters node
    params_node = _find_child_by_type(node, "parameters")
    if params_node:
        for child in params_node.children:
            if child.type == "parameter":
                param_name = None
                param_type = None
                for pc in child.children:
                    if pc.type == "identifier":
                        param_name = _node_text(pc, source)
                    elif pc.type in (
                        "primitive_type",
                        "type_identifier",
                        "pointer_type",
                        "optional_type",
                        "error_union_type",
                        "builtin_type",
                        "array_type",
                        "slice_type",
                    ):
                        param_type = _node_text(pc, source)
                if param_name and param_type:
                    params.append(f"{param_name}: {param_type}")
                elif param_name == "self":  # pragma: no cover - self always has type
                    # Handle self parameter (just "self" without type annotation)
                    params.append("self")

    # Find return type - look for type after parameters
    found_params = False
    for child in node.children:
        if child.type == "parameters":
            found_params = True
            continue
        if found_params and child.type in (
            "primitive_type",
            "type_identifier",
            "pointer_type",
            "optional_type",
            "error_union_type",
            "builtin_type",
            "array_type",
            "slice_type",
        ):
            return_type = _node_text(child, source)
            break

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type and return_type != "void":
        signature += f" {return_type}"

    return signature


def _extract_symbols_from_tree(
    root_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    symbol_table: dict[str, Symbol],
    run: "AnalysisRun",
) -> None:
    """Extract symbols from an AST using iterative traversal.

    This avoids nested function closure issues with loop variables.
    """
    # Stack: (node, container_name)
    stack: list[tuple["tree_sitter.Node", Optional[str]]] = [(root_node, None)]

    while stack:
        node, container = stack.pop()

        if node.type == "function_declaration":
            name = _get_function_name(node, source)
            if name:
                # Determine if it's a method (has self parameter)
                is_method = False
                params_node = _find_child_by_type(node, "parameters")
                if params_node:
                    for param in params_node.children:
                        if param.type == "parameter":
                            param_id = _find_child_by_type(param, "identifier")
                            if param_id and _node_text(param_id, source) == "self":
                                is_method = True
                                break

                kind = "method" if is_method and container else "function"
                qualified_name = f"{container}.{name}" if container else name
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                sym = Symbol(
                    id=_make_symbol_id(rel_path, start_line, end_line, qualified_name, kind),
                    name=qualified_name,
                    kind=kind,
                    language="zig",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=_extract_zig_signature(node, source),
                )
                symbols.append(sym)
                symbol_table[qualified_name] = sym

        elif node.type == "variable_declaration":
            # Check if this defines a struct, enum, union, or error set
            var_name = _get_struct_name_from_variable(node, source)
            if var_name:
                struct_node = _find_child_by_type(node, "struct_declaration")
                enum_node = _find_child_by_type(node, "enum_declaration")
                union_node = _find_child_by_type(node, "union_declaration")
                error_node = _find_child_by_type(node, "error_set_declaration")

                kind = None
                inner_node = None
                if struct_node:
                    kind = "struct"
                    inner_node = struct_node
                elif enum_node:
                    kind = "enum"
                    inner_node = enum_node
                elif union_node:
                    kind = "union"
                    inner_node = union_node
                elif error_node:
                    kind = "error_set"
                    inner_node = error_node

                if kind:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    sym = Symbol(
                        id=_make_symbol_id(rel_path, start_line, end_line, var_name, kind),
                        name=var_name,
                        kind=kind,
                        language="zig",
                        path=rel_path,
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    )
                    symbols.append(sym)
                    symbol_table[var_name] = sym

                    # Process nested declarations with updated container
                    if inner_node:
                        for child in reversed(inner_node.children):
                            stack.append((child, var_name))
                    continue  # Don't process other children

        elif node.type == "test_declaration":
            # Extract test name from string
            string_node = _find_child_by_type(node, "string")
            if string_node:
                test_name = _node_text(string_node, source).strip('"')
                sym_name = f"test \"{test_name}\""
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                sym = Symbol(
                    id=_make_symbol_id(rel_path, start_line, end_line, sym_name, "test"),
                    name=sym_name,
                    kind="test",
                    language="zig",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(sym)

        # Add children to stack (in reverse to maintain order)
        for child in reversed(node.children):
            stack.append((child, container))


def _extract_edges_from_tree(
    root_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    edges: list[Edge],
    symbol_table: dict[str, Symbol],
    run: "AnalysisRun",
) -> None:
    """Extract edges from an AST using iterative traversal.

    This avoids nested function closure issues with loop variables.
    """
    # Stack: (node, container_name, current_function_sym)
    stack: list[tuple["tree_sitter.Node", Optional[str], Optional[Symbol]]] = [
        (root_node, None, None)
    ]

    while stack:
        node, container, current_function_sym = stack.pop()

        if node.type == "function_declaration":
            name = _get_function_name(node, source)
            if name:
                # Check if method
                is_method = False
                params_node = _find_child_by_type(node, "parameters")
                if params_node:
                    for param in params_node.children:
                        if param.type == "parameter":
                            param_id = _find_child_by_type(param, "identifier")
                            if param_id and _node_text(param_id, source) == "self":
                                is_method = True
                                break

                qualified_name = f"{container}.{name}" if (container and is_method) else name
                func_sym = symbol_table.get(qualified_name)

                # Process children with updated function context
                for child in reversed(node.children):
                    stack.append((child, container, func_sym))
                continue

        elif node.type == "variable_declaration":
            # Check for struct/enum/union to update container context
            var_name = _get_struct_name_from_variable(node, source)
            struct_node = _find_child_by_type(node, "struct_declaration")
            enum_node = _find_child_by_type(node, "enum_declaration")
            union_node = _find_child_by_type(node, "union_declaration")

            if var_name and (struct_node or enum_node or union_node):
                inner_node = struct_node or enum_node or union_node
                if inner_node:
                    for child in reversed(inner_node.children):
                        stack.append((child, var_name, current_function_sym))
                continue

            # Also check for @import in variable declarations
            builtin_node = _find_child_by_type(node, "builtin_function")
            if builtin_node:
                builtin_id = _find_child_by_type(builtin_node, "builtin_identifier")
                if builtin_id and _node_text(builtin_id, source) == "@import":
                    module_name = _get_import_module(builtin_node, source)
                    if module_name:
                        # Create file-level import edge
                        src_id = f"zig:{rel_path}:0-0:file:file"
                        dst_id = f"zig:{module_name}:0-0:{module_name}:module"
                        line = node.start_point[0] + 1
                        edge = Edge.create(
                            src=src_id,
                            dst=dst_id,
                            edge_type="imports",
                            line=line,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="ast_import",
                            confidence=1.0,
                        )
                        edges.append(edge)

        elif node.type == "builtin_function":
            # Handle @import at other locations
            builtin_id = _find_child_by_type(node, "builtin_identifier")
            if builtin_id and _node_text(builtin_id, source) == "@import":
                module_name = _get_import_module(node, source)
                if module_name:
                    src_id = current_function_sym.id if current_function_sym else f"zig:{rel_path}:0-0:file:file"
                    dst_id = f"zig:{module_name}:0-0:{module_name}:module"
                    line = node.start_point[0] + 1
                    edge = Edge.create(
                        src=src_id,
                        dst=dst_id,
                        edge_type="imports",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="ast_import",
                        confidence=1.0,
                    )
                    edges.append(edge)

        elif node.type == "call_expression":
            if current_function_sym:
                call_name = _get_call_name(node, source)
                if call_name:
                    # Try to resolve the target
                    target_sym = symbol_table.get(call_name)
                    if target_sym:
                        dst_id = target_sym.id
                        confidence = 0.9
                    else:
                        # Create placeholder ID for unresolved call
                        dst_id = f"zig:{rel_path}:0-0:{call_name}:function"
                        confidence = 0.6

                    line = node.start_point[0] + 1
                    edge = Edge.create(
                        src=current_function_sym.id,
                        dst=dst_id,
                        edge_type="calls",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="ast_call_direct",
                        confidence=confidence,
                    )
                    edges.append(edge)

        # Add children to stack (in reverse to maintain order)
        for child in reversed(node.children):
            stack.append((child, container, current_function_sym))


def analyze_zig(root: Path) -> ZigAnalysisResult:
    """Analyze Zig files in the given directory.

    Args:
        root: Root directory to analyze

    Returns:
        ZigAnalysisResult with symbols, edges, and analysis run metadata
    """
    if not is_zig_tree_sitter_available():
        warnings.warn(
            "tree-sitter-zig not available. Install with: pip install tree-sitter-zig"
        )
        return ZigAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-zig not available",
        )

    import tree_sitter
    import tree_sitter_zig as ts_zig

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    try:
        language = tree_sitter.Language(ts_zig.language())
        parser = tree_sitter.Parser(language)
    except Exception:  # pragma: no cover
        run.duration_ms = int((time.time() - start_time) * 1000)
        return ZigAnalysisResult(
            run=run,
            skipped=True,
            skip_reason="Failed to load tree-sitter-zig parser",
        )

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    symbol_table: dict[str, Symbol] = {}  # name -> Symbol for resolution

    # Pass 1: Extract all symbols
    for file_path in find_zig_files(root):
        files_analyzed += 1
        try:
            source = file_path.read_bytes()
        except IOError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        rel_path = str(file_path.relative_to(root))

        # Extract symbols from this file using iterative traversal
        _extract_symbols_from_tree(
            tree.root_node, source, rel_path, symbols, symbol_table, run
        )

    # Pass 2: Extract edges (imports and calls)
    for file_path in find_zig_files(root):
        try:
            source = file_path.read_bytes()
        except IOError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        rel_path = str(file_path.relative_to(root))

        # Extract edges from this file using iterative traversal
        _extract_edges_from_tree(
            tree.root_node, source, rel_path, edges, symbol_table, run
        )

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return ZigAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
