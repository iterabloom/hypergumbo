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
  in the same Document and pick the *innermost* one whose EXTENT —
  ``enclosing_range`` when populated, else ``range`` — fully
  contains O's span. Innermost means: smallest span-area, with
  ``start <= O.start`` and ``end >= O.end``. Ties (equal-area)
  deterministically pick the first Definition in document order so
  results are reproducible without a sort key.
* Occurrences whose enclosing Definition cannot be found (top-level
  module statements, imports at module scope, occurrences before the
  first definition) are skipped silently. Attributing them to a
  phantom caller would introduce false edges.
* Edges are emitted with ``edge_type="calls"`` when the target's declared
  kind is callable and ``"references"`` otherwise (WI-zapuk); before that,
  uniformly ``"references"``,
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

import pytest

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
    assert scip_index_to_call_edges(scip_pb2.Index(), run_id="test") == []


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
    [edge] = scip_index_to_call_edges(_idx(doc), run_id="test")
    assert isinstance(edge, Edge)
    assert edge.src == caller
    assert edge.dst == callee
    # No declared kind on ``bar``; its METHOD descriptor (``bar().``) says
    # callable, so the reference is a call site (WI-nanom).
    assert edge.edge_type == "calls"
    assert edge.origin == ["scip"]
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
    assert scip_index_to_call_edges(_idx(doc), run_id="test") == []


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
    [edge] = scip_index_to_call_edges(_idx(doc), run_id="test")
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
    [edge] = scip_index_to_call_edges(_idx(doc), run_id="test")
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
    assert scip_index_to_call_edges(_idx(doc), run_id="test") == []


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
    assert scip_index_to_call_edges(_idx(doc), run_id="test") == []


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
    [edge] = scip_index_to_call_edges(_idx(doc), run_id="test")
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
    result = scip_index_to_call_edges(_idx(doc), run_id="test")
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
    result = scip_index_to_call_edges(_idx(doc_a, doc_b), run_id="test")
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
    [edge] = scip_index_to_call_edges(_idx(doc), resolve_symbol=lambda s: mapping.get(s), run_id="test")
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

        run_id="test",
    ) == []
    # Resolve callee but not caller → drop
    assert scip_index_to_call_edges(
        _idx(doc),
        resolve_symbol=lambda s: "resolved" if s == callee else None,

        run_id="test",
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
    [edge] = scip_index_to_call_edges(_idx(doc), run_id="test")
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
    [edge] = scip_index_to_call_edges(_idx(doc), run_id="test")
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
    assert scip_index_to_call_edges(_idx(doc), run_id="test") == []


# ---------------------------------------------------------------------------
# Locals are not endpoints (WI-jikok / INV-kukiz)
# ---------------------------------------------------------------------------


def test_ref_to_a_local_binding_emits_no_edge_even_in_raw_mode() -> None:
    """A ``local <id>`` is document-scoped and never minted as a Symbol
    (see test_scip_index), so an edge naming one — in raw mode as much as
    resolved mode — would name an endpoint nothing carries. On aardvark-dns
    455 such edges were emitted and 329 resolved across files."""
    outer, local, callee = _sym("outer"), "local 0", _sym("callee")
    doc = _doc_with([
        scip_pb2.Occurrence(symbol=outer, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
        # the local is defined and then read inside ``outer``
        scip_pb2.Occurrence(symbol=local, symbol_roles=DEFINITION_ROLE, range=[2, 8, 9]),
        scip_pb2.Occurrence(symbol=local, symbol_roles=0, range=[3, 8, 9]),
        # control: a global reference in the same body is still emitted
        scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[4, 4, 10]),
    ])
    edges = scip_index_to_call_edges(_idx(doc), run_id="test")
    assert [(e.src, e.dst) for e in edges] == [(outer, callee)]


def test_a_local_binding_never_encloses_a_reference() -> None:
    """The src side of the same rule: a local's Definition occurrence must
    not be chosen as the enclosing definition, even when its span happens
    to contain the reference."""
    local, callee = "local 0", _sym("callee")
    doc = _doc_with([
        scip_pb2.Occurrence(symbol=local, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
        scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[5, 4, 10]),
    ])
    assert scip_index_to_call_edges(_idx(doc), run_id="test") == []


# ---------------------------------------------------------------------------
# A Definition's extent is its enclosing_range, when the emitter gives one
# ---------------------------------------------------------------------------


def test_enclosing_range_attributes_a_body_reference_to_its_function() -> None:
    """rust-analyzer's Definition ``range`` is the identifier token — one line
    — so with ``range`` alone a reference in a function's BODY was never
    inside the function, and every SCIP edge on aardvark-dns (182 of 182) was
    sourced from the file's namespace. ``enclosing_range`` is the item range,
    and rust-analyzer populates it on every Definition; ranges below are the
    recorded ones for ``impl#[Counter]increment().`` on the parity sample.
    """
    ns, fn, callee = _sym("ns"), _sym("increment"), _sym("callee")
    doc = _doc_with([
        scip_pb2.Occurrence(
            symbol=ns, symbol_roles=DEFINITION_ROLE,
            range=[0, 0, 32, 0], enclosing_range=[0, 0, 32, 0],
        ),
        scip_pb2.Occurrence(
            symbol=fn, symbol_roles=DEFINITION_ROLE,
            range=[13, 11, 20], enclosing_range=[13, 4, 16, 5],
        ),
        # a reference inside increment's body: line 16 (0-based 15), past the token
        scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[15, 8, 14]),
    ])
    edges = scip_index_to_call_edges(_idx(doc), run_id="test")
    assert [(e.src, e.dst) for e in edges] == [(fn, callee)]


