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
  Document's Definition Occurrences and keep every one whose EXTENT
  fully contains O's span (start ≤ O.start and end ≥ O.end on both
  the line and column axes). We then pick the *innermost* — smallest
  span area, measured as ``(end_line - start_line, end_col)`` on a
  lexicographic comparison — and break ties by document order so the
  result is deterministic without depending on the protobuf parser's
  iteration stability for equal keys.

* A Definition's EXTENT is its ``enclosing_range`` when the emitter
  populated it, else its ``range`` (``_definition_extent``). The two
  are different things: ``range`` is where the symbol's NAME is,
  ``enclosing_range`` is where the whole item is. rust-analyzer's
  Definition ``range`` is the identifier token — one line — so with
  ``range`` alone no reference inside a function body was ever inside
  a function, and the only Definition that contained anything was the
  document's ``crate/`` namespace spanning the whole file. Measured
  on aardvark-dns after the locals were dropped: 182 of 182 SCIP edges
  were sourced from a namespace and 0 from a callable (INV-mofiv). An
  earlier version of this docstring, and of :mod:`.edges`, said
  ``enclosing_range`` "is rarely populated in practice"; rust-analyzer
  1.94.0 populates it on every Definition occurrence with exactly the
  item range (20 of 20 on the recorded sample: ``increment``
  range=[13,11,20], enclosing_range=[13,4,16,5]). Emitters that leave
  it empty keep the ``range`` behaviour. ``Symbol.span`` in
  :mod:`.index` is deliberately NOT moved to ``enclosing_range`` here:
  that makes the rust.py stable_id parity helper match, and the
  within-file collision splitter would then re-mint the SCIP record
  as a second site — a ruling WI-gojum owns.

* Occurrences whose enclosing Definition cannot be found (module-top-
  level statements, imports at file scope, or occurrences that precede
  the first Definition in the Document) are skipped silently.
  Attributing them to a phantom caller — say, the first Definition in
  document order — would introduce false edges that slice / rank code
  cannot distinguish from genuine call graph.

* Definition occurrences themselves do not emit edges. A Definition is
  the introduction of a symbol, not a reference to it.

* Occurrences of a local symbol (``local <id>``) emit nothing, and a
  local's Definition is never a candidate encloser (WI-jikok /
  INV-kukiz). :mod:`.index` does not mint locals — the id is
  document-scoped, so the same string is a different binding in every
  file — and an edge naming one would name an endpoint no Symbol
  carries. In resolved mode the resolver would drop it anyway; the
  filter here makes raw mode tell the same truth. On aardvark-dns the
  unfiltered shim emitted 455 local-pointing edges, 329 of them
  resolved across files.

* Self-edges (enclosing symbol == occurrence symbol) are dropped as
  noise. Recursive calls in particular show up here — rank / slice
  code treats self-edges as uninformative, and the AST analyzer for
  Python / Rust / etc. already emits recursive-call edges via its own
  path when needed.

Edge shape:

* ``edge_type`` is decided by the TARGET's declared kind (WI-zapuk,
  ADR-0057 §7): a reference whose target the producer declares callable
  (``SymbolInformation.kind`` in Function, Method, StaticMethod,
  TraitMethod, AbstractMethod, ProtocolMethod, PureVirtualMethod,
  Constructor) is ``calls``; every other reference — and every reference
  to a target whose kind the emitter left unset — is ``references``.
  Until WI-zapuk every occurrence edge was ``references`` "so a
  downstream specialization pass can refine to calls / writes_to /
  imports when it has target-kind context"; no such pass existed, and
  the tree-sitter arm labels the same call sites ``calls``, so under
  ``edge_key = (src, dst, edge_type)`` none of the shared call sites
  was a duplicate the merge pass could fold (121 of 130 callable-target
  SCIP edges on the recorded aardvark-dns fixture have a tree-sitter
  ``calls`` twin on the paired endpoints). The row prescribed mapping
  ``Occurrence.symbol_roles`` instead; measured on the recording,
  rust-analyzer 1.94.0 sets ``symbol_roles = 0`` on 3,543 of 3,543
  reference occurrences, so there is nothing there to map. The bitfield
  is still preserved in ``meta["symbol_roles"]``, and NO read / write
  edge type is minted from it: no recorded producer sets those bits
  yet, and a mapping nothing exercises is a claim (ADR-0057 §12). What
  this rule over-claims: a callable referenced as a VALUE (a function
  passed to ``.map``) is ``calls`` too — SCIP carries no call-position
  signal — which is why the confidence below stays at the
  occurrence-ref level rather than rising to a syntactic call's.

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

