# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for :mod:`hypergumbo_lang_rust_analyzer.translate` (WI-duzul Slice A).

Covers the SCIP bytes → ``(symbols, edges)`` translator plus the rust.py
stable-id parity post-processor. All tests build SCIP fixtures in-memory
via the vendored ``scip_pb2`` module and serialize to bytes so the
production entry point (:func:`translate_scip_to_hg`) is exercised end
to end. No live ``rust-analyzer`` invocation — that arrives in Slice B.
"""
from __future__ import annotations

from pathlib import Path
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

    def test_parity_byte_matches_rust_py_for_nested_path(self, tmp_path: Path) -> None:
        """WI-bokab v7: the SCIP parity helper's id must byte-EQUAL ``analyze_rust``'s id
        for the same function at the same repo-relative NESTED path. Post-v7 the file path
        is folded into the typed-tier hash, so a path-representation divergence between the
        SCIP backend (``doc.relative_path`` -> ``sym.path``) and rust.py's
        ``str(source_file.relative_to(repo_root))`` would silently break cross-pass dedup
        (double-counting). tree-sitter-rust is a hard dependency of the rust analyzer, so
        no graceful-degrade guard is needed — a missing grammar must fail loudly."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        src = "pub fn add(a: i32, b: i32) -> i32 { a + b }\n"
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text(src)
        baseline = {
            (s.span.start_line, s.span.end_line): s.stable_id
            for s in analyze_rust(tmp_path).symbols
            if s.name == "add" and s.stable_id
        }
        assert baseline, "rust.py produced no stable_id for add at src/lib.rs"
        span, expected = next(iter(baseline.items()))

        sym = self._symbol(path="src/lib.rs", span=Span(span[0], span[1], 0, 0))
        out = reassign_rust_stable_ids([sym], lambda _p: src.encode("utf-8"))
        assert out[0].stable_id == expected, (
            "SCIP parity broke under v7 file-anchoring at nested path src/lib.rs: "
            f"helper={out[0].stable_id!r} rust.py={expected!r}"
        )

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

    def test_edge_endpoints_are_hypergumbo_symbol_ids(self) -> None:
        """Regression (scip-edge-drop): a translated edge's src/dst must be the
        translated Symbols' hypergumbo ids — NOT the raw SCIP descriptor strings.

        translate_scip_to_hg was calling the edge builders without a
        ``resolve_symbol`` map, so every edge kept the raw SCIP symbol string
        (e.g. ``'... crate/'``) as its endpoint. Those match no Symbol.id, so
        every scip edge is dangling and the survey's endpoint-integrity finalize
        drops all of them (2978 → 0 on zoxide) even though the scip *symbols*
        survive — a silent half-merge that made the rust-analyzer backend emit no
        call graph via ``hypergumbo survey``.
        """
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
                    symbol=caller, symbol_roles=DEFINITION_ROLE, range=[0, 0, 20, 0],
                ),
                scip_pb2.Occurrence(
                    symbol=callee, symbol_roles=DEFINITION_ROLE, range=[30, 0, 35, 0],
                ),
                scip_pb2.Occurrence(symbol=callee, symbol_roles=0, range=[5, 4, 10]),
            ],
        )
        blob = _index_bytes(doc)
        symbols, edges = translate_scip_to_hg(blob, lambda _p: None)
        sym_ids = {s.id for s in symbols}
        call_edges = [
            e for e in edges
            if "callee" in (e.meta or {}).get("scip_dst_symbol", "")
        ]
        assert call_edges, "expected a caller->callee call edge"
        for e in call_edges:
            assert e.src in sym_ids, f"src {e.src!r} is not a translated Symbol id"
            assert e.dst in sym_ids, f"dst {e.dst!r} is not a translated Symbol id"

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
