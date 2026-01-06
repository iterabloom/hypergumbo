"""Path classification and symbol kind filtering for selection.

This module provides shared utilities for filtering symbols based on
file paths and symbol kinds. These filters are used by multiple output
modes (sketch, compact, tiered JSON) to exclude test code, examples,
and non-semantic symbol kinds.

How It Works
------------
Path classification uses pattern matching on file paths to identify:
- Test files: Matches test directories and filename patterns across
  Python, JavaScript/TypeScript, Go, Rust, Java/Kotlin, Swift, etc.
- Example code: Matches common example/demo directory conventions

Symbol kind filtering uses a predefined set of kinds that represent
infrastructure rather than meaningful code (dependencies, file nodes,
build targets, etc.).

Why This Design
---------------
Centralizing these filters ensures consistent behavior across all
output modes. Previously, compact.py and ranking.py had duplicate
implementations of is_test_path with different pattern sets.
"""

from __future__ import annotations

import os

# Symbol kinds to exclude from tiered output
# These have high centrality but don't represent useful code
EXCLUDED_KINDS = frozenset({
    "dependency",       # package.json, pyproject.toml dependencies
    "devDependency",    # package.json dev dependencies
    "file",             # file-level nodes (import targets)
    "target",           # Makefile targets
    "special_target",   # .PHONY and other special targets
    "project",          # project-level nodes
    "package",          # package.json package name
    "script",           # package.json scripts
    "event_subscriber", # CSS/JS event handlers (less useful in isolation)
    "class_selector",   # CSS class selectors
    "id_selector",      # CSS id selectors
})

# Path patterns indicating example/demo code
# Include both /examples/ and examples/ to handle absolute and relative paths
EXAMPLE_PATH_PATTERNS = (
    "/examples/",
    "/example/",
    "/demos/",
    "/demo/",
    "/samples/",
    "/sample/",
    "/playground/",
    "/tutorial/",
    "/tutorials/",
)


def is_test_path(path: str) -> bool:
    """Check if a path looks like a test file.

    Matches common test patterns across many languages:
    - Python: test_*.py, *_test.py, tests/, test/
    - JavaScript/TypeScript: *.test.js, *.spec.ts, __tests__/, *.test-d.ts
    - Swift: Tests/, *Tests.swift (Xcode convention)
    - Go: *_test.go
    - Java/Kotlin: src/test/, *Test.java, *Test.kt, testFixtures/, intTest/
    - Rust: tests/, *_test.rs

    Only matches actual test files, not directories that happen to contain 'test'.

    Args:
        path: File path to check.

    Returns:
        True if the path appears to be a test file.
    """
    if not path:
        return False

    filename = os.path.basename(path)

    # Directory patterns (case-insensitive for Tests/ vs tests/)
    # Note: Using lowercase comparison to catch both "tests/" and "Tests/"
    # This also catches Java/Kotlin's src/test/ convention since it contains /test/
    path_lower = path.lower()
    if "/test/" in path_lower or "/tests/" in path_lower or "/__tests__/" in path_lower:
        return True
    # Handle paths that start with test/ or Tests/
    if path_lower.startswith("test/") or path_lower.startswith("tests/"):
        return True
    # Gradle test fixtures and integration test source sets
    if "/testfixtures/" in path_lower or "/inttest/" in path_lower:
        return True
    if "/integrationtest/" in path_lower:
        return True

    # File name patterns: test_*.py, test_*.js, etc.
    if filename.startswith("test_"):
        return True

    # Python/JS/TS suffix patterns (.test.ts, .spec.js, _test.py, etc.)
    for ext in (".py", ".js", ".ts", ".jsx", ".tsx"):
        if filename.endswith(f".test{ext}") or filename.endswith(f".spec{ext}"):
            return True
        if filename.endswith(f"_test{ext}"):
            return True

    # TypeScript type test files (.test-d.ts, .test-d.tsx)
    if filename.endswith(".test-d.ts") or filename.endswith(".test-d.tsx"):
        return True

    # Go test files: *_test.go
    if filename.endswith("_test.go"):
        return True

    # Rust test files: *_test.rs
    if filename.endswith("_test.rs"):
        return True

    # Swift test files: *Tests.swift (Xcode convention - test class suffix)
    # Match "RouteTests.swift" but not "TestHelpers.swift"
    if filename.endswith("Tests.swift"):
        return True

    # Java/Kotlin test files: *Test.java, *Test.kt, *Tests.java, *Tests.kt
    for ext in (".java", ".kt"):
        if filename.endswith(f"Test{ext}") or filename.endswith(f"Tests{ext}"):
            return True

    return False


def is_example_path(path: str) -> bool:
    """Check if a path represents example/demo code.

    Matches common example directory conventions:
    - examples/, example/
    - demos/, demo/
    - samples/, sample/
    - playground/
    - tutorial/, tutorials/

    Args:
        path: File path to check.

    Returns:
        True if the path appears to be example code.
    """
    path_lower = path.lower()
    # Check standard patterns (with leading slash)
    if any(pattern in path_lower for pattern in EXAMPLE_PATH_PATTERNS):
        return True
    # Also check if path starts with example directory (relative paths)
    return path_lower.startswith(("examples/", "example/", "demos/", "demo/",
                                   "samples/", "sample/", "playground/",
                                   "tutorial/", "tutorials/"))
