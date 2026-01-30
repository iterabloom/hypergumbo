"""Centralized path handling utilities for hypergumbo.

This module provides consistent path normalization and comparison functions
used throughout the codebase. All paths stored in Symbol IDs, Symbol.path,
and UsageContext.path should be normalized using these utilities.

Key design decisions:
- Paths use forward slashes (/) regardless of OS
- Paths are stored relative to repo_root when possible
- Path comparisons handle mixed formats gracefully
"""

from pathlib import Path


def normalize_path(path: str | Path) -> str:
    """Normalize a path to use forward slashes.

    Converts backslashes to forward slashes for consistent storage and comparison.
    Does NOT resolve symlinks or make paths absolute/relative.

    Args:
        path: A file path as string or Path object

    Returns:
        Path string with forward slashes
    """
    return str(path).replace("\\", "/")


def to_relative_path(path: str | Path, repo_root: str | Path) -> str:
    """Convert a path to be relative to repo_root.

    Args:
        path: Absolute or relative file path
        repo_root: Root directory to make path relative to

    Returns:
        Normalized relative path (forward slashes, no leading ./)

    Raises:
        ValueError: If path is not under repo_root
    """
    path_obj = Path(path).resolve()
    root_obj = Path(repo_root).resolve()

    try:
        relative = path_obj.relative_to(root_obj)
        return normalize_path(relative)
    except ValueError:
        # Path is not under repo_root - return as-is but normalized
        return normalize_path(path)


def paths_match(path1: str, path2: str) -> bool:
    """Check if two paths refer to the same location.

    Handles different path formats (absolute/relative, slash directions).

    Args:
        path1: First path to compare
        path2: Second path to compare

    Returns:
        True if paths match after normalization
    """
    norm1 = normalize_path(path1)
    norm2 = normalize_path(path2)

    # Exact match after normalization
    if norm1 == norm2:
        return True

    # Check if one is a suffix of the other at a directory boundary
    # This handles comparing "/home/user/repo/src/main.py" with "src/main.py"
    return path_ends_with(norm1, norm2) or path_ends_with(norm2, norm1)


def path_ends_with(full_path: str, suffix: str) -> bool:
    """Check if full_path ends with suffix at a directory boundary.

    This ensures we don't match partial filenames:
    - "src/main.py" matches suffix "main.py" ✓
    - "src/main.py" matches suffix "src/main.py" ✓
    - "src/domain.py" does NOT match suffix "main.py" ✓

    Args:
        full_path: The complete path to check
        suffix: The path suffix to look for

    Returns:
        True if full_path ends with suffix at a directory boundary
    """
    norm_path = normalize_path(full_path)
    norm_suffix = normalize_path(suffix).lstrip("/")

    if not norm_suffix:
        return False

    # Exact match
    if norm_path == norm_suffix:
        return True

    # Suffix match at directory boundary
    # Ensure the character before the suffix is a /
    with_slash = "/" + norm_suffix
    return norm_path.endswith(with_slash)


def get_filename(path: str) -> str:
    """Extract the filename from a path.

    Args:
        path: A file path

    Returns:
        The filename (last component of the path)
    """
    normalized = normalize_path(path)
    return normalized.rsplit("/", 1)[-1] if "/" in normalized else normalized


def is_under_directory(path: str, directory: str) -> bool:
    """Check if path is under a given directory.

    Args:
        path: File path to check
        directory: Directory name to look for (e.g., "tests", "test")

    Returns:
        True if any component of path matches directory name
    """
    normalized = normalize_path(path)
    parts = normalized.split("/")
    return directory.lower() in [p.lower() for p in parts[:-1]]  # Exclude filename


def is_utility_file(path: str) -> bool:
    """Check if a path looks like a utility/example/documentation file.

    Used for deprioritizing utility code in entrypoint ranking. These are
    files that exist to demonstrate or document the main codebase, not
    production code that should be navigated to.

    Matches files in directories:
    - docs_src/, docs/, documentation/ (documentation source)
    - examples/, example/, samples/ (example code)
    - scripts/, tools/, bin/ (utility scripts)
    - benchmarks/, bench/ (performance tests)

    Args:
        path: File path to check

    Returns:
        True if the path appears to be a utility file
    """
    normalized = normalize_path(path)
    path_parts = normalized.split("/")

    utility_dirs = {
        # Documentation
        "docs_src", "docs", "documentation", "doc",
        # Examples
        "examples", "example", "samples", "sample", "demos", "demo",
        # Scripts/tools
        "scripts", "tools", "bin", "utils", "utilities",
        # Benchmarks
        "benchmarks", "bench", "perf",
    }

    for part in path_parts[:-1]:  # Exclude filename
        if part.lower() in utility_dirs:
            return True

    return False


def is_test_file(path: str) -> bool:
    """Check if a path looks like a test file.

    Used for filtering and deprioritizing test code in analysis results.

    Matches:
    - Files starting with test_ or ending with _test.* (py/js/ts/go)
    - Files starting with spec_ or ending with _spec.* or .spec.*
    - Files ending with .test.* (e.g., main.test.py, main.test.js)
    - Go test files (*_test.go)
    - Mock/fake files (*_mock.*, *_fake.*, fake_*.*, mock_*.*)
    - Files in tests/, test/, spec/, fakes/, mocks/, fixtures/ directories

    Args:
        path: File path to check

    Returns:
        True if the path appears to be a test file
    """
    filename = get_filename(path)
    filename_lower = filename.lower()

    # Test patterns with _test suffix (any language)
    if filename.startswith("test_"):
        return True
    if "_test." in filename_lower:  # Matches _test.py, _test.js, _test.ts, _test.go
        return True

    # Test patterns with .test. suffix (e.g., main.test.py, main.test.js)
    if ".test." in filename_lower:
        return True

    # Spec patterns
    if filename.startswith("spec_") or "_spec." in filename_lower:
        return True
    if ".spec." in filename_lower:  # Matches main.spec.js, main.spec.ts
        return True

    # Mock/fake filename patterns (any language)
    name_without_ext = filename_lower.rsplit(".", 1)[0] if "." in filename_lower else filename_lower
    if name_without_ext.endswith("_mock") or name_without_ext.endswith("_fake"):
        return True
    if name_without_ext.startswith("mock_") or name_without_ext.startswith("fake_"):
        return True

    # Directory patterns - test and mock directories
    normalized = normalize_path(path)
    path_parts = normalized.split("/")
    test_dirs = {
        "tests", "test", "spec", "__tests__",  # Test directories
        "fakes", "mocks", "testfakes", "testmocks",  # Mock directories
        "fixtures", "testdata", "testutils",  # Test support directories
    }
    # Also match compound names like "transportfakes" that end with "fakes"/"mocks"
    for part in path_parts:
        part_lower = part.lower()
        if part_lower in test_dirs:
            return True
        if part_lower.endswith("fakes") or part_lower.endswith("mocks"):
            return True
    return False