def test_without_enclosing_range_a_token_definition_cannot_enclose() -> None:
    """Control for the test above, and the contract for emitters that leave
    ``enclosing_range`` empty: the same document with only ``range`` attributes
    the body reference to the namespace, which is what production did."""
    ns, fn, callee = _sym("ns"), _sym("increment"), _sym("callee")
    doc = _doc_with([
        scip_pb2.Occurrence(symbol=ns, symbol_roles=DEFINITION_ROLE, range=[0, 0, 32, 0]),
        scip_pb2.Occurrence(symbol=fn, symbol_roles=DEFINITION_ROLE, range=[13, 11, 20]),
        scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[15, 8, 14]),
    ])
    edges = scip_index_to_call_edges(_idx(doc), run_id="test")
    assert [(e.src, e.dst) for e in edges] == [(ns, callee)]


def test_malformed_enclosing_range_falls_back_to_range() -> None:
    """A length-2 ``enclosing_range`` is treated as absent, not fatal."""
    ns, fn, callee = _sym("ns"), _sym("increment"), _sym("callee")
    doc = _doc_with([
        scip_pb2.Occurrence(symbol=ns, symbol_roles=DEFINITION_ROLE, range=[0, 0, 32, 0]),
        scip_pb2.Occurrence(
            symbol=fn, symbol_roles=DEFINITION_ROLE,
            range=[13, 11, 20], enclosing_range=[13, 4],
        ),
        scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[15, 8, 14]),
    ])
    edges = scip_index_to_call_edges(_idx(doc), run_id="test")
    assert [(e.src, e.dst) for e in edges] == [(ns, callee)]


def test_innermost_is_chosen_by_enclosing_range_when_items_nest() -> None:
    """A method inside an impl inside the namespace: the reference in the
    method's body goes to the method, not to the enclosing struct or file."""
    ns, strct, method, callee = _sym("ns"), _sym("Counter"), _sym("increment"), _sym("callee")
    doc = _doc_with([
        scip_pb2.Occurrence(
            symbol=ns, symbol_roles=DEFINITION_ROLE,
            range=[0, 0, 32, 0], enclosing_range=[0, 0, 32, 0],
        ),
        scip_pb2.Occurrence(
            symbol=strct, symbol_roles=DEFINITION_ROLE,
            range=[8, 11, 18], enclosing_range=[8, 0, 20, 1],
        ),
        scip_pb2.Occurrence(
            symbol=method, symbol_roles=DEFINITION_ROLE,
            range=[13, 11, 20], enclosing_range=[13, 4, 16, 5],
        ),
        scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[15, 8, 14]),
    ])
    edges = scip_index_to_call_edges(_idx(doc), run_id="test")
    assert [(e.src, e.dst) for e in edges] == [(method, callee)]


# ---------------------------------------------------------------------------
# WI-zapuk / ADR-0057 §7: the edge type comes from the TARGET's declared kind
# ---------------------------------------------------------------------------
#
# The row prescribed mapping ``Occurrence.symbol_roles``; measured on the
# recorded aardvark-dns index, rust-analyzer 1.94.0 sets ``symbol_roles = 0``
# on 3,543 of 3,543 reference occurrences, so there is nothing there to map.
# What the producer DOES declare is ``SymbolInformation.kind`` on every
# definition, and a reference whose target is declared callable is a call.

K = scip_pb2.SymbolInformation.Kind


def _one_ref(target_kind: int | None, *, callee_doc_path: str | None = None) -> Edge:
    caller, callee = _sym("foo"), _sym("bar")
    callee_info = (
        scip_pb2.SymbolInformation(symbol=callee)
        if target_kind is None
        else scip_pb2.SymbolInformation(symbol=callee, kind=target_kind)
    )
    caller_doc_symbols = [scip_pb2.SymbolInformation(symbol=caller)]
    caller_doc_occurrences = [
        scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
        scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[5, 4, 10]),
    ]
    if callee_doc_path is None:
        caller_doc_symbols.append(callee_info)
        caller_doc_occurrences.append(
            scip_pb2.Occurrence(symbol=callee, symbol_roles=DEFINITION_ROLE, range=[30, 0, 35, 0]),
        )
        docs = [_doc_with(symbols=caller_doc_symbols, occurrences=caller_doc_occurrences)]
    else:
        docs = [
            _doc_with(symbols=caller_doc_symbols, occurrences=caller_doc_occurrences),
            _doc_with(
                symbols=[callee_info],
                occurrences=[scip_pb2.Occurrence(symbol=callee, symbol_roles=DEFINITION_ROLE, range=[0, 0, 3, 0])],
                path=callee_doc_path,
            ),
        ]
    [edge] = scip_index_to_call_edges(_idx(*docs), run_id="test")
    return edge


