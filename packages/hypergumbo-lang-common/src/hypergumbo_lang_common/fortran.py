"""Fortran analysis pass using tree-sitter-fortran.

This analyzer uses tree-sitter to parse Fortran files and extract:
- Module definitions
- Program definitions
- Function definitions
- Subroutine definitions
- Derived type definitions
- Use statements (imports)
- Subroutine/function calls

If tree-sitter-fortran is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract modules, programs, functions, subroutines, types with signatures
2. Pass 2: Extract call edges and import edges using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Fortran-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-fortran package for grammar (grammar_module)
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Fortran-Specific Considerations
-------------------------------
- Fortran is used in scientific computing, HPC, and legacy codebases
- Modules organize code hierarchically
- Subroutines and functions are first-class constructs
- USE statements import modules (like imports)
- Derived types provide structured data
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("fortran")

# Fortran file extensions
FORTRAN_EXTENSIONS = ["*.f", "*.f90", "*.f95", "*.f03", "*.f08", "*.F", "*.F90", "*.F95", "*.F03", "*.F08", "*.for", "*.fpp"]


def find_fortran_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Fortran files in the repository."""
    yield from find_files(repo_root, FORTRAN_EXTENSIONS)


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _get_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract name from a definition node."""
    for child in node.children:
        if child.type == "name":
            return node_text(child, source).lower()
    return None  # pragma: no cover


def _get_type_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract type name from derived_type_definition."""
    for child in node.children:
        if child.type == "derived_type_statement":
            for grandchild in child.children:
                if grandchild.type == "type_name":
                    return node_text(grandchild, source).lower()
    return None  # pragma: no cover


def _get_statement_name(node: "tree_sitter.Node", source: bytes, stmt_type: str) -> Optional[str]:
    """Extract name from a statement node (function_statement, subroutine_statement, etc.)."""
    for child in node.children:
        if child.type == stmt_type:
            for grandchild in child.children:
                if grandchild.type == "name":
                    return node_text(grandchild, source).lower()
    return None


def _extract_fortran_signature(
    node: "tree_sitter.Node", source: bytes, is_function: bool = True
) -> Optional[str]:
    """Extract function/subroutine signature from a Fortran function/subroutine node.

    Returns signature like:
    - "(x, y): integer" for functions with known return type
    - "(message)" for subroutines (no return type)

    Note: Fortran declares parameter types separately, so we collect parameter names
    from the function/subroutine statement and try to find their types from
    variable declarations.

    Args:
        node: The function or subroutine node.
        source: The source code bytes.
        is_function: True for functions, False for subroutines.

    Returns:
        The signature string, or None if extraction fails.
    """
    param_names: list[str] = []
    result_var: Optional[str] = None
    param_types: dict[str, str] = {}

    # First pass: collect parameter names and result variable from statement
    stmt_type = "function_statement" if is_function else "subroutine_statement"
    for child in node.children:
        if child.type == stmt_type:
            for grandchild in child.children:
                if grandchild.type == "parameters":
                    for param_child in grandchild.children:
                        if param_child.type == "identifier":
                            param_names.append(node_text(param_child, source).lower())
                elif grandchild.type == "function_result":
                    for result_child in grandchild.children:
                        if result_child.type == "identifier":
                            result_var = node_text(result_child, source).lower()

    # Second pass: collect type declarations
    for child in node.children:
        if child.type == "variable_declaration":
            var_type: Optional[str] = None
            var_names: list[str] = []

            for decl_child in child.children:
                if decl_child.type == "intrinsic_type":
                    for type_child in decl_child.children:
                        if type_child.type in ("integer", "real", "character", "logical",
                                               "double", "complex"):
                            var_type = type_child.type
                            break
                elif decl_child.type == "identifier":
                    var_names.append(node_text(decl_child, source).lower())

            if var_type and var_names:
                for vn in var_names:
                    param_types[vn] = var_type

    # Build the signature
    params_str = ", ".join(param_names)
    signature = f"({params_str})"

    # For functions, try to get the return type from the result variable
    if is_function and result_var and result_var in param_types:
        signature += f": {param_types[result_var]}"

    return signature


