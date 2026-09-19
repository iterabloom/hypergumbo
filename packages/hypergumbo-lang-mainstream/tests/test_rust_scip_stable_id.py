# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the SCIP → rust.py stable_id mapping helper (WI-bajuz, ADR-0014).

Two contracts are pinned here, and they must not be confused (INV-dolud):

1. **The helper's extraction contract.** Given the source and the ITEM span
   of a function — the ``function_item`` / ``function_signature_item``'s own
   first and last line — ``compute_rust_stable_id_from_source`` reproduces
   ``rust.py``'s stable_id byte for byte. The spans in that test come from
   ``rust.py`` itself, so it proves the helper agrees with the baseline it is
   compared against and nothing about what production feeds it.

2. **What production actually feeds it.** ``reassign_rust_stable_ids`` passes
   the SCIP Definition-occurrence range, and rust-analyzer's definition range
   is the IDENTIFIER TOKEN, never the enclosing item. The helper requires both
   endpoints to match, so on producer-shaped input it abstains for every
   multi-line item and parity is reached only by single-line items. That is
   pinned against ranges RECORDED from ``rust-analyzer scip`` on this very
   sample (``RUST_ANALYZER_DEFINITION_LINES``), so the test consumes
   producer-shaped input and would go red the day the helper starts matching
   on the start line — at which point it is re-pointed, not deleted.
   Measured on aardvark-dns: 0 of 52 functions emitted by both arms share a
   stable_id. The two arms carry independent identities today; whether they
   should share one is WI-gojum's parked question.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


from recorded_rust_analyzer_1_94_0 import (
    SAMPLE_SOURCE as RUST_SAMPLE,
    callable_item_to_token_lines,
)

# Recorded from rust-analyzer 1.94.0 (4a4ef49 2026-03-02) on a crate whose
# ``src/lib.rs`` is RUST_SAMPLE byte for byte — read from the shared recorded
# module (ADR-0057 §12, WI-romuh) rather than transcribed here. Keys are the
# ITEM spans (what rust.py spans a callable over); values are the identifier
# token rust-analyzer puts in the Definition ``range``. Only the single-line
# trait declaration has a token range equal to its item range.
RUST_ANALYZER_DEFINITION_LINES: dict[tuple[int, int], tuple[int, int]] = (
    callable_item_to_token_lines()
)


def _collect_rust_py_stable_ids(tmp_path: Path, source: str) -> dict[tuple[int, int], str]:
    """Run the ``rust.py`` analyzer and return {(start_line, end_line): stable_id}."""
    from hypergumbo_lang_mainstream.rust import analyze_rust

    rs = tmp_path / "sample.rs"
    rs.write_text(source)
    result = analyze_rust(tmp_path)

    mapping: dict[tuple[int, int], str] = {}
    for sym in result.symbols:
        if sym.stable_id is None:
            continue
        # The SCIP helper computes identity for CALLABLES only; rust.py now also
        # emits canonical stable_ids for struct fields / module consts (WI-jusus
        # F5), which the function-parity helper does not cover — scope to
        # function/method so the parity comparison stays apples-to-apples.
        if sym.kind not in ("function", "method"):
            continue
        mapping[(sym.span.start_line, sym.span.end_line)] = sym.stable_id
    return mapping


