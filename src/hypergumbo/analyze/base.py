"""Base classes and utilities for language analyzers.

This module provides shared infrastructure for all language analyzers,
eliminating duplication across 65+ analyzer files.

Shared Components
-----------------
- **AnalysisResult**: Universal result type returned by all analyzers
- **FileAnalysis**: Intermediate per-file analysis result
- **Tree-sitter helpers**: node_text, find_child_by_type, find_child_by_field
- **ID generation**: make_symbol_id, make_file_id
- **Availability checking**: is_grammar_available

Why This Design
---------------
Previously, each analyzer duplicated these components. This led to:
- 65+ copies of identical dataclasses
- Inconsistent helper implementations
- High maintenance burden when adding new analyzers

Now, analyzers import from this module and focus only on
language-specific parsing logic.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Symbol

if TYPE_CHECKING:
    import tree_sitter


@dataclass
class AnalysisResult:
    """Universal result type for all language analyzers.

    This replaces the per-language XxxAnalysisResult dataclasses
    (GoAnalysisResult, RustAnalysisResult, etc.) which were all identical.

    Attributes:
        symbols: List of detected symbols (functions, classes, etc.)
        edges: List of relationships between symbols (calls, imports, etc.)
        run: Provenance tracking for the analysis pass
        skipped: Whether the analysis was skipped (e.g., missing dependency)
        skip_reason: Human-readable reason for skipping
    """

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single source file.

    Used during two-pass analysis: first pass collects symbols,
    second pass resolves cross-file references using the symbol registry.

    Attributes:
        symbols: Symbols detected in this file
        symbol_by_name: Quick lookup by symbol name for edge resolution
    """

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tree-sitter helper functions
# ---------------------------------------------------------------------------


def node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text content for a tree-sitter node.

    Args:
        node: A tree-sitter node
        source: Source file bytes

    Returns:
        The text content of the node, decoded as UTF-8.
    """
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def find_child_by_type(
    node: "tree_sitter.Node", type_name: str
) -> Optional["tree_sitter.Node"]:
    """Find the first child node of a given type.

    Args:
        node: Parent tree-sitter node
        type_name: The node type to search for

    Returns:
        The first matching child, or None if not found.
    """
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def find_child_by_field(
    node: "tree_sitter.Node", field_name: str
) -> Optional["tree_sitter.Node"]:
    """Find a child node by field name.

    Args:
        node: Parent tree-sitter node
        field_name: The field name to look up

    Returns:
        The child at that field, or None if not found.
    """
    return node.child_by_field_name(field_name)


# ---------------------------------------------------------------------------
# ID generation helpers
# ---------------------------------------------------------------------------


def make_symbol_id(
    lang: str, path: str, start_line: int, end_line: int, name: str, kind: str
) -> str:
    """Generate a location-based symbol ID.

    Format: {lang}:{path}:{start}-{end}:{name}:{kind}

    Args:
        lang: Language identifier (e.g., "go", "rust", "python")
        path: File path
        start_line: Starting line number
        end_line: Ending line number
        name: Symbol name
        kind: Symbol kind (function, class, etc.)

    Returns:
        A unique, location-based symbol ID.
    """
    return f"{lang}:{path}:{start_line}-{end_line}:{name}:{kind}"


def make_file_id(lang: str, path: str) -> str:
    """Generate an ID for a file node (used as import edge source).

    Args:
        lang: Language identifier
        path: File path

    Returns:
        A file-level symbol ID.
    """
    return f"{lang}:{path}:1-1:file:file"


# ---------------------------------------------------------------------------
# Grammar availability checking
# ---------------------------------------------------------------------------


def is_grammar_available(grammar_module: str) -> bool:
    """Check if a tree-sitter grammar is available.

    Args:
        grammar_module: The grammar module name (e.g., "tree_sitter_go")

    Returns:
        True if both tree_sitter and the grammar module are importable.
    """
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec(grammar_module) is None:
        return False
    return True


# ---------------------------------------------------------------------------
# File discovery helpers
# ---------------------------------------------------------------------------


def make_file_finder(patterns: list[str]) -> Callable[[Path], Iterator[Path]]:  # pragma: no cover
    """Create a file finder function for specific patterns.

    Args:
        patterns: Glob patterns to match (e.g., ["*.go"], ["*.rs"])

    Returns:
        A function that yields matching files from a repo root.
    """

    def finder(repo_root: Path) -> Iterator[Path]:
        yield from find_files(repo_root, patterns)

    return finder
