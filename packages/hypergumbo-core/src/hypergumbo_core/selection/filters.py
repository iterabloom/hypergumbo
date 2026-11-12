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

Symbol-kind filtering uses two parallel sets and one predicate,
reflecting the split (ADR-0027) between ``Symbol.kind`` (the
language-construct axis) and ``Symbol.meta["framework_role"]`` (the
framework-dispatch axis):

- ``EXCLUDED_KINDS``: language-construct labels to suppress in
  centrality / compact output (``dependency``, ``file``, ``package``,
  build/config shapes, etc.).
- ``EXCLUDED_FRAMEWORK_ROLES``: framework roles attached to otherwise-
  canonical ``method`` / ``function`` symbols that should be
  suppressed (e.g., framework-dispatched event subscribers, emitted as
  ``kind="method"`` with ``meta["framework_role"]="event_subscriber"``).
- ``is_excluded_kind(kind, meta, *, language=None)``: the public
  predicate consumers should call. A ``section`` whose language is known
  is excluded only in a document language
  (``noise_filter._NOISE_SECTION_LANGUAGES``), because a COBOL section is
  a callable. Otherwise it is True when ``kind in EXCLUDED_KINDS``, or
  when ``kind`` is
  ``function`` / ``method`` and ``meta.get("framework_role")`` is in
  ``EXCLUDED_FRAMEWORK_ROLES``. Only the canonical-kind-plus-role shape
  is matched: a symbol that carries a role literal as its kind (such as
  ``kind="event_subscriber"``) is not excluded.

Key-symbol selection is the population policy sketch ranks over and compact
applies (its centrality path filters to it; its connectivity path takes seeds
only from it). ``KEY_SYMBOL_KINDS`` is an allowlist of declaration kinds across
languages; ``is_key_symbol`` requires a kind in it, a symbol that is not
test code (neither in a test file nor carrying a test annotation,
``paths.is_test_node``), no ``test_`` substring in the name, and
``supply_chain_tier != 4``; ``key_symbols`` filters a list by it; and
``production_edges`` drops edges whose source symbol is test code by the
same predicate (edges into test code are kept). The
allowlist is deliberately not the complement of the ``EXCLUDED_KINDS``
denylist: ``variable`` is in both.

Why This Design
---------------
Centralizing these filters keeps all output modes consistent: separate
copies of a pattern set drift apart, and two views then disagree on which
files are tests. The
predicate lets consumers exclude framework-role symbols, which producers
emit as a canonical kind plus ``meta["framework_role"]``, without
widening ``EXCLUDED_KINDS`` to ``method`` (which would drop every real
method).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..noise_filter import _NOISE_SECTION_LANGUAGES
from ..paths import is_test_file, is_test_node

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from ..ir import Edge, Symbol

# Symbol kinds to exclude from tiered output.
# These have high centrality but don't represent useful code.
#
# Every member is a language-construct kind in the symbol-kind registry
# (``symbol_kinds.SYMBOL_KINDS``). Variants are excluded through their
# canonical kind plus a meta key: dev dependencies are ``kind="dependency"``
# with ``meta["dependency_scope"]="dev"``; scripts and module files are
# ``kind="file"`` with ``meta["entry_role"]="script"`` or
# ``meta["module_system"]``; npm packages are ``kind="package"`` with
# ``meta["package_ecosystem"]="npm"``. A producer that still emits a variant
# literal as its kind (``devDependency``, ``script``, ``module_file``,
# ``npm_package``) is not excluded. Framework-dispatched event subscribers are
# ``kind="method"`` and are excluded through ``EXCLUDED_FRAMEWORK_ROLES`` and
# :func:`is_excluded_kind`, not through this set. (history: ADR-0027, WI-jukav)
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
    "section",          # document headings; COBOL's are callables (INV-siza)
    "code_block",       # markdown fenced code blocks
    "link",             # markdown links
})

# ``meta["framework_role"]`` values that :func:`is_excluded_kind`
# treats as excluded. Kept apart from ``EXCLUDED_KINDS`` because these
# values live on ``Symbol.meta``, not on ``Symbol.kind``, so the
# symbol-kind drift linter must not check them against ``SYMBOL_KINDS``;
# their vocabulary is the ``framework_role`` meta key in
# :mod:`hypergumbo_core.axis_meta_keys`.
EXCLUDED_FRAMEWORK_ROLES = frozenset({
    "event_subscriber",  # CSS/JS event handlers (less useful in isolation)
})


