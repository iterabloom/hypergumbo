# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where an edge comes FROM: the src of every edge is an emitted symbol or the file.

The defect this module exists for
---------------------------------
An analyzer mints an edge's ``src`` from the declaration that contains the call.
When that declaration is a shape the analyzer does not recognise, one of two
things went wrong, both silent:

- **Absent.** The declaration has no symbol and the walk drops the call:
  ``procedure TA.Run`` in Pascal (WI-darik), a CUDA function returning a pointer
  (WI-fohuh), a C definition named by a macro tree-sitter cannot expand
  (WI-tikop). The call graph shows nothing where there is code.
- **Dangling.** The walk mints the caller id from the declaration's POSITION
  although Pass 1 never registered a symbol there: a Pascal nested procedure
  (WI-sigit). The edge's src names no node. Downstream,
  ``ir.create_boundary_nodes`` turns every dangling endpoint into an
  ``external_symbol``, so first-party code reaches the output dressed as a
  library boundary, and the endpoint-integrity check in ``spec_validator``
  (WI-mujor) runs AFTER that synthesis, so it cannot see the producer's defect.

What this module provides
-------------------------
- :func:`unemitted_edge_sources` -- the gate. The edges of one analyzer result
  whose ``src`` is neither an emitted symbol's id nor a ``make_file_id`` id (the
  file anchor, which the orchestrator materialises). Producer tests assert it is
  empty on the production path (``analyze_*`` over a fixture); a dangling src
  can then no longer pass as anchored.
- :func:`anchor_in_definitions` -- the rule for a call inside a definition that
  has no symbol and no honest name (an unexpandable macro): the call is drawn
  from the nearest ENCLOSING record that has a symbol (an outer definition, or
  the file) and the edge says so (:func:`mark_stand_in`). Inventing a name from
  the macro (``PFX``, ``TEST_BEGIN``'s argument) would assert a declaration the
  source does not spell; dropping the call would assert there is no call.
- :data:`SRC_STANDS_IN_FOR` / :data:`UNNAMED_DEFINITION` -- the edge meta key and
  its one value, registered in ``axis_meta_keys`` (per call site: two sites
  collapsed into one edge may disagree).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, AbstractSet, Any, Iterable, Optional

from .base import SymbolsAt, symbol_declared_by

if TYPE_CHECKING:
    from ..ir import Edge, Symbol

#: ``Edge.meta`` key: the edge's ``src`` is NOT the caller; it stands in for one.
SRC_STANDS_IN_FOR = "src_stands_in_for"

#: The value of :data:`SRC_STANDS_IN_FOR` when the caller is a definition the
#: analyzer could not name (its name comes from a macro tree-sitter cannot
#: expand), so the src is the nearest enclosing record that has a symbol.
UNNAMED_DEFINITION = "unnamed_definition"

#: The id suffix ``make_file_id`` gives every file anchor.
_FILE_ID_SUFFIX = ":1-1:file:file"


def unemitted_edge_sources(
    symbols: Iterable["Symbol"], edges: Iterable["Edge"],
) -> list["Edge"]:
    """The edges whose ``src`` names no emitted symbol and is not a file anchor.

    ``symbols`` and ``edges`` are one analyzer result's. A file-anchor src (a
    ``make_file_id`` id) passes: the orchestrator materialises the file symbol
    (``synthesize_file_symbols_for_dangling_edges``). Anything else that names
    no symbol is a producer defect: the caller id was minted for a declaration
    Pass 1 never emitted.
    """
    ids = {s.id for s in symbols}
    return [
        e for e in edges
        if e.src not in ids and not e.src.endswith(_FILE_ID_SUFFIX)
    ]


def anchor_in_definitions(
    node: Any,
    index: SymbolsAt,
    definition_types: AbstractSet[str],
    *,
    top_level: Optional["Symbol"],
    fallback: Optional["Symbol"],
) -> tuple[Optional["Symbol"], bool]:
    """The symbol a call at ``node`` is drawn from, and whether it stands in.

    Walks up from ``node`` to its innermost ancestor of ``definition_types``,
    found by POSITION (:func:`symbol_declared_by`, INV-midag):

    - none: ``(top_level, False)`` -- the caller's rule for code in no
      definition (the file anchor, or None where file-scope calls are not
      emitted);
    - it has a symbol: ``(that symbol, False)``;
    - it has none: ``(nearest outer definition with a symbol, or fallback,
      True)``. The ``True`` is the caller's cue to :func:`mark_stand_in` every
      edge the call yields.
    """
    current = node.parent
    while current is not None and current.type not in definition_types:
        current = current.parent
    if current is None:
        return top_level, False
    own = symbol_declared_by(current, index)
    if own is not None:
        return own, False
    outer = current.parent
    while outer is not None:
        if outer.type in definition_types:
            sym = symbol_declared_by(outer, index)
            if sym is not None:
                return sym, True
        outer = outer.parent
    return fallback, True


def mark_stand_in(edges: list["Edge"], start: int, src_id: str) -> None:
    """Stamp :data:`SRC_STANDS_IN_FOR` on ``edges[start:]`` drawn from ``src_id``.

    Called after the arm that handled one call, over the edges it appended, so
    every edge that call yields carries the marker (the INV-kaduh shape: one
    stamp after the branch, not one per ``Edge.create`` inside it). An edge in
    the slice from another src (an include edge drawn from the file) is left
    alone.
    """
    for edge in edges[start:]:
        if edge.src == src_id:
            edge.meta = dict(edge.meta or {})
            edge.meta[SRC_STANDS_IN_FOR] = UNNAMED_DEFINITION
