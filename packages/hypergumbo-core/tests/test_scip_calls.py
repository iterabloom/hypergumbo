# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the SCIP Occurrence-ref → Edge translator (WI-mafut Phase 2 Slice D, WI-kopav).

These tests drive
:func:`hypergumbo_core.scip.calls.scip_index_to_call_edges`, which
emits hypergumbo Edge objects for non-``Definition`` ``Occurrence``
entries in a SCIP Index. Each such occurrence is a reference site
(function call, variable read, field write, etc.); the edge goes from
the *enclosing* Definition symbol (the caller) to the occurrence's
target symbol.

Behavioural contract pinned here:

* Enclosure resolution is purely span-based. For each non-Definition
  Occurrence O in a Document, we look at all Definition Occurrences
  in the same Document and pick the *innermost* one whose span fully
  contains O's span. Innermost means: smallest span-area, with
  ``start <= O.start`` and ``end >= O.end``. Ties (equal-area)
  deterministically pick the first Definition in document order so
  results are reproducible without a sort key.
* Occurrences whose enclosing Definition cannot be found (top-level
  module statements, imports at module scope, occurrences before the
  first definition) are skipped silently. Attributing them to a
  phantom caller would introduce false edges.
* Edges are emitted with ``edge_type="references"``,
  ``evidence_type="scip_occurrence_ref"``, ``origin="scip"``, and
  meta carrying the scip symbol strings and the role bitfield. A
  downstream linker can specialize to "calls" / "writes_to" / "imports"
  when it has enough target-kind context.
* Self-references (caller symbol == target symbol) are dropped as
  noise.
* ``resolve_symbol`` callable semantics match scip.edges: None for
  either endpoint drops the edge.
