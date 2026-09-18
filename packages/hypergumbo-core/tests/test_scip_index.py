# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the SCIP Index → hypergumbo Symbol translation (WI-mafut Phase 2 Slice B).

These tests drive :func:`hypergumbo_core.scip.index.scip_index_to_symbols`,
the pure-function translator that walks a parsed SCIP ``Index`` (no bytes
decoding, no I/O) and emits hypergumbo ``Symbol`` objects. The unit under
test is intentionally thin: the Phase 1 descriptor parser already owns
the non-trivial grammar work, so this layer is mostly glue — pick the
right ``Definition`` occurrence for each ``SymbolInformation``, rewrite
a SCIP 3- or 4-int range into a ``Span``, look up the Phase 1 descriptor
kind, and emit a well-formed ``Symbol``.

Behavioural contract pinned here:

* Symbols are emitted only when a ``SymbolInformation`` has a matching
  ``Definition``-role ``Occurrence`` in the same document — external /
  unresolved symbols become no-op at this layer and will be wired by
  Slice C's edges module.
* ``Occurrence.range`` lengths other than 3 and 4 are the only input
  shape we reject: SCIP emitters can legally produce either shape, so
  both must work, but protobuf cannot forbid extra ints, so a length-5
  range is a real malformed input we must fail on loudly.
* SCIP is 0-indexed (``range[0]`` is the first source line); hypergumbo
  ``Span`` is 1-indexed. The +1 rewrite happens here so downstream
  ranking / slice / sketch code can keep its 1-indexed assumptions.
* Local symbols (``local <id>``) are NOT minted (WI-jikok / INV-kukiz).
  A SCIP local id is a document-scoped index, not an identifier — the
  same ``local 0`` is a different binding in every file — so hashing the
  moniker collided 498 of 1255 Symbols on one crate and resolving edges
  through one index-wide map made 329 of 455 local-pointing edges
  cross-file, which a document-scoped binding cannot be. And a
  function-local binding is not part of a behavior map by the rule every
  other backend already follows: py.py, kotlin.py, go.py and the
  tree-sitter rust.py all read locals for resolution and mint nothing.
  An earlier version of this bullet said locals were kept "because
  rust-analyzer and scip-python both emit locals that participate in
  cross-reference resolution"; the edges that resolution produced were
  the false ones.
* Malformed SCIP symbol strings are skipped, not raised, because a SCIP
  index from a buggy upstream emitter must not take down the whole
  translation pass.
* Empty ``SymbolInformation.relationships`` index-wide is the
  rust-analyzer case (WI-zakub §1). Callers who care log at the
  translation boundary; the translator itself stays silent to keep the
  function pure.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.analyze.base import _short_sha256
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.scip._generated import scip_pb2
from hypergumbo_core.scip.index import (
    _span_from_range,
    scip_index_to_symbols,
)


DEFINITION_ROLE = 0x01
IMPORT_ROLE = 0x02


def _py_symbol(name: str) -> str:
    """Build a plausible scip-python-style symbol string ending in a function term."""
    return f"scip-python pypi my_pkg 0.1.0 module/path/{name}()."


def _make_index(*, language: str = "Python", path: str = "mod.py",
                symbols=None, occurrences=None) -> scip_pb2.Index:
    doc = scip_pb2.Document(
        language=language,
        relative_path=path,
        symbols=list(symbols or []),
        occurrences=list(occurrences or []),
    )
    return scip_pb2.Index(documents=[doc])


# ---------------------------------------------------------------------------
# _span_from_range
# ---------------------------------------------------------------------------


def test_span_from_range_three_ints_single_line() -> None:
    span = _span_from_range([10, 4, 12])
    assert span == Span(start_line=11, end_line=11, start_col=4, end_col=12)


def test_span_from_range_four_ints_multi_line() -> None:
    span = _span_from_range([10, 4, 20, 0])
    assert span == Span(start_line=11, end_line=21, start_col=4, end_col=0)


def test_span_from_range_rejects_unexpected_length() -> None:
    with pytest.raises(ValueError, match="SCIP range"):
        _span_from_range([1, 2])
    with pytest.raises(ValueError, match="SCIP range"):
        _span_from_range([1, 2, 3, 4, 5])


# ---------------------------------------------------------------------------
# scip_index_to_symbols — happy path
# ---------------------------------------------------------------------------


def test_empty_index_returns_empty_list() -> None:
    assert scip_index_to_symbols(scip_pb2.Index()) == []


def test_document_with_no_symbols_returns_empty_list() -> None:
    idx = _make_index()
    assert scip_index_to_symbols(idx) == []


