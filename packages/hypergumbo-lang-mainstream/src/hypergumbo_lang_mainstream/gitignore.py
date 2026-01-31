"""Gitignore file analyzer using tree-sitter.

.gitignore files specify intentionally untracked files that Git should ignore.
Understanding ignore patterns reveals project structure and build tooling.

How It Works
------------
1. Uses tree-sitter-gitignore grammar from tree-sitter-language-pack
2. Extracts ignore patterns and categorizes them
3. Identifies common patterns for builds, IDEs, environments

Symbols Extracted
-----------------
- **Patterns**: Ignore patterns with category classification

Edges Extracted
---------------
- None (gitignore files are self-contained)

Why This Design
---------------
- .gitignore patterns reveal build outputs and tooling
- Patterns indicate language/framework usage (node_modules = JS, __pycache__ = Python)
- Understanding exclusions helps with codebase navigation
- IDE patterns reveal development environment preferences
"""

from __future__ import annotations

import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "gitignore.tree_sitter"
PASS_VERSION = "0.1.0"


class GitignoreAnalysisResult:
    """Result of gitignore analysis."""

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


def is_gitignore_tree_sitter_available() -> bool:
    """Check if tree-sitter-gitignore is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("gitignore")
        return True
    except Exception:  # pragma: no cover
        return False


def find_gitignore_files(repo_root: Path) -> list[Path]:
    """Find all gitignore files in the repository."""
    files = list(repo_root.glob("**/.gitignore"))
    # Also look for the root .gitignore
    root_gitignore = repo_root / ".gitignore"
    if root_gitignore.exists() and root_gitignore not in files:  # pragma: no cover
        files.append(root_gitignore)
    return sorted(files)


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    # Include line number to make patterns unique within a file
    return f"gitignore:{path}:{kind}:{line}:{name[:30]}"


# Pattern categories based on common patterns
PATTERN_CATEGORIES = {
    # Build outputs
    "build": {"build", "dist", "out", "target", "bin", "obj"},
    # Dependencies
    "dependencies": {"node_modules", "vendor", "packages", "__pycache__", ".venv", "venv"},
    # IDE/Editor
    "ide": {".idea", ".vscode", ".vs", ".eclipse", ".project", ".settings"},
    # Environment/Secrets
    "environment": {".env", "*.env", ".env.*"},
    # Logs
    "logs": {"*.log", "logs", "log"},
    # OS files
    "os": {".DS_Store", "Thumbs.db", "desktop.ini"},
    # Cache
    "cache": {".cache", "*.cache", "__pycache__"},
    # Test/Coverage
    "test": {"coverage", ".coverage", "htmlcov", ".pytest_cache", ".nyc_output"},
    # Compiled
    "compiled": {"*.pyc", "*.pyo", "*.class", "*.o", "*.so", "*.dll", "*.exe"},
    # Editor temp files
    "temp": {"*.swp", "*.swo", "*~", "*.bak", "*.tmp"},
}

# Reverse mapping for quick lookup
PATTERN_TO_CATEGORY: dict[str, str] = {}
for category, patterns in PATTERN_CATEGORIES.items():
    for pattern in patterns:
        PATTERN_TO_CATEGORY[pattern.lower()] = category


def _categorize_pattern(pattern: str) -> str:
    """Categorize a gitignore pattern."""
    # Clean pattern for comparison
    clean = pattern.strip().lower()
    # Remove leading / and trailing /
    if clean.startswith("/"):
        clean = clean[1:]
    if clean.endswith("/"):
        clean = clean[:-1]

    # Direct match
    if clean in PATTERN_TO_CATEGORY:
        return PATTERN_TO_CATEGORY[clean]

    # Check if any known pattern is contained
    for known, category in PATTERN_TO_CATEGORY.items():
        if known.startswith("*"):
            # Extension pattern like *.log
            ext = known[1:]  # .log
            if clean.endswith(ext):
                return category
        elif known in clean:
            return category

    return ""


class GitignoreAnalyzer:
    """Analyzer for gitignore files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0

    def analyze(self) -> GitignoreAnalysisResult:
        """Run the gitignore analysis."""
        start_time = time.time()

        files = find_gitignore_files(self.repo_root)
        if not files:
            return GitignoreAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("gitignore")

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
            toolchain={"name": "gitignore", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return GitignoreAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "pattern":
            self._extract_pattern(node, path)

        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_pattern(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract an ignore pattern."""
        pattern_text = _get_node_text(node).strip()

        if not pattern_text:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1
        symbol_id = _make_symbol_id(rel_path, pattern_text, "pattern", line)

        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Determine pattern characteristics
        is_negation = pattern_text.startswith("!")
        is_directory = pattern_text.endswith("/")
        is_rooted = pattern_text.startswith("/") or (
            is_negation and pattern_text[1:].startswith("/")
        )
        has_wildcard = "*" in pattern_text or "?" in pattern_text or "[" in pattern_text

        # Categorize the pattern
        category = _categorize_pattern(pattern_text)

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=pattern_text,
            kind="pattern",
            language="gitignore",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=pattern_text,
            meta={
                "is_negation": is_negation,
                "is_directory": is_directory,
                "is_rooted": is_rooted,
                "has_wildcard": has_wildcard,
                "category": category,
            },
        )
        self._symbols.append(symbol)


def analyze_gitignore(repo_root: Path) -> GitignoreAnalysisResult:
    """Analyze gitignore files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        GitignoreAnalysisResult containing extracted symbols and edges
    """
    if not is_gitignore_tree_sitter_available():
        warnings.warn(
            "Gitignore analysis skipped: tree-sitter-gitignore not available",
            UserWarning,
            stacklevel=2,
        )
        return GitignoreAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "gitignore", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-gitignore not available",
        )

    analyzer = GitignoreAnalyzer(repo_root)
    return analyzer.analyze()
