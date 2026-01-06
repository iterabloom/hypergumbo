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
1. Check if tree-sitter-solidity is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-solidity package for grammar
- Two-pass allows cross-file call resolution
- Solidity-specific: contracts, modifiers, events are first-class symbols
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

PASS_ID = "solidity-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_solidity_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Solidity files in the repository."""
    yield from find_files(repo_root, ["*.sol"])


def is_solidity_tree_sitter_available() -> bool:
    """Check if tree-sitter with Solidity grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_solidity") is None:
        return False
    return True


@dataclass
class SolidityAnalysisResult:
    """Result of analyzing Solidity files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"solidity:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Solidity file node (used as import edge source)."""
    return f"solidity:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _get_enclosing_contract(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing contract/interface/library name."""
    current = node.parent
    while current is not None:
        if current.type in ("contract_declaration", "interface_declaration", "library_declaration"):
            name_node = _find_child_by_type(current, "identifier")
            if name_node:
                return _node_text(name_node, source)
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
                func_name = _node_text(name_node, source)
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
                mod_name = _node_text(name_node, source)
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
            param_text = _node_text(child, source).strip()
            if param_text:
                params.append(param_text)
        elif child.type == "return_type_definition":
            # Return type definition: returns (type)
            return_type = _node_text(child, source).strip()

    sig = "(" + ", ".join(params) + ")"
    if return_type:
        sig += f" {return_type}"
    return sig


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)
    current_contract: str = ""


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Solidity file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()

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
            id=_make_symbol_id(str(file_path), start_line, end_line, full_name, kind),
            name=full_name,
            kind=kind,
            language="solidity",
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
        analysis.symbol_by_name[name] = symbol
        analysis.symbol_by_name[full_name] = symbol
        return symbol

    for node in iter_tree(tree.root_node):
        # Contract declaration
        if node.type == "contract_declaration":
            name_node = _find_child_by_type(node, "identifier")
            if name_node:
                contract_name = _node_text(name_node, source)
                add_symbol(contract_name, "contract", node)

        # Interface declaration
        elif node.type == "interface_declaration":
            name_node = _find_child_by_type(node, "identifier")
            if name_node:
                interface_name = _node_text(name_node, source)
                add_symbol(interface_name, "interface", node)

        # Library declaration
        elif node.type == "library_declaration":
            name_node = _find_child_by_type(node, "identifier")
            if name_node:
                lib_name = _node_text(name_node, source)
                add_symbol(lib_name, "library", node)

        # Function definition
        elif node.type == "function_definition":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                func_name = _node_text(name_node, source)
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
                mod_name = _node_text(name_node, source)
                current_contract = _get_enclosing_contract(node, source) or ""
                add_symbol(mod_name, "modifier", node, current_contract)

        # Event definition
        elif node.type == "event_definition":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                event_name = _node_text(name_node, source)
                current_contract = _get_enclosing_contract(node, source) or ""
                add_symbol(event_name, "event", node, current_contract)

    return analysis


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract edges (calls, imports) from a Solidity file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))

    for node in iter_tree(tree.root_node):
        # Import directive
        if node.type == "import_directive":
            # Find the import path (string node)
            string_node = _find_child_by_type(node, "string")
            if string_node:
                import_path = _node_text(string_node, source).strip('"\'')
                edge = Edge.create(
                    src=file_id,
                    dst=import_path,
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                edges.append(edge)

        # Function call
        elif node.type == "call_expression":
            func_node = _find_child_by_field(node, "function")
            current_function = _get_enclosing_function_solidity(
                node, source, local_symbols, global_symbols
            )
            if func_node and current_function:
                call_name = _node_text(func_node, source)
                # Try to resolve the called function
                target = local_symbols.get(call_name) or global_symbols.get(call_name)
                if target:
                    edge = Edge.create(
                        src=current_function.id,
                        dst=target.id,
                        edge_type="calls",
                        line=node.start_point[0] + 1,
                        confidence=0.90,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    )
                    edges.append(edge)

    return edges


def analyze_solidity(repo_root: Path) -> SolidityAnalysisResult:
    """Analyze Solidity files in a repository.

    Args:
        repo_root: Path to the repository root.

    Returns:
        SolidityAnalysisResult with symbols, edges, and analysis run info.
    """
    if not is_solidity_tree_sitter_available():
        return SolidityAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-solidity not installed. Install with: pip install tree-sitter-solidity",
        )

    # Import tree-sitter here to avoid import errors when not installed
    import tree_sitter
    import tree_sitter_solidity

    start_time = time.time()

    # Suppress deprecation warnings from tree-sitter
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        language = tree_sitter.Language(tree_sitter_solidity.language())
        parser = tree_sitter.Parser(language)

    run = AnalysisRun.create(
        pass_id=PASS_ID,
        version=PASS_VERSION,
    )

    # Find all Solidity files
    sol_files = list(find_solidity_files(repo_root))

    # Pass 1: Extract all symbols
    all_symbols: list[Symbol] = []
    global_symbols: dict[str, Symbol] = {}
    file_analyses: dict[Path, FileAnalysis] = {}

    for sol_file in sol_files:
        analysis = _extract_symbols_from_file(sol_file, parser, run)
        file_analyses[sol_file] = analysis
        all_symbols.extend(analysis.symbols)
        global_symbols.update(analysis.symbol_by_name)

    # Pass 2: Extract edges with cross-file resolution
    all_edges: list[Edge] = []
    for sol_file in sol_files:
        local_symbols = file_analyses[sol_file].symbol_by_name
        edges = _extract_edges_from_file(sol_file, parser, local_symbols, global_symbols, run)
        all_edges.extend(edges)

    # Update run with timing
    end_time = time.time()
    run.duration_ms = int((end_time - start_time) * 1000)

    return SolidityAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
