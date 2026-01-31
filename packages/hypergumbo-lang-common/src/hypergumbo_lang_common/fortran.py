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
1. Check if tree-sitter-fortran is available
2. If not available, return skipped result (not an error)
3. Parse all .f, .f90, .f95, .f03, .f08 files
4. Extract modules, programs, functions, subroutines, types
5. Create imports edges for use statements
6. Create calls edges for subroutine calls

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-fortran package for grammar
- Fortran-specific: modules, subroutines, types are first-class
- Important for scientific computing, HPC, and legacy codebases
"""
from __future__ import annotations

import hashlib
import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "fortran-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Fortran file extensions
FORTRAN_EXTENSIONS = ["*.f", "*.f90", "*.f95", "*.f03", "*.f08", "*.F", "*.F90", "*.F95", "*.F03", "*.F08", "*.for", "*.fpp"]


def find_fortran_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Fortran files in the repository."""
    yield from find_files(repo_root, FORTRAN_EXTENSIONS)


def is_fortran_tree_sitter_available() -> bool:
    """Check if tree-sitter with Fortran grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    if importlib.util.find_spec("tree_sitter_fortran") is None:
        return False  # pragma: no cover
    return True


@dataclass
class FortranAnalysisResult:
    """Result of analyzing Fortran files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"fortran:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract name from a definition node."""
    for child in node.children:
        if child.type == "name":
            return _node_text(child, source).lower()
    return None  # pragma: no cover


def _get_type_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract type name from derived_type_definition."""
    for child in node.children:
        if child.type == "derived_type_statement":
            for grandchild in child.children:
                if grandchild.type == "type_name":
                    return _node_text(grandchild, source).lower()
    return None  # pragma: no cover


def _get_statement_name(node: "tree_sitter.Node", source: bytes, stmt_type: str) -> Optional[str]:
    """Extract name from a statement node (function_statement, subroutine_statement, etc.)."""
    for child in node.children:
        if child.type == stmt_type:
            for grandchild in child.children:
                if grandchild.type == "name":
                    return _node_text(grandchild, source).lower()
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
                            param_names.append(_node_text(param_child, source).lower())
                elif grandchild.type == "function_result":
                    for result_child in grandchild.children:
                        if result_child.type == "identifier":
                            result_var = _node_text(result_child, source).lower()

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
                    var_names.append(_node_text(decl_child, source).lower())

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
    symbols: list[Symbol],
    symbol_registry: dict[str, Symbol],
) -> None:
    """Extract symbols from Fortran AST tree (pass 1).

    Uses iterative traversal to avoid RecursionError on deeply nested code.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "module")

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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "program")

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
                )
                symbols.append(sym)
                symbol_registry[name] = sym

        # Function definitions
        elif node.type == "function":
            name = _get_statement_name(node, source, "function_statement")
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "function")

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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "subroutine")

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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "type")

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
        Dict mapping alias → "module_name.original_name" for path_hint resolution.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(root_node):
        if node.type == "use_statement":
            # Get the module name
            module_name = None
            for child in node.children:
                if child.type == "module_name":
                    module_name = _node_text(child, source).lower()
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
                                    local_name = _node_text(alias_child, source).lower()
                                elif alias_child.type == "identifier":
                                    original_name = _node_text(alias_child, source).lower()
                            if local_name and original_name:
                                # Map alias to qualified path: module.original
                                aliases[local_name] = f"{module_name}.{original_name}"

    return aliases


def _extract_fortran_edges(
    root_node: "tree_sitter.Node",
    source: bytes,
    edges: list[Edge],
    local_symbols: dict[str, Symbol],
    resolver: NameResolver,
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
        import_aliases: Mapping of alias → module.original for path_hint (ADR-0007)
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
                    mod_name = _node_text(child, source).lower()
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

                edge = Edge(
                    id=_make_edge_id(current_symbol, dst_id, "imports"),
                    src=current_symbol,
                    dst=dst_id,
                    edge_type="imports",
                    line=start_line,
                    confidence=confidence,
                    origin=PASS_ID,
                    evidence_type="static",
                )
                edges.append(edge)

        # Subroutine calls
        elif node.type == "subroutine_call":
            call_name = None
            for child in node.children:
                if child.type == "identifier":
                    call_name = _node_text(child, source).lower()
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

                edge = Edge(
                    id=_make_edge_id(current_symbol, dst_id, "calls"),
                    src=current_symbol,
                    dst=dst_id,
                    edge_type="calls",
                    line=start_line,
                    confidence=confidence,
                    origin=PASS_ID,
                    evidence_type="static",
                )
                edges.append(edge)


def analyze_fortran_files(repo_root: Path) -> FortranAnalysisResult:
    """Analyze Fortran files in the repository.

    Uses two-pass analysis:
    - Pass 1: Extract all symbols from all files
    - Pass 2: Extract edges using NameResolver for cross-file resolution

    Args:
        repo_root: Path to the repository root

    Returns:
        FortranAnalysisResult with symbols and edges
    """
    if not is_fortran_tree_sitter_available():  # pragma: no cover
        return FortranAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-fortran not installed (pip install tree-sitter-fortran)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_fortran

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Global symbol registry for cross-file resolution: name -> Symbol
    global_symbol_registry: dict[str, Symbol] = {}

    # Create parser
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_fortran.language()))
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize Fortran parser: {e}")
        return FortranAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    fortran_files = list(find_fortran_files(repo_root))

    # Store parsed trees for pass 2: (rel_path, source, tree, import_aliases)
    parsed_files: list[tuple[str, bytes, object, dict[str, str]]] = []

    # Pass 1: Extract symbols from all files
    for fortran_path in fortran_files:
        try:
            rel_path = str(fortran_path.relative_to(repo_root))
            source = fortran_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Extract symbols
            _extract_fortran_symbols(
                tree.root_node,
                source,
                rel_path,
                symbols,
                global_symbol_registry,
            )

            # Extract import aliases for ADR-0007
            import_aliases = _extract_use_aliases(tree.root_node, source)

            # Store for pass 2
            parsed_files.append((rel_path, source, tree, import_aliases))

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {fortran_path}: {e}")  # pragma: no cover

    # Create resolver from global registry
    resolver = NameResolver(global_symbol_registry)

    # Pass 2: Extract edges using resolver
    for rel_path, source, tree, import_aliases in parsed_files:
        # Build local symbol map for this file
        local_symbols = {s.name: s for s in symbols if s.path == rel_path}

        _extract_fortran_edges(
            tree.root_node,  # type: ignore
            source,
            edges,
            local_symbols,
            resolver,
            import_aliases,
        )

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return FortranAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