def test_single_definition_becomes_symbol() -> None:
    sym = _py_symbol("foo")
    idx = _make_index(
        symbols=[scip_pb2.SymbolInformation(symbol=sym, display_name="foo")],
        occurrences=[scip_pb2.Occurrence(
            symbol=sym,
            symbol_roles=DEFINITION_ROLE,
            range=[5, 0, 8, 0],
        )],
    )
    result = scip_index_to_symbols(idx)
    assert len(result) == 1
    s = result[0]
    assert isinstance(s, Symbol)
    assert s.name == "foo"
    assert s.kind == "method"
    assert s.language == "python"
    assert s.path == "mod.py"
    assert s.span == Span(start_line=6, end_line=9, start_col=0, end_col=0)
    assert s.origin == ["scip"]
    # INV-hunup: stable_id is the canonical sha256 of the SCIP moniker; the raw
    # moniker is preserved in meta["scip_symbol"].
    assert s.stable_id == _short_sha256(sym)
    assert s.stable_id.startswith("sha256:")
    assert s.meta and s.meta.get("scip_symbol") == sym


def test_symbol_id_follows_hypergumbo_format() -> None:
    sym = _py_symbol("bar")
    idx = _make_index(
        symbols=[scip_pb2.SymbolInformation(symbol=sym)],
        occurrences=[scip_pb2.Occurrence(
            symbol=sym,
            symbol_roles=DEFINITION_ROLE,
            range=[0, 0, 2, 0],
        )],
    )
    [s] = scip_index_to_symbols(idx)
    assert s.id == "python:mod.py:1-3:bar:method"


# ---------------------------------------------------------------------------
# Occurrence selection
# ---------------------------------------------------------------------------


def test_non_definition_occurrence_does_not_emit_symbol() -> None:
    sym = _py_symbol("foo")
    idx = _make_index(
        symbols=[scip_pb2.SymbolInformation(symbol=sym)],
        occurrences=[scip_pb2.Occurrence(
            symbol=sym,
            symbol_roles=IMPORT_ROLE,
            range=[1, 0, 10],
        )],
    )
    assert scip_index_to_symbols(idx) == []


def test_symbol_info_without_any_occurrence_is_skipped() -> None:
    sym = _py_symbol("external")
    idx = _make_index(
        symbols=[scip_pb2.SymbolInformation(symbol=sym)],
        occurrences=[],
    )
    assert scip_index_to_symbols(idx) == []


def test_definition_plus_reference_emits_exactly_one_symbol() -> None:
    sym = _py_symbol("foo")
    idx = _make_index(
        symbols=[scip_pb2.SymbolInformation(symbol=sym)],
        occurrences=[
            scip_pb2.Occurrence(symbol=sym, symbol_roles=DEFINITION_ROLE, range=[5, 0, 10]),
            scip_pb2.Occurrence(symbol=sym, symbol_roles=0, range=[20, 4, 12]),
        ],
    )
    [s] = scip_index_to_symbols(idx)
    assert s.span.start_line == 6


def test_multiple_definitions_for_same_symbol_picks_first() -> None:
    sym = _py_symbol("foo")
    idx = _make_index(
        symbols=[scip_pb2.SymbolInformation(symbol=sym)],
        occurrences=[
            scip_pb2.Occurrence(symbol=sym, symbol_roles=DEFINITION_ROLE, range=[5, 0, 10]),
            scip_pb2.Occurrence(symbol=sym, symbol_roles=DEFINITION_ROLE, range=[25, 0, 10]),
        ],
    )
    [s] = scip_index_to_symbols(idx)
    assert s.span.start_line == 6


# ---------------------------------------------------------------------------
# Descriptor / kind handling
# ---------------------------------------------------------------------------


def test_type_descriptor_becomes_class_kind() -> None:
    sym = "scip-python pypi my_pkg 0.1.0 module/Foo#"
    idx = _make_index(
        symbols=[scip_pb2.SymbolInformation(symbol=sym)],
        occurrences=[scip_pb2.Occurrence(symbol=sym, symbol_roles=DEFINITION_ROLE, range=[0, 0, 2, 0])],
    )
    [s] = scip_index_to_symbols(idx)
    assert s.name == "Foo"
    assert s.kind == "class"


def test_term_descriptor_becomes_variable_kind() -> None:
    sym = "scip-python pypi my_pkg 0.1.0 module/x."
    idx = _make_index(
        symbols=[scip_pb2.SymbolInformation(symbol=sym)],
        occurrences=[scip_pb2.Occurrence(symbol=sym, symbol_roles=DEFINITION_ROLE, range=[3, 0, 4])],
    )
    [s] = scip_index_to_symbols(idx)
    assert s.name == "x"
    assert s.kind == "variable"


def test_local_symbol_is_not_minted() -> None:
    """RE-POINTED from ``test_local_symbol_keeps_local_kind`` (WI-jikok).

    The old test pinned ``local 7`` → ``Symbol(name="7", kind="local")``.
    That name carries no lexical content and that kind is the registry's
    Terraform ``local``; see the module docstring bullet for why the
    binding is not part of the map at all.
    """
    sym = "local 7"
    idx = _make_index(
        symbols=[scip_pb2.SymbolInformation(symbol=sym)],
        occurrences=[scip_pb2.Occurrence(symbol=sym, symbol_roles=DEFINITION_ROLE, range=[1, 0, 4])],
    )
    assert scip_index_to_symbols(idx) == []


