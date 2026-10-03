# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the SCIP Index → hypergumbo Edge translation (WI-mafut Phase 2 Slice C).

These tests drive :func:`hypergumbo_core.scip.edges.scip_index_to_edges`,
the pure-function translator that emits hypergumbo ``Edge`` objects from
a parsed SCIP ``Index``. Slice C covers the explicit
``SymbolInformation.relationships`` signal only — occurrence refs
(non-Definition ``Occurrence``-level call sites) require an
enclosing-symbol resolver that is not part of the SCIP wire format and
lives in a later slice / separate linker.

Behavioural contract pinned here:

* Each ``Relationship`` on a ``SymbolInformation`` fans out to one edge
  per DISTINCT edge type its set flags map to — ``is_implementation`` →
  ``implements``; ``is_reference``, ``is_type_definition`` and
  ``is_definition`` → ``references``, the flag kept on
  ``meta["scip_relationship_flag"]`` (INV-dahuh: the last two used to mint
  the unregistered ``has_type`` / ``defined_by``).
* ``src`` is the ``SymbolInformation.symbol`` that carries the
  relationship; ``dst`` is ``Relationship.symbol``. This direction is
  intuitive: "X implements Y" reads as edge ``X --implements--> Y``.
* Edges ship unresolved by default — ``src`` and ``dst`` are raw SCIP
  symbol strings, and downstream callers resolve them against Slice B's
  Symbol ``stable_id`` field (also a SCIP symbol string) to produce
  fully-qualified hypergumbo IDs. A caller-provided ``resolve_symbol``
  callable can remap either endpoint on the way out.
* Per WI-zakub §1 (rust-analyzer leaves ``relationships`` empty), a SCIP
  Index with zero populated ``Relationship`` entries produces an empty
  list silently. The translator does not log — callers that want the
  WI-zakub hint log at their own boundary.
* ``Relationship.symbol`` that matches the enclosing
  ``SymbolInformation.symbol`` produces no self-edge; self-reference is
  uninformative and downstream slice/rank code prefers the absence.
