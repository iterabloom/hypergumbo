# SPDX-License-Identifier: AGPL-3.0-or-later
"""Julia analysis pass using tree-sitter-julia.

This analyzer uses tree-sitter to parse Julia files and extract:
- Module declarations
- Function declarations (both full and short-form)
- Struct declarations (mutable and immutable)
- Abstract type declarations
- Macro declarations
- Constant declarations
- Function call relationships
- Import statements (import/using)

If tree-sitter with Julia support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration.
The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Julia-specific extraction logic.

1. Check if tree-sitter-julia is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-julia package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other language analyzers for consistency
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("julia")


def find_julia_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Julia files in the repository."""
    yield from find_files(repo_root, ["*.jl"])


def _get_enclosing_module(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing module name."""
    current = node.parent
    while current is not None:
        if current.type == "module_definition":
            id_node = find_child_by_type(current, "identifier")
            if id_node:
                return node_text(id_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_enclosing_function_julia(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function for Julia."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            func_name = _extract_function_name(current, source)
            if func_name and func_name in local_symbols:
                return local_symbols[func_name]
        elif current.type == "assignment":
            # Check for short-form function definition
            left_node = current.children[0] if current.children else None
            if left_node and left_node.type == "call_expression":
                id_node = find_child_by_type(left_node, "identifier")
                if id_node:
                    func_name = node_text(id_node, source)
                    if func_name in local_symbols:
                        return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from signature or assignment."""
    # For full function definitions: function name(args)
    sig_node = find_child_by_type(node, "signature")
    if sig_node:
        call_node = find_child_by_type(sig_node, "call_expression")
        if not call_node:
            # Handle typed_expression case: add(x::Int)::ReturnType
            typed_node = find_child_by_type(sig_node, "typed_expression")
            if typed_node:
                call_node = find_child_by_type(typed_node, "call_expression")
        if call_node:
            id_node = find_child_by_type(call_node, "identifier")
            if id_node:
                return node_text(id_node, source)
    return None  # pragma: no cover


def _extract_julia_signature(
    node: "tree_sitter.Node", source: bytes, is_short_form: bool = False
) -> Optional[str]:
    """Extract function signature from a Julia function definition.

    Returns signature like:
    - "(x::Int, y::Int)::Int" for typed functions
    - "(message::String)" for untyped return
    - "(x)" for short-form functions

    Args:
        node: The function_definition or assignment node.
        source: The source code bytes.
        is_short_form: True for assignment-based function definitions.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    return_type: Optional[str] = None

    if is_short_form:
        # Short form: f(x) = expr
        left_node = node.children[0] if node.children else None
        if left_node and left_node.type == "call_expression":
            arg_list = find_child_by_type(left_node, "argument_list")
            if arg_list:
                for child in arg_list.children:
                    if child.type == "typed_expression":  # pragma: no cover - rare in short form
                        params.append(node_text(child, source))
                    elif child.type == "identifier":
                        params.append(node_text(child, source))
    else:
        # Full form: function name(args)::ReturnType
        sig_node = find_child_by_type(node, "signature")
        if sig_node:
            # Check if signature has typed return
            typed_expr = find_child_by_type(sig_node, "typed_expression")
            if typed_expr:
                # Get call_expression and return type
                call_node = find_child_by_type(typed_expr, "call_expression")
                for child in typed_expr.children:
                    if child.type == "identifier" and call_node:
                        # This is the return type (after ::)
                        return_type = node_text(child, source)
                if call_node:
                    arg_list = find_child_by_type(call_node, "argument_list")
                    if arg_list:
                        for child in arg_list.children:
                            if child.type == "typed_expression":
                                params.append(node_text(child, source))
                            elif child.type == "identifier":  # pragma: no cover - untyped in typed func
                                params.append(node_text(child, source))
            else:
                # No typed return
                call_node = find_child_by_type(sig_node, "call_expression")
                if call_node:
                    arg_list = find_child_by_type(call_node, "argument_list")
                    if arg_list:
                        for child in arg_list.children:
                            if child.type == "typed_expression":
                                params.append(node_text(child, source))
                            elif child.type == "identifier":
                                params.append(node_text(child, source))

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type:
        signature += f"::{return_type}"

    return signature


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> FileAnalysis:
    """Extract symbols from a parsed Julia file's tree."""
    analysis = FileAnalysis()

    for node in iter_tree(tree.root_node):
        # Module definition
        if node.type == "module_definition":
            id_node = find_child_by_type(node, "identifier")
            if id_node:
                module_name = node_text(id_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=make_symbol_id("julia", file_path, start_line, end_line, module_name, "module"),
                    name=module_name,
                    kind="module",
                    language="julia",
                    path=file_path,
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[module_name] = symbol

        # Function definition (full form)
        elif node.type == "function_definition":
            func_name = _extract_function_name(node, source)
            if func_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                current_module = _get_enclosing_module(node, source)
                full_name = f"{current_module}.{func_name}" if current_module else func_name

                # Extract signature
                signature = _extract_julia_signature(node, source, is_short_form=False)

                symbol = Symbol(
                    id=make_symbol_id("julia", file_path, start_line, end_line, full_name, "function"),
                    name=func_name,
                    kind="function",
                    language="julia",
                    path=file_path,
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=signature,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[func_name] = symbol

        # Short-form function definition: f(x) = expr
        elif node.type == "assignment":
            # Check if left side is a call_expression (function definition)
            left_node = node.children[0] if node.children else None
            if left_node and left_node.type == "call_expression":
                id_node = find_child_by_type(left_node, "identifier")
                if id_node:
                    func_name = node_text(id_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    # Extract signature
                    signature = _extract_julia_signature(node, source, is_short_form=True)

                    symbol = Symbol(
                        id=make_symbol_id("julia", file_path, start_line, end_line, func_name, "function"),
                        name=func_name,
                        kind="function",
                        language="julia",
                        path=file_path,
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        signature=signature,
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[func_name] = symbol

        # Struct definition
        elif node.type == "struct_definition":
            type_head = find_child_by_type(node, "type_head")
            if type_head:
                # Get name from type_head (handles both simple and subtype syntax)
                id_node = find_child_by_type(type_head, "identifier")
                if not id_node:  # pragma: no cover
                    # Try binary_expression for subtype syntax (Circle <: Shape)
                    bin_node = find_child_by_type(type_head, "binary_expression")
                    if bin_node:
                        id_node = find_child_by_type(bin_node, "identifier")

                if id_node:
                    struct_name = node_text(id_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    symbol = Symbol(
                        id=make_symbol_id("julia", file_path, start_line, end_line, struct_name, "struct"),
                        name=struct_name,
                        kind="struct",
                        language="julia",
                        path=file_path,
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[struct_name] = symbol

        # Abstract type definition
        elif node.type == "abstract_definition":
            type_head = find_child_by_type(node, "type_head")
            if type_head:
                id_node = find_child_by_type(type_head, "identifier")
                if id_node:
                    abstract_name = node_text(id_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    symbol = Symbol(
                        id=make_symbol_id("julia", file_path, start_line, end_line, abstract_name, "abstract"),
                        name=abstract_name,
                        kind="abstract",
                        language="julia",
                        path=file_path,
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[abstract_name] = symbol

        # Macro definition
        elif node.type == "macro_definition":
            sig_node = find_child_by_type(node, "signature")
            if sig_node:
                call_node = find_child_by_type(sig_node, "call_expression")
                if call_node:
                    id_node = find_child_by_type(call_node, "identifier")
                    if id_node:
                        macro_name = node_text(id_node, source)
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1

                        symbol = Symbol(
                            id=make_symbol_id("julia", file_path, start_line, end_line, macro_name, "macro"),
                            name=macro_name,
                            kind="macro",
                            language="julia",
                            path=file_path,
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        )
                        analysis.symbols.append(symbol)
                        analysis.symbol_by_name[macro_name] = symbol

        # Const statement
        elif node.type == "const_statement":
            assign_node = find_child_by_type(node, "assignment")
            if assign_node:
                id_node = find_child_by_type(assign_node, "identifier")
                if id_node:
                    const_name = node_text(id_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    symbol = Symbol(
                        id=make_symbol_id("julia", file_path, start_line, end_line, const_name, "const"),
                        name=const_name,
                        kind="const",
                        language="julia",
                        path=file_path,
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[const_name] = symbol

    return analysis


def _extract_import_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import aliases for disambiguation.

    In Julia:
        import Pkg as P -> P maps to Pkg

    Returns a dict mapping alias names to module names.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type not in ("import_statement", "using_statement"):
            continue

        # Find import_alias child (e.g., Pkg as P)
        alias_node = find_child_by_type(node, "import_alias")
        if alias_node:
            module_name = None
            alias_name = None
            found_as = False

            for child in alias_node.children:
                if child.type == "identifier":
                    if not found_as:
                        module_name = node_text(child, source)
                    else:
                        alias_name = node_text(child, source)
                elif child.type == "as":
                    found_as = True

            if module_name and alias_name:
                aliases[alias_name] = module_name

    return aliases


def _extract_edges_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    run_id: str,
    resolver: "NameResolver",
    import_aliases: dict[str, str],
) -> list[Edge]:
    """Extract call and import edges from a parsed Julia file."""
    edges: list[Edge] = []
    file_id = make_file_id("julia", file_path)

    for node in iter_tree(tree.root_node):
        # Detect import/using statements
        if node.type in ("import_statement", "using_statement"):
            # Get the import path
            scoped_node = find_child_by_type(node, "scoped_identifier")
            if scoped_node:
                import_path = node_text(scoped_node, source)
            else:
                id_node = find_child_by_type(node, "identifier")
                if id_node:
                    import_path = node_text(id_node, source)
                else:
                    import_path = None  # pragma: no cover

            if import_path:
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"julia:{import_path}:0-0:package:package",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="import_statement",
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Detect function calls
        elif node.type == "call_expression":
            current_function = _get_enclosing_function_julia(node, source, local_symbols)
            if current_function is not None:
                callee_name: Optional[str] = None
                path_hint: Optional[str] = None

                # Check for qualified call (field_expression: P.add)
                field_node = find_child_by_type(node, "field_expression")
                if field_node:
                    # Get receiver and method from field_expression
                    identifiers = [c for c in field_node.children if c.type == "identifier"]
                    if len(identifiers) >= 2:
                        receiver = node_text(identifiers[0], source)
                        callee_name = node_text(identifiers[-1], source)
                        # Look up receiver in import aliases
                        path_hint = import_aliases.get(receiver)
                else:
                    # Simple call
                    id_node = find_child_by_type(node, "identifier")
                    if id_node:
                        callee_name = node_text(id_node, source)

                if callee_name:
                    # Check local symbols first
                    if callee_name in local_symbols:
                        callee = local_symbols[callee_name]
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="function_call",
                            confidence=0.85,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))
                    # Check global symbols via resolver
                    else:
                        lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol is not None:
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=lookup_result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="function_call",
                                confidence=0.80 * lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            ))

    return edges


class JuliaAnalyzer(TreeSitterAnalyzer):
    """Julia language analyzer using tree-sitter-julia."""

    lang = "julia"
    file_patterns: ClassVar[list[str]] = ["*.jl"]
    grammar_module = "tree_sitter_julia"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract Julia symbols from a single file."""
        return _extract_symbols_from_tree(tree, source, rel_path, run.execution_id)

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract Julia import aliases (import Pkg as P)."""
        return _extract_import_aliases(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a Julia file."""
        return _extract_edges_from_tree(
            tree, source, rel_path, local_symbols,
            run.execution_id, resolver, import_aliases,
        )


_analyzer = JuliaAnalyzer()


def is_julia_tree_sitter_available() -> bool:
    """Check if tree-sitter with Julia grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("julia")
def analyze_julia(repo_root: Path) -> AnalysisResult:
    """Analyze all Julia files in a repository.

    Returns a AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-julia is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