def is_excluded_kind(
    kind: str,
    meta: Optional[Dict[str, Any]] = None,
    *,
    language: Optional[str] = None,
) -> bool:
    """Whether a symbol is excluded by kind or by framework role.

    Besides ``kind in EXCLUDED_KINDS``, matches a symbol whose ``kind`` is
    the canonical language construct ``"function"`` or ``"method"`` and
    whose ``meta["framework_role"]`` is in ``EXCLUDED_FRAMEWORK_ROLES``. A
    symbol carrying a role literal directly as its kind (such as
    ``kind="event_subscriber"``) is not matched.

    Why this lives here rather than as ``sym.kind in EXCLUDED_KINDS``
    inline: synthetic nodes (Phoenix Channels event subscribers, Django
    signal receivers, etc.) emit ``kind="method"`` plus a
    ``framework_role`` meta key. The bare set membership check does not
    exclude them, silently inflating selection / compact output
    with framework-emitted synthetics. Widening the set to
    include ``"method"`` over-excludes every real method, so the check
    goes through this predicate instead. (history: WI-jukav)

    Args:
        kind: Symbol's ``kind`` field.
        meta: Symbol's ``meta`` dict (or ``None`` if no meta).
        language: Symbol's ``language``. ``section`` is a heading in the
            document languages and a callable in COBOL (INV-siza), so a
            known language outside ``noise_filter._NOISE_SECTION_LANGUAGES``
            keeps its sections; ``None`` keeps the kind-only answer.

    Returns:
        ``True`` iff the symbol should be excluded by selection /
        compact filters.

    Same shape as :func:`hypergumbo_core.linkers.registry._is_synthetic_node`,
    which matches ``SYNTHETIC_FRAMEWORK_ROLES`` the same way.
    """
    if kind == "section" and language is not None:
        return language in _NOISE_SECTION_LANGUAGES
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
    """Whether *path* is a test file: :func:`paths.is_test_file`, by this name.

    Do not add test-file shapes here: they belong in
    ``paths.classify_test_file``, so that a view filtering with this name and
    a view filtering with ``is_test_file`` agree on every file. A NODE's
    test-ness (annotations too) is :func:`paths.is_test_node`.
    (history: WI-dibuf)
    """
    return bool(path) and is_test_file(path)


# Symbol kinds that count as a "key symbol" — the declaration kinds a reader
# is asking about when they ask what a repository contains.
#
# Sketch and compact both rank with ``compute_dampened_centrality``; they agree
# on their top symbols only if they also rank the same POPULATION, so both take
# it from this set and the predicate below rather than keeping a private copy.
# (history: WI-zulij)
#
# Include OOP kinds (function, class, method) plus language-specific
# equivalents:
# - Nix: binding, derivation, input (core abstractions)
# - Terraform/HCL: resource, data, module, variable, output, provider, local
# - Elixir/Erlang: module, macro, record, type
# - Elm/F#: module, type, port, record, union, value
# - SQL: table, view, procedure, trigger
# - Dockerfile: stage
# - Lean: theorem, inductive, instance
# - Agda: data (algebraic data types)
# - Fortran/COBOL: program, subroutine
# - VHDL: entity, architecture, component
# - Other: struct, enum, trait, interface, protocol, object
#
# Every member is a language-construct kind in the symbol-kind registry
# (``symbol_kinds.SYMBOL_KINDS``).
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

    A key symbol is a declaration of a kind in :data:`KEY_SYMBOL_KINDS`, not
    test code (``paths.is_test_node``: a test file or a test annotation), not
    named like a test function, and not a derived artifact.

    The ``"test_" not in symbol.name`` clause is deliberately a SUBSTRING test
    rather than a prefix test: it also catches ``helper_test_case`` and the
    like. Sketch and compact share this one definition, so narrowing it changes
    both surfaces' output together; make that a deliberate decision about both
    rather than a local tweak.
    """
    return (
        symbol.kind in KEY_SYMBOL_KINDS
        and not is_test_node(symbol.path, symbol.meta)
        and "test_" not in symbol.name
        and symbol.supply_chain_tier != 4
    )


def key_symbols(symbols: List["Symbol"]) -> List["Symbol"]:
    """Filter *symbols* to the key-symbol population. See :func:`is_key_symbol`."""
    return [s for s in symbols if is_key_symbol(s)]


def production_edges(
    symbols: List["Symbol"], edges: List["Edge"]
) -> List["Edge"]:
    """Drop edges ORIGINATING in test code (``paths.is_test_node``).

    Centrality computed over test-sourced edges credits a symbol for being
    called by its own test suite, which inflates well-tested internals over
    genuinely central code. Filtering by source only (not destination) is
    deliberate: an edge INTO test code is still evidence about the target.

    Apply it together with :func:`key_symbols`: the two are one policy, and a
    surface that adopts the symbol filter without the edge filter ranks the
    same population as the others with different weights.
    """
    by_id = {s.id: s for s in symbols}

    def from_test(edge: Any) -> bool:
        src = by_id.get(getattr(edge, "src", ""))
        return src is not None and is_test_node(src.path, src.meta)

    return [e for e in edges if not from_test(e)]


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