from typing import Any, Callable, List, Optional, Tuple

from ..ir import Edge
from ._generated import scip_pb2
from .descriptor import is_local_symbol
from .index import symbol_information_kind_values


_ROLE_DEFINITION = 0x01

_EVIDENCE_TYPE = "scip_occurrence_ref"
_CONFIDENCE = 0.85

#: ``SymbolInformation.kind`` values a producer uses for a callable. A
#: reference to one of these is a call site (see the module docstring for
#: the over-claim this accepts and why role bits cannot decide instead).
_CALLABLE_KIND_VALUES: frozenset[int] = frozenset(
    symbol_information_kind_values()[name]
    for name in (
        "Function",
        "Method",
        "StaticMethod",
        "TraitMethod",
        "AbstractMethod",
        "ProtocolMethod",
        "PureVirtualMethod",
        "Constructor",
    )
)


def _declared_kinds(index: Any) -> "dict[str, int]":
    """``symbol -> SymbolInformation.kind`` over EVERY document.

    The kind lives with the DEFINING document's ``SymbolInformation``; a
    cross-file call must read it from there, not from the caller's document.
    """
    kinds: "dict[str, int]" = {}
    for doc in index.documents:
        for sym_info in doc.symbols:
            if sym_info.kind:
                kinds[sym_info.symbol] = int(sym_info.kind)
    return kinds


def _edge_type_for(target_symbol: str, declared_kinds: "dict[str, int]") -> str:
    return "calls" if declared_kinds.get(target_symbol) in _CALLABLE_KIND_VALUES else "references"


def _definition_extent(occ: Any) -> Optional[Tuple[int, int, int, int]]:
    """The span a Definition occurrence ENCLOSES, for attribution.

    ``enclosing_range`` when populated and well-formed, else ``range``.
    ``occ`` is a ``scip_pb2.Occurrence``; typed ``Any`` because the
    generated module has no stubs and a precise name would grow the
    mypy strict ratchet (name-defined), as :mod:`.index` also avoids.
    A malformed ``enclosing_range`` (a length other than 3 or 4) is
    treated as absent rather than as fatal, for the same reason a
    malformed ``range`` skips the occurrence: a buggy emitter must not
    abort the pass. See the module docstring for why the distinction
    decides whether this shim attributes to callables at all.
    """
    if len(occ.enclosing_range):
        extent = _parse_range(list(occ.enclosing_range))
        if extent is not None:
            return extent
    return _parse_range(list(occ.range))


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
    run_id: str,
) -> List[Edge]:
    """Emit hypergumbo ``Edge`` objects for SCIP non-Definition Occurrences.

    See the module docstring for enclosure semantics and edge shape.
    """
    out: List[Edge] = []
    declared_kinds = _declared_kinds(index)
    for doc in index.documents:
        # Build the definition list once per document, ordered to make
        # tie-breaking deterministic (equal-area ties go to the first
        # defined symbol).
        defs: "list[tuple[Tuple[int,int,int,int], str]]" = []
        for occ in doc.occurrences:
            if not (occ.symbol_roles & _ROLE_DEFINITION):
                continue
            if is_local_symbol(occ.symbol):
                continue
            span = _definition_extent(occ)
            if span is None:
                continue
            defs.append((span, occ.symbol))

        for occ in doc.occurrences:
            if occ.symbol_roles & _ROLE_DEFINITION:
                continue
            if is_local_symbol(occ.symbol):
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
                edge_type=_edge_type_for(occ.symbol, declared_kinds),
                line=ref_span[0] + 1,
                origin="scip",
                evidence_type=_EVIDENCE_TYPE,
                confidence=_CONFIDENCE,
                meta={
                    "scip_src_symbol": enclosing,
                    "scip_dst_symbol": occ.symbol,
                    "symbol_roles": int(occ.symbol_roles),
                },
                origin_run_id=run_id,
            ))
    return out