def _get_enclosing_fortran_symbol(
    node: "tree_sitter.Node",
    source: bytes,
    symbol_registry: dict[str, str],
) -> Optional[str]:
    """Walk up to find the enclosing symbol (module, program, function, subroutine)."""
    current = node.parent
    while current is not None:
        if current.type == "module":  # pragma: no cover - call context
            for child in current.children:  # pragma: no cover - call context
                if child.type == "module_statement":  # pragma: no cover - call context
                    name = _get_name(child, source)  # pragma: no cover - call context
                    if name and name in symbol_registry:  # pragma: no cover - call context
                        return symbol_registry[name]  # pragma: no cover - call context
        elif current.type == "program":
            for child in current.children:
                if child.type == "program_statement":
                    name = _get_name(child, source)
                    if name and name in symbol_registry:
                        return symbol_registry[name]
        elif current.type == "function":  # pragma: no cover - call context
            name = _get_statement_name(current, source, "function_statement")  # pragma: no cover - call context
            if name and name in symbol_registry:  # pragma: no cover - call context
                return symbol_registry[name]  # pragma: no cover - call context
        elif current.type == "subroutine":  # pragma: no cover - call context
            name = _get_statement_name(current, source, "subroutine_statement")  # pragma: no cover - call context
            if name and name in symbol_registry:  # pragma: no cover - call context
                return symbol_registry[name]  # pragma: no cover - call context
        current = current.parent  # pragma: no cover - loop continuation
    return None  # pragma: no cover - defensive


def _extract_fortran_symbols(
    root_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    run_id: str,
    symbols: list[Symbol],
    symbol_registry: dict[str, Symbol],
) -> None:
    """Extract symbols from Fortran AST tree (pass 1).

    Uses iterative traversal to avoid RecursionError on deeply nested code.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        run_id: The execution ID for provenance
        symbols: List to append symbols to
        symbol_registry: Registry mapping symbol names to Symbol objects
    """
    for node in iter_tree(root_node):
        # Module definitions
        if node.type == "module":
            name = None
            for child in node.children:
                if child.type == "module_statement":
                    name = _get_name(child, source)
                    break

            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("fortran", rel_path, start_line, end_line, name, "module")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="module",
                    name=name,
                    path=rel_path,
                    language="fortran",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(sym)
                symbol_registry[name] = sym

        # Program definitions
        elif node.type == "program":
            name = None
            for child in node.children:
                if child.type == "program_statement":
                    name = _get_name(child, source)
                    break

            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("fortran", rel_path, start_line, end_line, name, "program")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="program",
                    name=name,
                    path=rel_path,
                    language="fortran",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(sym)
                symbol_registry[name] = sym

        # Function definitions
        elif node.type == "function":
            name = _get_statement_name(node, source, "function_statement")
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("fortran", rel_path, start_line, end_line, name, "function")

                # Extract signature
                signature = _extract_fortran_signature(node, source, is_function=True)

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="function",
                    name=name,
                    path=rel_path,
                    language="fortran",
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
                symbols.append(sym)
                symbol_registry[name] = sym

        # Subroutine definitions
        elif node.type == "subroutine":
            name = _get_statement_name(node, source, "subroutine_statement")
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("fortran", rel_path, start_line, end_line, name, "subroutine")

                # Extract signature
                signature = _extract_fortran_signature(node, source, is_function=False)

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="subroutine",
                    name=name,
                    path=rel_path,
                    language="fortran",
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
                symbols.append(sym)
                symbol_registry[name] = sym

        # Derived type definitions
        elif node.type == "derived_type_definition":
            name = _get_type_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("fortran", rel_path, start_line, end_line, name, "type")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="type",
                    name=name,
                    path=rel_path,
                    language="fortran",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(sym)
                symbol_registry[name] = sym


