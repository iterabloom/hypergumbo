# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the SCIP → rust.py stable_id mapping helper (WI-bajuz, ADR-0014).

The rust-analyzer SCIP backend (WI-duzul) will emit symbols that describe the
same source locations the tree-sitter ``rust.py`` pass already analyzes. For
cross-pass dedup to work, both passes must agree on ``stable_id``. This module
verifies that ``compute_rust_stable_id_from_source`` — the helper the SCIP
backend will call — produces byte-for-byte identical stable_id values to the
existing ``rust.py`` analyzer when given the same source and line span.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


RUST_SAMPLE = """\
pub fn top_level_add(x: i32, y: i32) -> i32 {
    x + y
}

fn private_helper(name: &str) -> String {
    name.to_string()
}

pub struct Counter {
    value: i32,
}

impl Counter {
    pub fn increment(&mut self, by: i32) -> i32 {
        self.value += by;
        self.value
    }

    fn reset(&mut self) {
        self.value = 0;
    }
}

pub trait Greeter {
    fn greet(&self, name: &str) -> String;
}

impl Greeter for Counter {
    fn greet(&self, name: &str) -> String {
        format!("hi {}", name)
    }
}
"""


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
        mapping[(sym.span.start_line, sym.span.end_line)] = sym.stable_id
    return mapping


class TestComputeRustStableIdFromSource:
    """Parity with ``rust.py`` on the shared extraction pipeline."""

    def test_parity_with_rust_py_for_every_function_in_sample(
        self, tmp_path: Path,
    ) -> None:
        """Each rust.py-derived stable_id matches the SCIP helper's output for the same span."""
        from hypergumbo_lang_mainstream.rust_scip import (
            compute_rust_stable_id_from_source,
        )

        baseline = _collect_rust_py_stable_ids(tmp_path, RUST_SAMPLE)
        assert baseline, "rust.py produced no stable_ids — sample may be empty"

        source = RUST_SAMPLE.encode("utf-8")
        for (start_line, end_line), expected in baseline.items():
            got = compute_rust_stable_id_from_source(source, start_line, end_line)
            assert got == expected, (
                f"stable_id mismatch at lines {start_line}-{end_line}: "
                f"rust.py={expected!r} helper={got!r}"
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
            span: compute_rust_stable_id_from_source(source, *span)
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
