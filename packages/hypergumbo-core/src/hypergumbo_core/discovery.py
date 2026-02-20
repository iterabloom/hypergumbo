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

    try:
        rel_path = path.relative_to(repo_root)
    except ValueError:
        rel_path = path

    # Check each path component against exclude patterns
    for part in rel_path.parts:
        for pattern in excludes:
            if fnmatch(part, pattern):
                return True

    return False


def find_files(
    repo_root: Path,
    patterns: list[str],
    excludes: list[str] | None = None,
    max_files: int | None = None,
) -> Iterator[Path]:
    """Find files matching patterns while respecting exclude rules.

    Args:
        repo_root: The repository root to search from
        patterns: List of glob patterns to match (e.g., ["*.py", "*.pyi"])
        excludes: List of exclude patterns (default: DEFAULT_EXCLUDES)
        max_files: Maximum number of files to return (None = unlimited)

    Yields:
        Paths to files matching the patterns that are not excluded.
    """
    count = 0
    for pattern in patterns:
        for path in repo_root.rglob(pattern):
            if max_files is not None and count >= max_files:
                return
            if not is_excluded(path, repo_root, excludes):
                yield path
                count += 1
