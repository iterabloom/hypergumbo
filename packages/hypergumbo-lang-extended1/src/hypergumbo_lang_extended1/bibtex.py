"""BibTeX bibliography analyzer using tree-sitter.

BibTeX is the standard bibliography format for LaTeX documents, widely used
in academic writing. Understanding BibTeX structure helps with reference
management and citation analysis.

How It Works
------------
Uses TreeSitterAnalyzer base class for single-pass orchestration:
1. Pass 1: Extract bibliography entries with their fields
2. Categorizes entries by type (article, book, inproceedings, etc.)

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the BibTeX-specific extraction
logic.

Symbols Extracted
-----------------
- **Entries**: Bibliography entries (@article, @book, @inproceedings, etc.)

Why This Design
---------------
- BibTeX is the standard for academic references
- Entry types reveal document types being cited
- Fields like author, year, journal provide metadata
- Citation keys enable cross-reference analysis
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


PASS_ID = make_pass_id("bibtex")


def find_bibtex_files(repo_root: Path) -> list[Path]:
    """Find all BibTeX bibliography files in the repository."""
    files: list[Path] = []
    files.extend(find_files(repo_root, ["*.bib"]))
    files.extend(find_files(repo_root, ["*.bibtex"]))
    return sorted(set(files))


def is_bibtex_tree_sitter_available() -> bool:
    """Check if tree-sitter-bibtex is available."""
    return _analyzer._check_grammar_available()


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, key: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"bibtex:{path}:{kind}:{line}:{key}"


def _extract_entry(
    node: "tree_sitter.Node", rel_path: str, run_id: str,
) -> Symbol | None:
    """Extract a bibliography entry as a Symbol."""
    entry_type = ""
    citation_key = ""
    fields: dict[str, str] = {}

    for child in node.children:
        if child.type == "entry_type":
            entry_type = _get_node_text(child).lstrip("@").lower()
        elif child.type == "key_brace" or child.type == "key_paren":
            citation_key = _get_node_text(child)
        elif child.type == "field":
            field_name = ""
            field_value = ""
            for field_child in child.children:
                if field_child.type == "identifier":
                    field_name = _get_node_text(field_child).lower()
                elif field_child.type == "value":
                    field_value = _get_node_text(field_child).strip("{}")
            if field_name:
                fields[field_name] = field_value

    if not citation_key:
        return None  # pragma: no cover

    line = node.start_point[0] + 1

    symbol_id = _make_symbol_id(rel_path, citation_key, "entry", line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # Build a human-readable signature
    author = fields.get("author", "Unknown")
    year = fields.get("year", "")
    title = fields.get("title", "")
    # Truncate long titles
    if len(title) > 50:
        title = title[:47] + "..."

    signature = f"@{entry_type}{{{citation_key}}}"

    return Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=citation_key,
        kind="entry",
        language="bibtex",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=signature,
        meta={
            "entry_type": entry_type,
            "author": author,
            "year": year,
            "title": title,
            "field_count": len(fields),
        },
    )


class BibtexAnalyzer(TreeSitterAnalyzer):
    """Analyzer for BibTeX bibliography files using TreeSitterAnalyzer base class."""

    lang = "bibtex"
    file_patterns: ClassVar[list[str]] = ["*.bib", "*.bibtex"]
    language_pack_name = "bibtex"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract bibliography entry symbols from a BibTeX file."""
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            if node.type == "entry":
                sym = _extract_entry(node, rel_path, run.execution_id)
                if sym:
                    analysis.symbols.append(sym)

        return analysis


_analyzer = BibtexAnalyzer()


@register_analyzer("bibtex")
def analyze_bibtex(repo_root: Path) -> AnalysisResult:
    """Analyze BibTeX bibliography files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols
    """
    return _analyzer.analyze(repo_root)
