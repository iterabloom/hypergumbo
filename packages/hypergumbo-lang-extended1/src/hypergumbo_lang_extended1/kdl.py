"""KDL (KDL Document Language) configuration analyzer using tree-sitter.

KDL is a modern document language designed for configuration files. It offers
a cleaner syntax than JSON or XML while being more structured than YAML.

How It Works
------------
Uses TreeSitterAnalyzer base class for single-pass orchestration:
1. Pass 1: Extract nodes with their arguments and properties
2. Identifies top-level configuration sections and nested structures

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the KDL-specific extraction
logic.

Symbols Extracted
-----------------
- **Nodes**: KDL nodes representing configuration entries
- **Sections**: Top-level nodes that contain children (configuration sections)

Why This Design
---------------
- KDL is gaining adoption as a configuration format
- Node hierarchy reveals configuration structure
- Properties and arguments capture configuration values
- Understanding nesting helps with configuration inheritance
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun


PASS_ID = make_pass_id("kdl")


def find_kdl_files(repo_root: Path) -> list[Path]:
    """Find all KDL configuration files in the repository."""
    files: list[Path] = []
    files.extend(find_files(repo_root, ["*.kdl"]))
    return sorted(set(files))


def is_kdl_tree_sitter_available() -> bool:
    """Check if tree-sitter-kdl is available."""
    return _analyzer._check_grammar_available()


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: str, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"kdl:{path}:{kind}:{line}:{name}"


def _extract_value(node: "tree_sitter.Node") -> str:
    """Extract a value from a value node."""
    for child in node.children:
        if child.type == "string":
            # Look for string_fragment
            for string_child in child.children:
                if string_child.type == "string_fragment":
                    return _get_node_text(string_child)
            # Fallback to full string text without quotes  # pragma: no cover
            text = _get_node_text(child)  # pragma: no cover
            if text.startswith('"') and text.endswith('"'):  # pragma: no cover
                return text[1:-1]  # pragma: no cover
        elif child.type == "keyword":
            # true, false, null
            return _get_node_text(child)
        elif child.type == "number":
            return _get_node_text(child)
    return ""  # pragma: no cover


def _extract_node_field(
    node: "tree_sitter.Node",
    arguments: list[str],
    properties: dict[str, str],
) -> None:
    """Extract arguments and properties from a node_field."""
    for child in node.children:
        if child.type == "value":
            # Anonymous argument
            value = _extract_value(child)
            if value:
                arguments.append(value)
        elif child.type == "prop":
            # Named property
            prop_name = ""
            prop_value = ""
            for prop_child in child.children:
                if prop_child.type == "identifier":
                    prop_name = _get_node_text(prop_child)
                elif prop_child.type == "value":
                    prop_value = _extract_value(prop_child)
            if prop_name:
                properties[prop_name] = prop_value


def _extract_kdl_node(
    node: "tree_sitter.Node", rel_path: str, depth: int,
    symbols_out: list[Symbol],
) -> None:
    """Extract a KDL node and recursively process children."""
    node_name = ""
    arguments: list[str] = []
    properties: dict[str, str] = {}
    has_children = False

    for child in node.children:
        if child.type == "identifier":
            node_name = _get_node_text(child)
        elif child.type == "node_field":
            _extract_node_field(child, arguments, properties)
        elif child.type == "node_children":
            has_children = True
            # Recursively process children
            for nested in child.children:
                if nested.type == "node":
                    _extract_kdl_node(nested, rel_path, depth + 1, symbols_out)

    if not node_name:
        return  # pragma: no cover

    line = node.start_point[0] + 1

    # Determine kind based on structure
    kind = "section" if has_children else "node"

    symbol_id = _make_symbol_id(rel_path, node_name, kind, line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # Build signature
    sig_parts = [node_name]
    if arguments:
        sig_parts.extend(f'"{arg}"' for arg in arguments[:3])
        if len(arguments) > 3:
            sig_parts.append("...")
    if properties:
        prop_strs = [f"{k}={v}" for k, v in list(properties.items())[:3]]
        sig_parts.extend(prop_strs)
        if len(properties) > 3:
            sig_parts.append("...")
    if has_children:
        sig_parts.append("{ ... }")

    signature = " ".join(sig_parts)

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=node_name,
        kind=kind,
        language="kdl",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=signature,
        meta={
            "depth": depth,
            "arg_count": len(arguments),
            "prop_count": len(properties),
            "has_children": has_children,
            "arguments": arguments[:5],  # Limit for brevity
            "properties": dict(list(properties.items())[:5]),
        },
    )
    symbols_out.append(symbol)


def _extract_kdl_symbols(
    node: "tree_sitter.Node", rel_path: str, depth: int,
    symbols_out: list[Symbol],
) -> None:
    """Extract symbols from a syntax tree node."""
    if node.type == "node":
        _extract_kdl_node(node, rel_path, depth, symbols_out)
    elif node.type == "document":
        # Process children of document
        for child in node.children:
            _extract_kdl_symbols(child, rel_path, depth, symbols_out)


class KdlAnalyzer(TreeSitterAnalyzer):
    """Analyzer for KDL configuration files using TreeSitterAnalyzer base class."""

    lang = "kdl"
    file_patterns: ClassVar[list[str]] = ["*.kdl"]
    language_pack_name = "kdl"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract KDL node symbols from a KDL file."""
        analysis = FileAnalysis()
        _extract_kdl_symbols(tree.root_node, rel_path, 0, analysis.symbols)
        return analysis


_analyzer = KdlAnalyzer()


@register_analyzer("kdl")
def analyze_kdl(repo_root: Path) -> AnalysisResult:
    """Analyze KDL configuration files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols
    """
    return _analyzer.analyze(repo_root)
