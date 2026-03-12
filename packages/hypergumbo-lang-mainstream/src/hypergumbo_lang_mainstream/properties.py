# SPDX-License-Identifier: AGPL-3.0-or-later
"""Java properties file analyzer using tree-sitter.

Java .properties files are key-value configuration files used extensively
in Java applications, Android development, and i18n/l10n bundles.

How It Works
------------
Uses TreeSitterAnalyzer base class for single-pass orchestration:
1. Pass 1: Extract property key-value pairs with namespace categorization
2. No Pass 2 (properties files have no cross-references)

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the properties-specific
extraction logic.

Symbols Extracted
-----------------
- **Properties**: Key-value configuration entries

Edges Extracted
---------------
- None (properties files are typically self-contained)

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- .properties files are ubiquitous in Java ecosystem
- Configuration values reveal application behavior
- Namespace prefixes indicate logical groupings
- i18n bundles contain user-facing strings
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
    iter_tree,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun


PASS_ID = make_pass_id("properties")


def find_properties_files(repo_root: Path) -> list[Path]:
    """Find all properties files in the repository."""
    return sorted(find_files(repo_root, ["*.properties"]))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node.

    Uses errors="replace" because .properties files commonly use Latin-1/
    ISO-8859-1 encoding (Java default), not UTF-8.
    """
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str) -> str:
    """Create a stable symbol ID."""
    return f"properties:{path}:{kind}:{name}"


# Common property prefixes and their meanings
KNOWN_PREFIXES = {
    "database": "database",
    "db": "database",
    "spring": "framework",
    "server": "server",
    "logging": "logging",
    "log": "logging",
    "app": "application",
    "application": "application",
    "mail": "mail",
    "smtp": "mail",
    "security": "security",
    "auth": "security",
    "cache": "cache",
    "redis": "cache",
    "jpa": "persistence",
    "hibernate": "persistence",
    "kafka": "messaging",
    "rabbitmq": "messaging",
    "aws": "cloud",
    "azure": "cloud",
    "gcp": "cloud",
}


def _extract_property_symbols(
    tree: "tree_sitter.Tree",
    rel_path: str,
) -> list[Symbol]:
    """Extract property symbols from a parsed properties file.

    Args:
        tree: Parsed tree-sitter tree
        rel_path: Path relative to repo root

    Returns:
        List of property Symbol objects.
    """
    symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        if node.type != "property":
            continue

        key = ""
        value = ""

        for child in node.children:
            if child.type == "key":
                key = _get_node_text(child)
            elif child.type == "value":
                value = _get_node_text(child)

        if not key:
            continue  # pragma: no cover

        symbol_id = _make_symbol_id(Path(rel_path), key, "property")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Extract prefix/namespace from key
        prefix = key.split(".")[0].lower() if "." in key else ""
        category = KNOWN_PREFIXES.get(prefix, "")

        # Determine if this is a sensitive key
        is_sensitive = any(
            s in key.lower()
            for s in ("password", "secret", "token", "key", "credential")
        )

        # Truncate long values in signature
        sig_value = value[:30] + "..." if len(value) > 30 else value
        if is_sensitive and value:
            sig_value = "***"

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=key,
            kind="property",
            language="properties",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"{key}={sig_value}" if value else key,
            meta={
                "value": value if not is_sensitive else "***",
                "prefix": prefix,
                "category": category,
                "is_sensitive": is_sensitive,
            },
        )
        symbols.append(symbol)

    return symbols


class PropertiesAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Java properties files using tree-sitter-language-pack."""

    lang = "properties"
    file_patterns: ClassVar[list[str]] = ["*.properties"]
    language_pack_name = "properties"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract property key-value pairs from a properties file."""
        analysis = FileAnalysis()
        analysis.symbols.extend(_extract_property_symbols(tree, rel_path))
        return analysis


_analyzer = PropertiesAnalyzer()


def is_properties_tree_sitter_available() -> bool:
    """Check if tree-sitter-properties is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("properties")
def analyze_properties(repo_root: Path) -> AnalysisResult:
    """Analyze Java properties files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
