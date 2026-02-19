"""Elm analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse Elm files and extract:
- Module definitions (module_declaration)
- Function/value definitions (value_declaration)
- Type alias definitions (type_alias_declaration)
- Custom type definitions (type_declaration)
- Port declarations (port_annotation)
- Import statements (import_clause)
- Function call relationships

If tree-sitter with Elm support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract modules, functions, types, ports
2. Pass 2: Detect calls and import statements using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Elm-specific
extraction logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for grammar (elm)
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Elm-Specific Considerations
---------------------------
- Elm is a functional language for web frontends
- Functions are defined with type annotations and value declarations
- Custom types (union types) are like enums with associated data
- Type aliases provide named record types
- Ports enable JavaScript interop
- All functions are pure; side effects through Cmd messages
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol
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

PASS_ID = "elm-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_elm_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Elm files in the repository."""
    yield from find_files(repo_root, ["*.elm"])


def _extract_elm_signature(
    decl_left: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a function_declaration_left node.

    Returns signature in format: (param1, param2)
    Elm function parameters follow the function name.
    """
    params: list[str] = []

    # Skip the first child (function name) and collect parameters
    found_name = False
    for child in decl_left.children:
        if child.type == "lower_case_identifier":
            if not found_name:
                found_name = True
                continue
            # Additional lower_case_identifiers are parameters
            params.append(node_text(child, source))  # pragma: no cover - params are lower_pattern
        elif child.type == "pattern":  # pragma: no cover - complex patterns
            # Pattern matching in parameters
            params.append(node_text(child, source))
        elif child.type == "lower_pattern":
            # Simple parameter pattern
            params.append(node_text(child, source))

    return f"({', '.join(params)})"


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> tuple[list[Symbol], str]:
    """Extract all symbols from a parsed Elm file.

    Returns (symbols, module_name).

    Detects:
    - module_declaration (module name)
    - value_declaration (functions/values)
    - type_alias_declaration (type aliases)
    - type_declaration (custom types)
    - port_annotation (ports)
    """
    symbols: list[Symbol] = []
    module_name = ""

    for node in tree.root_node.children:
        # Module declaration
        if node.type == "module_declaration":
            qid = find_child_by_type(node, "upper_case_qid")
            if qid:
                module_name = node_text(qid, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = make_symbol_id("elm", file_path, start_line, end_line, module_name, "module")
                symbols.append(Symbol(
                    id=sym_id,
                    name=module_name,
                    kind="module",
                    language="elm",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Value/function declaration
        elif node.type == "value_declaration":
            decl_left = find_child_by_type(node, "function_declaration_left")
            if decl_left:
                name_node = find_child_by_type(decl_left, "lower_case_identifier")
                if name_node:
                    func_name = node_text(name_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    span = Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    )
                    sym_id = make_symbol_id("elm", file_path, start_line, end_line, func_name, "function")
                    symbols.append(Symbol(
                        id=sym_id,
                        name=func_name,
                        kind="function",
                        language="elm",
                        path=file_path,
                        span=span,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        signature=_extract_elm_signature(decl_left, source),
                    ))

        # Type alias
        elif node.type == "type_alias_declaration":
            name_node = find_child_by_type(node, "upper_case_identifier")
            if name_node:
                type_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = make_symbol_id("elm", file_path, start_line, end_line, type_name, "type")
                symbols.append(Symbol(
                    id=sym_id,
                    name=type_name,
                    kind="type",
                    language="elm",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Custom type (union type)
        elif node.type == "type_declaration":
            name_node = find_child_by_type(node, "upper_case_identifier")
            if name_node:
                type_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = make_symbol_id("elm", file_path, start_line, end_line, type_name, "type")
                symbols.append(Symbol(
                    id=sym_id,
                    name=type_name,
                    kind="type",
                    language="elm",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Port declaration
        elif node.type == "port_annotation":
            name_node = find_child_by_type(node, "lower_case_identifier")
            if name_node:
                port_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = make_symbol_id("elm", file_path, start_line, end_line, port_name, "port")
                symbols.append(Symbol(
                    id=sym_id,
                    name=port_name,
                    kind="port",
                    language="elm",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

    return symbols, module_name


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Symbol | None:
    """Walk up parent chain to find enclosing function."""
    current = node.parent
    while current is not None:
        if current.type == "value_declaration":
            decl_left = find_child_by_type(current, "function_declaration_left")
            if decl_left:
                name_node = find_child_by_type(decl_left, "lower_case_identifier")
                if name_node:
                    func_name = node_text(name_node, source)
                    sym = local_symbols.get(func_name)
                    if sym:
                        return sym
        current = current.parent
    return None  # pragma: no cover - no enclosing function found


def _extract_import_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import aliases for disambiguation.

    In Elm:
        import Dict as D -> D maps to Dict

    Returns a dict mapping alias names to full module names.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_clause":
            continue

        # Get the module name from upper_case_qid
        qid = find_child_by_type(node, "upper_case_qid")
        if not qid:  # pragma: no cover - defensive
            continue

        module_name = node_text(qid, source)

        # Check for as_clause (import Dict as D)
        as_clause = find_child_by_type(node, "as_clause")
        if as_clause:
            # Get alias name from upper_case_identifier in as_clause
            alias_node = find_child_by_type(as_clause, "upper_case_identifier")
            if alias_node:
                alias_name = node_text(alias_node, source)
                aliases[alias_name] = module_name

    return aliases


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: "NameResolver",
    run_id: str,
    import_aliases: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a parsed Elm file.

    Args:
        import_aliases: Optional dict mapping module aliases to full names.

    Detects:
    - Function calls (function_call_expr, value_expr)
    - Import statements (import_clause)
    """
    if import_aliases is None:  # pragma: no cover - defensive default
        import_aliases = {}
    edges: list[Edge] = []
    file_id = make_file_id("elm", file_path)

    # Build local symbol map (name -> symbol)
    local_symbols = {s.name: s for s in file_symbols}

    for node in iter_tree(tree.root_node):
        if node.type == "import_clause":
            # import Module [exposing (...)]
            qid = find_child_by_type(node, "upper_case_qid")
            if qid:
                module_name = node_text(qid, source)
                module_id = f"elm:{module_name}:0-0:module:module"
                edge = Edge.create(
                    src=file_id,
                    dst=module_id,
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="import",
                    confidence=0.95,
                )
                edges.append(edge)

        elif node.type == "function_call_expr":
            # Function application
            caller = _get_enclosing_function(node, source, local_symbols)
            if caller:
                # First child of function_call_expr is the function being called
                value_expr = find_child_by_type(node, "value_expr")
                if value_expr:
                    value_qid = find_child_by_type(value_expr, "value_qid")
                    if value_qid:
                        callee_name_node = find_child_by_type(value_qid, "lower_case_identifier")
                        if callee_name_node:
                            callee_name = node_text(callee_name_node, source)
                            path_hint: Optional[str] = None

                            # Check for qualified call (D.empty -> upper_case_identifier D)
                            qualifier_node = find_child_by_type(value_qid, "upper_case_identifier")
                            if qualifier_node:
                                qualifier = node_text(qualifier_node, source)
                                path_hint = import_aliases.get(qualifier)

                            lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                            if lookup_result.found and lookup_result.symbol:
                                callee = lookup_result.symbol
                                confidence = 0.85 * lookup_result.confidence
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="function_call",
                                    confidence=confidence,
                                )
                                edges.append(edge)

    return edges


class ElmAnalyzer(TreeSitterAnalyzer):
    """Elm language analyzer using tree-sitter-language-pack."""

    lang = "elm"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.elm"]
    language_pack_name = "elm"
    create_file_symbols = True

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from an Elm file."""
        analysis = FileAnalysis()
        symbols, module_name = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract import aliases for disambiguation."""
        return _extract_import_aliases(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from an Elm file."""
        file_symbols = list(local_symbols.values())
        return _extract_edges_from_file(
            tree, source, rel_path,
            file_symbols, resolver,
            run.execution_id, import_aliases=import_aliases,
        )


_analyzer = ElmAnalyzer()


def is_elm_tree_sitter_available() -> bool:
    """Check if tree-sitter with Elm grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("elm")
def analyze_elm(repo_root: Path) -> AnalysisResult:
    """Analyze Elm files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-language-pack is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
