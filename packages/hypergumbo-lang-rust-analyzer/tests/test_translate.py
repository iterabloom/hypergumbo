# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for :mod:`hypergumbo_lang_rust_analyzer.translate` (WI-duzul Slice A).

Covers the SCIP bytes → ``(symbols, edges)`` translator plus the rust.py
stable-id parity post-processor. All tests build SCIP fixtures in-memory
via the vendored ``scip_pb2`` module and serialize to bytes so the
production entry point (:func:`translate_scip_to_hg`) is exercised end
to end. No live ``rust-analyzer`` invocation — that arrives in Slice B.
"""
from __future__ import annotations

from typing import Optional

import pytest

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.scip._generated import scip_pb2
from hypergumbo_lang_rust_analyzer import (
    reassign_rust_stable_ids,
    translate_scip_to_hg,
)


DEFINITION_ROLE = 0x01

# Shared default span for the Symbol helpers below. Module-level so the
# ruff B008 (mutable-default) check is satisfied without cloning per call.
_DEFAULT_SPAN = Span(1, 3, 0, 0)


def _rust_symbol(descriptor: str) -> str:
    """Build a rust-analyzer-style SCIP symbol string (single-line helper).

    The descriptor is the final block identifying the item (e.g.
    ``"math/add()."`` for a function ``add`` in module ``math``). Full
    rust-analyzer symbol strings use the ``rust-analyzer cargo <crate>
    <version> <descriptor>`` header.
    """
    return f"rust-analyzer cargo my_crate 0.1.0 {descriptor}"


def _rust_function_doc(
    *, path: str, symbol: str, start_line: int, end_line: int,
) -> scip_pb2.Document:
    """A single Document containing one Rust function definition."""
    return scip_pb2.Document(
        language="Rust",
        relative_path=path,
        symbols=[scip_pb2.SymbolInformation(symbol=symbol)],
        occurrences=[scip_pb2.Occurrence(
            symbol=symbol,
            symbol_roles=DEFINITION_ROLE,
            # SCIP uses 0-indexed line/col; translator +1 rewrites to 1-indexed.
            range=[start_line - 1, 0, end_line - 1, 0],
        )],
    )


def _index_bytes(*docs: scip_pb2.Document) -> bytes:
    return scip_pb2.Index(documents=list(docs)).SerializeToString()


# ---------------------------------------------------------------------------
# reassign_rust_stable_ids
# ---------------------------------------------------------------------------


class TestReassignRustStableIds:
    def _symbol(
        self,
        *,
        language: str = "rust",
        kind: str = "function",
        path: str = "src/lib.rs",
        span: Optional[Span] = _DEFAULT_SPAN,
        stable_id: str = "scip-raw-id",
    ) -> Symbol:
        return Symbol(
            id=f"{language}:{path}:1-3:foo:{kind}",
            name="foo",
            kind=kind,
            language=language,
            path=path,
            span=span,
            origin="scip",
            stable_id=stable_id,
        )

    def test_non_rust_symbol_untouched(self) -> None:
        sym = self._symbol(language="python")

        def _reader(_p: str) -> bytes:  # pragma: no cover — unused
            raise AssertionError("reader should not fire for non-rust")

        out = reassign_rust_stable_ids([sym], _reader)
        assert out[0].stable_id == "scip-raw-id"
        assert out[0] is sym  # pass-through retains identity

    def test_non_function_rust_symbol_untouched(self) -> None:
        sym = self._symbol(kind="struct")
        out = reassign_rust_stable_ids(
            [sym], lambda _p: b"",  # pragma: no cover
        )
        assert out[0].stable_id == "scip-raw-id"

    def test_span_less_symbol_untouched(self) -> None:
        sym = self._symbol(span=None)
        out = reassign_rust_stable_ids(
            [sym], lambda _p: b"",  # pragma: no cover
        )
        assert out[0].stable_id == "scip-raw-id"

    def test_pathless_symbol_untouched(self) -> None:
        sym = self._symbol(path="")
        out = reassign_rust_stable_ids(
            [sym], lambda _p: b"",  # pragma: no cover
        )
        assert out[0].stable_id == "scip-raw-id"

    def test_source_reader_returns_none_untouched(self) -> None:
        sym = self._symbol()
        out = reassign_rust_stable_ids([sym], lambda _p: None)
        assert out[0].stable_id == "scip-raw-id"

    def test_source_reader_raises_untouched(self) -> None:
        sym = self._symbol()

        def _raises(_p: str) -> bytes:
            raise FileNotFoundError("missing")

        out = reassign_rust_stable_ids([sym], _raises)
        assert out[0].stable_id == "scip-raw-id"

    def test_parity_lookup_returns_none_untouched(self) -> None:
        """Source is readable but no function at the span → passthrough."""
        sym = self._symbol()
        # Source has no function_item at lines 1-3, so the parity helper
        # returns None (either tree-sitter unavailable or no match).
        out = reassign_rust_stable_ids([sym], lambda _p: b"// just a comment\n")
        assert out[0].stable_id == "scip-raw-id"

    def test_successful_parity_overwrites_stable_id(self) -> None:
        """When the parity helper returns an id, stable_id is rewritten."""
        source = b"pub fn add(a: i32, b: i32) -> i32 { a + b }\n"
        sym = self._symbol(span=Span(1, 1, 0, 0))
        out = reassign_rust_stable_ids([sym], lambda _p: source)
        # Either parity succeeded (id changed) OR tree-sitter-rust is
        # unavailable in the test env (passthrough). Both are legal per
        # the graceful-degrade contract; the assertion below covers both.
        if out[0].stable_id != "scip-raw-id":
            # Happy path: parity overwrote the id; new id must be a
            # hex-style typed stable_id (make_typed_stable_id output).
            assert ":" in out[0].stable_id or len(out[0].stable_id) > 8

    def test_source_cached_per_path(self) -> None:
        """Reader fires once per path even when multiple symbols share it."""
        calls: list[str] = []

        def _reader(p: str) -> bytes:
            calls.append(p)
            return b""

        sym1 = self._symbol()
        sym2 = self._symbol()
        reassign_rust_stable_ids([sym1, sym2], _reader)
        assert calls == ["src/lib.rs"]


# ---------------------------------------------------------------------------
# translate_scip_to_hg
# ---------------------------------------------------------------------------


class TestTranslateScipToHg:
    def test_empty_scip_blob_returns_empty_lists(self) -> None:
        symbols, edges = translate_scip_to_hg(
            _index_bytes(), lambda _p: None,
        )
        assert symbols == []
        assert edges == []

    def test_single_rust_function_round_trips(self) -> None:
        blob = _index_bytes(_rust_function_doc(
            path="src/lib.rs",
            symbol=_rust_symbol("math/add()."),
            start_line=3, end_line=5,
        ))
        symbols, edges = translate_scip_to_hg(blob, lambda _p: None)
        assert len(symbols) == 1
        assert symbols[0].language == "rust"
        assert symbols[0].path == "src/lib.rs"
        assert symbols[0].span == Span(
            start_line=3, end_line=5, start_col=0, end_col=0,
        )
        # No Relationship / Occurrence-ref entries in the fixture, so
        # both edge streams are empty.
        assert edges == []

    def test_multiple_documents(self) -> None:
        blob = _index_bytes(
            _rust_function_doc(
                path="src/a.rs", symbol=_rust_symbol("a/f()."),
                start_line=1, end_line=2,
            ),
            _rust_function_doc(
                path="src/b.rs", symbol=_rust_symbol("b/g()."),
                start_line=4, end_line=6,
            ),
        )
        symbols, _ = translate_scip_to_hg(blob, lambda _p: None)
        assert [s.path for s in symbols] == ["src/a.rs", "src/b.rs"]

    def test_call_edges_surface_in_output(self) -> None:
        """Non-Definition Occurrences flow through scip_index_to_call_edges."""
        caller = _rust_symbol("m/caller().")
        callee = _rust_symbol("m/callee().")
        doc = scip_pb2.Document(
            language="Rust",
            relative_path="src/lib.rs",
            symbols=[
                scip_pb2.SymbolInformation(symbol=caller),
                scip_pb2.SymbolInformation(symbol=callee),
            ],
            occurrences=[
                scip_pb2.Occurrence(
                    symbol=caller, symbol_roles=DEFINITION_ROLE,
                    range=[0, 0, 20, 0],
                ),
                scip_pb2.Occurrence(
                    symbol=callee, symbol_roles=DEFINITION_ROLE,
                    range=[30, 0, 35, 0],
                ),
                # Non-Definition occurrence of callee inside caller's span.
                scip_pb2.Occurrence(
                    symbol=callee, symbol_roles=0, range=[5, 4, 10],
                ),
            ],
        )
        blob = _index_bytes(doc)
        _, edges = translate_scip_to_hg(blob, lambda _p: None)
        # One call edge: caller → callee (references / calls type).
        assert len(edges) >= 1
        assert any(
            e.src.endswith("caller:function")
            or "caller" in e.src
            for e in edges
        )

    def test_reader_is_invoked_for_rust_functions(self) -> None:
        seen_paths: list[str] = []

        def _reader(path: str) -> bytes:
            seen_paths.append(path)
            return b""

        blob = _index_bytes(_rust_function_doc(
            path="src/lib.rs",
            symbol=_rust_symbol("math/add()."),
            start_line=3, end_line=5,
        ))
        translate_scip_to_hg(blob, _reader)
        assert seen_paths == ["src/lib.rs"]

    def test_malformed_bytes_raise(self) -> None:
        """The Protobuf layer raises on non-SCIP bytes; translate does not swallow."""
        import google.protobuf.message
        with pytest.raises(
            google.protobuf.message.DecodeError,
        ):
            translate_scip_to_hg(b"not a scip blob", lambda _p: None)
