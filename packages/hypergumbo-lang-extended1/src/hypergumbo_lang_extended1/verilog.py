"""Verilog/SystemVerilog analysis pass using tree-sitter-verilog.

This analyzer uses tree-sitter to parse Verilog/SystemVerilog files and extract:
- Module definitions
- Interface definitions (SystemVerilog)
- Module instantiations
- Input/output ports
- Wire/register declarations

If tree-sitter-verilog is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all module/interface definitions
2. Pass 2: Resolve module instantiations and create edges

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Verilog-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Optional dependency keeps base install lightweight
- Uses tree-sitter-verilog package for grammar
- Two-pass allows cross-file module resolution
- Hardware-specific: modules, interfaces, instantiations are first-class
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    node_text,
    find_child_by_type,
    make_symbol_id,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = "verilog-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_verilog_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Verilog/SystemVerilog files in the repository."""
    yield from find_files(repo_root, ["*.v", "*.sv", "*.vh", "*.svh"])


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _extract_module_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract module name from module_declaration node."""
    # Look for module_header -> simple_identifier
    header = find_child_by_type(node, "module_header")
    if header:
        for child in header.children:
            if child.type == "simple_identifier":
                return node_text(child, source)
    return None  # pragma: no cover


def _extract_interface_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract interface name from interface_declaration node."""
    # Look for interface_ansi_header -> interface_identifier -> simple_identifier
    for child in node.children:
        if child.type in ("interface_ansi_header", "interface_nonansi_header"):
            for subchild in child.children:
                if subchild.type == "interface_identifier":
                    ident = find_child_by_type(subchild, "simple_identifier")
                    if ident:
                        return node_text(ident, source)
                elif subchild.type == "simple_identifier":  # pragma: no cover
                    return node_text(subchild, source)  # pragma: no cover
        elif child.type == "interface_identifier":  # pragma: no cover
            ident = find_child_by_type(child, "simple_identifier")  # pragma: no cover
            if ident:  # pragma: no cover
                return node_text(ident, source)  # pragma: no cover
        elif child.type == "simple_identifier":  # pragma: no cover
            return node_text(child, source)  # pragma: no cover
    return None  # pragma: no cover


def _extract_instantiation_info(node: "tree_sitter.Node", source: bytes) -> Optional[tuple[str, str]]:
    """Extract module type and instance name from module_instantiation.

    Returns:
        Tuple of (module_type, instance_name) or None if not found
    """
    module_type = None
    instance_name = None

    for child in node.children:
        if child.type == "simple_identifier" and module_type is None:
            module_type = node_text(child, source)
        elif child.type == "hierarchical_instance":
            # Look for name_of_instance -> instance_identifier -> simple_identifier
            name_of_inst = find_child_by_type(child, "name_of_instance")
            if name_of_inst:
                inst_ident = find_child_by_type(name_of_inst, "instance_identifier")
                if inst_ident:
                    ident = find_child_by_type(inst_ident, "simple_identifier")
                    if ident:
                        instance_name = node_text(ident, source)

    if module_type and instance_name:
        return (module_type, instance_name)
    return None  # pragma: no cover


def _find_containing_module(
    node: "tree_sitter.Node", module_by_pos: dict[tuple[int, int], str]
) -> Optional[str]:
    """Walk up parents to find the containing module's symbol ID."""
    current = node.parent
    while current is not None:
        pos_key = (current.start_byte, current.end_byte)
        if pos_key in module_by_pos:
            return module_by_pos[pos_key]
        current = current.parent
    return None  # pragma: no cover - defensive


class VerilogAnalyzer(TreeSitterAnalyzer):
    """Verilog/SystemVerilog language analyzer using tree-sitter-verilog."""

    lang = "verilog"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.v", "*.sv", "*.vh", "*.svh"]
    grammar_module = "tree_sitter_verilog"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract module, interface, and instantiation symbols from a Verilog file."""
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            if node.type == "module_declaration":
                module_name = _extract_module_name(node, source)
                if module_name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbol_id = make_symbol_id("verilog", rel_path, start_line, end_line, module_name, "module")

                    sym = Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=module_name,
                        fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                        kind="module",
                        name=module_name,
                        path=rel_path,
                        language="verilog",
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                    analysis.symbols.append(sym)
                    analysis.symbol_by_name[module_name] = sym

            elif node.type == "interface_declaration":
                interface_name = _extract_interface_name(node, source)
                if interface_name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbol_id = make_symbol_id("verilog", rel_path, start_line, end_line, interface_name, "interface")

                    sym = Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=interface_name,
                        fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                        kind="interface",
                        name=interface_name,
                        path=rel_path,
                        language="verilog",
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                    analysis.symbols.append(sym)
                    analysis.symbol_by_name[interface_name] = sym

        return analysis

    def register_symbol(self, symbol: Symbol, global_symbols: dict) -> None:
        """Register symbols by lowercase name for case-insensitive Verilog lookup."""
        global_symbols[symbol.name.lower()] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract module instantiation edges from a Verilog file."""
        edges: list[Edge] = []

        # Build module_by_pos for parent walking
        module_by_pos: dict[tuple[int, int], str] = {}
        for node in iter_tree(tree.root_node):
            if node.type == "module_declaration":
                module_name = _extract_module_name(node, source)
                if module_name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbol_id = make_symbol_id("verilog", rel_path, start_line, end_line, module_name, "module")
                    module_by_pos[(node.start_byte, node.end_byte)] = symbol_id

        for node in iter_tree(tree.root_node):
            if node.type == "module_instantiation":
                current_module_id = _find_containing_module(node, module_by_pos)
                inst_info = _extract_instantiation_info(node, source)
                if inst_info and current_module_id:
                    module_type, _instance_name = inst_info
                    start_line = node.start_point[0] + 1

                    if module_type.lower() in global_symbols:
                        dst_id = global_symbols[module_type.lower()].id
                    else:
                        dst_id = f"verilog:external:{module_type}:module"

                    edge = Edge(
                        id=_make_edge_id(current_module_id, dst_id, "instantiates"),
                        src=current_module_id,
                        dst=dst_id,
                        edge_type="instantiates",
                        line=start_line,
                        confidence=0.90 if module_type.lower() in global_symbols else 0.70,
                        origin=PASS_ID,
                        evidence_type="verilog_instantiation",
                    )
                    edges.append(edge)

        return edges


_analyzer = VerilogAnalyzer()


def is_verilog_tree_sitter_available() -> bool:
    """Check if tree-sitter with Verilog grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("verilog")
def analyze_verilog_files(repo_root: Path) -> AnalysisResult:
    """Analyze Verilog/SystemVerilog files in the repository."""
    return _analyzer.analyze(repo_root)
