# SPDX-License-Identifier: AGPL-3.0-or-later
"""Multi-value Symbol-name indexes for analyzers and linkers (WI-sofaf).

## Why this module exists

The ``dict[str, Symbol]`` anti-pattern silently overwrites earlier
candidates when two Symbols share a short name across kinds, files, or
languages. Three sibling bugs surfaced in successive analyzers (Rust
`WI-milak`, VHDL `WI-morud`, Verilog `INV-paroh`) plus five cross-linker
audit-cohort instances (subprocess_cli `WI-jifiv`, route_handler
`WI-rusuh`, grpc `WI-patiz`, di_resolution `WI-fujum`, pyffi
`WI-sifiz`). After the third sibling, ``WI-morud``'s escalation rule
fired: any future analogous bug means we land a shared kind-segregated
multi-value-dict utility instead of another per-linker fix.

Path B-thin: a small importable helper that replaces the silent-
overwrite anti-pattern for name-keyed Symbol lookup. Sized to be cheaper
to import than to rewrite ad-hoc — see the parent ``INV-fosab`` for the
audit-cohort catalogue and the decision background.

## Contract

* :meth:`SymbolByName.add` accumulates (never overwrites).
* :meth:`SymbolByName.lookup` returns all candidates sorted with
  preferred-kind matches first when ``prefer_kind`` is supplied;
  insertion order is preserved as a stable tiebreaker.
* :meth:`SymbolByName.lookup_one` returns ``(symbol, is_fallback)`` so
  callers can apply the ``INV-zuhub`` ambiguous-resolution contract
  (``confidence ≤ 0.5`` + ``meta["disambiguation_fallback"]=True``)
  without having to count candidates themselves.
* ``case_insensitive=True`` matches Verilog's spec-level case-folding.
  Most languages should use the default (case-sensitive).

## Out of scope (by design)

* **Compound keys.** ``route_handler.py``'s ``_RailsIndex`` uses
  ``(action, class_lower)`` pairs. The helper's flat ``name → list``
  shape doesn't cover that. Stay bespoke.
* **Dual-shape registries.** ``grpc.py``'s ``servicer_by_name`` is
  single-value by design (one servicer class per name) and lives in a
  struct alongside ``proto_service_by_name``. Wrapping it in
  ``SymbolByName`` would either lose the single-value invariant or
  require a Path-B-elaborate wrapper. Stay bespoke.
* **Performance optimisation.** Multi-value lookup is still O(1)
  average; the constant-factor delta vs single-value dicts is
  negligible at hypergumbo's scale.

A future ``Path B-elaborate`` could subsume the bespoke cases, but
that's premature — see ``WI-sofaf`` description for the deferral
rationale.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .ir import Symbol


class SymbolByName:
    """Multi-value ``name → Symbol`` index with kind-aware lookup.

    Use instead of ``dict[str, Symbol]`` for any analyzer or linker
    indexing Symbols by short name where collisions are possible across
    kinds, files, or (with case-insensitive mode) case variants.
    """

    __slots__ = ("_case_insensitive", "_index")

    def __init__(self, *, case_insensitive: bool = False) -> None:
        self._case_insensitive = case_insensitive
        self._index: dict[str, list["Symbol"]] = {}

    def _key(self, name: str) -> str:
        return name.lower() if self._case_insensitive else name

    def __contains__(self, name: str) -> bool:
        return self._key(name) in self._index

    def add(self, sym: "Symbol") -> None:
        """Add ``sym`` to the index. Never overwrites prior entries."""
        self._index.setdefault(self._key(sym.name), []).append(sym)

    def lookup(
        self,
        name: str,
        *,
        prefer_kind: Optional[str] = None,
    ) -> list["Symbol"]:
        """Return all Symbols indexed under ``name``.

        When ``prefer_kind`` is supplied, candidates matching that kind
        sort first; insertion order is preserved as a stable tiebreaker
        (Python's ``sort`` is stable).
        """
        candidates = list(self._index.get(self._key(name), []))
        if prefer_kind is not None:
            candidates.sort(key=lambda s: 0 if s.kind == prefer_kind else 1)
        return candidates

    def lookup_one(
        self,
        name: str,
        *,
        prefer_kind: Optional[str] = None,
    ) -> Optional[tuple["Symbol", bool]]:
        """Return ``(symbol, is_fallback)`` for the best match, or ``None``.

        ``is_fallback`` is ``True`` when more than one candidate matched
        ``name`` regardless of ``prefer_kind`` filtering — i.e. the raw
        collision was real. Callers should apply the ``INV-zuhub``
        ambiguous-resolution contract (``confidence ≤ 0.5`` plus
        ``meta["disambiguation_fallback"]=True`` on the resulting edge)
        when ``is_fallback`` is set.
        """
        candidates = self.lookup(name, prefer_kind=prefer_kind)
        if not candidates:
            return None
        return candidates[0], len(candidates) > 1