"""
from __future__ import annotations

from hypergumbo_core.ir import Edge
from hypergumbo_core.scip._generated import scip_pb2
from hypergumbo_core.scip.calls import scip_index_to_call_edges


DEFINITION_ROLE = 0x01


def _sym(name: str) -> str:
    return f"scip-python pypi pkg 0.1.0 mod/{name}()."


def _doc_with(occurrences, symbols=None, *, path="mod.py"):
    return scip_pb2.Document(
        language="python",
        relative_path=path,
        occurrences=list(occurrences),
        symbols=list(symbols or []),
    )


def _idx(*docs):
    return scip_pb2.Index(documents=list(docs))


# ---------------------------------------------------------------------------
# Happy path: definition encloses a ref
# ---------------------------------------------------------------------------


def test_empty_index_emits_no_edges() -> None:
    assert scip_index_to_call_edges(scip_pb2.Index()) == []


def test_ref_inside_definition_emits_edge() -> None:
    caller = _sym("foo")
    callee = _sym("bar")
    doc = _doc_with(
        symbols=[
            scip_pb2.SymbolInformation(symbol=caller),
            scip_pb2.SymbolInformation(symbol=callee),
        ],
        occurrences=[
            # caller definition spans lines 0..20 (cols 0..0)
            scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
            # callee reference at line 5, cols 4..10 (single-line)
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[5, 4, 10]),
            # callee definition elsewhere (outside caller)
            scip_pb2.Occurrence(symbol=callee, symbol_roles=DEFINITION_ROLE, range=[30, 0, 35, 0]),
        ],
    )
    [edge] = scip_index_to_call_edges(_idx(doc))
    assert isinstance(edge, Edge)
    assert edge.src == caller
    assert edge.dst == callee
    assert edge.edge_type == "references"
    assert edge.origin == "scip"
    assert edge.evidence_type == "scip_occurrence_ref"
    assert edge.line == 6  # 5 + 1 (SCIP 0-index → hypergumbo 1-index)


def test_ref_outside_any_definition_is_dropped() -> None:
    # Top-level expression at line 0, but no definition encloses it.
    callee = _sym("bar")
    doc = _doc_with(
        symbols=[scip_pb2.SymbolInformation(symbol=callee)],
        occurrences=[
            scip_pb2.Occurrence(symbol=callee, symbol_roles=DEFINITION_ROLE, range=[30, 0, 35, 0]),
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[1, 0, 5]),
        ],
    )
    assert scip_index_to_call_edges(_idx(doc)) == []


def test_innermost_definition_wins_when_nested() -> None:
    outer = _sym("outer")
    inner = _sym("inner")
    callee = _sym("bar")
    doc = _doc_with(
        symbols=[
            scip_pb2.SymbolInformation(symbol=outer),
            scip_pb2.SymbolInformation(symbol=inner),
            scip_pb2.SymbolInformation(symbol=callee),
        ],
        occurrences=[
            # outer spans 0..50
            scip_pb2.Occurrence(symbol=outer, symbol_roles=DEFINITION_ROLE, range=[0, 0, 50, 0]),
            # inner spans 10..20 (nested inside outer)
            scip_pb2.Occurrence(symbol=inner, symbol_roles=DEFINITION_ROLE, range=[10, 0, 20, 0]),
            # ref at line 15 — innermost is inner, not outer
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[15, 4, 8]),
        ],
    )
    [edge] = scip_index_to_call_edges(_idx(doc))
    assert edge.src == inner


def test_single_line_definition_and_ref() -> None:
    caller = _sym("oneliner")
    callee = _sym("bar")
    doc = _doc_with(
        symbols=[
            scip_pb2.SymbolInformation(symbol=caller),
            scip_pb2.SymbolInformation(symbol=callee),
        ],
        occurrences=[
            # Single-line definition (3-int range)
            scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[10, 0, 40]),
            # Ref inside it, cols 5..8
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[10, 5, 8]),
        ],
    )
    [edge] = scip_index_to_call_edges(_idx(doc))
    assert edge.src == caller


def test_definition_occurrence_itself_does_not_emit_edge() -> None:
    # A Definition occurrence is the *introduction* of a symbol, not a
    # reference. It must not generate an edge back to itself.
    caller = _sym("foo")
    doc = _doc_with(
        symbols=[scip_pb2.SymbolInformation(symbol=caller)],
        occurrences=[
            scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 5, 0]),
        ],
    )
    assert scip_index_to_call_edges(_idx(doc)) == []


def test_self_reference_is_dropped() -> None:
    # Foo references itself inside its own body — recursive call.
    # We drop self-edges because downstream slice / rank treats them as noise.
    foo = _sym("foo")
    doc = _doc_with(
        symbols=[scip_pb2.SymbolInformation(symbol=foo)],
        occurrences=[
            scip_pb2.Occurrence(symbol=foo, symbol_roles=DEFINITION_ROLE, range=[0, 0, 10, 0]),
            scip_pb2.Occurrence(symbol=foo, symbol_roles=0, range=[5, 4, 8]),
        ],
    )
    assert scip_index_to_call_edges(_idx(doc)) == []


def test_tie_break_picks_first_definition_in_document_order() -> None:
    # Two definitions with the same span — pick the first.
    a = _sym("a")
    b = _sym("b")
    callee = _sym("target")
    doc = _doc_with(
        symbols=[
            scip_pb2.SymbolInformation(symbol=a),
            scip_pb2.SymbolInformation(symbol=b),
            scip_pb2.SymbolInformation(symbol=callee),
        ],
        occurrences=[
            scip_pb2.Occurrence(symbol=a, symbol_roles=DEFINITION_ROLE, range=[0, 0, 10, 0]),
            scip_pb2.Occurrence(symbol=b, symbol_roles=DEFINITION_ROLE, range=[0, 0, 10, 0]),
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[5, 0, 4]),
        ],
    )
    [edge] = scip_index_to_call_edges(_idx(doc))
    assert edge.src == a


def test_multiple_refs_in_same_definition_emit_multiple_edges() -> None:
    caller = _sym("foo")
    x = _sym("x")
    y = _sym("y")
    doc = _doc_with(
        symbols=[
            scip_pb2.SymbolInformation(symbol=caller),
            scip_pb2.SymbolInformation(symbol=x),
            scip_pb2.SymbolInformation(symbol=y),
        ],
        occurrences=[
            scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
            scip_pb2.Occurrence(symbol=x, symbol_roles=0, range=[5, 0, 4]),
            scip_pb2.Occurrence(symbol=y, symbol_roles=0, range=[10, 0, 4]),
        ],
    )
    result = scip_index_to_call_edges(_idx(doc))
    dsts = sorted(e.dst for e in result)
    assert dsts == [x, y]


def test_multiple_documents_scoped_independently() -> None:
    caller = _sym("foo")
    callee = _sym("bar")
    doc_a = _doc_with(
        path="a.py",
        symbols=[scip_pb2.SymbolInformation(symbol=caller), scip_pb2.SymbolInformation(symbol=callee)],
        occurrences=[
            scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[5, 0, 4]),
        ],
    )
    # Same callee reference but no caller definition in b.py → should drop.
    doc_b = _doc_with(
        path="b.py",
        symbols=[scip_pb2.SymbolInformation(symbol=callee)],
        occurrences=[
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[5, 0, 4]),
        ],
    )
    result = scip_index_to_call_edges(_idx(doc_a, doc_b))
    assert len(result) == 1
    assert result[0].src == caller


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def test_resolve_symbol_rewrites_endpoints() -> None:
    caller = _sym("foo")
    callee = _sym("bar")
    doc = _doc_with(
        symbols=[scip_pb2.SymbolInformation(symbol=caller), scip_pb2.SymbolInformation(symbol=callee)],
        occurrences=[
            scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[5, 0, 4]),
        ],
    )
    mapping = {caller: "python:mod.py:1-21:foo:method",
               callee: "python:mod.py:30-35:bar:method"}
    [edge] = scip_index_to_call_edges(_idx(doc), resolve_symbol=lambda s: mapping.get(s))
    assert edge.src == mapping[caller]
    assert edge.dst == mapping[callee]


def test_resolve_symbol_none_for_either_endpoint_drops_edge() -> None:
    caller = _sym("foo")
    callee = _sym("bar")
    doc = _doc_with(
        symbols=[scip_pb2.SymbolInformation(symbol=caller), scip_pb2.SymbolInformation(symbol=callee)],
        occurrences=[
            scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[5, 0, 4]),
        ],
    )
    # Resolve caller but not callee → drop
    assert scip_index_to_call_edges(
        _idx(doc),
        resolve_symbol=lambda s: "resolved" if s == caller else None,
    ) == []
    # Resolve callee but not caller → drop
    assert scip_index_to_call_edges(
        _idx(doc),
        resolve_symbol=lambda s: "resolved" if s == callee else None,
    ) == []


# ---------------------------------------------------------------------------
# Evidence / provenance
# ---------------------------------------------------------------------------


def test_edge_meta_carries_scip_provenance() -> None:
    caller = _sym("foo")
    callee = _sym("bar")
    doc = _doc_with(
        symbols=[scip_pb2.SymbolInformation(symbol=caller), scip_pb2.SymbolInformation(symbol=callee)],
        occurrences=[
            scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0x08, range=[5, 0, 4]),  # ReadAccess
        ],
    )
    [edge] = scip_index_to_call_edges(_idx(doc))
    assert edge.meta is not None
    assert edge.meta["scip_src_symbol"] == caller
    assert edge.meta["scip_dst_symbol"] == callee
    assert edge.meta["symbol_roles"] == 0x08


def test_definition_with_malformed_range_is_ignored() -> None:
    # Definition occurrence with an out-of-spec range length must not
    # crash the walker or become a phantom enclosing symbol for refs
    # elsewhere in the document.
    caller = _sym("foo")
    callee = _sym("bar")
    doc = _doc_with(
        symbols=[scip_pb2.SymbolInformation(symbol=caller), scip_pb2.SymbolInformation(symbol=callee)],
        occurrences=[
            # Valid definition
            scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
            # Malformed definition — skipped during def-list construction
            scip_pb2.Occurrence(symbol=_sym("broken"), symbol_roles=DEFINITION_ROLE, range=[0, 1]),
            # Ref that falls inside the valid caller
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[5, 0, 4]),
        ],
    )
    [edge] = scip_index_to_call_edges(_idx(doc))
    assert edge.src == caller
    assert edge.dst == callee


def test_occurrence_with_unsupported_range_length_skipped() -> None:
    caller = _sym("foo")
    callee = _sym("bar")
    doc = _doc_with(
        symbols=[scip_pb2.SymbolInformation(symbol=caller), scip_pb2.SymbolInformation(symbol=callee)],
        occurrences=[
            scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
            # Malformed 2-int range
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[5, 0]),
        ],
    )
    assert scip_index_to_call_edges(_idx(doc)) == []
