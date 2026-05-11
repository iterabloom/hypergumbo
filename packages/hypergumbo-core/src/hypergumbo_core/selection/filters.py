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
from typing import Any, Dict, Optional

from ..paths import is_test_file

# Symbol kinds to exclude from tiered output.
# These have high centrality but don't represent useful code.
#
# ADR-0027 Phase-2 audit (WI-jukav): MIXED axis membership.
# - AXIS_LANGUAGE_CONSTRUCT (Cluster A): ``variable``.
# - AXIS_ENDPOINT_SHAPE (Cluster D — Wave 5 fold target):
#   ``event_subscriber``. WI-jukav slice 2 closes this leg via the
#   :func:`is_excluded_kind` dual-shape predicate below — post-Wave-5
#   producers emit ``kind="method"`` + ``meta["framework_role"]=
#   "event_subscriber"``, which the predicate matches alongside the
#   pre-fold ``kind="event_subscriber"`` shape. The set still names
#   the legacy label so the predicate's ``meta`` lookup has a
#   reference vocabulary and so any unmigrated producer continues to
#   match.
# - AXIS_PENDING (Clusters B/G/H residue): ``target``,
#   ``special_target``, ``section``, ``code_block``, ``link``,
#   ``class_selector``, ``id_selector``, ``keyframes``, ``media``,
#   ``font_face``. Forward-compatibility verdict gates on per-cluster
#   audit-findings outcomes (filed as Wave 6 follow-through in
#   WI-runod).
# - AXIS_LANGUAGE_CONSTRUCT post-promotion (Wave 6 PR 2, audit-findings
#   0006): ``dependency``. The set keeps the canonical name because
#   the policy excludes both production and dev dependencies from
#   centrality tables.
# - AXIS_ENDPOINT_SHAPE post-fold (Wave 6 PR 5, audit-findings 0006):
#   ``devDependency``. Producer now emits ``kind="dependency"`` +
#   ``meta["dependency_scope"]="dev"``; the post-fold shape is
#   excluded automatically because the fold target (``dependency``)
#   is already in the set; the legacy literal stays through the
#   Phase 4a deprecation window.
# - AXIS_LANGUAGE_CONSTRUCT post-promotion (Wave 6 PR 1, audit-findings
#   0005): ``file``, ``project``, ``package``. The set keeps these
#   names because the policy is "synthetic file/package-shape nodes
#   should not show up in centrality tables / compact output," and
#   these canonical kinds carry the synthetic load.
# - AXIS_ENDPOINT_SHAPE post-fold (Wave 6 PR 3, audit-findings 0005):
#   ``script``, ``module_file``, ``npm_package``. Producers now emit
#   ``kind="file"`` + ``meta["entry_role"]="script"`` (script),
#   ``kind="file"`` + ``meta["module_system"]`` (module_file), and
#   ``kind="package"`` + ``meta["package_ecosystem"]="npm"``
#   (npm_package). The post-fold shapes are excluded automatically
#   because their fold targets (``file`` / ``package``) are already
#   in the set; the legacy literals stay through the Phase 4a
#   deprecation window for any unmigrated producer.
EXCLUDED_KINDS = frozenset({
    "dependency",       # package.json, pyproject.toml dependencies
    "file",             # file-level nodes (import targets)
    "target",           # Makefile targets
    "special_target",   # .PHONY and other special targets
    "project",          # project-level nodes
    "package",          # package.json package name
    "class_selector",   # CSS class selectors
    "id_selector",      # CSS id selectors
    "variable",         # CSS custom properties / SCSS variables (zero edges)
    "keyframes",        # CSS @keyframes animation definitions
    "media",            # CSS @media query blocks
    "font_face",        # CSS @font-face declarations
    "section",          # markdown headings (inflate centrality over code)
    "code_block",       # markdown fenced code blocks
    "link",             # markdown links
})

# ``meta["framework_role"]`` values that the dual-shape predicate
# below treats as excluded. Distinct from ``EXCLUDED_KINDS`` because
# these values live on ``Symbol.meta`` post-Wave-5 framework-role
# fold, not on ``Symbol.kind`` — so the L1 drift linter must not
# enforce them against ``SYMBOL_KINDS`` (their canonical home is
# :mod:`hypergumbo_core.axis_meta_keys`'s ``framework_role`` meta
# key vocabulary, not the Symbol.kind registry). The dual predicate
# previously folded both layers into ``EXCLUDED_KINDS``; after
# Phase 4b (ADR-0027 §6) removed the Symbol.kind legacy literals,
# the framework_role layer needs its own home.
EXCLUDED_FRAMEWORK_ROLES = frozenset({
    "event_subscriber",  # CSS/JS event handlers (less useful in isolation)
})


def is_excluded_kind(kind: str, meta: Optional[Dict[str, Any]] = None) -> bool:
    """Dual-shape predicate for ``EXCLUDED_KINDS`` (WI-jukav slice 2).

    Forward-compatible across ADR-0027 §"Phase 3" Wave 5 framework_role
    fold: matches both the pre-fold emit shape (``Symbol.kind`` directly
    carries the legacy framework-role label, e.g. ``"event_subscriber"``)
    and the post-fold shape (``Symbol.kind`` is the canonical language
    construct ``"function"`` or ``"method"`` and the role moves to
    ``Symbol.meta["framework_role"]``).

    Why this lives here rather than as ``sym.kind in EXCLUDED_KINDS``
    inline: post-fold synthetic nodes (Phoenix Channels event subscribers,
    Django signal receivers, etc.) emit ``kind="method"`` plus a
    ``framework_role`` meta key. The bare set membership check would no
    longer exclude them, silently inflating selection / compact output
    with framework-emitted synthetics. Naively widening the set to
    include ``"method"`` over-excludes every real method, so the
    forward-compat path goes through this predicate instead.

    Args:
        kind: Symbol's ``kind`` field.
        meta: Symbol's ``meta`` dict (or ``None`` if no meta).

    Returns:
        ``True`` iff the symbol should be excluded by selection /
        compact filters.

    Mirrors :func:`hypergumbo_core.linkers.registry._is_synthetic_node`
    in shape — the slice 1 idiom for SYNTHETIC_FRAMEWORK_ROLES — applied here to
    the slice 2 at-risk surface.
    """
    if kind in EXCLUDED_KINDS:
        return True
    if kind in {"function", "method"} and meta:
        return meta.get("framework_role") in EXCLUDED_FRAMEWORK_ROLES
    return False

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
