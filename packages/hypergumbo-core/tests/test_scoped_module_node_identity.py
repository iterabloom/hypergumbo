# SPDX-License-Identifier: AGPL-3.0-or-later
"""One library entity must have ONE node identity in a run (INV-rilit).

A language whose module separator is ``::`` must spell a module the same way
on every edge that reaches it: ``imports``, ``calls`` and ``module_attr_ref``
all emit ``std::io``, never ``std.io`` on one of them. Two spellings are two
external nodes for one module (``std::io`` / ``std.io``,
``crate::gadgets::boolean::Boolean`` / ``crate.gadgets.boolean.Boolean``), so
every per-dependency question — who imports it, its centrality, its
supply-chain tier, whether it is dead — would be answered over a split
population.

The colon-bearing spelling is safe in an id. ADR-0036 (D1a) makes the path
slot the one colon-TOLERANT slot in the id grammar, and
:func:`ir.symbol_path_slot` — the chokepoint consumers route through — carries
``rust:std::fs:0-0:write:external_symbol`` as a worked example in its own
docstring. :func:`ir.symbol_name_slot` is span-anchored for the same reason and
handles a colon-bearing name.

This is conformance rather than a new shape, which is the whole risk argument:
colon-bearing rust path and name slots are ordinary on ``calls`` edges
(``rust:std::cmp:0-0:min:external_symbol``,
``rust:std::io:0-0:Error::new:external_symbol``) and flow through every
consumer, so ``module_attr_ref`` matching its siblings introduces no spelling
downstream has not seen.

Separators must also not mix within one id: rewriting only the path taken from
the imports map while the inner qualified text passes through verbatim yields
ids like ``cpp:std:0-0:std.numbers::pi:attribute``.
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
        entry for (INV-pusin)."""
        dsts = _attr_dsts(_rust_edges(tmp_path, (
            "use std::env;\n"
            "pub fn f() -> &'static str { std::env::consts::OS }\n"
        )))
        assert any(symbol_path_slot(d) == "std::env" for d in dsts), dsts
        assert not any("std.env" in d for d in dsts), dsts

    def test_rust_module_reached_BOTH_WAYS_yields_ONE_node_identity(
        self, tmp_path: Path,
    ) -> None:
        """THE INVARIANT ITSELF.

        ``std::io`` is reached by an import, by a call (``io::Error::new``) and
        by a scoped attribute read (``std::io::ErrorKind``). All three must
        name ONE node."""
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
        """No id carries BOTH separators. A half-applied normalisation, which
        rewrites only the imports-map half, would emit
        ``cpp:std:0-0:std.numbers::pi:attribute``."""
        dsts = _attr_dsts(_cpp_edges(tmp_path, (
            "#include <numbers>\n"
            "#include <iostream>\n"
            "double f() { std::cout << 1; return std::numbers::pi; }\n"
        )))
        assert dsts, "fixture emitted no attribute read — vacuous"
        # THE MIXING SHOWS UP IN THE NAME SLOT, not the path slot; an
        # assertion on the path slot would pass vacuously.
        mixed = [
            d for d in dsts
            if "::" in symbol_name_slot(d) and "." in symbol_name_slot(d)
        ]
        assert not mixed, f"id carries both separators: {mixed}"


class TestTheCatalogueStillMatches:
    """POSITIVE CONTROLS: every separator spelling classifies identically
    against the I/O catalogue.

    ``IoPrimitive.qualified_name`` joins module and name with a DOT
    unconditionally, so a ``module: std::env`` row's raw key is the MIXED
    ``std::env.consts`` — never the fully-colon ``std::env::consts`` the
    producer emits. Catalogue matching therefore depends on the separator
    fold: the catalogue indexes each row under its folded name and folds the
    queried name too. The fold lives in one helper on the read side rather
    than in a third registered spelling, because folding in ``lookup`` but not
    in ``lookup_with_module`` leaves ``classify_call`` returning ``None``.
    Without these controls a spelling change could silently lose every Rust
    and C++ attribute classification."""

    @pytest.mark.parametrize("dst,expected", [
        # THE KEY CONTROL. ``qualified_name`` joins with a DOT
        # unconditionally, so a ``::`` module's raw key is the MIXED
        # ``std::env.consts``, never ``std::env::consts``; without the fold
        # the fully-colon spelling would classify as NOTHING while its
        # dotted twin matched.
        # The boundary is ``host_info_read`` because ``std::env::consts::OS``
        # is the target platform's NAME, not configuration that could hold a
        # credential (INV-tutar). The subject of this parametrisation is the
        # SEPARATOR fold, not the boundary value.
        ("rust:std::env:0-0:std::env::consts:attribute", "host_info_read"),
        ("rust:std.env:0-0:std.env.consts:attribute", "host_info_read"),
        ("rust:std::fs:0-0:read_to_string:external_symbol", "fs_read"),
        ("rust:std.fs:0-0:read_to_string:external_symbol", "fs_read"),
        # C++ IS THE OTHER ``scoped_path`` LANGUAGE, so it gets its own row: a
        # fix verified on one language is not verified on another. ``cout`` is
        # declared under module ``std`` with no separator at all, which is the
        # case where the fold must be a NO-OP rather than a rescue.
        ("cpp:std:0-0:std::cout:attribute", "logging"),
        ("cpp:std:0-0:std.cout:attribute", "logging"),
    ])
    def test_both_spellings_classify_identically(
        self, dst: str, expected: str,
    ) -> None:
        from hypergumbo_core.io_boundary import classify_call, load_catalog

        lang = dst.split(":", 1)[0]
        hit = classify_call({lang: load_catalog(lang)}, dst, None)
        assert hit is not None, f"{dst} classified as nothing"
        assert expected in str(hit), f"{dst} -> {hit}, expected {expected}"
