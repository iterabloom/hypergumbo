# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIP ``Occurrence`` → hypergumbo call-edge translation shim (WI-mafut Phase 2 Slice D, WI-kopav).

This module completes the SCIP ingest by emitting hypergumbo ``Edge``
objects for non-``Definition`` ``Occurrence`` entries — references,
reads, writes, and other "this symbol appears at this span" signals.
Each such occurrence is attributed to its enclosing Definition, so a
downstream slice / reachability / dead-code pass can see the edge
``caller → callee`` even when the upstream language has no static
call graph of its own.

Slices A (vendor), B (Index → Symbol), and C (Relationship → Edge)
landed earlier. Slice D is the final piece: it walks the
``Occurrence`` list of each Document, finds the enclosing
``Definition``-role Occurrence for every non-Definition Occurrence via
span containment, and emits an Edge from the enclosing definition's
symbol to the occurrence's symbol.

Enclosure resolution:

* For each non-Definition Occurrence O in a Document, we iterate the
  Document's Definition Occurrences and keep every one whose span
  fully contains O's span (start ≤ O.start and end ≥ O.end on both
  the line and column axes). We then pick the *innermost* — smallest
  span area, measured as ``(end_line - start_line, end_col)`` on a
  lexicographic comparison — and break ties by document order so the
  result is deterministic without depending on the protobuf parser's
  iteration stability for equal keys.

* Occurrences whose enclosing Definition cannot be found (module-top-
  level statements, imports at file scope, or occurrences that precede
  the first Definition in the Document) are skipped silently.
  Attributing them to a phantom caller — say, the first Definition in
  document order — would introduce false edges that slice / rank code
  cannot distinguish from genuine call graph.

* Definition occurrences themselves do not emit edges. A Definition is
  the introduction of a symbol, not a reference to it.

* Self-edges (enclosing symbol == occurrence symbol) are dropped as
  noise. Recursive calls in particular show up here — rank / slice
  code treats self-edges as uninformative, and the AST analyzer for
  Python / Rust / etc. already emits recursive-call edges via its own
  path when needed.

Edge shape:

* ``edge_type="references"`` uniformly. SCIP's SymbolRole bitfield
  (ReadAccess / WriteAccess / Import / Generated / Test / Definition /
  ForwardDefinition) is preserved in ``meta["symbol_roles"]`` so a
  downstream specialization pass can refine to "calls", "writes_to",
  or "imports" when it has target-kind context. Keeping Slice D
  generic avoids having to re-parse the SCIP symbol string here; the
  Symbol list from Slice B already carries that via ``stable_id``.

* ``evidence_type="scip_occurrence_ref"`` distinguishes these edges
  from Slice C's ``scip_relationship`` edges so the bakeoff-reflect
  prompt and downstream diagnostics can tell explicit SCIP
  relationships apart from span-enclosed references.

* Confidence 0.85 — explicit SCIP-indexer data, but the span-enclosure
  heuristic is a layer of indirection compared to Slice C's direct
  relationship emission, so the floor is slightly lower.

Symbol resolution contract matches :mod:`hypergumbo_core.scip.edges`:
a caller-supplied ``resolve_symbol`` callable remaps either endpoint;
returning ``None`` for either endpoint drops the edge, preventing
half-resolved artefacts in the output.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from ..ir import Edge
from ._generated import scip_pb2


_ROLE_DEFINITION = 0x01

_EVIDENCE_TYPE = "scip_occurrence_ref"
_CONFIDENCE = 0.85


def _parse_range(arr: "list[int]") -> Optional[Tuple[int, int, int, int]]:
    """Convert a SCIP ``Occurrence.range`` to (start_line, start_col, end_line, end_col).

    Returns None for unexpected lengths so the caller can skip the
    occurrence rather than aborting the whole document.
    """
    n = len(arr)
    if n == 3:
        return int(arr[0]), int(arr[1]), int(arr[0]), int(arr[2])
    if n == 4:
        return int(arr[0]), int(arr[1]), int(arr[2]), int(arr[3])
    return None


def _contains(outer: Tuple[int, int, int, int], inner: Tuple[int, int, int, int]) -> bool:
    """True if ``outer`` span fully encloses ``inner`` (line/col-wise).

    A point-position (line=start_line, col=start_col) precedes another
    when its line is less, or its line is equal and its col is less
    or equal.
    """
    o_sl, o_sc, o_el, o_ec = outer
    i_sl, i_sc, i_el, i_ec = inner
    starts_ok = (o_sl, o_sc) <= (i_sl, i_sc)
    ends_ok = (o_el, o_ec) >= (i_el, i_ec)
    return starts_ok and ends_ok


def _area(span: Tuple[int, int, int, int]) -> Tuple[int, int]:
    """Lexicographic span size used to rank enclosing definitions.

    Smaller is tighter. We compare line-span first then column-span so
    a 5-line definition always beats a 20-line one regardless of
    column math, which matches how a human reader thinks about nested
    scope.
    """
    sl, sc, el, ec = span
    return (el - sl, ec - sc)


def scip_index_to_call_edges(
    index: scip_pb2.Index,
    *,
    resolve_symbol: Optional[Callable[[str], Optional[str]]] = None,
) -> List[Edge]:
    """Emit hypergumbo ``Edge`` objects for SCIP non-Definition Occurrences.

    See the module docstring for enclosure semantics and edge shape.
    """
    out: List[Edge] = []
    for doc in index.documents:
        # Build the definition list once per document, ordered to make
        # tie-breaking deterministic (equal-area ties go to the first
        # defined symbol).
        defs: "list[tuple[Tuple[int,int,int,int], str]]" = []
        for occ in doc.occurrences:
            if not (occ.symbol_roles & _ROLE_DEFINITION):
                continue
            span = _parse_range(list(occ.range))
            if span is None:
                continue
            defs.append((span, occ.symbol))

        for occ in doc.occurrences:
            if occ.symbol_roles & _ROLE_DEFINITION:
                continue
            ref_span = _parse_range(list(occ.range))
            if ref_span is None:
                continue

            enclosing: Optional[str] = None
            enclosing_area: Optional[Tuple[int, int]] = None
            for d_span, d_sym in defs:
                if not _contains(d_span, ref_span):
                    continue
                area = _area(d_span)
                if enclosing_area is None or area < enclosing_area:
                    enclosing = d_sym
                    enclosing_area = area

            if enclosing is None or enclosing == occ.symbol:
                continue

            src_resolved = enclosing if resolve_symbol is None else resolve_symbol(enclosing)
            dst_resolved = occ.symbol if resolve_symbol is None else resolve_symbol(occ.symbol)
            if src_resolved is None or dst_resolved is None:
                continue

            out.append(Edge.create(
                src=src_resolved,
                dst=dst_resolved,
                edge_type="references",
                line=ref_span[0] + 1,
                origin="scip",
                evidence_type=_EVIDENCE_TYPE,
                confidence=_CONFIDENCE,
                meta={
                    "scip_src_symbol": enclosing,
                    "scip_dst_symbol": occ.symbol,
                    "symbol_roles": int(occ.symbol_roles),
                },
            ))
    return out
