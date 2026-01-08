"""File discovery with exclude patterns."""
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterator

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
    # Documentation output
    "site",  # mkdocs
    "_site",  # Jekyll
    "public",  # Hugo (common but may have false positives)
    # Hypergumbo output artifacts
    ".hypergumbo",
    "hypergumbo.results.json",
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
