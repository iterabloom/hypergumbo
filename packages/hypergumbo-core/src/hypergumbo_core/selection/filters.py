# SPDX-License-Identifier: AGPL-3.0-or-later
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

from ..paths import is_test_file

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
    "variable",         # CSS custom properties / SCSS variables (zero edges)
    "keyframes",        # CSS @keyframes animation definitions
    "media",            # CSS @media query blocks
    "font_face",        # CSS @font-face declarations
    "npm_package",      # external npm dependencies (inflate centrality)
    "module_file",      # synthetic JS/TS module resolution nodes
    "section",          # markdown headings (inflate centrality over code)
    "code_block",       # markdown fenced code blocks
    "link",             # markdown links
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

    Delegates to ``paths.is_test_file()`` for core patterns (t/ directory,
    test-* prefix, mock/fake files, spec/, fixtures/, testdata/) and adds
    language-specific patterns not covered there.

    Matches common test patterns across many languages:
    - Python: test_*.py, *_test.py, tests.py, tests/, test/
    - JavaScript/TypeScript: *.test.js, *.spec.ts, __tests__/, *.test-d.ts
    - Ruby: *_spec.rb, test_*.rb, spec/
    - Swift: Tests/, *Tests.swift (Xcode convention)
    - Go: *_test.go
    - Java/Kotlin: src/test/, *Test.java, *Test.kt, testFixtures/, intTest/
    - Rust: tests/, *_test.rs
    - C/Perl: t/, test-*.c

    Only matches actual test files, not directories that happen to contain 'test'.

    Args:
        path: File path to check.

    Returns:
        True if the path appears to be a test file.
    """
    if not path:
        return False

    # Delegate to is_test_file for core patterns: t/ directory, test-* prefix,
    # mock/fake files, spec/, fixtures/, testdata/, etc.
    if is_test_file(path):
        return True

    filename = os.path.basename(path)

    # Additional directory patterns not in is_test_file
    path_lower = path.lower()
    # Gradle test fixtures and integration test source sets
    if "/testfixtures/" in path_lower or "/inttest/" in path_lower:
        return True
    if "/integrationtest/" in path_lower:
        return True
    # Gradle/Maven integration test source set: src/integration/
    if "/src/integration/" in path_lower:
        return True

    # Python single-file test module (tests.py)
    if filename == "tests.py":
        return True

    # TypeScript type test files (.test-d.ts, .test-d.tsx)
    if filename.endswith(".test-d.ts") or filename.endswith(".test-d.tsx"):
        return True

    # Go test files: *_test.go (also in is_test_file via _test. pattern)
    # Rust test files: *_test.rs (also in is_test_file via _test. pattern)

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
