# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared helpers for reading ``symbol.meta['concepts']`` in Framework
linkers.

Every Framework-subcategory linker that dispatches on concept tags
(e.g. ``middleware``, ``route``, ``model``, ``command``) must treat the
concept list as ``list[dict]`` with a ``"concept"`` string key — that is
the shape that ``framework_patterns.py`` emits. See INV-tuzub for the
invariant and the concept_tag_audit_04172026_0335.md report for why
this matters: prior to the helper, one linker (middleware_chain.py)
diverged to a ``list[str]`` membership test, making it silently inert
in production despite passing 100%-coverage tests.

Use ``has_concept(sym, name)`` to filter and ``get_concept(sym, name)``
to read the full dict (including ``path``, ``method``, and other
extract-* keys).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..ir import Symbol


def _iter_concept_dicts(symbol: Symbol) -> list[dict[str, Any]]:
    """Return the symbol's concept-tag dicts, or an empty list.

    Tolerates ``symbol.meta is None`` and non-dict entries inside the
    concept list (defensive; no such entries appear in production).
    """
    meta = symbol.meta
    if not meta:
        return []
    raw = meta.get("concepts", [])
    return [c for c in raw if isinstance(c, dict)]


def has_concept(symbol: Symbol, name: str) -> bool:
    """Return True iff ``symbol`` carries a concept dict with
    ``concept == name``."""
    return any(c.get("concept") == name for c in _iter_concept_dicts(symbol))


def get_concept(symbol: Symbol, name: str) -> Optional[dict[str, Any]]:
    """Return the first concept dict with ``concept == name``, or
    ``None``."""
    for c in _iter_concept_dicts(symbol):
        if c.get("concept") == name:
            return c
    return None
