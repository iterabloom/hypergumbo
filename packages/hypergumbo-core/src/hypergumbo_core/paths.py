# SPDX-License-Identifier: AGPL-3.0-or-later
"""Centralized path handling utilities for hypergumbo.

This module provides (1) path normalization and comparison (normalize_path,
to_relative_path, paths_match, path_ends_with, get_filename,
is_under_directory), and (2) heuristic path/node classification used by
entrypoint ranking and production-slice filtering (is_utility_file,
is_infrastructure_path, is_test_file, is_test_node). All paths stored in
Symbol IDs, Symbol.path, and UsageContext.path should be normalized using
the normalization utilities.

Key design decisions:
- Paths use forward slashes (/) regardless of OS
- Paths are stored relative to repo_root when possible
- Path comparisons handle mixed formats gracefully
"""

from pathlib import Path
from typing import Any, Dict, Optional


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
    - dev/, contrib/, hack/, devel*/ (dev tooling / contributor scripts)
    - benchmarks/, benchmark/, bench/ (performance tests)

    Args:
        path: File path to check

    Returns:
        True if the path appears to be a utility file
    """
    normalized = normalize_path(path)
    path_parts = normalized.split("/")

    # Always-utility: these names are unambiguously non-production
    # regardless of where they appear in the path.
    utility_dirs = {
        # Documentation
        "docs_src", "docs", "documentation", "doc",
        # Examples
        "examples", "example", "samples", "sample", "demos", "demo",
        # Scripts
        "scripts",
        # Build systems
        "vcbuild", "cmake",
        # Benchmarks
        "benchmarks", "benchmark", "benches", "bench", "perf",
        "microbench", "microbenchmarks",
        # Dev environment (e.g., Grafana devenv/)
        "devenv",
    }

    # Ambiguous names: at the project root these are tooling directories
    # (e.g., dev/check_providers.py, tools/build.py, bin/run.sh,
    # contrib/foo.py, hack/release.sh), but inside a package they are
    # legitimate modules: django/utils/html.py is core HTML rendering,
    # django/contrib/admin/ is the bundled admin app, requests/utils.py
    # is the canonical HTTP utility module, etc. WI-gigib (2026-05-12)
    # closed a class of false-positive prod-rslice emptiness on Django
    # by tightening this check to root-only.
    _AMBIGUOUS_UTILITY_DIRS = {
        "dev", "tools", "bin", "utils", "utilities",
        "contrib", "hack",
    }

    for i, part in enumerate(path_parts[:-1]):  # Exclude filename
        lower = part.lower()
        if lower in utility_dirs:
            return True
        # Ambiguous dirs are utility only at the repo root (depth 0).
        # WI-gigib: previously the check was "ambiguous AND haven't seen
        # a known source root yet", which falsely flagged any path whose
        # top-level dir wasn't one of {src, lib, app, crates, packages}
        # — e.g. django/utils/html.py, django/contrib/admin/options.py,
        # requests/utils.py, urllib3/contrib/socks.py. Root-only is a
        # tighter rule that matches the Kubernetes-style "contrib/ at
        # the repo root means community add-ons" convention while keeping
        # nested same-named directories as production code.
        if lower in _AMBIGUOUS_UTILITY_DIRS and i == 0:
            return True
        # devel-common/, devel-tools/, etc. — CI/dev tooling directories.
        # These are universally root-level by convention so we keep the
        # broader prefix check.
        if lower.startswith("devel"):
            return True

    # Filename-level build script detection:
    # Cargo's build.rs is a compile-time build script, not user-facing code.
    filename = path_parts[-1].lower() if path_parts else ""
    if filename == "build.rs":
        return True

    # Go codegen scripts: gen.go, generate.go, *_gen.go, *_generate.go
    # These are code generation programs, not production application code.
    # Scoped to .go files to avoid false positives in other languages.
    if filename.endswith(".go") and (
        filename in ("gen.go", "generate.go")
        or filename.endswith("_gen.go")
        or filename.endswith("_generate.go")
    ):
        return True

    return False


# Infrastructure directory names that indicate internal plumbing, not
# developer-facing API.  Exports from these paths are dampened in
# entrypoint ranking when semantic entrypoints (routes, commands) exist.
_INFRASTRUCTURE_DIRS = frozenset({
    "telemetry", "metrics", "instrumentation",
    "logging", "logger", "loggers",
    "tracing", "observability",
    "internal",
})


def is_infrastructure_path(path: str) -> bool:
    """Check if a path is in an infrastructure directory.

    Infrastructure directories contain internal plumbing (telemetry, logging,
    metrics, tracing) that is production code but not developer-facing API.
    In gemini-cli, 77 of 111 entrypoints were telemetry exports from
    packages/core/src/telemetry/*.ts — internal infrastructure that dominated
    the entrypoint list over actual API classes.

    Args:
        path: File path to check

    Returns:
        True if any directory component matches an infrastructure pattern
    """
    normalized = normalize_path(path)
    parts = normalized.split("/")
    return any(part.lower() in _INFRASTRUCTURE_DIRS for part in parts[:-1])


#: Reason categories returned by :func:`classify_test_file`, coarsest first.
#: These sub-divide the population :func:`is_test_file` accepts; they do NOT
#: change it. A consumer that only needs the boolean should keep calling
#: ``is_test_file``.
TEST_FILE_REASON_TEST = "test"
TEST_FILE_REASON_MOCK = "mock"
TEST_FILE_REASON_FIXTURE = "fixture"
TEST_FILE_REASON_BENCHMARK = "benchmark"
TEST_FILE_REASON_SUPPORT = "test_support"

#: Directory-component → reason. Every key here is a member of the population
#: ``is_test_file`` accepts; splitting the map is what lets a disclosure say
#: WHICH rule fired instead of calling a benchmark directory a test.
_TEST_DIR_REASONS = {
    # Test directories
    "tests": TEST_FILE_REASON_TEST,
    "test": TEST_FILE_REASON_TEST,
    "t": TEST_FILE_REASON_TEST,
    "__tests__": TEST_FILE_REASON_TEST,
    # Go/Java convention (e.g. keycloak testsuite/)
    "testing": TEST_FILE_REASON_TEST,
    "testsuite": TEST_FILE_REASON_TEST,
    # Mock directories
    "fakes": TEST_FILE_REASON_MOCK,
    "mocks": TEST_FILE_REASON_MOCK,
    "testfakes": TEST_FILE_REASON_MOCK,
    "testmocks": TEST_FILE_REASON_MOCK,
    # Fixture / static-input directories
    "fixtures": TEST_FILE_REASON_FIXTURE,
    "testdata": TEST_FILE_REASON_FIXTURE,
    "testfixtures": TEST_FILE_REASON_FIXTURE,  # Gradle testFixtures convention
    # Test support: helpers, harnesses, formal verification
    "testutils": TEST_FILE_REASON_SUPPORT,
    "testutil": TEST_FILE_REASON_SUPPORT,
    "testhelper": TEST_FILE_REASON_SUPPORT,
    "testhelpers": TEST_FILE_REASON_SUPPORT,
    "fv": TEST_FILE_REASON_SUPPORT,
    "harnesses": TEST_FILE_REASON_SUPPORT,
    # Benchmark directories
    "bench": TEST_FILE_REASON_BENCHMARK,
    "benches": TEST_FILE_REASON_BENCHMARK,
    "benchmark": TEST_FILE_REASON_BENCHMARK,
    "benchmarks": TEST_FILE_REASON_BENCHMARK,
}


def classify_test_file(path: str) -> Optional[str]:
    """Which rule makes *path* look like test-or-support code, if any.

    This is the IMPLEMENTATION; :func:`is_test_file` is ``reason is not None``.
    One classifier with two consumers, deliberately — a second predicate that
    re-derived the categories beside the boolean would drift from it on the
    first edit, and "when a production classification exists for the thing you
    are counting, counting it yourself IS the bug" (L53) is a lesson this
    codebase has already paid for repeatedly.

    Why the reason exists at all: ``is_test_file`` is BROAD on purpose, and
    that breadth is right for its callers (deprioritize as a non-production
    entrypoint) but wrong for any *disclosure* keyed on it. A taint verdict
    reporting "3 flows excluded as test_sourced" when the real cause is a
    ``benches/`` directory tells the reader something false. The owner ruling
    (2026-08-03) was to keep the breadth and disclose which rule fired.

    Returns one of ``test`` / ``mock`` / ``fixture`` / ``benchmark`` /
    ``test_support``, or ``None`` when the path is production code. Filename
    rules are checked before directory rules, matching the original order, so
    ``mocks/db_test.py`` reports ``test`` exactly as it always returned True.

    Args:
        path: File path to check

    Returns:
        The matching reason category, or None.
    """
    filename = get_filename(path)
    filename_lower = filename.lower()

    # Test patterns with _test suffix or test- prefix (any language)
    if filename.startswith("test_"):
        return TEST_FILE_REASON_TEST
    # Hyphen-separated test prefix: test-reach.c, test-date.c, test-parse.c
    # Common in C projects. Check for "test-" followed by non-empty string.
    if filename_lower.startswith("test-"):
        return TEST_FILE_REASON_TEST
    if "_test." in filename_lower:  # Matches _test.py, _test.js, _test.ts, _test.go
        return TEST_FILE_REASON_TEST

    # Test patterns with .test. suffix (e.g., main.test.py, main.test.js)
    if ".test." in filename_lower:
        return TEST_FILE_REASON_TEST

    # Spec patterns
    if filename.startswith("spec_") or "_spec." in filename_lower:
        return TEST_FILE_REASON_TEST
    if ".spec." in filename_lower:  # Matches main.spec.js, main.spec.ts
        return TEST_FILE_REASON_TEST

    # Rust co-located test modules: tests.rs and testonly.rs live alongside
    # production code (e.g., core/lib/dal/src/consensus/tests.rs).  These are
    # test-only modules that should be excluded from production slices.
    if filename_lower in ("tests.rs", "testonly.rs"):
        return TEST_FILE_REASON_TEST

    # Mock/fake filename patterns (any language)
    name_without_ext = filename_lower.rsplit(".", 1)[0] if "." in filename_lower else filename_lower
    if name_without_ext.endswith("_mock") or name_without_ext.endswith("_fake"):
        return TEST_FILE_REASON_MOCK
    if name_without_ext.startswith("mock_") or name_without_ext.startswith("fake_"):
        return TEST_FILE_REASON_MOCK

    # Directory patterns - test and mock directories
    normalized = normalize_path(path)
    path_parts = normalized.split("/")
    # Also match compound names like "transportfakes" that end with "fakes"/"mocks",
    # directories starting with "test-" (test-artifacts, test-fixtures, test-data),
    # and directories starting with "testsuite" (testsuite-providers, etc.).
    for part in path_parts:
        part_lower = part.lower()
        mapped = _TEST_DIR_REASONS.get(part_lower)
        if mapped is not None:
            return mapped
        if part_lower.endswith("fakes") or part_lower.endswith("mocks"):
            return TEST_FILE_REASON_MOCK
        if part_lower.startswith("test-") or part_lower.startswith("testsuite"):
            return TEST_FILE_REASON_TEST

    # spec/ only matches as the first path component (Ruby RSpec convention).
    # Nested spec/ dirs (e.g., airflow/listeners/spec/) are often production
    # interface definitions, not tests.
    if path_parts and path_parts[0].lower() == "spec":
        return TEST_FILE_REASON_TEST

    return None


def is_test_file(path: str) -> bool:
    """Check if a path looks like a test file.

    Used for filtering and deprioritizing test code in analysis results.

    This is the BROAD "test-OR-support" ranking/scan predicate: beyond test
    code it also flags mocks/, fakes/, fixtures/, testdata/, benches/, ``t/``,
    ``_test.<any-ext>``, ``spec_*``, ``test-*``, ``*_mock`` and friends. It is
    the single shared chokepoint for entrypoint ranking and slice/linker
    filtering, and is DELIBERATELY distinct from the narrow supply-chain role
    flag ``Symbol.is_test_file`` ("test *code*" only, spec §14). The two answer
    different questions ("deprioritize as a non-production entrypoint?" vs
    "is this test code for tier classification?") and diverge in both
    directions — see the WI-popok fundamental-concept-audit KEEP verdict.

    Thin wrapper over :func:`classify_test_file`, which holds the rules and names
    WHICH one fired. Consumers that only branch on test-or-not should keep
    calling this; consumers that REPORT the exclusion to a human should call
    the reason function, because "excluded as test" is a false statement about
    a ``benches/`` path.

    Matches:
    - Files starting with test_ or test- or ending with _test.* (py/js/ts/go)
    - Files starting with spec_ or ending with _spec.* or .spec.*
    - Files ending with .test.* (e.g., main.test.py, main.test.js)
    - Go test files (*_test.go)
    - Mock/fake files (*_mock.*, *_fake.*, fake_*.*, mock_*.*)
    - Files in tests/, test/, t/, spec/, fakes/, mocks/, fixtures/ directories

    Args:
        path: File path to check

    Returns:
        True if the path appears to be a test file
    """
    return classify_test_file(path) is not None


def is_migration_file(path: str) -> bool:
    """Check if a path is a schema/data MIGRATION.

    Deliberately separate from :func:`is_test_file`, which does not and should
    not match these: a migration is production code that ships and runs, just
    once, at deploy time. It is not test scaffolding. What the two share is
    only that neither describes the *running application's* behavior, which is
    the question a taint-flow finding is meant to answer — a migration that
    writes to the database is not an untrusted-input finding about the product
    (WI-bifob).

    DECLARED SCOPE, because a predicate's silence is not the same as its
    absence (L23). This matches the directory conventions of the four
    migration frameworks the cohort and catalogs actually exercise:

    - ``**/migrations/`` — Django, and the general Python convention
    - ``db/migrate/`` — Rails / ActiveRecord
    - ``**/alembic/versions/`` — Alembic (SQLAlchemy)
    - ``db/migration/`` — Flyway

    ``versions/`` is matched ONLY under an ``alembic/`` parent, and ``migrate``
    / ``migration`` ONLY under a ``db/`` parent, because each is far too common
    a word to claim on its own — a ``versions/`` directory is usually
    documentation and a ``migration/`` directory is often a runtime feature.
    Anything outside these conventions is not recognised, and that is a known
    gap rather than an implied guarantee.

    Args:
        path: File path to check

    Returns:
        True if the path is a recognised migration file
    """
    parts = [p.lower() for p in normalize_path(path).split("/")]
    directories = parts[:-1]
    if "migrations" in directories:
        return True
    for index, part in enumerate(directories):
        # db/migrate (Rails) and db/migration (Flyway)
        if part in ("migrate", "migration") and index > 0:
            if directories[index - 1] == "db":
                return True
        # alembic/versions
        if part == "versions" and index > 0:
            if directories[index - 1] == "alembic":
                return True
    return False


# Annotation names that indicate a test function/method, matched case-sensitively.
# Covers Rust (#[test], #[cfg(test)], #[tokio::test]), Java/Kotlin (@Test,
# @org.junit.Test, @org.junit.jupiter.api.Test), and C# ([TestMethod],
# [Fact], [Theory]).
_TEST_ANNOTATION_NAMES = frozenset({
    "test", "Test",
    "tokio::test", "actix_rt::test", "async_std::test",
    "org.junit.Test", "org.junit.jupiter.api.Test",
    "TestMethod", "Fact", "Theory",
})


def _has_test_annotation(decorators: list[Dict[str, Any]]) -> bool:
    """Check if any decorator/annotation indicates a test function.

    Matches exact annotation names (e.g., ``#[test]``, ``@Test``) and also
    the Rust ``#[cfg(test)]`` pattern where ``cfg`` has ``"test"`` as an arg.
    """
    for dec in decorators:
        name = dec.get("name", "")
        if name in _TEST_ANNOTATION_NAMES:
            return True
        # Rust #[cfg(test)] — "cfg" with "test" in args
        if name == "cfg" and "test" in dec.get("args", []):
            return True
    return False


def is_test_node(path: str, meta: Optional[Dict[str, Any]]) -> bool:
    """Check if a node is test code, using both file path and annotations.

    Extends ``is_test_file`` by also consulting the node's metadata for
    language-specific test annotations. This catches:

    - Rust ``#[test]``, ``#[cfg(test)]``, ``#[tokio::test]`` functions
      inside non-test files (e.g., ``src/lib.rs``)
    - Java ``@Test`` / ``@org.junit.Test`` methods in production paths
    - C# ``[TestMethod]`` / ``[Fact]`` attributes

    Args:
        path: File path of the node.
        meta: Node metadata dict (may contain ``decorators`` or ``annotations``
              lists). None or empty dict means no annotation data.

    Returns:
        True if the node is test code (by path or by annotation).
    """
    if is_test_file(path):
        return True

    if not meta:
        return False

    # Check both 'decorators' and 'annotations' keys — different analyzers
    # use different names for the same concept.
    for key in ("decorators", "annotations"):
        annotations = meta.get(key)
        if annotations and _has_test_annotation(annotations):
            return True

    return False
