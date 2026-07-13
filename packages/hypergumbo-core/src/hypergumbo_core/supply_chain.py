# SPDX-License-Identifier: AGPL-3.0-or-later
"""Supply chain classification for code analysis.

Classifies files into tiers based on their position in the project's
dependency graph. This enables focused analysis (first-party code
prioritized) and noise reduction (derived artifacts excluded).

Tiers
-----
- FIRST_PARTY (1): Project's own source code (highest priority)
- INTERNAL_DEP (2): Org-internal dependency packages (configured
  internal_package_roots only)
- EXTERNAL_DEP (3): Third-party dependencies in readable form
- DERIVED (4): Build artifacts, transpiled/bundled output (skip analysis)

Classification Algorithm
------------------------
Classification happens at discovery time, before analysis. Signals are
checked in order; first match wins:

1. Derived artifact detection (tier 4) - path patterns + content heuristics
2. External dependency detection (tier 3) - node_modules/, vendor/, etc.
3. Example/demo detection (tier 1, is_example=True) - examples/, demos/,
   samples/, tutorials/ (in-repo → first-party; the role is on is_example)
4. Workspace package detection:
   - If file matches a test directory pattern → tier 1 with is_test=True
   - Otherwise → tier 1 (workspace IS the project)
5. Configured internal_package_roots → tier 2 (the only tier-2 producer)
6. Test code detection (tier 1 with is_test=True) - tests/, spec/,
   __tests__/, _test.go, .test.js, etc. Routing tests through tier 2
   historically made tier 2 a synonym for is_test and drowned out the
   real internal-dep signal (INV-tisid).
7. Documentation / notebook (.ipynb) / fuzz-bench detection (tier 1) -
   in-repo role files; the role is carried by the reason string (INV-naduh).
8. First-party detection (tier 1) - src/, lib/, app/ or default

Per INV-naduh / ADR-0041 §1 the tier names supply-chain DISTANCE only: all
in-repo files are first-party (distance 0), and tier 2 is reserved for
org-internal *dependency* packages declared via config. Role files
(examples, docs, notebooks, fuzz/bench, tests) carry their role on a
separate axis (is_example / is_test / reason), not by tier.

See §14 of the hypergumbo spec for full details.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Optional


class Tier(IntEnum):
    """Supply chain position, lower = higher priority."""

    FIRST_PARTY = 1
    INTERNAL_DEP = 2
    EXTERNAL_DEP = 3
    DERIVED = 4


@dataclass
class DependencyManifest:
    """Language-agnostic dependency manifest for tier classification of boundary nodes.

    Maps module paths (e.g., Go module paths from go.mod, npm package names
    from package.json) to dependency metadata. Used by ``create_boundary_nodes``
    to assign tier 2 (direct dependency) vs tier 3 (indirect/stdlib) to
    synthetic boundary nodes that represent unresolved external references.

    Entries map module path strings to dicts with at least a ``direct`` bool key.

    ADR-0041 §1/§2 (supply:F5): the manifest no longer influences *tier*. Tier
    names supply-chain distance only, so every third-party import is tier 3
    (``classify_import`` is now constant ``EXTERNAL_DEP``). The direct/transitive
    declaration relationship the old mapping burned into tier 2 is exposed by
    ``classify_directness`` and recorded on the ``directness`` meta key instead.
    Tier 2 (``internal_dep``) is reserved for workspace/org-internal packages,
    assigned by file classification — never by this manifest classifier.
    """

    entries: dict[str, dict] = field(default_factory=dict)

    def classify_import(self, import_path: str) -> "Tier":
        """Classify the supply-chain *tier* of an external import path.

        ADR-0041 §1: tier names supply-chain distance and nothing else, so
        every third-party / external import — direct, transitive, stdlib, or
        unknown alike — is :data:`Tier.EXTERNAL_DEP` (3). Boundary nodes are
        external by construction; first-party (tier 1) and workspace-internal
        (tier 2) code is assigned by file classification, not here.

        The direct/transitive/undeclared *declaration relationship* this method
        used to fold into tier 2 now lives on :meth:`classify_directness`.

        Returns:
            Tier.EXTERNAL_DEP (3), always.
        """
        return Tier.EXTERNAL_DEP

    def classify_directness(self, import_path: str) -> str:
        """Classify the declaration relationship of an external import (ADR-0041 §2).

        Records how the project's manifests relate to an external dependency:

        * ``"direct"`` — declared in a project manifest (longest-prefix match
          carries ``direct: True``);
        * ``"transitive"`` — present in the manifest but not declared direct
          (pulled in by another dependency);
        * ``"undeclared"`` — imported but declared in no manifest (a phantom
          dependency; also the bucket the language runtime's stdlib falls into,
          since stdlib is declared nowhere — the stdlib-vs-third_party split is
          the separate ``ecosystem`` axis, ADR-0041 §3).

        Matching mirrors the old tier classifier's longest-prefix logic across
        slash-separated (Go: ``github.com/foo/bar``) and dot-separated
        (Java/Kotlin: ``com.fasterxml.jackson.core``) module paths.
        """
        best_match = ""
        for module_path in self.entries:
            if (
                import_path == module_path
                or import_path.startswith(module_path + "/")
                or import_path.startswith(module_path + ".")
            ):
                if len(module_path) > len(best_match):
                    best_match = module_path

        if not best_match:
            return "undeclared"
        return "direct" if self.entries[best_match].get("direct", False) else "transitive"

    @classmethod
    def merge(cls, manifests: list["DependencyManifest"]) -> "DependencyManifest":
        """Merge multiple manifests into one.

        Later entries override earlier ones for the same module path.
        """
        merged: dict[str, dict] = {}
        for m in manifests:
            merged.update(m.entries)
        return cls(entries=merged)


@dataclass
class SupplyChainConfig:
    """Configuration for supply chain classification.

    Allows customizing tier classification via capsule plan.

    Attributes:
        analysis_tiers: Which tiers to include in analysis (default: [1, 2, 3])
        first_party_patterns: Additional patterns to classify as tier 1
        derived_patterns: Additional patterns to classify as tier 4
        internal_package_roots: Explicit internal package paths
    """

    analysis_tiers: list[int] = field(default_factory=lambda: [1, 2, 3])
    first_party_patterns: list[str] = field(default_factory=list)
    derived_patterns: list[str] = field(default_factory=list)
    internal_package_roots: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dict for JSON output."""
        return {
            "analysis_tiers": self.analysis_tiers,
            "first_party_patterns": self.first_party_patterns,
            "derived_patterns": self.derived_patterns,
            "internal_package_roots": self.internal_package_roots,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SupplyChainConfig":
        """Parse from dict."""
        return cls(
            analysis_tiers=data.get("analysis_tiers", [1, 2, 3]),
            first_party_patterns=data.get("first_party_patterns", []),
            derived_patterns=data.get("derived_patterns", []),
            internal_package_roots=data.get("internal_package_roots", []),
        )


@dataclass
class FileClassification:
    """Classification result for a file.

    `tier` and the boolean role flags (`is_test`, `is_example`,
    `is_config`, `is_generated`) are independent axes (WI-rigun-patuz,
    WI-jobuj). Per INV-tisid, test code is tier 1 with is_test=True —
    tests are first-party code, and routing them through tier 2 made
    tier 2 a synonym for is_test (~99% of self-analysis tier-2 entries
    were tests, drowning out actual internal-dep signal). Per INV-naduh /
    ADR-0041 §1, tier 2 is reserved for org-internal *dependency* packages
    (configured ``internal_package_roots`` only); other in-repo role files
    — examples, docs, notebooks, fuzz/bench harnesses — are first-party
    (tier 1) with the role carried by the flag/reason, not by tier.

    At most one of `is_test`, `is_example`, `is_config` is True per file
    (mutual exclusion preserved by the classifier order); the property is
    tier-independent now that role files are first-party.
    """

    tier: Tier
    reason: str
    package_name: Optional[str] = None
    is_test: bool = False
    is_example: bool = False
    is_config: bool = False
    is_generated: bool = False


# Path patterns for tier inference (checked as prefixes on relative path)
DERIVED_PATH_PATTERNS = [
    r"^dist/",
    r"^build/",
    r"^out/",
    r"^target/",
    r"^\.next/",
    r"^\.nuxt/",
    r"^\.output/",
    r"^\.svelte-kit/",
    r"^\.build/",         # Swift Package Manager build artifacts
    r"^__pycache__/",
    r"/__pycache__/",
]

DERIVED_FILENAME_PATTERNS = [
    r"\.min\.js$",
    r"\.min\.css$",
    r"\.bundle\.js$",
    r"\.compiled\.js$",
    r"\.pyc$",
    r"\.pyo$",
    # Protobuf/gRPC code generation artifacts
    r"\.serde\.rs$",        # Rust prost-build generated serde implementations
    r"\.pb\.go$",           # Go protobuf generated code
    r"_pb2\.py$",           # Python protobuf generated code
    r"_pb2_grpc\.py$",      # Python gRPC generated code
]

# WI-tizij: patterns for generated code (OpenAPI/Swagger, protobuf, etc.)
# These files are structurally central but have low developer relevance.
# The is_generated flag lets ranking apply a centrality penalty.
GENERATED_CODE_PATTERNS = [
    # OpenAPI/Swagger generated Python models (kserve, kubernetes-client, etc.)
    r"^(?:.*/)?(v\d+(?:alpha\d+|beta\d+)?_\w+)\.py$",  # v1alpha1_foo.py, v1beta1_bar.py, v1_baz.py
    r"^(?:.*/)?knative_\w+\.py$",                        # knative_foo.py
    # Protobuf/gRPC (already DERIVED tier, but also mark generated)
    r"\.pb\.go$",
    r"_pb2\.py$",
    r"_pb2_grpc\.py$",
    r"\.serde\.rs$",
    # OpenAPI generated Go clients
    r"^(?:.*/)?zz_generated\.\w+\.go$",                  # Kubernetes code-gen
    r"^(?:.*/)?mock_\w+\.go$",                           # mockgen output
    # WI-sozah: go-swagger output layouts. Conventional alertmanager-style
    # ``api/vN/{restapi,models}/`` directory hierarchies hold generated server
    # stubs and DTOs. The catch-all is anchored at ``api/v\d+/(restapi|models)/``
    # so first-party ``models/`` directories without an ``api/vN/`` parent are
    # not falsely flagged.
    r"(?:^|/)api/v\d+/(?:restapi|models)/",
    # WI-sozah: go-swagger fingerprint files. These names are unambiguous
    # — the tool always emits them under the chosen output package — so
    # they can be matched at any depth without needing the ``api/vN/`` anchor.
    r"(?:^|/)restapi/embedded_spec\.go$",
    r"(?:^|/)restapi/configure_[^/]+\.go$",
    r"(?:^|/)restapi/server\.go$",
    r"(?:^|/)restapi/doc\.go$",
    # WI-vubad: openapi-codegen TypeScript SDK output. Airflow, FastAPI,
    # and many Python web projects ship a generated TS client under
    # ``openapi-gen/`` (sometimes ``openapi/`` or ``api-client/``). The
    # prospector evidence from WI-tubot 2026-04-11 showed ~200 candidate
    # dead-code functions per airflow run came from
    # ``airflow-core/src/airflow/api_fastapi/auth/managers/simple/ui/
    # openapi-gen/requests/core/{request,CancelablePromise}.ts`` alone.
    # These files are generated by openapi-codegen / openapi-typescript
    # and are effectively unreachable via hypergumbo's normal call
    # graph — they should be is_generated=True and demoted out of
    # dead-code candidate ranking.
    r"(?:^|/)openapi-gen/.*\.(?:ts|tsx|js|jsx|mjs|cjs)$",
]

EXTERNAL_DEP_PATTERNS = [
    (r"^node_modules/", "node_modules/"),
    (r"^vendor/", "vendor/"),
    (r"^third_party/", "third_party/"),
    (r"^third-party/", "third-party/"),
    (r"^thirdparty/", "thirdparty/"),
    (r"^external/", "external/"),
    (r"^deps/", "deps/"),
    (r"^Pods/", "Pods/"),
    (r"^Carthage/", "Carthage/"),
    (r"^\.yarn/cache/", ".yarn/cache/"),
    (r"^_vendor/", "_vendor/"),
]

# Patterns matched with re.search (anywhere in path) for vendored SDKs
# that live inside subdirectories rather than at the repo root.
# Common in Go monorepos where cloud providers embed SDK copies.
# INV-mogud: cluster-autoscaler has 19K+ nodes from these SDKs classified
# as tier-1 because they don't match the root-anchored patterns above.
EXTERNAL_DEP_DEEP_PATTERNS = [
    (r"(?:^|/)[^/]+-sdk-go(?:-[^/]+)?/", "vendored Go SDK"),
    (r"(?:^|/)[^/]+-go-sdk/", "vendored Go SDK"),
    (r"(?:^|/)[^/]+-sdk-golang/", "vendored Go SDK"),
    (r"(?:^|/)vendor-internal/", "vendor-internal/"),
    # INV-kokik: vendored third-party front-end assets nested under a
    # first-party root (typically src/.../static/). These marker directory
    # names are unambiguous vendoring signals (same rationale as
    # vendor-internal/ above), so they fire even under a src/ prefix and
    # demote un-minified vendored JS/CSS out of tier-1. Deliberately NOT a
    # blanket static/ rule — many projects keep first-party JS/CSS under
    # static/; only explicit vendored-marker dirs are demoted. (Minified
    # bundles are already caught upstream by the DERIVED filename patterns.)
    (r"(?:^|/)vendored/", "vendored assets"),
    (r"(?:^|/)npm_mirror/", "vendored npm mirror"),
    (r"(?:^|/)bower_components/", "bower_components/"),
]

FIRST_PARTY_PATTERNS = [
    r"^src/",
    r"^lib/",
    r"^app/",
    r"^pkg/",
    r"^cmd/",
    r"^internal/",
    r"^crates/[^/]+/src/",
    r"^packages/[^/]+/src/",
]

# Patterns for example/demo code (lower priority than workspace packages)
EXAMPLE_PATTERNS = [
    r"^examples?/",  # examples/ or example/
    r"^demos?/",     # demos/ or demo/
    r"^samples?/",   # samples/ or sample/
    r"^tutorials?/",  # tutorials/ or tutorial/
]

# WI-jobuj: dependency/build manifest filenames. Files matching these
# names are flagged with ``is_config=True`` independently of tier (see
# FileClassification). The set is intentionally narrow — canonical
# package-manager and build-tool manifests, not arbitrary dotfiles —
# so the bit means "this file declares dependencies / build config",
# not "this file happens to configure something."
CONFIG_FILE_NAMES = frozenset({
    # Python
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Pipfile.lock",
    # Node
    "package.json",
    "package-lock.json",
    "yarn.lock",
    # Rust
    "Cargo.toml",
    "Cargo.lock",
    # Go
    "go.mod",
    "go.sum",
    # Ruby
    "Gemfile",
    "Gemfile.lock",
    # Java / Kotlin / JVM
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    # PHP
    "composer.json",
    "composer.lock",
    # Elixir
    "mix.exs",
    "mix.lock",
})

# Patterns for documentation directories (tier 2) — not production code.
# Checked with re.search to match at any depth (e.g., Sources/Lib/Documentation.docc/).
# Swift DocC (.docc) bundles contain tutorial fragments, articles, and extension files
# that look like code but are documentation content (not importable modules).
DOCUMENTATION_PATTERNS = [
    r"(?:^|/)\.docc/",              # .docc/ at any level (Swift DocC bundles)
    r"(?:^|/)Documentation\.docc/",  # Documentation.docc/ (conventional name)
]

# Patterns for fuzz targets and benchmarks (tier 2) — not production code.
# Checked with re.search to match at any depth (e.g., crates/core/fuzz/).
FUZZ_BENCH_PATTERNS = [
    r"(?:^|/)fuzz(?:ing)?/",       # fuzz/ or fuzzing/ at any level
    r"(?:^|/)fuzz_targets/",       # fuzz_targets/ (cargo-fuzz convention)
    r"(?:^|/)benchmarks?/",        # benchmark/ or benchmarks/ at any level
    r"(?:^|/)benches/",            # benches/ (Rust convention) at any level
]

# Patterns for test code — checked BEFORE first-party patterns.
# Per INV-tisid, BOTH dedicated test directories AND co-located test
# files route to tier 1 (FIRST_PARTY) with is_test=True. Tier 2 is
# reserved for in-repo non-test code (examples, fuzz harnesses,
# vendored deps); a "test dir → tier 2" rule made tier 2 a synonym
# for is_test (~99% of self-analysis tier-2 entries were tests).
TEST_DIR_PATTERNS = [
    r"(?:^|/)tests?/",       # tests/ or test/ at any level
    r"(?:^|/)__tests__/",    # __tests__/ (Jest convention) at any level
    r"(?:^|/)specs?/",       # spec/ or specs/ (RSpec/Jasmine) at any level
    r"(?:^|/)unit_tests?/",  # unit_tests/ or unit_test/ (C++ GTest convention)
]

# File suffix patterns: language-specific test naming conventions (co-located → tier 1)
TEST_FILE_PATTERNS = [
    r"_test\.go$",                # Go: handler_test.go
    r"\.test\.[jt]sx?$",         # JS/TS: app.test.js, app.test.tsx
    r"\.spec\.[jt]sx?$",         # JS/TS: service.spec.ts, component.spec.tsx
    r"_spec\.rb$",               # Ruby: user_spec.rb
    r"/test_[^/]+\.(?:cpp|cc|cxx|c|h|hpp)$",  # C/C++: test_utils.cpp (GTest convention)
    r"(?:^|/)test_[^/]+\.py$",   # Python: test_foo.py (pytest/unittest, WI-mozum)
    r"_test\.py$",               # Python: foo_test.py (WI-mozum)
    r"(?:^|/)test_[^/]+\.sh$",   # Bash: test_hooks.sh (WI-mozum)
    r"(?:^|/)tests\.rs$",        # Rust: co-located test module (src/consensus/tests.rs)
    r"(?:^|/)testonly\.rs$",     # Rust: test-only helpers (src/vm_executor/testonly.rs)
]

# File suffix patterns that take precedence over TEST_DIR_PATTERNS (tier 1).
# The standard TEST_FILE_PATTERNS are checked AFTER the dir patterns — that's
# correct for Go / JS / Ruby etc., where the dir pattern's "tests live in
# tests/" is uncommon (Go has none, JS uses __tests__/ which IS the test
# subtree, Ruby uses spec/). For Elixir + Phoenix, the conventional layout
# puts `*_test.exs` files at `test/<context>/<thing>_test.exs` — they ARE
# in a `test/` directory by convention, but they're still first-party
# production-adjacent code (the dir is part of the project, not vendored).
# WI-pugas (FCA finding on phoenix bakeoff 2026-05-10): 1328/1328 nodes
# at supply_chain.tier=2 on phoenix were Elixir test files, none vendored
# — tier 2 had become a synonym for "is_test" instead of "internal_dep".
# Adding the Elixir test pattern HERE (override) routes Phoenix tests to
# tier 1 with is_test=True, freeing tier 2 to mean what it should:
# "in-repo non-test internal dependency / fuzz / example / etc."
TEST_FILE_PATTERNS_DIR_OVERRIDE = [
    r"_test\.exs$",              # Elixir/Phoenix: user_test.exs
]

# Simple first-party patterns to check within workspaces
WORKSPACE_FIRST_PARTY_PATTERNS = [
    r"^src/",
    r"^lib/",
    r"^app/",
]


_GENERATED_CODE_RE = [re.compile(p) for p in GENERATED_CODE_PATTERNS]

# WI-pofin: content-based generated-code detection.
#
# Matches the canonical ``@generated`` marker (Facebook / openapi /
# various codegen tools) and the Go stdlib convention
# ``Code generated by ... DO NOT EDIT`` from go-tooling and protoc.
# The pattern is intentionally narrow: it requires the phrase to sit
# on a comment line so we don't accidentally flag a file that
# mentions the word in prose. Matches bytes (not str) so we can skip
# decoding for most files.
#
# Covered cases:
# - ``// @generated`` or ``/* @generated`` (JS/TS/Go/C/C++/Rust)
# - ``# @generated`` (Python/Ruby/YAML)
# - ``// Code generated by X. DO NOT EDIT.`` (Go stdlib convention)
# - ``# Code generated by X. DO NOT EDIT`` (Python codegen)
# - ``<!-- @generated -->`` (HTML/XML/Markdown)
_GENERATED_HEADER_PATTERN = re.compile(
    rb"(?mi)^(?:"
    rb"(?://|#|/\*|\*|<!--)\s*"
    rb"(?:@generated|Code generated by[^\n]*DO NOT EDIT|"
    rb"Autogenerated|AUTO-GENERATED|automatically generated)"
    rb")",
)

# Only scan the first chunk of the file. Generated-file markers
# conventionally live in the first few lines, so a 4 KiB cap is more
# than enough and keeps the cost bounded on large files.
_HEADER_SCAN_BYTES = 4096

# Extensions that make sense to scan for content markers. Text-like
# source extensions. Binary files (images, archives) are skipped.
_CONTENT_SCAN_EXTS = frozenset({
    ".py", ".pyi", ".go", ".rs", ".ts", ".tsx", ".js", ".jsx",
    ".mjs", ".cjs", ".java", ".kt", ".kts", ".scala", ".sc",
    ".rb", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh",
    ".cs", ".m", ".mm", ".swift", ".php", ".ex", ".exs", ".erl",
    ".elm", ".dart", ".html", ".xml", ".md", ".yaml", ".yml",
    ".proto", ".thrift", ".gql", ".graphql",
})


def _is_generated_file(rel_path: str) -> bool:
    """Check if a file path matches known generated-code patterns.

    WI-tizij: generated code (OpenAPI models, protobuf stubs, Kubernetes
    code-gen) is structurally central but has low developer relevance.
    """
    for pat in _GENERATED_CODE_RE:
        if pat.search(rel_path):
            return True
    return False


def _is_config_file(rel_path: str) -> bool:
    """Check if a file path matches a known dependency/build manifest filename.

    WI-jobuj: matches the basename against ``CONFIG_FILE_NAMES``.
    Path-position-agnostic — a ``pyproject.toml`` at the repo root and
    one inside a workspace are both flagged.
    """
    rel_norm = rel_path.replace("\\", "/")
    basename = rel_norm.rsplit("/", 1)[-1]
    return basename in CONFIG_FILE_NAMES


def _has_generated_header(path: Path) -> bool:
    """Check the first few KB of *path* for a generated-file marker.

    WI-pofin: complements path-pattern detection for files that don't
    match known paths but clearly announce themselves as generated via
    a comment-line marker in the file header. Reads only
    ``_HEADER_SCAN_BYTES`` bytes and skips files whose extension isn't
    in ``_CONTENT_SCAN_EXTS`` to keep the cost bounded. Errors
    reading the file (permission, missing, etc.) are silently treated
    as "not generated" — the caller's fallback is the path-based
    check.
    """
    if path.suffix.lower() not in _CONTENT_SCAN_EXTS:
        return False
    try:
        with path.open("rb") as fh:
            head = fh.read(_HEADER_SCAN_BYTES)
    except (OSError, IOError):  # pragma: no cover
        return False
    return _GENERATED_HEADER_PATTERN.search(head) is not None


def classify_file(
    path: Path,
    repo_root: Path,
    package_roots: Optional[set[Path]] = None,
    config: Optional[SupplyChainConfig] = None,
) -> FileClassification:
    """Classify a file's supply chain tier.

    Args:
        path: Absolute path to the file
        repo_root: Root directory of the repository
        package_roots: Set of internal package root paths (from detect_package_roots)
        config: Optional custom classification configuration

    Returns:
        FileClassification with tier, reason, and optional package_name.
        The ``is_generated`` flag is set independently of tier when the
        file path matches known generated-code patterns (WI-tizij).
        The ``is_config`` flag (WI-jobuj) is set independently of tier
        when the basename matches a dependency/build manifest filename
        (``pyproject.toml``, ``package.json``, ``Cargo.toml``, etc.) —
        but NOT when ``is_test`` or ``is_example`` is already True, so
        the role flags remain mutually exclusive within tier 2.
    """
    # Get relative path for pattern matching
    try:
        rel = str(path.relative_to(repo_root))
    except ValueError:
        # Path not under repo_root, default to first-party
        return FileClassification(Tier.FIRST_PARTY, "default (outside repo)")

    # WI-tizij: path-based generated-code detection.
    # WI-pofin: fall back to a content-based header scan when the path
    # is unambiguous. Path check first because it's cheaper.
    generated = _is_generated_file(rel) or _has_generated_header(path)
    config_filename = _is_config_file(rel)

    result = _classify_file_core(rel, path, repo_root, package_roots, config)
    if generated:
        result.is_generated = True
    # WI-jobuj: is_config is mutually exclusive with is_test / is_example
    # within tier 2 (test and example detection wins on a tie, e.g., a
    # package.json under examples/ is is_example=True, not is_config).
    if config_filename and not result.is_test and not result.is_example:
        result.is_config = True
    return result


def _classify_file_core(
    rel: str,
    path: Path,
    repo_root: Path,
    package_roots: Optional[set[Path]] = None,
    config: Optional[SupplyChainConfig] = None,
) -> FileClassification:
    """Core classification logic (called by classify_file)."""

    # Normalize path separators for consistent matching
    rel = rel.replace("\\", "/")

    # 0. Check custom derived patterns from config first
    if config and config.derived_patterns:
        for pattern in config.derived_patterns:
            if rel.startswith(pattern) or re.match(f"^{re.escape(pattern)}", rel):
                return FileClassification(Tier.DERIVED, f"config derived_patterns: {pattern}")

    # 1. Check derived patterns first (these should be skipped)
    for pattern in DERIVED_PATH_PATTERNS:
        if re.match(pattern, rel):
            return FileClassification(Tier.DERIVED, f"path matches {pattern}")

    for pattern in DERIVED_FILENAME_PATTERNS:
        if re.search(pattern, rel):
            return FileClassification(Tier.DERIVED, f"filename matches {pattern}")

    # 2. Check content heuristics for minification (only for existing files)
    if path.exists() and is_likely_minified(path):
        return FileClassification(Tier.DERIVED, "detected as minified/generated")

    # 3. Check external dependencies
    for pattern, label in EXTERNAL_DEP_PATTERNS:
        if re.match(pattern, rel):
            pkg = _extract_package_name(rel, label)
            return FileClassification(Tier.EXTERNAL_DEP, f"in {label}", pkg)

    # 3b. Check deep external dependency patterns (vendored SDKs anywhere in path)
    for pattern, label in EXTERNAL_DEP_DEEP_PATTERNS:
        if re.search(pattern, rel):
            pkg = _extract_package_name(rel, label)
            return FileClassification(Tier.EXTERNAL_DEP, f"in {label}", pkg)

    # 4. Check example/demo patterns (lower priority than workspace packages).
    # INV-naduh / ADR-0041 §1: tier names supply-chain DISTANCE only. In-repo
    # example/demo files are the project's OWN code (distance 0) → tier 1
    # first_party; their role is carried by is_example, NOT by tier 2 (which is
    # reserved for org-internal *dependency* packages).
    for pattern in EXAMPLE_PATTERNS:
        if re.match(pattern, rel):
            return FileClassification(
                Tier.FIRST_PARTY, f"path matches {pattern}", is_example=True
            )

    # 4b. Check documentation patterns (DocC bundles, etc.). INV-naduh: in-repo
    # docs are first-party (tier 1); the role is captured by the reason string.
    for pattern in DOCUMENTATION_PATTERNS:
        if re.search(pattern, rel):
            return FileClassification(Tier.FIRST_PARTY, f"documentation path matches {pattern}")

    # 5a. Check custom internal_package_roots from config
    if config and config.internal_package_roots:
        for pkg_pattern in config.internal_package_roots:
            if rel.startswith(pkg_pattern) or rel.startswith(pkg_pattern + "/"):
                return FileClassification(
                    Tier.INTERNAL_DEP, f"config internal_package_roots: {pkg_pattern}"
                )

    # 5. Check internal packages (monorepo workspaces)
    if package_roots:
        for pkg_root in package_roots:
            try:
                if path.is_relative_to(pkg_root):
                    rel_to_pkg = str(path.relative_to(pkg_root)).replace("\\", "/")
                    # INV-tisid: test directories inside workspace packages
                    # are tier 1 with is_test=True. Tier 2 is reserved for
                    # in-repo non-test code (examples, fuzz harnesses,
                    # vendored deps). Previously this branch returned
                    # INTERNAL_DEP, causing 99% of self-analysis tier-2
                    # entries to be tests rather than actual internal deps.
                    for test_pat in TEST_DIR_PATTERNS:
                        if re.search(test_pat, rel_to_pkg):
                            return FileClassification(
                                Tier.FIRST_PARTY,
                                f"in workspace {pkg_root.name} (test dir)",
                                is_test=True,
                            )
                    # Co-located test FILE patterns (e.g. _test.go next to
                    # handler.go) are tier 1 with is_test=True.  The is_test
                    # flag captures their test nature; tier 1 reflects that
                    # they live in the same package as the source they test.
                    for test_pat in TEST_FILE_PATTERNS:
                        if re.search(test_pat, rel_to_pkg):
                            return FileClassification(
                                Tier.FIRST_PARTY,
                                f"in workspace {pkg_root.name} (co-located test)",
                                is_test=True,
                            )
                    # All other workspace files are first-party.
                    # Workspace members are by definition part of the project.
                    return FileClassification(
                        Tier.FIRST_PARTY,
                        f"in workspace {pkg_root.name}",
                    )
            except (ValueError, TypeError):
                continue

    # 5b-pre. WI-pugas: language-specific test patterns that OVERRIDE
    # the dir pattern. Phoenix/Elixir tests live at `test/<ctx>/foo_test.exs`
    # by convention — they match both the dir pattern (test/) and the
    # file pattern (_test.exs), and the right verdict is tier 1
    # (first-party with is_test=True), not tier 2 (which conflates "is
    # test" with "is vendored").
    for pattern in TEST_FILE_PATTERNS_DIR_OVERRIDE:
        if re.search(pattern, rel):
            return FileClassification(
                Tier.FIRST_PARTY,
                f"co-located test file matches {pattern}",
                is_test=True,
            )

    # 5b. INV-tisid: test directories are tier 1 with is_test=True.
    # Tier 2 is reserved for in-repo non-test code (examples, fuzz
    # harnesses, vendored deps); test code is first-party code that
    # the project's authors write, not a vendored dependency.
    for pattern in TEST_DIR_PATTERNS:
        if re.search(pattern, rel):
            return FileClassification(
                Tier.FIRST_PARTY,
                f"test path matches {pattern}",
                is_test=True,
            )
    # Co-located test FILE patterns (e.g. _test.go, .test.js next to
    # source files) → tier 1 with is_test=True.  Prior to this change,
    # these were tier 2, which made tier-2 useless for distinguishing
    # first-party tests from actual third-party dependencies in Go and
    # other languages where tests are co-located with source.
    for pattern in TEST_FILE_PATTERNS:
        if re.search(pattern, rel):
            return FileClassification(
                Tier.FIRST_PARTY,
                f"co-located test file matches {pattern}",
                is_test=True,
            )

    # 5c. Jupyter notebooks are exploratory, not part of the import namespace
    if rel.endswith(".ipynb"):
        # INV-naduh: in-repo notebooks are first-party (tier 1), not a tier-2
        # dependency; the role is captured by the reason string.
        return FileClassification(Tier.FIRST_PARTY, "notebook file (.ipynb)")

    # 5d. Check fuzz/benchmark patterns (not production code). INV-naduh: in-repo
    # fuzz/bench harnesses are first-party (tier 1); "not production" is captured
    # by the reason string, not by mislabeling them as a tier-2 dependency.
    for pattern in FUZZ_BENCH_PATTERNS:
        if re.search(pattern, rel):
            return FileClassification(Tier.FIRST_PARTY, f"fuzz/bench path matches {pattern}")

    # 5e. Check custom first_party_patterns from config
    if config and config.first_party_patterns:
        for pattern in config.first_party_patterns:
            if rel.startswith(pattern) or re.match(f"^{re.escape(pattern)}", rel):
                return FileClassification(
                    Tier.FIRST_PARTY, f"config first_party_patterns: {pattern}"
                )

    # 6. Check first-party patterns
    for pattern in FIRST_PARTY_PATTERNS:
        if re.match(pattern, rel):
            return FileClassification(Tier.FIRST_PARTY, f"path matches {pattern}")

    # 7. Default: assume first-party if no other signals
    return FileClassification(Tier.FIRST_PARTY, "default (no matching pattern)")


# INV-lukop: the average-line-length minification heuristic (Heuristic 1 in
# ``is_likely_minified``) only makes sense for WEB ASSETS — JS/CSS/HTML bundles
# are the artifacts that get minified into long single lines. A dense-but-real
# source file in another language (a Python data/lookup/i18n module, a very long
# function signature, generated protobuf) legitimately has a high average line
# length and must NOT be misclassified as minified: doing so classifies its whole
# file tier-4 (derived), and the default tier filter then drops EVERY symbol and
# edge for that file with no diagnostic. Gating the heuristic to these extensions
# closes that silent-whole-file-drop vector; the @generated / sourcemap / webpack
# heuristics stay universal because they are language-agnostic generation signals.
_MINIFIABLE_WEB_EXTENSIONS = frozenset({
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
    ".css", ".scss", ".sass", ".less",
    ".html", ".htm", ".xhtml",
    ".vue", ".svelte",
})


def is_likely_minified(path: Path) -> bool:
    """Detect likely minified/bundled/generated files via content heuristics.

    Checks:
    1. Average line length > 150 chars (minified code) — WEB ASSETS ONLY
       (``_MINIFIABLE_WEB_EXTENSIONS``); see the INV-lukop note above.
    2. Source map reference in last 3 lines (transpiled)
    3. "Generated by" or "@generated" in first 5 lines
    4. Webpack bootstrap pattern in first 10 lines

    Args:
        path: Path to the file to check

    Returns:
        True if file appears to be minified/generated
    """
    try:
        content = path.read_text(errors="ignore")
    except (OSError, IOError):
        return False

    lines = content.splitlines()
    if not lines:
        return False

    # Heuristic 1: Average line length > 150 chars, but only for web assets that
    # actually get minified (INV-lukop) — a dense non-web source file is not.
    if path.suffix.lower() in _MINIFIABLE_WEB_EXTENSIONS:
        avg_line_len = len(content) / len(lines)
        if avg_line_len > 150:
            return True

    # Heuristic 2: Source map reference in last 3 lines
    tail = "\n".join(lines[-3:])
    if re.search(r"//[#@]\s*sourceMappingURL=", tail):
        return True

    # Heuristic 3: Generator header in first 5 lines
    head = "\n".join(lines[:5])
    if re.search(r"(Generated by|@generated|DO NOT EDIT)", head, re.IGNORECASE):
        return True

    # Heuristic 4: Webpack bootstrap in first 10 lines (bundled but not minified)
    head_10 = "\n".join(lines[:10])
    if re.search(r"__webpack_require__|webpackBootstrap", head_10):
        return True

    return False


def detect_package_roots(repo_root: Path) -> set[Path]:
    """Detect internal package roots from workspace/monorepo configs.

    Scans for:
    - npm/yarn/pnpm workspaces in package.json
    - Cargo workspace members in Cargo.toml
    - Maven modules in pom.xml
    - Gradle subprojects in settings.gradle / settings.gradle.kts

    Args:
        repo_root: Root directory of the repository

    Returns:
        Set of Path objects pointing to internal package directories
    """
    roots: set[Path] = set()

    # npm/yarn/pnpm workspaces
    pkg_json = repo_root / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text())
            # Skip non-dict package.json files (e.g., string or array at top level)
            if not isinstance(data, dict):
                data = {}
            workspaces = data.get("workspaces", [])

            # Handle object format: {"packages": [...]}
            if isinstance(workspaces, dict):
                workspaces = workspaces.get("packages", [])

            for pattern in workspaces:
                # Skip empty or current-dir patterns
                if not pattern or pattern == ".":
                    continue
                # Expand globs like "packages/*"
                for match in repo_root.glob(pattern):
                    if match.is_dir():
                        roots.add(match)
        except (json.JSONDecodeError, OSError):
            pass

    # Cargo workspaces
    cargo_toml = repo_root / "Cargo.toml"
    if cargo_toml.exists():
        try:
            content = cargo_toml.read_text()
            # Simple TOML parsing for workspace members
            if "[workspace]" in content:
                for match in re.finditer(
                    r"members\s*=\s*\[(.*?)\]", content, re.DOTALL
                ):
                    for member in re.findall(r'"([^"]+)"', match.group(1)):
                        if not member or member == ".":
                            continue
                        for path in repo_root.glob(member):
                            if path.is_dir():
                                roots.add(path)
        except OSError:
            pass

    # Maven multi-module projects
    pom_xml = repo_root / "pom.xml"
    if pom_xml.exists():
        try:
            import xml.etree.ElementTree as ET  # nosec B405 - parsing local pom.xml

            tree = ET.parse(pom_xml)  # noqa: S314  # nosec B314
            root_el = tree.getroot()
            # Handle Maven namespace (xmlns="http://maven.apache.org/POM/4.0.0")
            ns = ""
            if root_el.tag.startswith("{"):
                ns = root_el.tag.split("}")[0] + "}"
            modules_el = root_el.find(f"{ns}modules")
            if modules_el is not None:
                for mod_el in modules_el.findall(f"{ns}module"):
                    if mod_el.text:
                        mod_path = repo_root / mod_el.text.strip()
                        if mod_path.is_dir():
                            roots.add(mod_path)
        except (OSError, ET.ParseError):
            pass

    # Gradle multi-project builds (WI-zizuf)
    # settings.gradle or settings.gradle.kts declares subprojects via
    # include('mod') or include 'mod'.  Colon separators (e.g., 'connect:api')
    # map to directory nesting (connect/api/).
    for settings_name in ("settings.gradle", "settings.gradle.kts"):
        settings_file = repo_root / settings_name
        if settings_file.exists():
            try:
                content = settings_file.read_text()
                # Match both Groovy (include 'a', 'b') and Kotlin DSL
                # (include("a", "b")) styles. Each include() call may
                # list multiple projects separated by commas.
                for m in re.finditer(
                    r"""include\s*\(?\s*((?:['"]:?[^'"]*['"],?\s*)+)\)?""",
                    content,
                ):
                    for name in re.findall(r"""['"][:.]?([^'"]+)['"]""", m.group(1)):
                        if not name:  # pragma: no cover - regex guarantees non-empty
                            continue
                        # Gradle ':' separator maps to directory '/'
                        dir_name = name.replace(":", "/")
                        mod_path = repo_root / dir_name
                        if mod_path.is_dir():
                            roots.add(mod_path)
            except OSError:
                pass

    return roots


def _extract_package_name(rel_path: str, pattern_label: str) -> Optional[str]:
    """Extract package name from dependency path.

    For node_modules/, extracts the npm package name (handling scoped packages).

    Args:
        rel_path: Relative path within the repo
        pattern_label: The matched pattern label (e.g., "node_modules/")

    Returns:
        Package name if extractable, None otherwise
    """
    if pattern_label == "node_modules/":
        parts = rel_path.split("node_modules/")[-1].split("/")
        if not parts[0]:
            # Empty path after node_modules/ (edge case)
            return None
        if parts[0].startswith("@"):
            # Scoped package: @scope/package
            if len(parts) >= 2:
                return "/".join(parts[:2])
            return parts[0]
        return parts[0]

    return None
