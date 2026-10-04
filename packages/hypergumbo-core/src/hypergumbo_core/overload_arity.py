# SPDX-License-Identifier: AGPL-3.0-or-later
"""Choose among same-named callables by the call's argument count.

THE DEFECT THIS EXISTS FOR. Several analyzers resolve a call by NAME and then
take whatever the name gives them: one symbol (a name-keyed registry keeps the
last one registered), every symbol (Elixir fanned out to each clause of every
arity), or a ``ListNameResolver`` guess among all visible ones (Nim). None of
them looked at how many arguments the call passes, which every one of these
languages uses to pick the callee. So an overload calling its sibling --
``InitKeywords()`` calling ``InitKeywords(is)`` -- was drawn as a call to
itself (WI-hilum, cpp), ``get_timeout()`` inside ``get_timeout/2`` was a
self-loop (WI-rodiz, elixir), and a two-argument ``add`` took a one-parameter
project overload (WI-bivab, nim).

WHERE ARITY IS READ FROM. ``Symbol.meta["parameters"]``, the declared home of
the parameter-arity fact (ADR-0058, :data:`hypergumbo_core.signature_axis.
FACT_HOMES`). It is never parsed back out of ``Symbol.signature``: that string
is a display surface, and the C++ one does not even list a defaulted or a
variadic parameter. A producer builds each entry with :func:`parameter_entry`,
so every language spells "has a default" and "takes any number" the same way.
An entry's ``default`` and ``variadic`` are what :func:`arity_bounds` reads;
``name`` and ``type`` are for readers. Python's entries (``py.py``) predate the
``variadic`` key and spell it ``*args`` in the name; :func:`arity_bounds` reads
that spelling too, so the helper is not wrong about a symbol it was not written
for, though no Python call site consults it.

ABSENT IS NOT EMPTY. A symbol with no ``parameters`` key has an UNKNOWN arity
and is never excluded; ``[]`` is a callable that takes no arguments. Producers
that adopt this helper write the key for every callable, the empty list
included, so the two cannot be confused.

THE CHOICE (:func:`choose_overload`). Candidates arrive in TIERS, nearest
first (cpp: the picked symbol's own file, then the run; nim: the caller's
file, its module, then what it imports). The first tier holding a candidate
whose bounds admit the argument count decides:

* one admits: that one, at full confidence;
* several admit: arity cannot decide. The record says so --
  ``ambiguous=True``, confidence scaled by ``1/sqrt(N)`` exactly as
  ``ListNameResolver`` scales an ambiguous guess -- and the edge is drawn to
  the caller's ``preferred`` candidate when it is among them (so a choice the
  analyzer made by other evidence survives), else the first by position;
* none admits in any tier: ``symbol=None``; the analyzer decides what that
  means in its language (see each analyzer's call path).

An unknown argument count (a spread, a splice, a macro) admits everything, so
the choice degrades to the pre-arity behaviour rather than inventing a fact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Sequence

if TYPE_CHECKING:
    from .ir import Symbol


#: The ``Symbol.meta`` key holding the structured parameter list.
PARAMETERS_KEY = "parameters"


def parameter_entry(
    name: str,
    type_: Optional[str] = None,
    *,
    default: bool = False,
    variadic: bool = False,
) -> dict[str, Any]:
    """One element of ``meta["parameters"]``, in the shape every producer shares.

    ``variadic`` is written only when true, so an entry for an ordinary
    parameter is exactly the ``{name, type, default}`` triple ``py.py`` has
    always written.
    """
    entry: dict[str, Any] = {"name": name, "type": type_, "default": default}
    if variadic:
        entry["variadic"] = True
    return entry


@dataclass(frozen=True)
class ArityBounds:
    """How many arguments a callable accepts: ``required`` up to ``maximum``.

    ``maximum`` is None when a variadic parameter makes the count unbounded.
    """

    required: int
    maximum: Optional[int]

    def admits(self, arg_count: int) -> bool:
        """True when a call passing ``arg_count`` arguments can bind here."""
        if arg_count < self.required:
            return False
        return self.maximum is None or arg_count <= self.maximum


def bounds_of_entries(entries: Sequence[Any]) -> Optional[ArityBounds]:
    """Arity bounds of a ``meta["parameters"]`` list; None if it is malformed."""
    required = 0
    total = 0
    unbounded = False
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        name = entry.get("name")
        if entry.get("variadic") or (
            isinstance(name, str) and name.startswith("*")
        ):
            unbounded = True
            continue
        total += 1
        if not entry.get("default"):
            required += 1
    return ArityBounds(required=required, maximum=None if unbounded else total)


def arity_bounds(symbol: "Symbol") -> Optional[ArityBounds]:
    """The arity bounds a symbol declares, or None when it declares none.

    None is UNKNOWN, never zero: a symbol whose producer wrote no
    ``parameters`` key may take any number of arguments as far as this
    helper knows.
    """
    entries = (symbol.meta or {}).get(PARAMETERS_KEY)
    if not isinstance(entries, list):
        return None
    return bounds_of_entries(entries)


def narrow_by_arity(
    candidates: Sequence["Symbol"], arg_count: Optional[int],
) -> list["Symbol"]:
    """The candidates a call with ``arg_count`` arguments can bind to.

    A candidate of unknown arity is kept (it cannot be excluded), and an
    unknown ``arg_count`` keeps every candidate.
    """
    if arg_count is None:
        return list(candidates)
    kept: list["Symbol"] = []
    for cand in candidates:
        bounds = arity_bounds(cand)
        if bounds is None or bounds.admits(arg_count):
            kept.append(cand)
    return kept


@dataclass(frozen=True)
class OverloadChoice:
    """The outcome of :func:`choose_overload`.

    ``symbol`` is None when no candidate in any tier admits the call.
    ``admitted`` is the set the choice was made from; ``ambiguous`` is True
    when it held more than one, in which case ``confidence`` is
    ``1/sqrt(len(admitted))`` and ``symbol`` is one of them, not THE callee.
    """

    symbol: Optional["Symbol"]
    confidence: float
    ambiguous: bool
    admitted: tuple["Symbol", ...]


def _position_key(sym: "Symbol") -> tuple[str, int, int]:
    span = sym.span
    return (
        sym.path,
        span.start_line if span is not None else 0,
        span.start_col if span is not None else 0,
    )


def choose_overload(
    tiers: Sequence[Sequence["Symbol"]],
    arg_count: Optional[int],
    *,
    preferred: Optional["Symbol"] = None,
) -> OverloadChoice:
    """Pick the callee among same-named candidates; see the module docstring."""
    for tier in tiers:
        admitted = narrow_by_arity(tier, arg_count)
        if not admitted:
            continue
        if len(admitted) == 1:
            return OverloadChoice(admitted[0], 1.0, False, (admitted[0],))
        ordered = sorted(admitted, key=_position_key)
        pick = preferred if preferred is not None and any(
            c is preferred for c in ordered
        ) else ordered[0]
        return OverloadChoice(
            pick, 1.0 / math.sqrt(len(ordered)), True, tuple(ordered),
        )
    return OverloadChoice(None, 0.0, False, ())
