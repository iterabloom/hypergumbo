"""Markdown documentation analyzer using tree-sitter.

Markdown is the standard format for README files, documentation, and API
references in most software projects. Understanding documentation structure
helps with project navigation and knowledge extraction.

How It Works
------------
1. Uses tree-sitter-markdown grammar from tree-sitter-language-pack
2. Extracts document structure (sections, headings)
3. Identifies code blocks and links

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
import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "markdown.tree_sitter"
PASS_VERSION = "0.1.0"


class MarkdownAnalysisResult:
    """Result of markdown documentation analysis."""

    def __init__(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        run: AnalysisRun | None = None,
        skipped: bool = False,
        skip_reason: str = "",
    ) -> None:
        self.symbols = symbols
        self.edges = edges
        self.run = run
        self.skipped = skipped
        self.skip_reason = skip_reason


def is_markdown_tree_sitter_available() -> bool:
    """Check if tree-sitter-markdown is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("markdown")
        return True
    except Exception:  # pragma: no cover
        return False


def find_markdown_files(repo_root: Path) -> list[Path]:
    """Find all markdown files in the repository."""
    files = list(repo_root.glob("**/*.md"))
    files.extend(repo_root.glob("**/*.markdown"))
    return sorted(set(files))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    # Include line number for uniqueness (same heading can appear multiple times)
    return f"markdown:{path}:{kind}:{line}:{name[:30]}"


# Known documentation file patterns
DOCUMENTATION_FILES = {
    "readme", "readme.md", "readme.markdown",
    "changelog", "changelog.md",
    "contributing", "contributing.md",
    "license", "license.md",
    "install", "installation", "install.md", "installation.md",
    "api", "api.md",
    "usage", "usage.md",
    "faq", "faq.md",
    "getting-started", "getting-started.md",
    "quickstart", "quickstart.md",
}


class MarkdownAnalyzer:
    """Analyzer for markdown documentation files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0
        self._current_path: Path | None = None

    def analyze(self) -> MarkdownAnalysisResult:
        """Run the markdown analysis."""
        start_time = time.time()

        files = find_markdown_files(self.repo_root)
        if not files:
            return MarkdownAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("markdown")

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_path = path
                self._extract_symbols(tree.root_node, path)
                self._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._execution_id,
            version=PASS_VERSION,
            toolchain={"name": "markdown", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return MarkdownAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "atx_heading":
            self._extract_heading(node, path)
        elif node.type == "fenced_code_block":
            self._extract_code_block(node, path)
        elif node.type == "paragraph":
            # Extract links from paragraph inline content using regex
            self._extract_links_from_paragraph(node, path)

        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_heading(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a heading/section."""
        level = 0
        text = ""

        for child in node.children:
            if child.type.startswith("atx_h") and child.type.endswith("_marker"):
                # Count # characters for level
                level = len(_get_node_text(child).strip())
            elif child.type == "inline":
                text = _get_node_text(child).strip()

        if not text:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1
        symbol_id = _make_symbol_id(rel_path, text, "section", line)

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
            signature=f"{'#' * level} {text}",
            meta={
                "level": level,
                "is_api": "api" in text.lower() or "reference" in text.lower(),
                "is_usage": "usage" in text.lower() or "example" in text.lower(),
                "is_install": "install" in text.lower() or "setup" in text.lower(),
            },
        )
        self._symbols.append(symbol)

    def _extract_code_block(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a fenced code block."""
        language = ""
        content = ""

        for child in node.children:
            if child.type == "info_string":
                language = _get_node_text(child).strip().split()[0] if _get_node_text(child).strip() else ""
            elif child.type == "code_fence_content":
                content = _get_node_text(child)

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1
        symbol_id = _make_symbol_id(rel_path, f"code:{language or 'text'}", "code_block", line)

        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Count lines of code
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
            signature=f"```{language}" if language else "```",
            meta={
                "code_language": language,
                "lines_of_code": loc,
                "is_example": loc > 0,
            },
        )
        self._symbols.append(symbol)

    def _extract_links_from_paragraph(
        self, node: "tree_sitter.Node", path: Path
    ) -> None:
        """Extract links from paragraph content using regex."""
        # Get the inline content from the paragraph
        content = ""
        for child in node.children:
            if child.type == "inline":
                content = _get_node_text(child)
                break

        if not content:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        # Match markdown links: [text](url)
        link_pattern = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

        for match in link_pattern.finditer(content):
            text = match.group(1)
            url = match.group(2)

            symbol_id = _make_symbol_id(rel_path, text or url[:20], "link", line)

            span = Span(
                start_line=line,
                start_col=node.start_point[1] + match.start(),
                end_line=line,
                end_col=node.start_point[1] + match.end(),
            )

            # Categorize the link
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
                signature=f"[{text}]({url[:30]}{'...' if len(url) > 30 else ''})",
                meta={
                    "url": url,
                    "is_internal": is_internal,
                    "is_anchor": is_anchor,
                    "is_external": is_external,
                },
            )
            self._symbols.append(symbol)

            # Create edge for internal links
            if is_internal and self._current_path:
                edge = Edge.create(
                    src=symbol_id,
                    dst=url,
                    edge_type="links_to",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=self._execution_id,
                    evidence_type="link",
                    confidence=0.95,
                )
                self._edges.append(edge)


def analyze_markdown(repo_root: Path) -> MarkdownAnalysisResult:
    """Analyze markdown documentation files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        MarkdownAnalysisResult containing extracted symbols and edges
    """
    if not is_markdown_tree_sitter_available():
        warnings.warn(
            "Markdown analysis skipped: tree-sitter-markdown not available",
            UserWarning,
            stacklevel=2,
        )
        return MarkdownAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "markdown", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-markdown not available",
        )

    analyzer = MarkdownAnalyzer(repo_root)
    return analyzer.analyze()
