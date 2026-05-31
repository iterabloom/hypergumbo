# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-language separator policy for ``Symbol.qualified_name`` (ADR-0032).

ADR-0032 introduces ``Symbol.qualified_name: Optional[str]`` as the typed
sibling field for the fully-qualified-name semantic that
``Symbol.canonical_name`` previously carried (Use 3 in the desire-paths
analysis). For code-language Symbols, the field records a fully-qualified
dotted/scoped identifier — ``hypergumbo_core.cli.run_behavior_map`` for
Python, ``hypergumbo_core::cli::run_behavior_map`` for Rust,
``Example\\HelloService::bidiHello`` for PHP.

Staging note: this module lands in Phase 2 PR1 of the ADR-0032 campaign
together with the ``display_label`` typed field, the static-AST axis
registration, and the property tests. The ``Symbol.qualified_name`` typed
field itself lands in Phase 2 PR2 alongside the meta-key retirement and
consumer migration from the existing ``meta["qualified_name"]`` shape.
This phasing keeps Phase 2 PR1 small while letting the catalog module
land first so Phase 2 PR2's atomic meta-to-typed promotion has its
runtime separator policy ready.

Unlike a registry-backed axis with an enumerable value set (e.g.,
``Symbol.protocol_origin``'s catalog), the qualified-name axis has a
**structural policy** rather than a value set: each language declares
which separator its qualified names use. ADR-0024 §4 "use judgment"
carveout permits this lightweight pattern (a module-level declaration
plus accessor functions, no per-value ``*Spec`` dataclass).

How consumers and the validator use this module
-----------------------------------------------

- The static-AST validator (:mod:`hypergumbo_core.multi_value_field_axis`)
  wires ``"qualified-name"`` into ``_known_axes()`` so the
  ``# axis: qualified-name`` annotation on ``Symbol.qualified_name``
  passes the lint at PR-review time. The wired callable is
  :func:`all_qualified_name_languages` — it returns the set of
  languages with declared separators, used by the validator as the
  "axis-is-wired" sanity check.

- The runtime spec validator (ADR-0033 Phase 3 PR1, future) reads
  :func:`separator_for_language` to verify each emitted
  ``Symbol.qualified_name`` value uses the correct separator for its
  ``Symbol.language``. The check is structural ("does this value match
  the per-language separator?"), not catalog-membership.

- The symbol-field-population campaign (ADR-0032 Phase 4 PR4) reads
  :func:`separator_for_language` at Symbol-emit time so the analyzer
  populating ``qualified_name`` doesn't need to hardcode the separator.

Why a separate module rather than free-text
-------------------------------------------

A ``# axis: free-text`` declaration on ``Symbol.qualified_name`` would
also be defensible — qualified names are open-ended strings no consumer
mechanically branches on. The reason for the separate ``qualified-name``
axis instead:

1. The per-language separator policy is non-trivial documentation worth
   capturing in one place rather than scattering ``"."`` / ``"::"`` /
   ``"\\"`` literals across ten analyzer files.

2. The Phase 3 PR1 axis-conformance validator gains a structural check
   it can run against the live corpus — "every qualified_name value
   uses the right separator for its host language" is a falsifiable
   invariant the free-text classification couldn't surface.

3. Adding a sibling field ``Symbol.qualified_name`` AND classifying it
   as free-text would make the field indistinguishable at the lint
   layer from the other free-text fields on ``Symbol`` (``name``,
   ``path``, ``docstring``, etc.) — losing the per-language structural
   contract the ADR-0032 audit identified.
"""
from __future__ import annotations

from typing import Final


# Per-language separator policy. Languages absent from this mapping have
# no declared qualified-name policy yet; their ``Symbol.qualified_name``
# stays ``None`` until the Phase 4 PR4 population campaign extends them.
# Languages present here will have ``Symbol.qualified_name`` populated by
# their analyzer with their declared separator.
QUALIFIED_NAME_SEPARATORS: Final[dict[str, str]] = {
    # C-family languages and JVM family use dot-separated package paths.
    "python": ".",
    "go": ".",
    "java": ".",
    "csharp": ".",
    "kotlin": ".",
    "swift": ".",
    "typescript": ".",
    "javascript": ".",
    "elixir": ".",
    "scala": ".",
    "groovy": ".",
    "dart": ".",
    # Languages with C++-style scope-resolution operator.
    "rust": "::",
    "cpp": "::",
    "ruby": "::",
    # PHP uses backslash for namespace separators (qualified static-method
    # calls use ``\Namespace\Class::method`` but the namespace boundary
    # is the backslash). Method-on-class within a namespace uses ``::``
    # but qualified_name canonicalises to the backslash form.
    "php": "\\",
    # Lua uses dot for module paths.
    "lua": ".",
    # Perl uses ``::`` for package separators.
    "perl": "::",
    # PowerShell uses backslash for module-qualified cmdlet names.
    "powershell": "\\",
    # TCL uses ``::`` for namespace separators.
    "tcl": "::",
    # Objective-C qualified names are typically Module.Class.method
    # (when imported via Swift) or [Receiver method:] (bare). The Symbol
    # we emit is dot-separated for cross-language ergonomics.
    "objc": ".",
}


def separator_for_language(language: str) -> str | None:
    """Return the qualified-name separator for *language*, or ``None``.

    ``None`` means the language has no declared qualified-name policy
    yet; analyzers for these languages should leave
    ``Symbol.qualified_name = None``. The Phase 4 PR4 population
    campaign extends this mapping language-by-language.
    """
    return QUALIFIED_NAME_SEPARATORS.get(language)


def all_qualified_name_languages() -> frozenset[str]:
    """Return the set of languages with a declared qualified-name policy.

    This is the accessor wired into
    ``multi_value_field_axis._known_axes()`` for the ``qualified-name``
    axis. The set serves two purposes: (a) confirming the axis is
    wired at lint time, (b) documenting which analyzers should emit
    ``Symbol.qualified_name`` values.
    """
    return frozenset(QUALIFIED_NAME_SEPARATORS.keys())
