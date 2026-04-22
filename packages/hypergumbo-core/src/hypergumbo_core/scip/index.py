# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIP ``Index`` → hypergumbo ``Symbol`` translation shim (WI-mafut Phase 2 Slice B).

This module turns a parsed Sourcegraph SCIP ``Index`` protobuf into a list
of hypergumbo :class:`~hypergumbo_core.ir.Symbol` objects. It is
intentionally narrow: no I/O, no byte decoding, no edge emission. The
caller is expected to have already decoded wire bytes with
``scip_pb2.Index().ParseFromString(buf)`` (or to have built an Index
object directly, as the tests do). Edge emission (``Occurrence`` refs
→ ``calls``, ``Relationship`` → ``implements`` / ``has_type``) lives in
a sibling module and is the subject of Slice C.

Why this is a thin glue layer:

* The non-trivial SCIP work — parsing the ``<scheme> <manager> <package>
  <version> <descriptor>+`` symbol string including WI-zakub's trait-
  dispatch shape ``impl#[T][Trait]method().`` — already lives in Phase 1's
  :mod:`hypergumbo_core.scip.descriptor`. We reuse it unchanged.
* The hypergumbo ``Symbol`` dataclass is already the downstream IR for
  every analyzer; this module just fills the same fields.
* A SCIP ``Document`` is self-contained (its ``symbols`` list names
  everything the ``occurrences`` list refers to), so the translator is
  one linear pass over ``index.documents`` with a tiny
  ``symbol_string → Definition occurrence`` map kept per document.

Offset and indexing conventions pinned here:

* ``Occurrence.range`` is ``repeated int32`` and SCIP encodes it either
  as ``[start_line, start_col, end_col]`` (single-line span) or
  ``[start_line, start_col, end_line, end_col]`` (multi-line span).
  Any other length is a malformed input and raises ``ValueError``.
* SCIP lines are 0-indexed; hypergumbo :class:`~hypergumbo_core.ir.Span`
  lines are 1-indexed. The ``+1`` rewrite happens here so downstream
  sketch/slice/rank code never sees a 0-indexed line number.
* SCIP columns are UTF-8 code-unit offsets by default (WI-zakub §3).
  hypergumbo ``Span`` columns are also offsets, so we pass them through
  unchanged. If a SCIP index ever declares a non-UTF-8
  ``PositionEncoding`` in ``Metadata``, the column values would need
  re-encoding; we don't handle that yet — no in-the-wild emitter uses
  a non-UTF-8 encoding, and adding a conversion pass without a real
  input to test against would be speculative.

Edge cases intentionally handled by skip rather than raise:

* ``SymbolInformation`` with no ``Definition``-role occurrence in the
  document (typically an imported / external symbol) — skipped. Slice C
  will wire those to external-tier nodes.
* Malformed SCIP symbol strings (``parse_scip_symbol`` raises) — skipped
  with no warning. A buggy upstream emitter must not abort the whole
  translation pass; downstream analyses will simply see missing symbols
  rather than a crash.
* ``SymbolInformation`` whose descriptor chain is empty (e.g. header
  with no trailing descriptor) — skipped. The Phase 1 parser raises on
  this input, so the try/except handles it uniformly with the malformed
  case above.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..analyze.base import make_symbol_id
from ..ir import Span, Symbol
from ._generated import scip_pb2
from .descriptor import DescriptorKind, parse_scip_symbol


_ROLE_DEFINITION = 0x01

_KIND_MAP: Dict[DescriptorKind, str] = {
    DescriptorKind.NAMESPACE: "namespace",
    DescriptorKind.TYPE: "class",
    DescriptorKind.TERM: "variable",
    DescriptorKind.METHOD: "method",
    DescriptorKind.MACRO: "macro",
    DescriptorKind.TYPE_PARAMETER: "type_parameter",
    DescriptorKind.PARAMETER: "parameter",
    DescriptorKind.META: "meta",
}


def _span_from_range(range_array: "list[int]") -> Span:
    """Convert a SCIP ``Occurrence.range`` int array into a :class:`Span`.

    SCIP encodes ranges as either a 3-int or 4-int array. 3 ints are a
    single-line span ``[start_line, start_col, end_col]``; 4 ints are a
    multi-line span ``[start_line, start_col, end_line, end_col]``.
    SCIP lines are 0-indexed; we return 1-indexed lines to match the
    rest of hypergumbo's IR.
    """
    n = len(range_array)
    if n == 3:
        start_line, start_col, end_col = range_array
        end_line = start_line
    elif n == 4:
        start_line, start_col, end_line, end_col = range_array
    else:
        raise ValueError(
            f"SCIP range must have 3 or 4 elements; got {n}"
        )
    return Span(
        start_line=int(start_line) + 1,
        end_line=int(end_line) + 1,
        start_col=int(start_col),
        end_col=int(end_col),
    )