def _extract_use_aliases(
    root_node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, str]:
    """Extract import aliases from Fortran use statements (ADR-0007).

    Fortran renaming syntax in use statements:
        use linear_algebra, only: my_solve => solve
        use std_io, only: print_msg, my_read => read

    The use_alias node contains:
    - local_name: the alias (e.g., "my_solve")
    - identifier: the original name (e.g., "solve")

    Returns:
        Dict mapping alias -> "module_name.original_name" for path_hint resolution.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(root_node):
        if node.type == "use_statement":
            # Get the module name
            module_name = None
            for child in node.children:
                if child.type == "module_name":
                    module_name = node_text(child, source).lower()
                    break

            if not module_name:
                continue  # pragma: no cover - use_statement always has module_name

            # Look for included_items with use_alias children
            for child in node.children:
                if child.type == "included_items":
                    for item in child.children:
                        if item.type == "use_alias":
                            local_name = None
                            original_name = None
                            for alias_child in item.children:
                                if alias_child.type == "local_name":
                                    local_name = node_text(alias_child, source).lower()
                                elif alias_child.type == "identifier":
                                    original_name = node_text(alias_child, source).lower()
                            if local_name and original_name:
                                # Map alias to qualified path: module.original
                                aliases[local_name] = f"{module_name}.{original_name}"

    return aliases


def _extract_fortran_edges(
    root_node: "tree_sitter.Node",
    source: bytes,
    edges: list[Edge],
    local_symbols: dict[str, Symbol],
    resolver: "NameResolver",
    run_id: str,
    import_aliases: dict[str, str] | None = None,
) -> None:
    """Extract edges from Fortran AST tree (pass 2).

    Uses NameResolver for callee resolution to enable cross-file symbol lookup.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        edges: List to append edges to
        local_symbols: Local symbol registry for finding enclosing functions
        resolver: NameResolver for callee resolution
        run_id: The execution ID for provenance
        import_aliases: Mapping of alias -> module.original for path_hint (ADR-0007)
    """
    if import_aliases is None:
        import_aliases = {}  # pragma: no cover - always passed by caller
    # Build local ID registry for _get_enclosing_fortran_symbol
    local_id_registry: dict[str, str] = {name: sym.id for name, sym in local_symbols.items()}

    for node in iter_tree(root_node):
        # Use statements (imports)
        if node.type == "use_statement":
            mod_name = None
            for child in node.children:
                if child.type == "module_name":
                    mod_name = node_text(child, source).lower()
                    break

            current_symbol = _get_enclosing_fortran_symbol(node, source, local_id_registry)
            if mod_name and current_symbol:
                start_line = node.start_point[0] + 1
                # Use resolver for callee resolution
                lookup_result = resolver.lookup(mod_name)
                if lookup_result.found and lookup_result.symbol:
                    dst_id = lookup_result.symbol.id
                    confidence = 0.90 * lookup_result.confidence
                else:
                    # External module not in our codebase
                    dst_id = f"fortran:external:{mod_name}"
                    confidence = 0.70

                edge = Edge.create(
                    src=current_symbol,
                    dst=dst_id,
                    edge_type="imports",
                    line=start_line,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="static",
                )
                edges.append(edge)

        # Subroutine calls
        elif node.type == "subroutine_call":
            call_name = None
            for child in node.children:
                if child.type == "identifier":
                    call_name = node_text(child, source).lower()
                    break

            current_symbol = _get_enclosing_fortran_symbol(node, source, local_id_registry)
            if call_name and current_symbol:
                start_line = node.start_point[0] + 1
                # Use path_hint from import aliases if available (ADR-0007)
                path_hint = import_aliases.get(call_name)
                # Use resolver for callee resolution
                lookup_result = resolver.lookup(call_name, path_hint=path_hint)
                if lookup_result.found and lookup_result.symbol:
                    dst_id = lookup_result.symbol.id
                    confidence = 0.90 * lookup_result.confidence
                else:
                    # External subroutine not in our codebase
                    dst_id = f"fortran:external:{call_name}"
                    confidence = 0.70

                edge = Edge.create(
                    src=current_symbol,
                    dst=dst_id,
                    edge_type="calls",
                    line=start_line,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="static",
                )
                edges.append(edge)


class FortranAnalyzer(TreeSitterAnalyzer):
    """Fortran language analyzer using tree-sitter-fortran."""

    lang = "fortran"
    file_patterns: ClassVar[list[str]] = FORTRAN_EXTENSIONS
    grammar_module = "tree_sitter_fortran"
    create_file_symbols = False

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract modules, programs, functions, subroutines, types from a Fortran file."""
        analysis = FileAnalysis()

        _extract_fortran_symbols(
            tree.root_node, source, rel_path, run.execution_id,
            analysis.symbols, analysis.symbol_by_name,
        )

        return analysis

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract Fortran use-statement aliases."""
        return _extract_use_aliases(tree.root_node, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a Fortran file."""
        edges: list[Edge] = []
        _extract_fortran_edges(
            tree.root_node, source, edges,
            local_symbols, resolver, run.execution_id,
            import_aliases=import_aliases,
        )
        return edges


_analyzer = FortranAnalyzer()


def is_fortran_tree_sitter_available() -> bool:
    """Check if tree-sitter with Fortran grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("fortran")
def analyze_fortran_files(repo_root: Path) -> AnalysisResult:
    """Analyze Fortran files in the repository."""
    return _analyzer.analyze(repo_root)
