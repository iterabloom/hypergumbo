"""BibTeX bibliography analyzer using tree-sitter.

BibTeX is the standard bibliography format for LaTeX documents, widely used
in academic writing. Understanding BibTeX structure helps with reference
management and citation analysis.

How It Works
------------
1. Uses tree-sitter-bibtex grammar from tree-sitter-language-pack
2. Extracts bibliography entries with their fields
3. Categorizes entries by type (article, book, inproceedings, etc.)

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

import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "bibtex.tree_sitter"
PASS_VERSION = "0.1.0"


class BibtexAnalysisResult:
    """Result of BibTeX bibliography analysis."""

    def __init__(
        self,
        symbols: list[Symbol],
        run: AnalysisRun | None = None,
        skipped: bool = False,
        skip_reason: str = "",
    ) -> None:
        self.symbols = symbols
        self.edges: list = []  # BibTeX files typically don't have edges
        self.run = run
        self.skipped = skipped
        self.skip_reason = skip_reason


def is_bibtex_tree_sitter_available() -> bool:
    """Check if tree-sitter-bibtex is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("bibtex")
        return True
    except Exception:  # pragma: no cover
        return False


def find_bibtex_files(repo_root: Path) -> list[Path]:
    """Find all BibTeX bibliography files in the repository."""
    files: list[Path] = []
    files.extend(repo_root.glob("**/*.bib"))
    files.extend(repo_root.glob("**/*.bibtex"))
    return sorted(set(files))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, key: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"bibtex:{path}:{kind}:{line}:{key}"


class BibtexAnalyzer:
    """Analyzer for BibTeX bibliography files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0

    def analyze(self) -> BibtexAnalysisResult:
        """Run the BibTeX analysis."""
        start_time = time.time()

        files = find_bibtex_files(self.repo_root)
        if not files:
            return BibtexAnalysisResult(
                symbols=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("bibtex")

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path)
                self._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._execution_id,
            version=PASS_VERSION,
            toolchain={"name": "bibtex", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return BibtexAnalysisResult(
            symbols=self._symbols,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "entry":
            self._extract_entry(node, path)

        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_entry(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a bibliography entry."""
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
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
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

        symbol = Symbol(
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
        self._symbols.append(symbol)


def analyze_bibtex(repo_root: Path) -> BibtexAnalysisResult:
    """Analyze BibTeX bibliography files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        BibtexAnalysisResult containing extracted symbols
    """
    if not is_bibtex_tree_sitter_available():
        warnings.warn(
            "BibTeX analysis skipped: tree-sitter-bibtex not available",
            UserWarning,
            stacklevel=2,
        )
        return BibtexAnalysisResult(
            symbols=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "bibtex", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-bibtex not available",
        )

    analyzer = BibtexAnalyzer(repo_root)
    return analyzer.analyze()
