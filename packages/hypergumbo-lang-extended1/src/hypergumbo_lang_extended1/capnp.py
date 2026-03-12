# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cap'n Proto analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse .capnp files and extract:
- Struct definitions
- Interface definitions (RPC services)
- Method definitions (RPC methods)
- Enum definitions
- Const definitions
- Import relationships (using import)

If tree-sitter with Cap'n Proto support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for single-pass orchestration:
1. Parse all .capnp files and extract symbols
2. Detect import statements and create import edges
3. Create contains edges from interfaces to their methods

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Cap'n Proto-specific
extraction logic.

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Cap'n Proto grammar
- Cap'n Proto files define cross-language interfaces (like Proto/Thrift)
- Enables full-stack tracing for Cap'n Proto-based systems

Cap'n Proto-Specific Considerations
----------------------------------
- Cap'n Proto uses unique IDs (@0x...) for schema evolution
- Interfaces are like Proto services (contain RPC methods)
- Methods have parameters and return types
- Structs can be nested
- Uses positional field numbers (@N) like Proto
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
    make_symbol_id,
    make_file_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("capnp")


def find_capnp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Cap'n Proto files in the repository."""
    yield from find_files(repo_root, ["*.capnp"])


def is_capnp_tree_sitter_available() -> bool:
    """Check if tree-sitter with Cap'n Proto grammar is available."""
    return _analyzer._check_grammar_available()


