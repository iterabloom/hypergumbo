# SPDX-License-Identifier: AGPL-3.0-or-later
"""One library entity must have ONE node identity in a run (INV-rilit).

WHAT WAS BROKEN. A language whose module separator is ``::`` spelled the same
module two ways depending on which edge reached it: ``imports`` and ``calls``
emitted ``std::io``, while ``module_attr_ref`` emitted ``std.io``. Those are two
external nodes for one module, so every per-dependency question — who imports
it, its centrality, its supply-chain tier, whether it is dead — was answered
over a split population.

MEASURED 2026-08-24 on bellman (2,467 edges), by folding every rust node's path
slot and counting the survivors::

    TEN entities existed under two spellings in one run
      bellman.VerificationError / bellman::VerificationError
      bls12_381.Scalar          / bls12_381::Scalar
      crate.SynthesisError      / crate::SynthesisError
      crate.gadgets.boolean.Boolean / crate::gadgets::boolean::Boolean
      ff.Field                  / ff::Field
      std.cmp.Ordering          / std::cmp::Ordering
      std.io                    / std::io
      super.SynthesisError      / super::SynthesisError
      super.boolean.Boolean     / super::boolean::Boolean
      super.uint32.UInt32       / super::uint32::UInt32

THE ITEM'S OWN COST FIGURE WAS TOO HIGH, AND SO WAS THE CORRECTION TO IT. The
item filed "4 of 23 withhold entries are one module under two spellings"; a
later note repeated the 4. Measured directly, only TWO of the coverage gate's
entries are a second spelling of one already listed (``bls12_381::Scalar`` and
``std::io``). The other two counted pairs — ``std.cmp.Ordering`` / ``std::cmp``
and ``std.sync`` / ``std::sync::Arc`` — are a TYPE path beside a MODULE path,
which is a different fact and not a spelling split at all. The node-level cost
(ten entities) is the larger and the real one; the gate-level cost is two.

WHY THE DOTTED SPELLING EXISTED, and why that reason no longer holds.
``emit_module_attribute_refs`` documented it as dot-normalising "so the
resulting edge ID survives downstream ``:``-split parsing". ADR-0036 (D1a) made
the path slot the one colon-TOLERANT slot in the id grammar, and
:func:`ir.symbol_path_slot` — the chokepoint every consumer now routes through —
carries ``rust:std::fs:0-0:write:external_symbol`` as a WORKED EXAMPLE in its
own docstring. :func:`ir.symbol_name_slot` is span-anchored for the same reason
and explicitly handles a colon-bearing name. The workaround outlived its defect
(LIVE.md rule 1: verify the premise in the CODE, not the design doc).

WHY THIS IS CONFORMANCE RATHER THAN A NEW SHAPE, which is the whole risk
argument. A colon-bearing rust path slot is ALREADY in production and has been
all along — ``rust:std::cmp:0-0:min:external_symbol`` and
``rust:bls12_381::Scalar:0-0:from:external_symbol`` are ordinary ``calls``
edges on bellman, and they flow through every consumer this change can reach.
The name slot too: ``rust:std::io:0-0:Error::new:external_symbol``. So
``module_attr_ref`` was the OUTLIER, and this makes it match its siblings
rather than introducing a spelling nothing downstream has seen.

THE NORMALISATION WAS ALSO ONLY HALF-APPLIED, which is its own tell: C++ emitted
``cpp:std:0-0:std.numbers::pi:attribute`` — ONE id carrying BOTH separators —
because only the path taken from the imports map was rewritten and the inner
qualified text passed through verbatim.
"""

from pathlib import Path

import pytest

# IMPORTED HARD, not via ``importorskip``. Both grammars are declared
# dependencies of hypergumbo-lang-mainstream, so a skip here could only ever
# mean the environment is broken — and it would hide that by passing green,
# which is the one outcome this file must not produce.
from hypergumbo_core.io_boundary import normalize_module_separators
from hypergumbo_core.ir import symbol_name_slot, symbol_path_slot


def _rust_edges(tmp_path: Path, source: str) -> list:
    from hypergumbo_lang_mainstream.rust import analyze_rust

    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "lib.rs").write_text(source)
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "probe"\nversion = "0.1.0"\n')
    return list(analyze_rust(tmp_path).edges)


def _cpp_edges(tmp_path: Path, source: str) -> list:
    from hypergumbo_lang_mainstream.cpp import analyze_cpp

    (tmp_path / "main.cpp").write_text(source)
    return list(analyze_cpp(tmp_path).edges)


def _attr_dsts(edges: list) -> list[str]:
    return [e.dst for e in edges if e.edge_type == "module_attr_ref"]


