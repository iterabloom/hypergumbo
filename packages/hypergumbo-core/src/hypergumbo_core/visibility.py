# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical visibility axis (INV-jusot).

Before this module, "is this symbol publicly visible?" was encoded two
incompatible ways that could silently disagree:

1. A per-language ``modifiers`` list with **asymmetric polarity** and a
   different vocabulary per language — Python emits only the negative
   ``private`` (never a positive marker); C#/Java emit ``public``; Go
   ``exported`` / ``unexported``; Rust ``pub``; Solidity ``external``. A
   consumer asking "is this exported" needed per-language polarity logic
   (Python: absence-of-private; others: presence-of-public).
2. A separate path-heuristic boolean (``supply_chain.is_exported``).

The two encodings disagreed on a per-symbol basis (7 contradictions on the
self-corpus): ``_``-prefixed Python functions inside ``src/`` paths that the
path heuristic marked exported, and Solidity ``external`` functions under
``tests/`` that the path heuristic marked non-exported.

This module declares the single canonical visibility vocabulary and the
mapping from each language's native modifier term onto it. The
``Symbol.visibility`` field is COMPUTED once (in finalize) from the
highest-priority available signal — **a language modifier wins over the
Python name convention, which wins over the default** — and the deciding
signal is recorded in ``Symbol.meta['visibility_signal']`` for provenance.

Why language-syntax wins over the path heuristic: a ``_foo`` Python function
is private by PEP-8 convention *regardless* of living in a published ``src/``
package, and a Solidity ``external`` function is public *regardless* of being
under ``tests/``. The old path heuristic conflated orthogonal concerns
(publishedness, test-penalty) with visibility; the language's own syntax is
authoritative for *visibility*.