def _extract_method_signature(method_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract method signature showing parameters and return types.

    Cap'n Proto method syntax:
        methodName @N (param1 :Type1, param2 :Type2) -> (retName :RetType);

    Returns signature like "(userId :Text) -> (user :User)".
    """
    params: Optional[str] = None
    returns: Optional[str] = None

    for child in method_node.children:
        if child.type == "method_parameters":
            params = node_text(child, source).strip()
        elif child.type == "return_type":
            returns = node_text(child, source).strip()

    sig = params or "()"
    if returns:
        sig += f" -> {returns}"
    return sig


def _get_struct_prefix(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to build the prefix for nested structs.

    Returns the prefix if this struct is nested inside other structs.
    """
    parts: list[str] = []
    current = node.parent
    while current is not None:
        # If parent is "nested_struct", its parent is "field", and field's parent is "struct"
        if current.type == "struct":
            name_node = find_child_by_type(current, "type_identifier")
            if name_node:
                parts.append(node_text(name_node, source).strip())
        current = current.parent
    if parts:
        # Reverse because we walked up the tree
        return ".".join(reversed(parts))
    return None  # pragma: no cover - defensive


def _get_interface_name_for_method(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up to find the interface containing this method."""
    current = node.parent
    while current is not None:
        if current.type == "interface":
            name_node = find_child_by_type(current, "type_identifier")
            if name_node:
                return node_text(name_node, source).strip()
        current = current.parent  # pragma: no cover - loop continuation
    return None  # pragma: no cover - defensive


def _extract_symbols_and_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> tuple[list[Symbol], list[Edge]]:
    """Extract all symbols and edges from a parsed Cap'n Proto file."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Track interface symbols for creating contains edges
    interface_symbols: dict[str, Symbol] = {}

    def make_symbol(
        node: "tree_sitter.Node",
        name: str,
        kind: str,
        prefix: Optional[str] = None,
        signature: Optional[str] = None,
    ) -> Symbol:
        """Create a Symbol from a tree-sitter node."""
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        start_col = node.start_point[1]
        end_col = node.end_point[1]

        # Build canonical name with prefix
        if prefix:
            canonical_name = f"{prefix}.{name}"
        else:
            canonical_name = name

        span = Span(
            start_line=start_line,
            end_line=end_line,
            start_col=start_col,
            end_col=end_col,
        )
        sym_id = make_symbol_id("capnp", file_path, start_line, end_line, name, kind)
        return Symbol(
            id=sym_id,
            name=name,
            canonical_name=canonical_name,
            kind=kind,
            language="capnp",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
        )

    # Process all nodes using iterative traversal
    for node in iter_tree(tree.root_node):
        if node.type == "struct":
            name_node = find_child_by_type(node, "type_identifier")
            if name_node:
                struct_name = node_text(name_node, source).strip()
                prefix = _get_struct_prefix(node, source)
                symbols.append(make_symbol(node, struct_name, "struct", prefix=prefix))

        elif node.type == "interface":
            name_node = find_child_by_type(node, "type_identifier")
            if name_node:
                interface_name = node_text(name_node, source).strip()
                interface_sym = make_symbol(node, interface_name, "interface")
                symbols.append(interface_sym)
                interface_symbols[interface_name] = interface_sym

        elif node.type == "method":
            method_name_node = find_child_by_type(node, "method_identifier")
            if method_name_node:
                method_name = node_text(method_name_node, source).strip()
                interface_name = _get_interface_name_for_method(node, source)
                method_sig = _extract_method_signature(node, source)
                method_sym = make_symbol(
                    node, method_name, "method",
                    prefix=interface_name,
                    signature=method_sig
                )
                symbols.append(method_sym)

                # Create contains edge from interface to method
                if interface_name and interface_name in interface_symbols:
                    interface_sym = interface_symbols[interface_name]
                    edges.append(Edge.create(
                        src=interface_sym.id,
                        dst=method_sym.id,
                        edge_type="contains",
                        line=method_sym.span.start_line,
                    ))

        elif node.type == "enum":
            name_node = find_child_by_type(node, "enum_identifier")
            if name_node:
                enum_name = node_text(name_node, source).strip()
                symbols.append(make_symbol(node, enum_name, "enum"))

        elif node.type == "const":
            name_node = find_child_by_type(node, "const_identifier")
            if name_node:
                const_name = node_text(name_node, source).strip()
                symbols.append(make_symbol(node, const_name, "const"))

        elif node.type == "using_directive":
            # Structure: using_directive > import_using > import_path > string_fragment
            for child in node.children:
                if child.type == "import_using":
                    for import_child in child.children:
                        if import_child.type == "import_path":
                            for path_child in import_child.children:
                                if path_child.type == "string_fragment":
                                    import_path = node_text(path_child, source).strip()
                                    edges.append(Edge.create(
                                        src=make_file_id("capnp", file_path),
                                        dst=f"capnp:{import_path}:1-1:file:file",
                                        edge_type="imports",
                                        line=node.start_point[0] + 1,
                                    ))

    return symbols, edges


class CapnpAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Cap'n Proto files using TreeSitterAnalyzer base class."""

    lang = "capnp"
    file_patterns: ClassVar[list[str]] = ["*.capnp"]
    language_pack_name = "capnp"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract Cap'n Proto symbols (structs, interfaces, methods, etc.)."""
        analysis = FileAnalysis()
        symbols, edges = _extract_symbols_and_edges(tree, source, rel_path, run.execution_id)
        analysis.symbols.extend(symbols)
        # Store edge data for Pass 2 retrieval via import_aliases
        for i, edge in enumerate(edges):
            analysis.import_aliases[f"__edge_{i}__"] = (
                f"{edge.src}|{edge.dst}|{edge.edge_type}|{edge.line}"
            )
        # Register callable symbols by name
        for sym in symbols:
            if sym.kind in ("method", "interface"):
                analysis.symbol_by_name[sym.name] = sym
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Re-extract edges from Cap'n Proto file during Pass 2."""
        _, edges = _extract_symbols_and_edges(tree, source, rel_path, run.execution_id)
        return edges


_analyzer = CapnpAnalyzer()


@register_analyzer("capnp")
def analyze_capnp(repo_root: Path) -> AnalysisResult:
    """Analyze all Cap'n Proto files in the repository.

    Args:
        repo_root: Path to the repository root.

    Returns:
        AnalysisResult with symbols and edges found.
    """
    return _analyzer.analyze(repo_root)
