"""HTML script tag analysis pass.

This analyzer uses regex pattern matching to detect <script src="...">
tags in HTML files, creating edges from HTML documents to their
referenced JavaScript files.

How It Works
------------
1. Find all .html and .htm files in the repository
2. For each file, create a file-level symbol
3. Scan content with regex for <script src="..."> patterns
4. Create script_src edges from the HTML file to referenced scripts
5. Track line numbers for accurate source mapping

The regex pattern handles both single and double quotes, and is
case-insensitive to match HTML conventions.

Detected Patterns
-----------------
- <script src="path/to/file.js">
- <script src='path/to/file.js'>
- <script type="module" src="...">

Edge Destinations
-----------------
Script references create edges to synthetic IDs like:
  javascript:path/to/file.js:0-0:ref:script

These reference IDs may not correspond to analyzed symbols (external
CDN scripts, etc.), but enable graph construction when the script
is also analyzed.

Why This Design
---------------
- Regex is sufficient for this simple pattern (no need for HTML parser)
- File-level symbols enable graph connectivity from HTML entry points
- High confidence (0.95) reflects reliability of static <script> tags
- Reference IDs allow graceful handling of external/missing scripts
"""
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol

PASS_ID = "html-pattern-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Regex to match <script src="..."> or <script src='...'>
SCRIPT_SRC_PATTERN = re.compile(
    r'<script\s+[^>]*src\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE
)


def find_html_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all HTML files in the repository, excluding common non-source dirs."""
    yield from find_files(repo_root, ["*.html", "*.htm"], max_files=max_files)


def _make_file_id(path: str) -> str:
    """Generate ID for an HTML file node."""
    return f"html:{path}:1-1:file:file"


@dataclass
class HtmlAnalysisResult:
    """Result of analyzing HTML files."""

    symbols: list[Symbol]
    edges: list[Edge]
    run: AnalysisRun | None = None


def analyze_html(
    repo_root: Path, max_files: int | None = None
) -> HtmlAnalysisResult:
    """
    Analyze all HTML files in a repository for script tags.

    Returns symbols for HTML files and edges for script references.

    Args:
        repo_root: Root directory of the repository
        max_files: Optional limit on number of files to analyze
    """
    start_time = time.time()

    # Create analysis run for provenance tracking
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    files_skipped = 0

    for html_file in find_html_files(repo_root, max_files=max_files):
        try:
            content = html_file.read_text(errors="ignore")
            files_analyzed += 1
        except (OSError, IOError):
            files_skipped += 1
            continue

        # Count lines for span info
        lines = content.split("\n")
        total_lines = len(lines)

        # Create a file node for the HTML file
        file_id = _make_file_id(str(html_file))
        span = Span(start_line=1, end_line=total_lines, start_col=0, end_col=0)
        file_symbol = Symbol(
            id=file_id,
            name=html_file.name,
            kind="file",
            language="html",
            path=str(html_file),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
        )
        symbols.append(file_symbol)

        # Find all script src references
        for match in SCRIPT_SRC_PATTERN.finditer(content):
            script_src = match.group(1)

            # Find line number of this match
            char_pos = match.start()
            line_num = content[:char_pos].count("\n") + 1

            # Create edge from HTML file to script
            # The dst is a reference ID (the script may not exist in our analysis)
            script_ref_id = f"javascript:{script_src}:0-0:ref:script"

            edge = Edge.create(
                src=file_id,
                dst=script_ref_id,
                edge_type="script_src",
                line=line_num,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="script_src",
                confidence=0.95,  # High confidence for static HTML
            )
            edges.append(edge)

    # Update run metadata
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return HtmlAnalysisResult(symbols=symbols, edges=edges, run=run)
