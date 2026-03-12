# SPDX-License-Identifier: AGPL-3.0-or-later
"""Markdown documentation analyzer using tree-sitter.

Markdown is the standard format for README files, documentation, and API
references in most software projects. Understanding documentation structure
helps with project navigation and knowledge extraction.

How It Works
------------
Uses the TreeSitterAnalyzer base class with the tree-sitter-markdown grammar
from tree-sitter-language-pack. Single-pass for symbols; edges (internal links)
are collected during symbol extraction and flushed via post_process.

1. extract_symbols_from_file: walks AST to find headings, code blocks, and
   paragraph links (using regex on paragraph inline content)
2. post_process: flushes accumulated link edges into the final edge list
3. _find_source_files: overridden for *.md + *.markdown with dedup

Symbols Extracted
-----------------
- **Sections**: Document sections with heading levels (h1-h6)
- **Code blocks**: Fenced code blocks with language annotations
- **Links**: Internal and external links

Edges Extracted
---------------
- **links_to**: Links from document to referenced targets

Why This Design
---------------
- README.md is often the first file developers read
- Section structure reveals documentation organization
- Code blocks show examples and usage patterns
- Links indicate related documentation and resources
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = make_pass_id("markdown")

# Regex for markdown links: [text](url)
_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def find_markdown_files(repo_root: Path) -> list[Path]:
    """Find all markdown files in the repository."""
    files = list(find_files(repo_root, ["*.md"]))
    files.extend(find_files(repo_root, ["*.markdown"]))
    return sorted(set(files))


class MarkdownAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based markdown documentation analyzer.

    Uses tree-sitter-markdown from the language-pack to extract document
    structure: headings (sections), fenced code blocks, and inline links.

    Edges for internal links are collected during symbol extraction (Pass 1)
    and flushed into the result via ``post_process``, since link symbols and
    their edges are tightly coupled (the symbol ID is the edge source).

    Overrides ``_find_source_files`` because markdown files use two extensions
    (*.md and *.markdown) that need deduplication.
    """

    lang = "markdown"
    file_patterns: ClassVar[list[str]] = ["*.md", "*.markdown"]
    language_pack_name = "markdown"

    def analyze(
        self, repo_root: Path, max_files: Optional[int] = None
    ) -> AnalysisResult:
        """Reset pending edges and delegate to the base class."""
        self._pending_edges: list[Edge] = []
        return super().analyze(repo_root, max_files)

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Yield markdown files (*.md and *.markdown, deduplicated)."""
        yield from find_markdown_files(repo_root)

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract headings, code blocks, and links from a markdown file.

        Walks the AST iteratively. For paragraph nodes, uses regex to extract
        inline links. Internal link edges are accumulated on self._pending_edges
        for flushing in post_process.
        """
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            if node.type == "atx_heading":
                self._extract_heading(node, source, rel_path, run, analysis)
            elif node.type == "fenced_code_block":
                self._extract_code_block(node, source, rel_path, run, analysis)
            elif node.type == "paragraph":
                self._extract_links_from_paragraph(
                    node, source, rel_path, run, analysis,
                )

        return analysis

    def post_process(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        usage_contexts: list,
        run: AnalysisRun,
    ) -> tuple[list[Symbol], list[Edge], list]:
        """Flush accumulated link edges into the final edge list."""
        edges.extend(self._pending_edges)
        return symbols, edges, usage_contexts

    # -- Extraction helpers ---------------------------------------------------

    def _extract_heading(
        self,
        node: "tree_sitter.Node",
        source: bytes,
        rel_path: str,
        run: AnalysisRun,
        analysis: FileAnalysis,
    ) -> None:
        """Extract a heading/section symbol."""
        level = 0
        text = ""

        for child in node.children:
            if child.type.startswith("atx_h") and child.type.endswith("_marker"):
                level = len(node_text(child, source).strip())
            elif child.type == "inline":
                text = node_text(child, source).strip()

        if not text:
            return  # pragma: no cover

        line = node.start_point[0] + 1
        symbol_id = make_symbol_id(
            "markdown", rel_path, line, node.end_point[0] + 1, text, "section",
        )

        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=text,
            kind="section",
            language="markdown",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            signature=f"{'#' * level} {text}",
            meta={
                "level": level,
                "is_api": "api" in text.lower() or "reference" in text.lower(),
                "is_usage": "usage" in text.lower() or "example" in text.lower(),
                "is_install": "install" in text.lower() or "setup" in text.lower(),
            },
        )
        analysis.symbols.append(symbol)

    def _extract_code_block(
        self,
        node: "tree_sitter.Node",
        source: bytes,
        rel_path: str,
        run: AnalysisRun,
        analysis: FileAnalysis,
    ) -> None:
        """Extract a fenced code block symbol."""
        language = ""
        content = ""

        for child in node.children:
            if child.type == "info_string":
                info = node_text(child, source).strip()
                language = info.split()[0] if info else ""
            elif child.type == "code_fence_content":
                content = node_text(child, source)

        line = node.start_point[0] + 1
        symbol_id = make_symbol_id(
            "markdown", rel_path, line, node.end_point[0] + 1,
            f"code:{language or 'text'}", "code_block",
        )

        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        loc = len(content.strip().split("\n")) if content.strip() else 0

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=f"code:{language}" if language else "code",
            kind="code_block",
            language="markdown",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            signature=f"```{language}" if language else "```",
            meta={
                "code_language": language,
                "lines_of_code": loc,
                "is_example": loc > 0,
            },
        )
        analysis.symbols.append(symbol)

    def _extract_links_from_paragraph(
        self,
        node: "tree_sitter.Node",
        source: bytes,
        rel_path: str,
        run: AnalysisRun,
        analysis: FileAnalysis,
    ) -> None:
        """Extract links from paragraph content using regex.

        Creates link symbols for all links, and edges for internal links.
        Edges are accumulated on self._pending_edges for post_process.
        """
        content = ""
        for child in node.children:
            if child.type == "inline":
                content = node_text(child, source)
                break

        if not content:
            return  # pragma: no cover

        line = node.start_point[0] + 1

        for match in _LINK_PATTERN.finditer(content):
            text = match.group(1)
            url = match.group(2)

            symbol_id = make_symbol_id(
                "markdown", rel_path, line, line,
                text or url[:20], "link",
            )

            span = Span(
                start_line=line,
                start_col=node.start_point[1] + match.start(),
                end_line=line,
                end_col=node.start_point[1] + match.end(),
            )

            is_internal = url.startswith("./") or url.startswith("../") or (
                not url.startswith("http") and not url.startswith("#")
            )
            is_anchor = url.startswith("#")
            is_external = url.startswith("http://") or url.startswith("https://")

            symbol = Symbol(
                id=symbol_id,
                stable_id=symbol_id,
                name=text or url,
                kind="link",
                language="markdown",
                path=str(rel_path),
                span=span,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                signature=f"[{text}]({url[:30]}{'...' if len(url) > 30 else ''})",
                meta={
                    "url": url,
                    "is_internal": is_internal,
                    "is_anchor": is_anchor,
                    "is_external": is_external,
                },
            )
            analysis.symbols.append(symbol)

            if is_internal:
                edge = Edge.create(
                    src=symbol_id,
                    dst=url,
                    edge_type="links_to",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="link",
                    confidence=0.95,
                )
                self._pending_edges.append(edge)


_analyzer = MarkdownAnalyzer()


def is_markdown_tree_sitter_available() -> bool:
    """Check if tree-sitter-markdown is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("markdown")
def analyze_markdown(repo_root: Path) -> AnalysisResult:
    """Analyze markdown documentation files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
