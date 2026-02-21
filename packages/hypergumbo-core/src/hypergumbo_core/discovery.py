"""File discovery with exclude patterns and .m file disambiguation.

Provides shared utilities for finding source files across the repository while
respecting exclude patterns. Also provides content-based classification for
`.m` files, which are ambiguously shared by Objective-C, MATLAB, and Wolfram.

The `classify_dot_m_file` function reads file content and uses syntactic
heuristics to determine which language a `.m` file belongs to. This prevents
all three analyzers from independently processing the same file.
"""
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterator

# Compiled patterns for .m file disambiguation — compiled once at import time.
# Objective-C: preprocessor directives and @-keywords
_OBJC_PATTERNS = re.compile(
    r"(?:^#import\b|^#include\b|^@interface\b|^@implementation\b"
    r"|^@protocol\b|^@end\b|^@property\b|^@synthesize\b|^@dynamic\b"
    r"|^#pragma\b)",
    re.MULTILINE,
)
# Wolfram: pattern-match arguments (x_), scoping constructs, package structure
_WOLFRAM_PATTERNS = re.compile(
    r"(?:\w+\[[\w_,\s]*\w+_[\w]*\]\s*:="  # f[x_] := or f[x_, y_Integer] :=
    r"|(?:Module|Block|With)\["            # scoping constructs
    r"|BeginPackage\[|EndPackage\["         # package structure
    r"|Needs\[|Get\["                       # imports
    r"|\(\*)",                              # block comments (* ... *)
    re.MULTILINE,
)
# MATLAB: function/classdef keywords at start of line
_MATLAB_PATTERNS = re.compile(
    r"(?:^function\b|^classdef\b)",
    re.MULTILINE,
)

# How many bytes to read for classification. 8 KB is enough to capture
# the file header, imports, and first few definitions.
_CLASSIFY_READ_LIMIT = 8192


def classify_dot_m_file(path: Path) -> str:
    """Classify a .m file as 'objc', 'wolfram', or 'matlab'.

    Uses content-based heuristics to disambiguate .m files, which are shared
    by three languages:
    - Objective-C: #import, @interface/@implementation/@protocol, #pragma
    - Wolfram/Mathematica: f[x_] :=, Module[, BeginPackage[, (* comments *)
    - MATLAB: function keyword, classdef keyword, % comments

    Reads the first 8 KB of the file for classification. Returns 'matlab' as
    the default when no strong signals are found (MATLAB is the most common
    user of .m files in practice).

    Args:
        path: Path to a .m file.

    Returns:
        One of 'objc', 'wolfram', or 'matlab'.
    """
    try:
        content = path.read_bytes()[:_CLASSIFY_READ_LIMIT].decode("utf-8", errors="replace")
    except (OSError, PermissionError):
        return "matlab"

    # Check Objective-C first — it has the most distinctive syntax
    if _OBJC_PATTERNS.search(content):
        return "objc"

    # Check Wolfram — pattern-match syntax (x_) is very distinctive
    if _WOLFRAM_PATTERNS.search(content):
        return "wolfram"

    # Check MATLAB keywords
    if _MATLAB_PATTERNS.search(content):
        return "matlab"

    # Default: MATLAB is the most common use of .m files
    return "matlab"


# Default exclude patterns (gitignore-style)
DEFAULT_EXCLUDES = [
    # Dependency directories
    "node_modules",
    "vendor",  # PHP (Composer), Go
    "venv",
    ".venv",
    "env",
    ".eggs",
    # Build output
    "dist",
    "build",
    "_build",  # Sphinx docs
    "out",
    "target",  # Rust, Maven
    # VCS and IDE
    ".git",
    ".svn",
    ".hg",
    # Caches
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".cache",
    "*.egg-info",
    ".terraform",  # Terraform providers and modules cache
    # Coverage and test reports
    "htmlcov",  # Python (pytest-cov)
    "coverage",  # Generic (Ruby, JS)
    ".coverage",  # Python coverage.py
    "coverage.xml",  # Python, generic (Cobertura format)
    ".nyc_output",  # JavaScript (nyc/Istanbul)
    "lcov-report",  # JavaScript/C++ (LCOV HTML)
    "lcov.info",  # JavaScript/C++ (LCOV data)
    ".c8_output",  # JavaScript (c8)
    "coverage.out",  # Go
    "cover.out",  # Go (alternate name)
    "cover.html",  # Go (HTML report)
    "tarpaulin-report",  # Rust (cargo-tarpaulin)
    "TestResults",  # .NET (dotnet test)
    "coverlet",  # .NET (Coverlet)
    "gcov-reports",  # C/C++ (gcov)
    "jest-coverage",  # JavaScript (Jest)
    ".jest",  # JavaScript (Jest cache)
    "snapshot_report.html",  # pytest-html snapshot report
    # Documentation output
    "site",  # mkdocs
    "_site",  # Jekyll
    "public",  # Hugo (common but may have false positives)
    # Hypergumbo output artifacts
    ".hypergumbo",
    "hypergumbo.results*.json",  # Matches .json, .4k.json, .16k.json, .64k.json, etc.
    # Lock files - generated, inflate LOC counts
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "Gemfile.lock",
    "composer.lock",
    "Cargo.lock",
    "go.sum",
    "pubspec.lock",  # Dart/Flutter
    "packages.lock.json",  # NuGet (.NET)
]