class TestOneEntityOneSpelling:
    """The invariant, at the producer that mints the id."""

    def test_rust_attribute_read_spells_the_module_with_the_language_separator(
        self, tmp_path: Path,
    ) -> None:
        """``std::env::consts`` — the shape rust.yaml declares an ``attributes:``
        entry for, and the one INV-pusin's fix left standing."""
        dsts = _attr_dsts(_rust_edges(tmp_path, (
            "use std::env;\n"
            "pub fn f() -> &'static str { std::env::consts::OS }\n"
        )))
        assert any(symbol_path_slot(d) == "std::env" for d in dsts), dsts
        assert not any("std.env" in d for d in dsts), dsts

    def test_rust_module_reached_BOTH_WAYS_yields_ONE_node_identity(
        self, tmp_path: Path,
    ) -> None:
        """THE INVARIANT ITSELF, and the only test here that would have failed
        for the right reason before the fix.

        bellman's shape: ``std::io`` is reached by an import, by a call
        (``io::Error::new``) and by a scoped attribute read
        (``std::io::ErrorKind``). All three must name ONE node."""
        edges = _rust_edges(tmp_path, (
            "use std::io;\n"
            "pub fn f() -> io::Error {\n"
            "    let _k = std::io::ErrorKind::Other;\n"
            "    io::Error::new(std::io::ErrorKind::Other, \"x\")\n"
            "}\n"
        ))
        slots = {
            symbol_path_slot(e.dst)
            for e in edges
            if e.dst.startswith("rust:") and "io" in e.dst
        }
        folded = {normalize_module_separators(s) for s in slots}
        for f in folded:
            spellings = {s for s in slots if normalize_module_separators(s) == f}
            assert len(spellings) == 1, (
                f"one entity {f!r} under {len(spellings)} spellings: "
                f"{sorted(spellings)}"
            )

    def test_cpp_attribute_read_does_not_mix_separators_in_one_id(
        self, tmp_path: Path,
    ) -> None:
        """The half-applied normalisation, pinned. C++ emitted
        ``cpp:std:0-0:std.numbers::pi:attribute`` — one id carrying BOTH
        separators, because only the imports-map half was rewritten."""
        dsts = _attr_dsts(_cpp_edges(tmp_path, (
            "#include <numbers>\n"
            "#include <iostream>\n"
            "double f() { std::cout << 1; return std::numbers::pi; }\n"
        )))
        assert dsts, "fixture emitted no attribute read — vacuous"
        # THE MIXING IS IN THE NAME SLOT, not the path slot, and a first draft
        # of this assertion looked in the path slot and PASSED VACUOUSLY.
        mixed = [
            d for d in dsts
            if "::" in symbol_name_slot(d) and "." in symbol_name_slot(d)
        ]
        assert not mixed, f"id carries both separators: {mixed}"


class TestTheCatalogueStillMatches:
    """POSITIVE CONTROLS, and they earned their keep — the premise they were
    written to confirm turned out to be FALSE.

    The dotted spelling's one real job was catalogue matching, and the code
    said ``IoBoundaryCatalog`` "registers both ``::`` and ``.`` forms in its
    qualified-name index", so changing the emitted spelling looked free. It was
    not. ``IoPrimitive.qualified_name`` joins module and name with a DOT
    unconditionally, so a ``module: std::env`` row was indexed as the MIXED
    ``std::env.consts`` plus a fully-dotted alias — and NEVER as the
    fully-colon ``std::env::consts`` the producer was about to emit. Run
    before the producer changed, this class failed, which is the only reason
    the change did not ship a silent loss of every Rust and C++ attribute
    classification.

    The fix is one separator-folding helper on the read side rather than a
    third registered spelling, because the same gap reappeared WITHIN this
    session: folding in ``lookup`` but not in ``lookup_with_module`` left
    ``classify_call`` still returning ``None``."""

    @pytest.mark.parametrize("dst,expected", [
        # THE ONE THAT CAUGHT THE FALSE PREMISE. Before this landed, the
        # fully-colon spelling classified as NOTHING while its dotted twin
        # matched, because ``qualified_name`` joins with a DOT unconditionally
        # — so a ``::`` module was indexed as the MIXED ``std::env.consts``
        # plus a dotted alias, and never as ``std::env::consts``. Shipping the
        # producer change without this control would have silently lost every
        # Rust and C++ attribute classification.
        ("rust:std::env:0-0:std::env::consts:attribute", "env_read"),
        ("rust:std.env:0-0:std.env.consts:attribute", "env_read"),
        ("rust:std::fs:0-0:read_to_string:external_symbol", "fs_read"),
        ("rust:std.fs:0-0:read_to_string:external_symbol", "fs_read"),
        # C++ IS THE OTHER ``scoped_path`` LANGUAGE, so it gets its own row: a
        # fix verified on one language is not verified on another. ``cout`` is
        # declared under module ``std`` with no separator at all, which is the
        # case where the fold must be a NO-OP rather than a rescue.
        ("cpp:std:0-0:std::cout:attribute", "ipc_send"),
        ("cpp:std:0-0:std.cout:attribute", "ipc_send"),
    ])
    def test_both_spellings_classify_identically(
        self, dst: str, expected: str,
    ) -> None:
        from hypergumbo_core.io_boundary import classify_call, load_catalog

        lang = dst.split(":", 1)[0]
        hit = classify_call({lang: load_catalog(lang)}, dst, None)
        assert hit is not None, f"{dst} classified as nothing"
        assert expected in str(hit), f"{dst} -> {hit}, expected {expected}"