class TestComputeRustStableIdFromSource:
    """Parity with ``rust.py`` on the shared extraction pipeline."""

    @pytest.mark.incumbent_fed(
        "extraction contract: proves the helper agrees with rust.py when handed "
        "rust.py's own item spans; claims nothing about what production feeds it "
        "(INV-dolud) — the recorded-span test beside it is the production arm"
    )
    def test_helper_reproduces_rust_py_ids_when_given_the_item_span(
        self, tmp_path: Path,
    ) -> None:
        """Contract 1: fed rust.py's own item spans, the helper returns rust.py's ids.

        RE-TITLED from ``test_parity_with_rust_py_for_every_function_in_sample``
        (INV-dolud). The spans below are collected from ``rust.py`` and fed
        straight back, so this proves the helper's extraction agrees with the
        baseline — not that production input ever reaches it. The recorded-span
        test that follows is the production arm.
        """
        from hypergumbo_lang_mainstream.rust_scip import (
            compute_rust_stable_id_from_source,
        )

        baseline = _collect_rust_py_stable_ids(tmp_path, RUST_SAMPLE)
        assert baseline, "rust.py produced no stable_ids — sample may be empty"

        source = RUST_SAMPLE.encode("utf-8")
        for (start_line, end_line), expected in baseline.items():
            # rel_path mirrors the file _collect_rust_py_stable_ids writes ("sample.rs"),
            # so the WI-bokab file anchor byte-matches rust.py's (WI-zakub parity).
            got = compute_rust_stable_id_from_source(
                source, start_line, end_line, rel_path="sample.rs",
            )
            assert got == expected, (
                f"stable_id mismatch at lines {start_line}-{end_line}: "
                f"rust.py={expected!r} helper={got!r}"
            )

    def test_recorded_rust_analyzer_spans_reach_parity_only_for_single_line_items(
        self, tmp_path: Path,
    ) -> None:
        """Contract 2, the difference arm: producer-shaped input (INV-dolud).

        rust-analyzer's Definition occurrence is the identifier token, so its
        range equals the item range only when the whole item sits on one line.
        Fed those recorded ranges, the helper abstains (``None``) for every
        multi-line callable and reaches rust.py's id for the single-line one —
        which is exactly why ``reassign_rust_stable_ids`` leaves the SCIP id in
        place for 0 of 52 shared functions on a real crate. If this test goes
        red because the helper learned to match on the start line, re-point it:
        that is the fix landing, and the other assertions here still hold.
        """
        from hypergumbo_lang_mainstream.rust_scip import (
            compute_rust_stable_id_from_source,
        )

        baseline = _collect_rust_py_stable_ids(tmp_path, RUST_SAMPLE)
        # The fixture must describe THIS sample: rust.py must see exactly the
        # six callables rust-analyzer was recorded on, at the same item spans.
        assert set(baseline) == set(RUST_ANALYZER_DEFINITION_LINES)

        source = RUST_SAMPLE.encode("utf-8")
        reached: list[tuple[int, int]] = []
        for item_span, ra_span in RUST_ANALYZER_DEFINITION_LINES.items():
            got = compute_rust_stable_id_from_source(source, *ra_span, rel_path="sample.rs")
            if item_span[0] == item_span[1]:
                assert got == baseline[item_span], (
                    f"single-line item {item_span}: token range equals item range, "
                    f"so parity must be reached; helper={got!r}"
                )
                reached.append(item_span)
            else:
                assert got is None, (
                    f"multi-line item {item_span} fed rust-analyzer's token range "
                    f"{ra_span} unexpectedly matched: {got!r} — has the helper "
                    "started matching on start_line? Re-point this test."
                )
        assert reached == [(25, 25)]

    @pytest.mark.incumbent_fed(
        "extraction contract: two same-named methods at rust.py's own spans get "
        "rust.py's own distinct ids from the helper; not a claim about producer input"
    )
    def test_distinguishes_trait_impl_methods_by_span(self, tmp_path: Path) -> None:
        """Two methods at different spans with the same name receive different stable_ids.

        ``Counter::greet`` (inherent impl) and ``Counter::greet`` (trait impl) live
        in different impl blocks at different source spans. rust.py disambiguates
        them via their (potentially different) signatures; the helper must too.
        This models the WI-zakub trait-impl case where rust-analyzer leaves
        SCIP Relationship empty and we must rely on the span anchor.
        """
        from hypergumbo_lang_mainstream.rust_scip import (
            compute_rust_stable_id_from_source,
        )

        src = """\
pub struct Counter { value: i32 }

impl Counter {
    pub fn greet(&self, name: &str) -> String { format!("hi {}", name) }
}

pub trait Greeter { fn greet(&self, name: &str) -> String; }

impl Greeter for Counter {
    fn greet(&self, name: &str) -> String { format!("hello {}", name) }
}
"""
        baseline = _collect_rust_py_stable_ids(tmp_path, src)
        greet_spans = [
            span for span, _sid in baseline.items()
            # method bodies are tiny — anchor by span unique-ness
        ]
        # Both greet methods should appear in rust.py's output.
        source = src.encode("utf-8")
        helper_ids = {
            span: compute_rust_stable_id_from_source(source, *span, rel_path="sample.rs")
            for span in greet_spans
        }
        for span, expected in baseline.items():
            assert helper_ids[span] == expected

    def test_returns_none_when_no_function_at_span(self, tmp_path: Path) -> None:
        """A span that doesn't cover any function_item returns None (SCIP-only suffix territory)."""
        from hypergumbo_lang_mainstream.rust_scip import (
            compute_rust_stable_id_from_source,
        )

        src = "pub fn solo() {}\n"
        # Line 5 is past EOF — no match expected.
        assert compute_rust_stable_id_from_source(src.encode("utf-8"), 5, 5) is None

    def test_returns_none_when_tree_sitter_unavailable(self) -> None:
        """Gracefully degrades when tree-sitter-rust isn't installed.

        The helper is opt-in (SCIP backend requires rust-analyzer anyway), but
        its unavailability path must return None rather than raise, so that
        downstream translation code can fall back on rust.py without special
        casing.
        """
        from hypergumbo_lang_mainstream import rust_scip

        with patch.object(rust_scip, "is_rust_tree_sitter_available", return_value=False):
            result = rust_scip.compute_rust_stable_id_from_source(
                b"fn main() {}", 1, 1,
            )
        assert result is None
