"""Zig language analyzer using tree-sitter.

This module provides static analysis for Zig source code, extracting symbols
(functions, structs, enums, unions, error sets, tests) and edges (imports, calls).

Implementation approach:
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
  with cross-file resolution using the symbol table from pass 1.
- Uses tree-sitter-zig grammar for parsing
- Handles Zig-specific constructs like comptime, error sets, test blocks, etc.

Uses TreeSitterAnalyzer base class for two-pass orchestration.
The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Zig-specific extraction logic.

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

from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING, ClassVar

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = "zig.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"



def find_zig_files(root: Path) -> Iterator[Path]:
    """Find all Zig files in the given directory."""
    for path in find_files(root, ["*.zig"]):
        if path.is_file():
            yield path


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from a function_declaration node."""
    # Look for identifier child that is the function name
    for child in node.children:
        if child.type == "identifier":
            return node_text(child, source)
    return None  # pragma: no cover - defensive


def _get_struct_name_from_variable(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract name from a variable_declaration that defines a struct/enum/union."""
    # Pattern: const Name = struct { ... }
    for child in node.children:
        if child.type == "identifier":
            return node_text(child, source)
    return None


def _get_import_module(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract module name from @import builtin call."""
    # Pattern: @import("module")
    args_node = find_child_by_type(node, "arguments")
    if args_node is None:
        return None  # pragma: no cover - defensive

    for child in args_node.children:
        if child.type == "string":
            text = node_text(child, source)
            # Remove quotes
            return text.strip('"')
    return None  # pragma: no cover - defensive


def _get_call_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function/method name from a call_expression."""
    # Can be: identifier(args) or field_expression(args)
    for child in node.children:
        if child.type == "identifier":
            return node_text(child, source)
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
                    last_id = node_text(fc, source)
            return last_id
    return None  # pragma: no cover - defensive



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
    params_node = find_child_by_type(node, "parameters")
    if params_node:
        for child in params_node.children:
            if child.type == "parameter":
                param_name = None
                param_type = None
                for pc in child.children:
                    if pc.type == "identifier":
                        param_name = node_text(pc, source)
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
                        param_type = node_text(pc, source)
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
            return_type = node_text(child, source)
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
                params_node = find_child_by_type(node, "parameters")
                if params_node:
                    for param in params_node.children:
                        if param.type == "parameter":
                            param_id = find_child_by_type(param, "identifier")
                            if param_id and node_text(param_id, source) == "self":
                                is_method = True
                                break

                kind = "method" if is_method and container else "function"
                qualified_name = f"{container}.{name}" if container else name
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                sym = Symbol(
                    id=make_symbol_id("zig", rel_path, start_line, end_line, qualified_name, kind),
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
                struct_node = find_child_by_type(node, "struct_declaration")
                enum_node = find_child_by_type(node, "enum_declaration")
                union_node = find_child_by_type(node, "union_declaration")
                error_node = find_child_by_type(node, "error_set_declaration")

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
                        id=make_symbol_id("zig", rel_path, start_line, end_line, var_name, kind),
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
            string_node = find_child_by_type(node, "string")
            if string_node:
                test_name = node_text(string_node, source).strip('"')
                sym_name = f"test \"{test_name}\""
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                sym = Symbol(
                    id=make_symbol_id("zig", rel_path, start_line, end_line, sym_name, "test"),
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
    resolver: "NameResolver",
    run: "AnalysisRun",
    import_aliases: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Extract edges from an AST using iterative traversal.

    This avoids nested function closure issues with loop variables.

    Returns a dict of import aliases (var_name -> module_path) for path_hint resolution.
    In Zig, imports look like: const std = @import("std");
    So 'std' becomes an alias for the "std" module.
    """
    if import_aliases is None:
        import_aliases = {}

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
                params_node = find_child_by_type(node, "parameters")
                if params_node:
                    for param in params_node.children:
                        if param.type == "parameter":
                            param_id = find_child_by_type(param, "identifier")
                            if param_id and node_text(param_id, source) == "self":
                                is_method = True
                                break

                qualified_name = f"{container}.{name}" if (container and is_method) else name
                lookup_result = resolver.lookup(qualified_name)
                func_sym = lookup_result.symbol if lookup_result.found else None

                # Process children with updated function context
                for child in reversed(node.children):
                    stack.append((child, container, func_sym))
                continue

        elif node.type == "variable_declaration":
            # Check for struct/enum/union to update container context
            var_name = _get_struct_name_from_variable(node, source)
            struct_node = find_child_by_type(node, "struct_declaration")
            enum_node = find_child_by_type(node, "enum_declaration")
            union_node = find_child_by_type(node, "union_declaration")

            if var_name and (struct_node or enum_node or union_node):
                inner_node = struct_node or enum_node or union_node
                if inner_node:
                    for child in reversed(inner_node.children):
                        stack.append((child, var_name, current_function_sym))
                continue

            # Also check for @import in variable declarations
            builtin_node = find_child_by_type(node, "builtin_function")
            if builtin_node:
                builtin_id = find_child_by_type(builtin_node, "builtin_identifier")
                if builtin_id and node_text(builtin_id, source) == "@import":
                    module_name = _get_import_module(builtin_node, source)
                    if module_name:
                        # Track the import alias (e.g., const std = @import("std"))
                        if var_name:
                            import_aliases[var_name] = module_name

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
            builtin_id = find_child_by_type(node, "builtin_identifier")
            if builtin_id and node_text(builtin_id, source) == "@import":
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
                    # Check if this is a field_expression call (e.g., std.debug.print)
                    # to get path_hint from import aliases
                    path_hint: Optional[str] = None
                    field_node = find_child_by_type(node, "field_expression")
                    if field_node:
                        # Get the first identifier (receiver)
                        first_id = find_child_by_type(field_node, "identifier")
                        if first_id:
                            receiver = node_text(first_id, source)
                            if receiver in import_aliases:
                                path_hint = import_aliases[receiver]

                    # Try to resolve the target using NameResolver with path_hint
                    base_confidence = 0.9
                    lookup_result = resolver.lookup(call_name, path_hint=path_hint)
                    if lookup_result.found and lookup_result.symbol:
                        dst_id = lookup_result.symbol.id
                        confidence = base_confidence * lookup_result.confidence
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

    return import_aliases


class ZigAnalyzer(TreeSitterAnalyzer):
    """Zig language analyzer using tree-sitter-zig."""

    lang = "zig"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.zig"]
    grammar_module = "tree_sitter_zig"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract function, struct, enum, union, error set, and test symbols."""
        analysis = FileAnalysis()
        symbol_table: dict[str, Symbol] = {}

        _extract_symbols_from_tree(
            tree.root_node, source, rel_path, analysis.symbols, symbol_table, run
        )

        # Populate symbol_by_name for callable symbols
        for sym in analysis.symbols:
            if sym.kind in ("function", "method"):
                analysis.symbol_by_name[sym.name] = sym

        return analysis

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract Zig import aliases (const X = @import("Y"))."""
        aliases: dict[str, str] = {}
        from hypergumbo_core.analyze.base import iter_tree as _iter_tree

        for node in _iter_tree(tree.root_node):
            if node.type == "variable_declaration":
                var_name = _get_struct_name_from_variable(node, source)
                builtin_node = find_child_by_type(node, "builtin_function")
                if builtin_node and var_name:
                    builtin_id = find_child_by_type(builtin_node, "builtin_identifier")
                    if builtin_id and node_text(builtin_id, source) == "@import":
                        module_name = _get_import_module(builtin_node, source)
                        if module_name:
                            aliases[var_name] = module_name
        return aliases

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import and call edges from a Zig file."""
        edges: list[Edge] = []
        _extract_edges_from_tree(
            tree.root_node, source, rel_path, edges, resolver, run, import_aliases
        )
        return edges


_analyzer = ZigAnalyzer()


def is_zig_tree_sitter_available() -> bool:
    """Check if tree-sitter and tree-sitter-zig are available."""
    return _analyzer._check_grammar_available()


@register_analyzer("zig")
def analyze_zig(root: Path) -> AnalysisResult:
    """Analyze Zig files in the given directory."""
    return _analyzer.analyze(root)
