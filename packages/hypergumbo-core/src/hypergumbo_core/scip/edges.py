# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIP ``Index`` → hypergumbo ``Edge`` translation shim (WI-mafut Phase 2 Slice C).

This module turns ``SymbolInformation.relationships`` entries on a parsed
SCIP Index into hypergumbo :class:`~hypergumbo_core.ir.Edge` objects. It
sits alongside :mod:`hypergumbo_core.scip.index` (which handles the
Symbol side) and reads the same parsed index — the two functions are
deliberately split so callers can run either independently.

Scope pinned in Slice C:

* Only ``SymbolInformation.relationships`` is consumed. Occurrence-level
  refs (non-``Definition`` ``Occurrence`` entries, which conceptually
  represent call / read / write sites) are **not** turned into edges by
  this shim. Converting a ref occurrence to a call-graph edge requires
  resolving the *enclosing* symbol that contains the occurrence span,
  which SCIP does not encode directly on the ``Occurrence`` (the
  ``enclosing_range`` field is rarely populated in practice). A later
  slice / linker will own that span-based resolution against the Symbol
  list produced by Slice B. Keeping Slice C narrow means the shim is a
  pure, deterministic projection of explicit SCIP relationships, which
  is easy to test and review.

* Per WI-zakub §1, rust-analyzer leaves ``relationships`` empty; its
  trait-dispatch information lives inside the SCIP symbol string's
  descriptor chain. This module does not reconstruct those — that's
  the rust-analyzer-specific descriptor decoder planned as a sibling
  module in the ``hypergumbo-lang-rust-analyzer`` package (WI-duzul).

Edge direction convention:

    edge.src = the enclosing SymbolInformation.symbol
    edge.dst = the related Relationship.symbol

So ``X implements Y`` becomes ``src=X, dst=Y, edge_type="implements"``,
matching how most hypergumbo linkers emit ``dispatches_to`` /
``implements`` edges.

SCIP ``Relationship`` → edge type map:

* ``is_implementation=True`` → ``implements``
* ``is_type_definition=True`` → ``has_type``
* ``is_reference=True`` → ``references``
* ``is_definition=True`` → ``defined_by``

Multiple flags on the same Relationship entry fan out to multiple
Edges. A Relationship with no flag set produces nothing (legal but
uninformative per the SCIP spec). Self-relationships (``src == dst``)
are dropped because downstream rank / slice code treats them as noise.

Symbol resolution:

By default the shim emits Edges with ``src`` / ``dst`` set to the raw
SCIP symbol strings. Callers are expected to resolve those against the
Slice B output's ``Symbol.stable_id`` field (which also holds the raw
SCIP string) to produce fully-qualified hypergumbo IDs. A caller-
provided ``resolve_symbol`` callable remaps either endpoint; if it
returns ``None`` for either endpoint, the Edge is dropped rather than
emitted with a partial / raw endpoint. This matches the same-pass-
emits-resolved pattern used by other hypergumbo shims.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from ..ir import Edge
from ._generated import scip_pb2


_RELATION_EDGE_TYPES: "list[tuple[str, str]]" = [
    ("is_implementation", "implements"),
    ("is_type_definition", "has_type"),
    ("is_reference", "references"),
    ("is_definition", "defined_by"),
]

_SCIP_EVIDENCE_TYPE = "scip_relationship"
_SCIP_EDGE_CONFIDENCE = 0.9  # Explicit SCIP relationship from the upstream indexer


def scip_index_to_edges(
    index: scip_pb2.Index,
    *,
    resolve_symbol: Optional[Callable[[str], Optional[str]]] = None,
) -> List[Edge]:
    """Emit hypergumbo ``Edge`` objects from a parsed SCIP ``Index``.

    Only ``SymbolInformation.relationships`` is consumed; see module
    docstring for the scope boundary.

    Args:
        index: A parsed ``scip_pb2.Index``.
        resolve_symbol: Optional callable mapping SCIP symbol strings
            to hypergumbo Symbol IDs. When provided, the Edge is dropped
            if the resolver returns ``None`` for either endpoint.

    Returns:
        A list of ``Edge`` objects. Order follows the document /
        relationship / flag order in the input Index; callers that care
        about stability should rely on that ordering, not on sets.
    """
    out: List[Edge] = []
    for doc in index.documents:
        for sym_info in doc.symbols:
            src_raw = sym_info.symbol
            src_resolved = _resolve(src_raw, resolve_symbol)
            if src_resolved is None:
                continue
            for rel in sym_info.relationships:
                dst_raw = rel.symbol
                if dst_raw == src_raw:
                    continue
                dst_resolved = _resolve(dst_raw, resolve_symbol)
                if dst_resolved is None:
                    continue
                for flag_name, edge_type in _RELATION_EDGE_TYPES:
                    if not getattr(rel, flag_name, False):
                        continue
                    out.append(Edge.create(
                        src=src_resolved,
                        dst=dst_resolved,
                        edge_type=edge_type,
                        line=0,
                        origin="scip",
                        evidence_type=_SCIP_EVIDENCE_TYPE,
                        confidence=_SCIP_EDGE_CONFIDENCE,
                        meta={
                            "scip_src_symbol": src_raw,
                            "scip_dst_symbol": dst_raw,
                            "scip_relationship_flag": flag_name,
                        },
                    ))
    return out


def _resolve(
    scip_symbol: str,
    resolver: Optional[Callable[[str], Optional[str]]],
) -> Optional[str]:
    """Apply the caller's resolver when present, else pass through.

    The passthrough case keeps the shim usable as a pure translator in
    contexts that want raw SCIP symbol strings on the Edge endpoints
    (e.g. the Slice C unit tests, or a downstream caller that builds
    its own mapping from the Slice B Symbol list afterwards).
    """
    if resolver is None:
        return scip_symbol
    return resolver(scip_symbol)
