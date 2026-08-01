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

Symbol-kind filtering uses two parallel sets and a dual-shape
predicate, reflecting the ADR-0027 Phase-4b axis split between
``Symbol.kind`` (the language-construct axis) and
``Symbol.meta["framework_role"]`` (the framework-dispatch axis):

- ``EXCLUDED_KINDS``: language-construct labels to suppress in
  centrality / compact output (``dependency``, ``file``, ``package``,
  build/config shapes, etc.).
- ``EXCLUDED_FRAMEWORK_ROLES``: framework roles attached to otherwise-
  canonical ``method`` / ``function`` symbols that should be
  suppressed (e.g., framework-dispatched event subscribers folded out
  of the old ``kind="event_subscriber"`` shape by Wave 5 of ADR-0027).
- ``is_excluded_kind(symbol)``: the public predicate consumers should
  call. Matches either ``symbol.kind in EXCLUDED_KINDS`` or
  ``symbol.meta.get("framework_role") in EXCLUDED_FRAMEWORK_ROLES``,
  so it handles pre-fold and post-fold producer shapes uniformly.

Why This Design
---------------
Centralizing these filters ensures consistent behavior across all
output modes. Previously, compact.py and ranking.py had duplicate
implementations of is_test_path with different pattern sets. The
dual-shape predicate lets producers migrate from ad-hoc
``kind="<framework-role>"`` to canonical kind + ``meta`` without
forcing every consumer to switch in the same release.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..paths import is_test_file

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from ..ir import Edge, Symbol

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


# Symbol kinds that count as a "key symbol" — the declaration kinds a reader
# is asking about when they ask what a repository contains.
#
# WI-zulij: this set and the four-clause predicate below were previously a
# frozenset declared INSIDE ``sketch._format_symbols``'s body, with the clauses
# as an inline comprehension beneath it. Nothing outside that one function could
# reach either, so "what counts as a key symbol" had no home — and compact's
# default selection, which advertises the same thing, filtered nothing at all.
# The two surfaces disagreed on their top-10 for exactly that reason: not the
# ranking function (both now rank with ``compute_dampened_centrality``) but the
# POPULATION each ranks over. This module's own header already named sketch,
# compact and tiered JSON as its consumers; the policy just never landed here.
#
# Include OOP kinds (function, class, method) plus language-specific
# equivalents:
# - Nix: binding, derivation, input (core abstractions)
# - Terraform/HCL: resource, data, module, variable, output, provider, local
# - Elixir/Erlang: module, macro, record, type
# - Elm/F#: module, type, port, record, union, value
# - SQL: table, view, procedure, trigger
# - Dockerfile: stage
# - Lean: theorem, structure, inductive, instance
# - Agda: data (algebraic data types)
# - Fortran/COBOL: program, subroutine
# - VHDL: entity, architecture, component
# - Other: struct, enum, trait, interface, protocol, object
#
# ADR-0027 Phase-2 audit (WI-jukav): MIXED axis membership.
# - AXIS_LANGUAGE_CONSTRUCT (Cluster A): ``function``, ``class``, ``method``,
#   ``constructor``, ``struct``, ``enum``, ``type``, ``union``, ``interface``,
#   ``trait``, ``module``, ``namespace``, ``object``, ``macro``, ``binding`` —
#   stable across Phase 3.
# - AXIS_PENDING (Clusters B/G/H — domain long-tail): ``record``, ``abstract``,
#   ``protocol``, ``instance``, ``derivation``, ``input``, ``resource``,
#   ``data``, ``variable``, ``output``, ``provider``, ``local``, ``port``,
#   ``table``, ``view``, ``procedure``, ``trigger``, ``stage``, ``value``,
#   ``theorem``, ``inductive``, ``program``, ``subroutine``, ``entity``,
#   ``architecture``, ``component``, ``structure``. Audit-findings 0006/0007
#   (Cluster G/H) recommend canonical promotions for many of these; the registry
#   update is Wave 6 follow-through per WI-runod. Until then, none of these
#   values is scheduled for fold/rename in Phase 3, so this set is
#   forward-compatible — it captures the intent "key declaration kinds across
#   all languages" and Phase 3 doesn't change which values populate that intent.
#
# NOTE the deliberate asymmetry with ``EXCLUDED_KINDS`` above: this is an
# ALLOWLIST and that is a DENYLIST, and they are not complements. ``variable``
# appears in both — here because Terraform/HCL variables are a real declaration
# surface, there because CSS custom properties and SCSS variables are zero-edge
# noise. Whichever a consumer applies is a policy choice about its own output,
# not a fact about the kind, which is why both sets survive.
KEY_SYMBOL_KINDS = frozenset({
    # OOP languages
    "function", "class", "method", "constructor",
    # Structs and data types
    "struct", "enum", "type", "record", "union", "abstract",
    # Interfaces and traits
    "interface", "trait", "protocol",
    # Modules and namespaces
    "module", "object", "namespace", "instance",
    # Nix
    "binding", "derivation", "input",
    # Terraform/HCL
    "resource", "data", "variable", "output", "provider", "local",
    # Elixir/Erlang
    "macro",
    # Elm
    "port",
    # SQL
    "table", "view", "procedure", "trigger",
    # Dockerfile
    "stage",
    # F#
    "value",
    # Lean (theorem prover)
    "theorem", "inductive",
    # Fortran/COBOL
    "program", "subroutine",
    # VHDL (hardware design)
    "entity", "architecture", "component",
})


def is_key_symbol(symbol: "Symbol") -> bool:
    """The four-clause "is this worth showing a reader" predicate.

    A key symbol is a declaration of a kind in :data:`KEY_SYMBOL_KINDS`, not in
    a test file, not named like a test function, and not a derived artifact.

    The ``"test_" not in symbol.name`` clause is deliberately a SUBSTRING test
    rather than a prefix test, and it is kept verbatim from the sketch original
    rather than tightened here: it also catches ``helper_test_case`` and the
    like. Narrowing it would change sketch's output, which this extraction is
    specifically not doing — the whole point is that both surfaces now share one
    definition, so any change to the definition is a separate, visible decision
    that moves both together instead of one drifting from the other.
    """
    return (
        symbol.kind in KEY_SYMBOL_KINDS
        and not is_test_path(symbol.path)
        and "test_" not in symbol.name
        and symbol.supply_chain_tier != 4
    )


def key_symbols(symbols: List["Symbol"]) -> List["Symbol"]:
    """Filter *symbols* to the key-symbol population. See :func:`is_key_symbol`."""
    return [s for s in symbols if is_key_symbol(s)]


def production_edges(
    symbols: List["Symbol"], edges: List["Edge"]
) -> List["Edge"]:
    """Drop edges ORIGINATING in a test file.

    Centrality computed over test-sourced edges credits a symbol for being
    called by its own test suite, which inflates well-tested internals over
    genuinely central code. Filtering by source only (not destination) is
    deliberate: an edge INTO a test file is still evidence about the target.

    Extracted alongside :func:`key_symbols` because the two are one policy —
    sketch applied both and compact's default applied neither, so adopting the
    symbol filter without the edge filter would leave the two surfaces ranking
    the same population with different weights.
    """
    path_by_id = {s.id: s.path for s in symbols}
    return [
        e for e in edges
        if not is_test_path(path_by_id.get(getattr(e, "src", ""), ""))
    ]


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