def _has_glob_chars(pattern: str) -> bool:
    """Check whether a pattern contains fnmatch glob characters."""
    return any(c in pattern for c in "*?[")


# Pre-classify DEFAULT_EXCLUDES at module load time for fast matching.
# Exact names (no glob chars) use O(1) frozenset lookup; only the 2 glob
# patterns (*.egg-info, hypergumbo.results*.json) fall back to fnmatch.
_EXACT_DEFAULT_EXCLUDES = frozenset(
    p for p in DEFAULT_EXCLUDES if not _has_glob_chars(p)
)
_GLOB_DEFAULT_EXCLUDES = tuple(
    p for p in DEFAULT_EXCLUDES if _has_glob_chars(p)
)


def _classify_excludes(
    excludes: list[str],
) -> tuple[frozenset[str], tuple[str, ...]]:
    """Split exclude patterns into exact names and glob patterns.

    Exact names are matched via frozenset membership (O(1) per check).
    Glob patterns are matched via fnmatch (O(n) per check, but n is
    typically 2 for DEFAULT_EXCLUDES).
    """
    if excludes is DEFAULT_EXCLUDES:
        return _EXACT_DEFAULT_EXCLUDES, _GLOB_DEFAULT_EXCLUDES
    exact = frozenset(p for p in excludes if not _has_glob_chars(p))
    globs = tuple(p for p in excludes if _has_glob_chars(p))
    return exact, globs


def _is_excluded_classified(
    path: Path,
    repo_root: Path,
    exact: frozenset[str],
    globs: tuple[str, ...],
) -> bool:
    """Fast exclusion check using pre-classified patterns.

    Uses frozenset membership for exact names and fnmatch only for
    the small number of glob patterns.
    """
    try:
        rel_path = path.relative_to(repo_root)
    except ValueError:
        rel_path = path

    for part in rel_path.parts:
        if part in exact:
            return True
        for pattern in globs:
            if fnmatch(part, pattern):
                return True

    return False


def is_excluded(path: Path, repo_root: Path, excludes: list[str] | None = None) -> bool:
    """Check if a path should be excluded from analysis.

    Args:
        path: The file or directory path to check
        repo_root: The repository root (for computing relative paths)
        excludes: List of exclude patterns (default: DEFAULT_EXCLUDES)

    Returns:
        True if the path should be excluded, False otherwise.

    Patterns are matched against each component of the relative path.
    For example, 'node_modules' matches any directory named 'node_modules'
    at any depth in the tree.
    """
    if excludes is None:
        excludes = DEFAULT_EXCLUDES

    exact, globs = _classify_excludes(excludes)
    return _is_excluded_classified(path, repo_root, exact, globs)


def find_files(
    repo_root: Path,
    patterns: list[str],
    excludes: list[str] | None = None,
    max_files: int | None = None,
) -> Iterator[Path]:
    """Find files matching patterns while respecting exclude rules.

    Exclude patterns are classified once per call into exact names
    (frozenset lookup) and glob patterns (fnmatch). This avoids
    per-file pattern classification overhead.

    Args:
        repo_root: The repository root to search from
        patterns: List of glob patterns to match (e.g., ["*.py", "*.pyi"])
        excludes: List of exclude patterns (default: DEFAULT_EXCLUDES)
        max_files: Maximum number of files to return (None = unlimited)

    Yields:
        Paths to files matching the patterns that are not excluded.
    """
    if excludes is None:
        excludes = DEFAULT_EXCLUDES

    # Classify once, use for all files in this call
    exact, globs = _classify_excludes(excludes)

    count = 0
    for pattern in patterns:
        for path in repo_root.rglob(pattern):
            if max_files is not None and count >= max_files:
                return
            if not path.is_file():
                continue
            if not _is_excluded_classified(path, repo_root, exact, globs):
                yield path
                count += 1
