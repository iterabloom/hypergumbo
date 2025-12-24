"""File discovery with exclude patterns."""
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterator

# Default exclude patterns (gitignore-style)
DEFAULT_EXCLUDES = [
    "node_modules",
    "vendor",  # PHP dependencies (Composer)
    "venv",
    ".venv",
    "dist",
    "build",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "*.egg-info",
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
) -> Iterator[Path]:
    """Find files matching patterns while respecting exclude rules.

    Args:
        repo_root: The repository root to search from
        patterns: List of glob patterns to match (e.g., ["*.py", "*.pyi"])
        excludes: List of exclude patterns (default: DEFAULT_EXCLUDES)

    Yields:
        Paths to files matching the patterns that are not excluded.
    """
    for pattern in patterns:
        for path in repo_root.rglob(pattern):
            if not is_excluded(path, repo_root, excludes):
                yield path
