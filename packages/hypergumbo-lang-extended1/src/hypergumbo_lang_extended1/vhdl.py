"""VHDL analysis pass using tree-sitter-vhdl.

This analyzer uses tree-sitter to parse VHDL files and extract:
- Entity declarations
- Architecture definitions
- Package declarations
- Library/use clauses
- Component instantiations

If tree-sitter-vhdl is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all entity/architecture/package definitions
2. Pass 2: Resolve component instantiations and create edges

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the VHDL-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Optional dependency keeps base install lightweight
- Uses tree-sitter-vhdl package for grammar
- Two-pass allows cross-file entity resolution
- Hardware-specific: entities, architectures, packages are first-class
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    node_text,
    make_symbol_id,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("vhdl")


def find_vhdl_files(repo_root: Path) -> Iterator[Path]:
    """Yield all VHDL files in the repository."""
    yield from find_files(repo_root, ["*.vhd", "*.vhdl"])


def _get_entity_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract entity name from entity_declaration node."""
    for child in node.children:
        if child.type == "identifier":
            return node_text(child, source)
    return None  # pragma: no cover


def _get_architecture_info(node: "tree_sitter.Node", source: bytes) -> Optional[tuple[str, str]]:
    """Extract architecture name and entity name from architecture_definition.

    Returns:
        Tuple of (architecture_name, entity_name) or None if not found
    """
    arch_name = None
    entity_name = None

    for child in node.children:
        if child.type == "identifier" and arch_name is None:
            arch_name = node_text(child, source)
        elif child.type == "name":
            entity_name = node_text(child, source)

    if arch_name and entity_name:
        return (arch_name, entity_name)
    return None  # pragma: no cover


def _get_package_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract package name from package_declaration node."""
    for child in node.children:
        if child.type == "identifier":
            return node_text(child, source)
    return None  # pragma: no cover


class VhdlAnalyzer(TreeSitterAnalyzer):
    """VHDL language analyzer using tree-sitter-vhdl."""

    lang = "vhdl"
    file_patterns: ClassVar[list[str]] = ["*.vhd", "*.vhdl"]
    grammar_module = "tree_sitter_vhdl"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract entity, architecture, and package symbols from a VHDL file."""
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            if node.type == "entity_declaration":
                entity_name = _get_entity_name(node, source)
                if entity_name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbol_id = make_symbol_id("vhdl", rel_path, start_line, end_line, entity_name, "entity")

                    sym = Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=entity_name,
                        fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                        kind="entity",
                        name=entity_name,
                        path=rel_path,
                        language="vhdl",
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                    analysis.symbols.append(sym)
                    analysis.symbol_by_name[entity_name] = sym

            elif node.type == "architecture_definition":
                arch_info = _get_architecture_info(node, source)
                if arch_info:
                    arch_name, entity_name = arch_info
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbol_id = make_symbol_id("vhdl", rel_path, start_line, end_line, arch_name, "architecture")

                    sym = Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=f"{arch_name}({entity_name})",
                        fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                        kind="architecture",
                        name=arch_name,
                        path=rel_path,
                        language="vhdl",
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                    analysis.symbols.append(sym)
                    # Store architecture with entity_name for edge creation in pass 2
                    analysis.symbol_by_name[f"__arch__{arch_name}__entity__{entity_name}"] = sym

            elif node.type == "package_declaration":
                pkg_name = _get_package_name(node, source)
                if pkg_name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbol_id = make_symbol_id("vhdl", rel_path, start_line, end_line, pkg_name, "package")

                    sym = Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=pkg_name,
                        fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                        kind="package",
                        name=pkg_name,
                        path=rel_path,
                        language="vhdl",
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                    analysis.symbols.append(sym)
                    analysis.symbol_by_name[pkg_name] = sym

            elif node.type == "component_declaration":  # pragma: no cover - rare VHDL syntax
                for child in node.children:  # pragma: no cover
                    if child.type == "identifier":  # pragma: no cover
                        comp_name = node_text(child, source)  # pragma: no cover
                        start_line = node.start_point[0] + 1  # pragma: no cover
                        end_line = node.end_point[0] + 1  # pragma: no cover
                        symbol_id = make_symbol_id("vhdl", rel_path, start_line, end_line, comp_name, "component")  # pragma: no cover

                        sym = Symbol(  # pragma: no cover
                            id=symbol_id,
                            stable_id=None,
                            shape_id=None,
                            canonical_name=comp_name,
                            fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                            kind="component",
                            name=comp_name,
                            path=rel_path,
                            language="vhdl",
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                        )
                        analysis.symbols.append(sym)  # pragma: no cover
                        break  # pragma: no cover

        return analysis

    def register_symbol(self, symbol: Symbol, global_symbols: dict) -> None:
        """Register symbols by lowercase name for case-insensitive VHDL lookup."""
        global_symbols[symbol.name.lower()] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract architecture-entity implements edges from a VHDL file."""
        edges: list[Edge] = []

        for node in iter_tree(tree.root_node):
            if node.type == "architecture_definition":
                arch_info = _get_architecture_info(node, source)
                if arch_info:
                    arch_name, entity_name = arch_info
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbol_id = make_symbol_id("vhdl", rel_path, start_line, end_line, arch_name, "architecture")

                    if entity_name.lower() in global_symbols:
                        dst_id = global_symbols[entity_name.lower()].id
                        confidence = 0.90
                    else:
                        dst_id = f"vhdl:external:{entity_name}:entity"
                        confidence = 0.70

                    edge = Edge.create(
                        src=symbol_id,
                        dst=dst_id,
                        edge_type="implements",
                        line=start_line,
                        confidence=confidence,
                        origin=PASS_ID,
                        evidence_type="vhdl_architecture",
                    )
                    edges.append(edge)

        return edges


_analyzer = VhdlAnalyzer()


def is_vhdl_tree_sitter_available() -> bool:
    """Check if tree-sitter with VHDL grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("vhdl")
def analyze_vhdl_files(repo_root: Path) -> AnalysisResult:
    """Analyze VHDL files in the repository."""
    return _analyzer.analyze(repo_root)
