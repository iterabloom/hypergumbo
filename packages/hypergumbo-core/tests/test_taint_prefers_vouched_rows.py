# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taint picks the same row io-boundaries does: a vouched row over a community twin (INV-tobur).

The row choice is shared (``io_boundary.gate_named_entry``), and it prefers a
vouched row by the hit's ``unvouched`` bit. io-boundaries' ``IoPrimitive``
carries that bit; the taint rows auto-derived from the same primitives did
not -- ``TaintSource`` had neither ``unvouched`` nor ``community``, and a
``TaintSink`` derived from a community overlay was stamped ``community=False``.
So every taint hit sorted as vouched and the overlay row, merged in FRONT of
the base, won by order: ``readProcess`` after ``import System.Process
(readProcess)`` was the untrusted_input source ``System.Process.Text.readProcess``
(process-extras, a package the program never imported) in ``verify-claims``
while ``io-boundaries`` named ``System.Process.readProcess``.

A derived row now carries its primitive's provenance as ``community``, a
community taint-file source is stamped like its sinks already were, and both
row types read that as ``unvouched`` -- the one attribute the gate sorts on.
"""
from __future__ import annotations

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.taint import TaintSink, TaintSource, load_builtin_taint_catalog


class TestTheLiveRepro:
    """Through the production loader, with every default overlay merged."""

    def test_the_haskell_overlay_twin_is_in_play(self) -> None:
        """Reach: the community twin really is a candidate for the bare name,
        or the assertions below hold vacuously."""
        cat = load_catalog("haskell")
        assert cat is not None
        modules = {p.module for p in cat.primitives if p.name == "readProcess"}
        assert {"System.Process", "System.Process.Text"} <= modules, modules

    def test_a_hintless_readprocess_source_is_the_boot_library(self) -> None:
        cat = load_builtin_taint_catalog()
        for hint in (None, "external"):
            src = cat.match_source("haskell", "readProcess", hint)
            assert src is not None, hint
            assert src.module == "System.Process", (hint, src.module)
            assert not src.unvouched

    def test_a_hintless_readprocess_sink_is_the_boot_library(self) -> None:
        cat = load_builtin_taint_catalog()
        sink = cat.match_sink("haskell", "readProcess", "external")
        assert sink is not None
        assert sink.module == "System.Process", sink.module
        assert not sink.unvouched

    def test_taint_and_io_boundaries_name_the_same_row(self) -> None:
        io = load_catalog("haskell")
        assert io is not None
        taint = load_builtin_taint_catalog()
        for name in ("readProcess", "readProcessWithExitCode"):
            io_hit = io.lookup_with_module(name, "external")
            src = taint.match_source("haskell", name, "external")
            assert io_hit is not None and src is not None, name
            assert src.module == io_hit.module, (name, src.module, io_hit.module)

    def test_without_default_overlays_the_base_row_is_unchanged(self) -> None:
        cat = load_builtin_taint_catalog(include_community=False)
        src = cat.match_source("haskell", "readProcess", "external")
        assert src is not None and src.module == "System.Process"


class TestTheRowsCarryTheirProvenance:
    def test_a_row_derived_from_a_community_overlay_is_unvouched(self) -> None:
        cat = load_builtin_taint_catalog()
        rows = [
            s for s in cat._sources.get("haskell", [])
            if s.qualified_name == "System.Process.Text.readProcess"
        ]
        assert rows, "the overlay's derived source is missing"
        assert all(s.community and s.unvouched for s in rows)
        base = [
            s for s in cat._sources.get("haskell", [])
            if s.qualified_name == "System.Process.readProcess"
        ]
        assert base and not any(s.unvouched for s in base)

    def test_unvouched_reads_community_on_both_row_types(self) -> None:
        assert TaintSource("untrusted_input", "m", "f", "function", community=True).unvouched
        assert not TaintSource("untrusted_input", "m", "f", "function").unvouched
        assert TaintSink("ipc", "untrusted", "m", "f", "function", community=True).unvouched
        assert not TaintSink("ipc", "untrusted", "m", "f", "function").unvouched

    def test_a_community_taint_file_source_is_unvouched(self) -> None:
        """crypto_community.yaml declares ``provenance: community``; its sources
        are stamped as its sinks and sanitizers already were."""
        cat = load_builtin_taint_catalog()
        rows = [
            s for s in cat._sources.get("python", [])
            if s.qualified_name == "cryptography.fernet.Fernet.decrypt"
        ]
        assert rows, "the community crypto source is missing"
        assert all(s.unvouched for s in rows)
