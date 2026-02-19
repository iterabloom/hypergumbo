"""Gitignore file analyzer using tree-sitter.

.gitignore files specify intentionally untracked files that Git should ignore.
Understanding ignore patterns reveals project structure and build tooling.

How It Works
------------
Uses the TreeSitterAnalyzer base class with the tree-sitter-gitignore grammar
from tree-sitter-language-pack. Single-pass analysis (no cross-file resolution
needed — gitignore files are self-contained).

1. extract_symbols_from_file: walks AST to find pattern nodes, categorizes them
2. _find_source_files: overridden because find_files uses glob patterns but
   the root .gitignore may not match the standard discovery path

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

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Span, Symbol, make_pass_id
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


PASS_ID = make_pass_id("gitignore")


def find_gitignore_files(repo_root: Path) -> list[Path]:
    """Find all gitignore files in the repository."""
    files = list(find_files(repo_root, [".gitignore"]))
    # Also look for the root .gitignore
    root_gitignore = repo_root / ".gitignore"
    if root_gitignore.exists() and root_gitignore not in files:  # pragma: no cover
        files.append(root_gitignore)
    return sorted(files)


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
for _category, _patterns in PATTERN_CATEGORIES.items():
    for _pattern in _patterns:
        PATTERN_TO_CATEGORY[_pattern.lower()] = _category


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


class GitignoreAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based gitignore file analyzer.

    Uses tree-sitter-gitignore from the language-pack to extract ignore patterns
    and categorize them by type (build, dependencies, IDE, environment, etc.).

    Single-pass only — gitignore files are self-contained with no cross-file
    references, so extract_edges_from_file is not overridden (base returns []).

    Overrides ``_find_source_files`` because the root .gitignore requires
    special discovery beyond standard glob patterns.
    """

    lang = "gitignore"
    file_patterns: ClassVar[list[str]] = [".gitignore"]
    language_pack_name = "gitignore"

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Yield gitignore files, including root .gitignore."""
        yield from find_gitignore_files(repo_root)

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract ignore patterns from a single gitignore file.

        Walks the AST iteratively, extracting pattern nodes and categorizing
        them by type (build, dependencies, IDE, etc.). Each pattern gets
        metadata for negation, directory, rooted, wildcard, and category.
        """
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            if node.type != "pattern":
                continue

            pattern_text = node_text(node, source).strip()
            if not pattern_text:
                continue  # pragma: no cover

            line = node.start_point[0] + 1
            symbol_id = make_symbol_id(
                "gitignore", rel_path, line, node.end_point[0] + 1,
                pattern_text, "pattern",
            )

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
                origin_run_id=run.execution_id,
                signature=pattern_text,
                meta={
                    "is_negation": is_negation,
                    "is_directory": is_directory,
                    "is_rooted": is_rooted,
                    "has_wildcard": has_wildcard,
                    "category": category,
                },
            )
            analysis.symbols.append(symbol)

        return analysis


_analyzer = GitignoreAnalyzer()


def is_gitignore_tree_sitter_available() -> bool:
    """Check if tree-sitter-gitignore is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("gitignore")
def analyze_gitignore(repo_root: Path) -> AnalysisResult:
    """Analyze gitignore files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