class TestEdgeTypeFromTheTargetsDeclaredKind:
    @pytest.mark.parametrize("kind", [
        K.Function, K.Method, K.StaticMethod, K.TraitMethod, K.AbstractMethod, K.Constructor,
    ])
    def test_a_reference_to_a_declared_callable_is_a_call(self, kind: int) -> None:
        edge = _one_ref(kind)
        assert edge.edge_type == "calls"
        # The pathway is unchanged: this is still a span-enclosed occurrence.
        assert edge.evidence_type == "scip_occurrence_ref"

    @pytest.mark.parametrize("kind", [K.Field, K.Variable, K.Constant, K.Struct, K.Enum, K.TypeAlias, K.Module])
    def test_a_reference_to_a_declared_non_callable_stays_a_reference(self, kind: int) -> None:
        assert _one_ref(kind).edge_type == "references"

    def test_an_undeclared_target_kind_reads_the_descriptor(self) -> None:
        """An emitter that leaves ``kind`` unset (scip-python 0.6.6 on every
        symbol, WI-nanom) is answered by the SCIP descriptor grammar: the
        ``_sym`` helper's METHOD descriptor is callable, so ``calls``. The
        TERM-descriptor counterpart stays ``references`` (see the WI-nanom
        block below)."""
        assert _one_ref(None).edge_type == "calls"

    def test_the_callee_declared_in_another_document_is_still_a_call(self) -> None:
        """The kind lives with the DEFINING document's SymbolInformation; a
        cross-file call must read it from there, not from the caller's."""
        assert _one_ref(K.Function, callee_doc_path="other.py").edge_type == "calls"

    def test_role_bits_are_still_preserved_but_do_not_decide(self) -> None:
        """rust-analyzer sets no role bits; an emitter that does keeps them
        in meta for a later reader. No read/write edge type is minted from
        them here — no recorded producer sets them yet (ADR-0057 §12)."""
        caller, callee = _sym("foo"), _sym("bar")
        doc = _doc_with(
            symbols=[scip_pb2.SymbolInformation(symbol=caller),
                     scip_pb2.SymbolInformation(symbol=callee, kind=K.Variable)],
            occurrences=[
                scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
                scip_pb2.Occurrence(symbol=callee, symbol_roles=scip_pb2.SymbolRole.WriteAccess, range=[5, 4, 10]),
                scip_pb2.Occurrence(symbol=callee, symbol_roles=DEFINITION_ROLE, range=[30, 0, 35, 0]),
            ],
        )
        [edge] = scip_index_to_call_edges(_idx(doc), run_id="test")
        assert edge.edge_type == "references"
        assert edge.meta["symbol_roles"] == int(scip_pb2.SymbolRole.WriteAccess)


# ---------------------------------------------------------------------------
# WI-nanom: a producer that declares NO SymbolInformation.kind at all
# (scip-python 0.6.6: kind == 0 on 17,851 of 17,851 symbols) emitted only
# `references` — every call site typed as a bare reference, the shape that
# measured zero receiver typing on the Rust arm. When the producer is silent
# the SCIP descriptor grammar still says what is callable: a METHOD
# descriptor (`name().`) is a function or method. A declared kind, when
# present, keeps winning (WI-gapup).
# ---------------------------------------------------------------------------


def _term(name: str) -> str:
    return f"scip-python pypi pkg 0.1.0 mod/{name}."


def _call_site(callee: str, *, declared_kind: int = 0) -> scip_pb2.Index:
    caller = _sym("caller")
    return _idx(_doc_with(
        symbols=[
            scip_pb2.SymbolInformation(symbol=caller),
            scip_pb2.SymbolInformation(symbol=callee, kind=declared_kind),
        ],
        occurrences=[
            scip_pb2.Occurrence(symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0]),
            scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[5, 4, 10]),
            scip_pb2.Occurrence(symbol=callee, symbol_roles=DEFINITION_ROLE, range=[30, 0, 35, 0]),
        ],
    ))


def test_an_undeclared_kind_falls_back_to_the_method_descriptor_for_calls() -> None:
    [edge] = scip_index_to_call_edges(_call_site(_sym("callee")), run_id="test")
    assert edge.edge_type == "calls"


def test_an_undeclared_kind_with_a_term_descriptor_is_a_reference() -> None:
    [edge] = scip_index_to_call_edges(_call_site(_term("value")), run_id="test")
    assert edge.edge_type == "references"


def test_a_declared_non_callable_kind_beats_the_method_descriptor() -> None:
    from hypergumbo_core.scip.index import symbol_information_kind_values

    variable = symbol_information_kind_values()["Variable"]
    [edge] = scip_index_to_call_edges(_call_site(_sym("callee"), declared_kind=variable), run_id="test")
    assert edge.edge_type == "references"