"""
from __future__ import annotations

from hypergumbo_core.edge_types import all_edge_type_names
from hypergumbo_core.ir import Edge
from hypergumbo_core.scip._generated import scip_pb2
from hypergumbo_core.scip.edges import _RELATION_EDGE_TYPES, scip_index_to_edges


def _sym(name: str) -> str:
    return f"scip-python pypi pkg 0.1.0 mod/{name}()."


def _idx_with_relationship(
    *, src_symbol: str, rel_symbol: str, **flags,
) -> scip_pb2.Index:
    doc = scip_pb2.Document(
        language="python",
        relative_path="mod.py",
        symbols=[
            scip_pb2.SymbolInformation(
                symbol=src_symbol,
                relationships=[scip_pb2.Relationship(symbol=rel_symbol, **flags)],
            )
        ],
    )
    return scip_pb2.Index(documents=[doc])


# ---------------------------------------------------------------------------
# Happy paths — one flag each
# ---------------------------------------------------------------------------


def test_empty_index_emits_no_edges() -> None:
    assert scip_index_to_edges(scip_pb2.Index(), run_id="test") == []


def test_symbol_without_relationships_emits_no_edges() -> None:
    idx = scip_pb2.Index(documents=[
        scip_pb2.Document(
            language="python", relative_path="mod.py",
            symbols=[scip_pb2.SymbolInformation(symbol=_sym("foo"))],
        )
    ])
    assert scip_index_to_edges(idx, run_id="test") == []


def test_is_implementation_produces_implements_edge() -> None:
    src = _sym("Impl")
    dst = _sym("Trait")
    idx = _idx_with_relationship(src_symbol=src, rel_symbol=dst, is_implementation=True)
    [e] = scip_index_to_edges(idx, run_id="test")
    assert isinstance(e, Edge)
    assert e.src == src
    assert e.dst == dst
    assert e.edge_type == "implements"
    assert e.origin == ["scip"]


def test_is_type_definition_produces_references_edge() -> None:
    src = _sym("x")
    dst = _sym("int")
    idx = _idx_with_relationship(src_symbol=src, rel_symbol=dst, is_type_definition=True)
    [e] = scip_index_to_edges(idx, run_id="test")
    assert e.edge_type == "references"
    assert e.meta is not None
    assert e.meta["scip_relationship_flag"] == "is_type_definition"


def test_is_reference_produces_references_edge() -> None:
    src = _sym("a")
    dst = _sym("b")
    idx = _idx_with_relationship(src_symbol=src, rel_symbol=dst, is_reference=True)
    [e] = scip_index_to_edges(idx, run_id="test")
    assert e.edge_type == "references"


def test_is_definition_produces_references_edge() -> None:
    src = _sym("alias")
    dst = _sym("canonical")
    idx = _idx_with_relationship(src_symbol=src, rel_symbol=dst, is_definition=True)
    [e] = scip_index_to_edges(idx, run_id="test")
    assert e.edge_type == "references"
    assert e.meta is not None
    assert e.meta["scip_relationship_flag"] == "is_definition"


# ---------------------------------------------------------------------------
# Multiple flags, multiple relationships, multiple documents
# ---------------------------------------------------------------------------


def test_multiple_flags_on_one_relationship_fan_out() -> None:
    src = _sym("Foo")
    dst = _sym("Bar")
    idx = _idx_with_relationship(
        src_symbol=src, rel_symbol=dst,
        is_implementation=True,
        is_type_definition=True,
    )
    result = scip_index_to_edges(idx, run_id="test")
    edge_types = sorted(e.edge_type for e in result)
    assert edge_types == ["implements", "references"]


def test_flags_sharing_an_edge_type_emit_it_once() -> None:
    """``is_reference`` + ``is_type_definition`` + ``is_definition`` all map
    to ``references``; three edges would share one id (src, dst, type,
    line), so the first flag in map order names the one edge."""
    idx = _idx_with_relationship(
        src_symbol=_sym("Foo"), rel_symbol=_sym("Bar"),
        is_type_definition=True, is_reference=True, is_definition=True,
    )
    [e] = scip_index_to_edges(idx, run_id="test")
    assert e.edge_type == "references"
    assert e.meta is not None
    assert e.meta["scip_relationship_flag"] == "is_reference"


def test_multiple_relationships_each_emit_edges() -> None:
    src = _sym("Multi")
    doc = scip_pb2.Document(
        language="python", relative_path="mod.py",
        symbols=[scip_pb2.SymbolInformation(
            symbol=src,
            relationships=[
                scip_pb2.Relationship(symbol=_sym("A"), is_implementation=True),
                scip_pb2.Relationship(symbol=_sym("B"), is_reference=True),
            ],
        )],
    )
    idx = scip_pb2.Index(documents=[doc])
    result = scip_index_to_edges(idx, run_id="test")
    assert len(result) == 2
    dsts = {e.dst for e in result}
    assert dsts == {_sym("A"), _sym("B")}


def test_relationship_with_no_flags_produces_no_edges() -> None:
    # Protobuf allows all-false relationships — skip them.
    src = _sym("x")
    idx = _idx_with_relationship(src_symbol=src, rel_symbol=_sym("y"))
    assert scip_index_to_edges(idx, run_id="test") == []


def test_self_reference_relationship_is_dropped() -> None:
    sym = _sym("self")
    idx = _idx_with_relationship(src_symbol=sym, rel_symbol=sym, is_reference=True)
    assert scip_index_to_edges(idx, run_id="test") == []


def test_multiple_documents_each_contribute() -> None:
    d1 = scip_pb2.Document(
        language="python", relative_path="a.py",
        symbols=[scip_pb2.SymbolInformation(
            symbol=_sym("A"),
            relationships=[scip_pb2.Relationship(symbol=_sym("B"), is_implementation=True)],
        )],
    )
    d2 = scip_pb2.Document(
        language="python", relative_path="b.py",
        symbols=[scip_pb2.SymbolInformation(
            symbol=_sym("C"),
            relationships=[scip_pb2.Relationship(symbol=_sym("D"), is_type_definition=True)],
        )],
    )
    idx = scip_pb2.Index(documents=[d1, d2])
    result = scip_index_to_edges(idx, run_id="test")
    assert len(result) == 2
    types = {e.edge_type for e in result}
    assert types == {"implements", "references"}


# ---------------------------------------------------------------------------
# resolve_symbol hook
# ---------------------------------------------------------------------------


def test_resolve_symbol_rewrites_both_endpoints() -> None:
    src = _sym("s")
    dst = _sym("d")
    idx = _idx_with_relationship(src_symbol=src, rel_symbol=dst, is_implementation=True)

    mapping = {src: "python:s.py:1-1:s:class", dst: "python:d.py:1-1:d:class"}

    def resolve(scip_symbol: str) -> "str | None":
        return mapping.get(scip_symbol)

    [e] = scip_index_to_edges(idx, resolve_symbol=resolve, run_id="test")
    assert e.src == mapping[src]
    assert e.dst == mapping[dst]


def test_resolve_symbol_returning_none_for_src_skips_edge() -> None:
    src = _sym("s")
    dst = _sym("d")
    idx = _idx_with_relationship(src_symbol=src, rel_symbol=dst, is_implementation=True)

    # When src cannot be resolved, no edge is emitted at all.
    result = scip_index_to_edges(
        idx,
        resolve_symbol=lambda s: None if s == src else "python:resolved:1-1:x:class",

        run_id="test",
    )
    assert result == []


def test_resolve_symbol_returning_none_for_dst_skips_edge() -> None:
    src = _sym("s")
    dst = _sym("d")
    idx = _idx_with_relationship(src_symbol=src, rel_symbol=dst, is_implementation=True)

    result = scip_index_to_edges(
        idx,
        resolve_symbol=lambda s: None if s == dst else "python:resolved:1-1:x:class",

        run_id="test",
    )
    assert result == []


# ---------------------------------------------------------------------------
# Evidence / provenance fields
# ---------------------------------------------------------------------------


def test_edge_carries_scip_evidence_type_and_origin() -> None:
    src = _sym("s")
    dst = _sym("d")
    idx = _idx_with_relationship(src_symbol=src, rel_symbol=dst, is_implementation=True)
    [e] = scip_index_to_edges(idx, run_id="test")
    assert e.origin == ["scip"]
    assert e.evidence_type.startswith("scip_")
    assert e.confidence > 0.0


def test_edge_meta_includes_scip_endpoints() -> None:
    src = _sym("s")
    dst = _sym("d")
    idx = _idx_with_relationship(src_symbol=src, rel_symbol=dst, is_implementation=True)
    [e] = scip_index_to_edges(idx, run_id="test")
    assert e.meta is not None
    assert e.meta.get("scip_src_symbol") == src
    assert e.meta.get("scip_dst_symbol") == dst


# ---------------------------------------------------------------------------
# Locals are not endpoints (WI-jikok / INV-kukiz)
# ---------------------------------------------------------------------------


def test_relationship_touching_a_local_binding_emits_no_edge() -> None:
    """Same rule as the Symbol and Occurrence shims: a ``local <id>`` is
    document-scoped and never minted, so no Relationship edge may name one
    at either end. rust-analyzer leaves ``relationships`` empty, so this is
    a contract for the other SCIP emitters."""
    to_local = _idx_with_relationship(
        src_symbol=_sym("f"), rel_symbol="local 3", is_reference=True,
    )
    assert scip_index_to_edges(to_local, run_id="test") == []
    from_local = _idx_with_relationship(
        src_symbol="local 3", rel_symbol=_sym("f"), is_reference=True,
    )
    assert scip_index_to_edges(from_local, run_id="test") == []


# ---------------------------------------------------------------------------
# INV-dahuh: the relationship map stays on the edge-type axis
# ---------------------------------------------------------------------------


class TestRelationMapConformance:
    """``_RELATION_EDGE_TYPES`` is a list of tuples, which no edge-type gate
    could read until the producer gates learned to follow a loop over one
    (WI-nakur). These are the direct gates the list never had."""

    def test_every_mapped_value_is_a_registered_edge_type(self) -> None:
        known = all_edge_type_names()
        offenders = sorted({t for _flag, t in _RELATION_EDGE_TYPES} - known)
        assert offenders == [], (
            f"_RELATION_EDGE_TYPES mints edge types absent from the ADR-0023 "
            f"registry: {offenders}. Register them or fold the flag onto a "
            f"registered type."
        )

    def test_every_relationship_flag_is_mapped(self) -> None:
        """Totality over the wire format, so a flag cannot be dropped by
        omission: every boolean field of ``Relationship`` has an entry."""
        flags = {
            f.name for f in scip_pb2.Relationship.DESCRIPTOR.fields
            if f.type == f.TYPE_BOOL
        }
        assert flags == {flag for flag, _type in _RELATION_EDGE_TYPES}

    def test_the_gate_can_fail(self) -> None:
        """Positive control: the pre-fix map is rejected."""
        known = all_edge_type_names()
        pre_fix = [("is_type_definition", "has_type"), ("is_definition", "defined_by")]
        assert sorted({t for _f, t in pre_fix} - known) == ["defined_by", "has_type"]