def _resolve_language(doc: scip_pb2.Document) -> str:
    """Normalize ``Document.language`` to a lowercase hypergumbo language.

    SCIP emitters set ``Document.language`` to strings like ``"Python"``
    (scip-python), ``"Rust"`` (rust-analyzer), or a lowercase form.
    Hypergumbo analyzers use lowercase identifiers across the board, so
    we flatten here. An empty language field becomes ``"unknown"``
    rather than an empty string so downstream ``id`` construction never
    produces a malformed ``:filename:...`` prefix.
    """
    raw = doc.language or ""
    return raw.lower() if raw else "unknown"


def _name_and_kind(scip_sym: Any) -> "tuple[str, str]":
    """Pick the (name, kind) pair for a parsed :class:`ScipSymbol`.

    For local symbols (``local <id>``) we return the local id as the
    name and ``"local"`` as the kind; those survive translation so
    later link passes that key off SCIP symbol strings can still find
    them. For fully qualified symbols we use the last descriptor in the
    chain — SCIP puts the most specific piece last, so for
    ``module/Class#method().`` the last descriptor is ``method`` with
    the METHOD suffix.
    """
    if scip_sym.is_local:
        return scip_sym.local_id, "local"
    if not scip_sym.descriptors:  # pragma: no cover
        # Defensive: parse_scip_symbol already rejects a header with
        # zero descriptors. Kept as a guard against a future parser
        # regression that would otherwise hand us an invalid ScipSymbol.
        return "", "unknown"
    last = scip_sym.descriptors[-1]
    return last.name, _KIND_MAP.get(last.kind, "unknown")


def _build_meta(sym_info: scip_pb2.SymbolInformation) -> Dict[str, Any]:
    """Collect the SCIP metadata fields worth preserving on a Symbol.

    We always record the raw SCIP symbol string so later passes can
    re-resolve references without re-parsing the descriptor chain.
    ``display_name`` and SCIP ``Kind`` are preserved when non-default
    so downstream renderers can show the upstream-provided labels.
    """
    meta: Dict[str, Any] = {"scip_symbol": sym_info.symbol}
    if sym_info.display_name:
        meta["display_name"] = sym_info.display_name
    if sym_info.kind:
        meta["scip_kind"] = int(sym_info.kind)
    return meta


def scip_index_to_symbols(index: scip_pb2.Index) -> List[Symbol]:
    """Walk a parsed SCIP ``Index`` and emit hypergumbo ``Symbol`` objects.

    Returns one Symbol per ``SymbolInformation`` that has a
    ``Definition``-role ``Occurrence`` in the same document. External
    symbols (no Definition in this index), malformed SCIP symbol
    strings, and Occurrences with out-of-range int arrays other than
    3/4 elements are handled as described in the module docstring.
    """
    out: List[Symbol] = []
    for doc in index.documents:
        lang = _resolve_language(doc)
        def_occ: Dict[str, scip_pb2.Occurrence] = {}
        for occ in doc.occurrences:
            if occ.symbol_roles & _ROLE_DEFINITION and occ.symbol not in def_occ:
                def_occ[occ.symbol] = occ
        for sym_info in doc.symbols:
            occ = def_occ.get(sym_info.symbol)
            if occ is None:
                continue
            try:
                parsed = parse_scip_symbol(sym_info.symbol)
            except ValueError:
                continue
            name, kind = _name_and_kind(parsed)
            if not name:  # pragma: no cover
                # Defensive: only reachable if _name_and_kind's empty-
                # descriptor guard fires, which parse_scip_symbol already
                # precludes. Kept for robustness against parser changes.
                continue
            span = _span_from_range(list(occ.range))
            sid = make_symbol_id(
                lang, doc.relative_path, span.start_line, span.end_line, name, kind
            )
            out.append(
                Symbol(
                    id=sid,
                    name=name,
                    kind=kind,
                    language=lang,
                    path=doc.relative_path,
                    span=span,
                    origin="scip",
                    stable_id=sym_info.symbol,
                    meta=_build_meta(sym_info),
                )
            )
    return out
