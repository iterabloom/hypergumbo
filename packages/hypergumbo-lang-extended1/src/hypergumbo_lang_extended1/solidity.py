"""Solidity analysis pass using tree-sitter-solidity.

This analyzer uses tree-sitter to parse Solidity smart contract files and extract:
- Contract declarations
- Interface declarations
- Library declarations
- Function definitions
- Constructor definitions
- Modifier definitions
- Event definitions
- Function call relationships
- Import relationships

If tree-sitter with Solidity support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
- Pass 1: Parse all files, extract all symbols into global registry
- Pass 2: Detect calls, imports, and resolve against global symbol registry

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Solidity-specific
extraction logic.

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-solidity package for grammar (direct grammar module)
- Two-pass allows cross-file call resolution
- Solidity-specific: contracts, modifiers, events are first-class symbols
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver
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

PASS_ID = "solidity-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_solidity_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Solidity files in the repository."""
    yield from find_files(repo_root, ["*.sol"])


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _get_enclosing_contract(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing contract/interface/library name."""
    current = node.parent
    while current is not None:
        if current.type in ("contract_declaration", "interface_declaration", "library_declaration"):
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_enclosing_function_solidity(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/constructor/modifier."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                func_name = node_text(name_node, source)
                sym = local_symbols.get(func_name) or global_symbols.get(func_name)
                if sym:
                    return sym
        elif current.type == "constructor_definition":  # pragma: no cover - constructor calls rare
            sym = local_symbols.get("constructor") or global_symbols.get("constructor")
            if sym:
                return sym
        elif current.type == "modifier_definition":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                mod_name = node_text(name_node, source)
                sym = local_symbols.get(mod_name) or global_symbols.get(mod_name)
                if sym:
                    return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_solidity_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Solidity function definition.

    Solidity syntax: function name(type1 param1, type2 param2) returns (type3)
    Returns signature like "(address to, uint256 amount) returns (bool)".
    """
    params: list[str] = []
    return_type: Optional[str] = None

    for child in node.children:
        # Parameters are direct children with type "parameter"
        if child.type == "parameter":
            param_text = node_text(child, source).strip()
            if param_text:
                params.append(param_text)
        elif child.type == "return_type_definition":
            # Return type definition: returns (type)
            return_type = node_text(child, source).strip()

    sig = "(" + ", ".join(params) + ")"
    if return_type:
        sig += f" {return_type}"
    return sig


def _extract_import_aliases(
    node: "tree_sitter.Node",
    source: bytes,
) -> tuple[str, dict[str, str]]:
    """Extract import path and alias mappings from an import directive.

    Solidity import patterns:
    - import "file.sol";                      -> path, no aliases
    - import * as Alias from "file.sol";      -> path, {Alias: path}
    - import {X as Y} from "file.sol";        -> path, {Y: path}
    - import {X, Y as Z} from "file.sol";     -> path, {Z: path}

    Returns (import_path, {alias: import_path}).
    """
    import_path = ""
    aliases: dict[str, str] = {}

    # Find the import path (string node)
    string_node = find_child_by_type(node, "string")
    if string_node:
        import_path = node_text(string_node, source).strip('"\'')

    if not import_path:
        return "", {}  # pragma: no cover - defensive

    # Look for alias patterns
    children = list(node.children)
    i = 0
    while i < len(children):
        child = children[i]
        if child.type == "as" and i + 1 < len(children):
            # The next identifier is the alias
            next_child = children[i + 1]
            if next_child.type == "identifier":
                alias = node_text(next_child, source)
                aliases[alias] = import_path
        i += 1

    return import_path, aliases


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree", source: bytes, file_path: str,
    run_id: str, analysis: FileAnalysis,
) -> None:
    """Extract symbols from a single Solidity file's parse tree."""
    def add_symbol(
        name: str,
        kind: str,
        node: "tree_sitter.Node",
        prefix: str = "",
        signature: Optional[str] = None,
    ) -> Symbol:
        """Helper to create and register a symbol."""
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        full_name = f"{prefix}.{name}" if prefix else name

        symbol = Symbol(
            id=make_symbol_id("solidity", file_path, start_line, end_line, full_name, kind),
            name=full_name,
            kind=kind,
            language="solidity",
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
        analysis.symbol_by_name[name] = symbol
        analysis.symbol_by_name[full_name] = symbol
        return symbol

    for node in iter_tree(tree.root_node):
        # Contract declaration
        if node.type == "contract_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                contract_name = node_text(name_node, source)
                add_symbol(contract_name, "contract", node)

        # Interface declaration
        elif node.type == "interface_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                interface_name = node_text(name_node, source)
                add_symbol(interface_name, "interface", node)

        # Library declaration
        elif node.type == "library_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                lib_name = node_text(name_node, source)
                add_symbol(lib_name, "library", node)

        # Function definition
        elif node.type == "function_definition":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                func_name = node_text(name_node, source)
                current_contract = _get_enclosing_contract(node, source) or ""
                signature = _extract_solidity_signature(node, source)
                add_symbol(func_name, "function", node, current_contract, signature=signature)

        # Constructor definition
        elif node.type == "constructor_definition":
            current_contract = _get_enclosing_contract(node, source) or ""
            add_symbol("constructor", "constructor", node, current_contract)

        # Modifier definition
        elif node.type == "modifier_definition":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                mod_name = node_text(name_node, source)
                current_contract = _get_enclosing_contract(node, source) or ""
                add_symbol(mod_name, "modifier", node, current_contract)

        # Event definition
        elif node.type == "event_definition":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                event_name = node_text(name_node, source)
                current_contract = _get_enclosing_contract(node, source) or ""
                add_symbol(event_name, "event", node, current_contract)


def _extract_edges_from_tree(
    tree: "tree_sitter.Tree", source: bytes, file_path: str,
    local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
    run_id: str, resolver: NameResolver,
) -> tuple[list[Edge], dict[str, str]]:
    """Extract edges (calls, imports) from a Solidity file's parse tree.

    Returns (edges, import_aliases) where import_aliases maps alias names
    to import paths for path_hint resolution.
    """
    edges: list[Edge] = []
    import_aliases: dict[str, str] = {}
    file_id = make_file_id("solidity", file_path)

    # First pass: extract import aliases
    for node in iter_tree(tree.root_node):
        if node.type == "import_directive":
            import_path, aliases = _extract_import_aliases(node, source)
            if import_path:
                edge = Edge.create(
                    src=file_id,
                    dst=import_path,
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                edges.append(edge)
                import_aliases.update(aliases)

    # Second pass: extract call edges with alias resolution
    for node in iter_tree(tree.root_node):
        # Function call
        if node.type == "call_expression":
            func_node = _find_child_by_field(node, "function")
            current_function = _get_enclosing_function_solidity(
                node, source, local_symbols, global_symbols
            )
            if func_node and current_function:
                call_name = node_text(func_node, source)
                # Try to resolve the called function - local first
                target = local_symbols.get(call_name)
                if target:
                    edge = Edge.create(
                        src=current_function.id,
                        dst=target.id,
                        edge_type="calls",
                        line=node.start_point[0] + 1,
                        confidence=0.90,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                    edges.append(edge)
                else:
                    # Get path_hint from import aliases if available
                    path_hint = import_aliases.get(call_name)
                    # Try global symbols via resolver with path_hint
                    lookup_result = resolver.lookup(call_name, path_hint=path_hint)
                    if lookup_result.found and lookup_result.symbol is not None:  # pragma: no cover - suffix fallback
                        edge = Edge.create(
                            src=current_function.id,
                            dst=lookup_result.symbol.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=0.90 * lookup_result.confidence,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        )
                        edges.append(edge)

    return edges, import_aliases


# ---------------------------------------------------------------------------
# SolidityAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class SolidityAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Solidity smart contract files using TreeSitterAnalyzer base class."""

    lang = "solidity"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.sol"]
    grammar_module = "tree_sitter_solidity"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a single Solidity file."""
        analysis = FileAnalysis()
        _extract_symbols_from_tree(
            tree, source, str(file_path), run.execution_id, analysis,
        )
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a single Solidity file."""
        edges, _aliases = _extract_edges_from_tree(
            tree, source, str(file_path),
            local_symbols, global_symbols,
            run.execution_id, resolver,
        )
        return edges


_analyzer = SolidityAnalyzer()


def is_solidity_tree_sitter_available() -> bool:
    """Check if tree-sitter with Solidity grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("solidity")
def analyze_solidity(repo_root: Path) -> AnalysisResult:
    """Analyze Solidity files in a repository.

    Args:
        repo_root: Path to the repository root.

    Returns:
        AnalysisResult with symbols, edges, and analysis run info.
    """
    return _analyzer.analyze(repo_root)