``is_exported`` becomes a derived alias (``visibility == 'public'``) and
``modifiers`` keeps only its non-visibility terms (``static`` / ``native`` /
``abstract`` / …) — see the finalize visibility pass.
"""
from __future__ import annotations

from typing import Final

VISIBILITY_PUBLIC: Final[str] = "public"
VISIBILITY_PRIVATE: Final[str] = "private"
VISIBILITY_PROTECTED: Final[str] = "protected"
VISIBILITY_INTERNAL: Final[str] = "internal"
VISIBILITY_PACKAGE: Final[str] = "package"

#: The canonical, fixed visibility vocabulary. Computed-not-derived: this is
#: a closed enum (unlike the registry-derived language / pass-id axes), but it
#: is exposed via :func:`all_known_visibility_levels` and wired into
#: ``multi_value_field_axis._known_axes`` so the ``# axis: visibility`` field
#: annotation resolves the same way every other axis does.
VISIBILITY_LEVELS: Final[frozenset[str]] = frozenset({
    VISIBILITY_PUBLIC,
    VISIBILITY_PRIVATE,
    VISIBILITY_PROTECTED,
    VISIBILITY_INTERNAL,
    VISIBILITY_PACKAGE,
})

#: Per-language native modifier term → canonical visibility level. The
#: asymmetric polarity is normalised here: Python's lone negative ``private``
#: and Go's ``unexported`` map to private; the positive markers from every
#: other language (``public`` / ``pub`` / ``exported`` / ``external`` /
#: ``re_exported``) map to public.
MODIFIER_TO_VISIBILITY: Final[dict[str, str]] = {
    "private": VISIBILITY_PRIVATE,
    "unexported": VISIBILITY_PRIVATE,
    "public": VISIBILITY_PUBLIC,
    "pub": VISIBILITY_PUBLIC,
    "exported": VISIBILITY_PUBLIC,
    "external": VISIBILITY_PUBLIC,
    "re_exported": VISIBILITY_PUBLIC,
    "protected": VISIBILITY_PROTECTED,
    "internal": VISIBILITY_INTERNAL,
    "package-private": VISIBILITY_PACKAGE,
}

#: The ``modifiers`` terms that are PURELY visibility and are stripped from
#: ``modifiers`` in finalize (their level moves to the typed ``visibility``
#: field). Deliberately NARROWER than :data:`MODIFIER_TO_VISIBILITY`:
#: ``re_exported`` implies public visibility (so it stays in the visibility
#: map and computes ``visibility='public'``) but ALSO marks the re-export
#: mechanism — a fact orthogonal to visibility that library-export detection
#: consumes — so it is NOT stripped and survives in ``modifiers``.
VISIBILITY_MODIFIER_TERMS: Final[frozenset[str]] = (
    frozenset(MODIFIER_TO_VISIBILITY) - {"re_exported"}
)

#: Values of the legacy ``Symbol.meta['visibility']`` key (written by the Apex
#: and Clojure analyzers) → canonical level. Apex's ``global`` (the broadest
#: access modifier) maps to public. This key is a pre-finalize INPUT signal:
#: the finalize visibility pass folds it into the ``visibility`` field and
#: removes it from ``meta`` (INV-jusot retires the ``meta['visibility']`` key
#: in favour of the typed field).
META_VISIBILITY_TO_VISIBILITY: Final[dict[str, str]] = {
    "public": VISIBILITY_PUBLIC,
    "private": VISIBILITY_PRIVATE,
    "protected": VISIBILITY_PROTECTED,
    "global": VISIBILITY_PUBLIC,
    "internal": VISIBILITY_INTERNAL,
}

# Provenance signal values recorded in ``Symbol.meta['visibility_signal']``.
SIGNAL_LANGUAGE_MODIFIER: Final[str] = "language_modifier"
SIGNAL_NAME_CONVENTION: Final[str] = "name_convention"
SIGNAL_DEFAULT: Final[str] = "default"


def all_known_visibility_levels() -> frozenset[str]:
    """Single source of truth for the canonical visibility vocabulary.

    Mirrors ``catalog.all_known_languages`` / ``entrypoints.all_known_
    entrypoint_kinds``; wired into ``multi_value_field_axis._known_axes`` so
    a ``# axis: visibility`` field annotation can be resolved.
    """
    return VISIBILITY_LEVELS


def _is_python_private_name(name: str) -> bool:
    """Leading-underscore (PEP-8) private, excluding dunder protocol names.

    A method may be qualified (``Class.method``); only the final segment's
    name carries the convention. ``__init__`` and other dunders are the
    public protocol, not private.
    """
    base = name.rsplit(".", 1)[-1]
    if base.startswith("__") and base.endswith("__"):
        return False
    return base.startswith("_")


def compute_visibility(
    *,
    modifiers: list[str],
    name: str,
    language: str | None,
    meta_visibility: str | None = None,
) -> tuple[str, str]:
    """Compute the canonical visibility level + the deciding signal (INV-jusot).

    Precedence:

    1. an explicit language ``modifiers`` term (normalised via
       :data:`MODIFIER_TO_VISIBILITY`), else
    2. the legacy ``meta['visibility']`` term (Apex / Clojure, via
       :data:`META_VISIBILITY_TO_VISIBILITY`) — same language-syntactic
       tier, checked second only because most languages use ``modifiers``,
       else
    3. for Python, the leading-underscore name convention, else
    4. the default (``public``).

    Returns ``(level, signal)`` with ``signal`` ∈ {``language_modifier``,
    ``name_convention``, ``default``}.
    """
    for mod in modifiers:
        level = MODIFIER_TO_VISIBILITY.get(mod)
        if level is not None:
            return level, SIGNAL_LANGUAGE_MODIFIER
    if meta_visibility is not None:
        level = META_VISIBILITY_TO_VISIBILITY.get(meta_visibility)
        if level is not None:
            return level, SIGNAL_LANGUAGE_MODIFIER
    if language == "python" and _is_python_private_name(name):
        return VISIBILITY_PRIVATE, SIGNAL_NAME_CONVENTION
    return VISIBILITY_PUBLIC, SIGNAL_DEFAULT