def test_global_symbol_beside_a_local_is_still_minted() -> None:
    """Control for the test above: dropping locals must not drop the global
    defined in the same document."""
    local, glob = "local 7", _py_symbol("f")
    idx = _make_index(
        symbols=[
            scip_pb2.SymbolInformation(symbol=local),
            scip_pb2.SymbolInformation(symbol=glob),
        ],
        occurrences=[
            scip_pb2.Occurrence(symbol=local, symbol_roles=DEFINITION_ROLE, range=[1, 4, 5]),
            scip_pb2.Occurrence(symbol=glob, symbol_roles=DEFINITION_ROLE, range=[0, 0, 4, 0]),
        ],
    )
    assert [s.name for s in scip_index_to_symbols(idx)] == ["f"]


def test_document_scoped_local_ids_recur_across_documents_and_mint_nothing() -> None:
    """INV-kukiz's shape in miniature: the SAME ``local 0`` string is
    defined in two documents. Before the fix that minted two Symbols whose
    ``stable_id`` (``sha256(moniker)``) collided; now it mints none."""
    local = "local 0"
    docs = [
        scip_pb2.Document(
            language="Rust", relative_path=path,
            symbols=[scip_pb2.SymbolInformation(symbol=local)],
            occurrences=[scip_pb2.Occurrence(
                symbol=local, symbol_roles=DEFINITION_ROLE, range=[2, 8, 9],
            )],
        )
        for path in ("src/a.rs", "src/b.rs")
    ]
    assert scip_index_to_symbols(scip_pb2.Index(documents=docs)) == []


def test_malformed_symbol_string_is_skipped_not_raised() -> None:
    idx = _make_index(
        symbols=[scip_pb2.SymbolInformation(symbol="nonsense")],
        occurrences=[scip_pb2.Occurrence(symbol="nonsense", symbol_roles=DEFINITION_ROLE, range=[0, 0, 1])],
    )
    assert scip_index_to_symbols(idx) == []


def test_symbol_with_no_descriptors_is_skipped() -> None:
    # A valid header with zero descriptors — parse_scip_symbol rejects; skip.
    idx = _make_index(
        symbols=[scip_pb2.SymbolInformation(symbol="scip-python pypi my_pkg 0.1.0 ")],
        occurrences=[scip_pb2.Occurrence(
            symbol="scip-python pypi my_pkg 0.1.0 ",
            symbol_roles=DEFINITION_ROLE,
            range=[0, 0, 1],
        )],
    )
    assert scip_index_to_symbols(idx) == []


# ---------------------------------------------------------------------------
# Language / path / multi-document
# ---------------------------------------------------------------------------


def test_language_is_lowercased() -> None:
    sym = "rust-analyzer cargo my_crate 0.1.0 foo()."
    idx = _make_index(language="Rust", path="src/lib.rs",
                     symbols=[scip_pb2.SymbolInformation(symbol=sym)],
                     occurrences=[scip_pb2.Occurrence(symbol=sym, symbol_roles=DEFINITION_ROLE, range=[0, 0, 1])])
    [s] = scip_index_to_symbols(idx)
    assert s.language == "rust"


def test_empty_language_falls_back_to_unknown() -> None:
    sym = _py_symbol("foo")
    idx = _make_index(language="",
                     symbols=[scip_pb2.SymbolInformation(symbol=sym)],
                     occurrences=[scip_pb2.Occurrence(symbol=sym, symbol_roles=DEFINITION_ROLE, range=[0, 0, 1])])
    [s] = scip_index_to_symbols(idx)
    assert s.language == "unknown"


def test_multiple_documents_emit_from_each() -> None:
    sym_a = "scip-python pypi pkg 0.1.0 a/foo()."
    sym_b = "scip-python pypi pkg 0.1.0 b/bar()."
    doc_a = scip_pb2.Document(
        language="python", relative_path="a.py",
        symbols=[scip_pb2.SymbolInformation(symbol=sym_a)],
        occurrences=[scip_pb2.Occurrence(symbol=sym_a, symbol_roles=DEFINITION_ROLE, range=[0, 0, 1])],
    )
    doc_b = scip_pb2.Document(
        language="python", relative_path="b.py",
        symbols=[scip_pb2.SymbolInformation(symbol=sym_b)],
        occurrences=[scip_pb2.Occurrence(symbol=sym_b, symbol_roles=DEFINITION_ROLE, range=[5, 0, 10])],
    )
    idx = scip_pb2.Index(documents=[doc_a, doc_b])
    result = scip_index_to_symbols(idx)
    names = sorted(s.name for s in result)
    assert names == ["bar", "foo"]


def test_display_name_is_preserved_in_meta() -> None:
    sym = _py_symbol("foo")
    idx = _make_index(
        symbols=[scip_pb2.SymbolInformation(symbol=sym, display_name="foo", kind=17)],
        occurrences=[scip_pb2.Occurrence(symbol=sym, symbol_roles=DEFINITION_ROLE, range=[0, 0, 2, 0])],
    )
    [s] = scip_index_to_symbols(idx)
    assert s.meta is not None
    assert s.meta["scip_symbol"] == sym
    assert s.meta["display_name"] == "foo"
    assert s.meta["scip_kind"] == 17
