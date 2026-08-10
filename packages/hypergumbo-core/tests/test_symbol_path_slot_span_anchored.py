# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fokik: the path-slot chokepoint must anchor on the SPAN, not on slot count.

WHAT THIS CLOSES. ``io_boundary._extract_module_hint`` took ``edge_dst.split(":")[1]``,
which is correct only when the path slot is colon-FREE. ADR-0036 Ruling 1 makes ``path``
the one colon-TOLERANT slot, so on ``rust:std::env:0-0:var:external_symbol`` it returned
``std``. ``_module_matches("std::env", "std")`` is False, and ``_lookup_named_entry``
treats a present-but-mismatched module as a REJECTION rather than a degrade — the
finding is dropped silently. All nine of Rust's catalogued sink modules are
colon-bearing, so this refused the entire Rust sink surface.

WHY MIGRATING TO THE EXISTING CHOKEPOINT WAS NOT ENOUGH, which is the part the item did
not have. ``symbol_path_slot`` was RIGHT-anchored (``parts[1:-3]``) on the grammar's
promise that ``name`` is colon-free. Measured over three real maps
(``scripts/measure-symbol-id-colon-conformance.py``; just, rage, hypergumbo itself):

    conformant                 352,254   both parses agree; migration is a no-op
    path colon, LEFT wrong         740   rust ``std::env`` etc. — migration FIXES
    name colon, RIGHT wrong        483   migration would have REGRESSED these
    unparseable                    584   no ``\\d+-\\d+`` token at all

The 483 are not the objc selectors the item predicted. They are DOUBLE-SPAN ids —
``rust:external:0-0:Stdio:0-0:null:external_symbol`` — an emission defect, not a parse
one. Right-anchoring them yields ``external:0-0:Stdio``, which is not in
``_UNRESOLVED_MODULE_PLACEHOLDERS``, so it takes the module-FILTER branch and silently
rejects. That is the same fatal shape the item was filed about, arrived at from the
other side. Left-anchoring happens to yield the safe ``external``.

SPAN-ANCHORING IS THE ONLY PARSE THAT IS RIGHT ON BOTH. It also makes this function
agree with ``taint._extract_callee_name``, which has anchored on the span all along —
the item names that disagreement between two parsers of the same string as "the tell".

FAILING SAFE IS THE DESIGN CHOICE, stated because a malformed id has no correct answer:
for a double-span id there is no true path slot, and the two candidate answers are
``external`` (a placeholder → degrade to short-name matching plus the F3 gate) and
``external:0-0:Stdio`` (a fake module → silent rejection). The first is wrong in a way a
downstream gate can still catch; the second is wrong in the way that deletes findings.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import _extract_module_hint
from hypergumbo_core.ir import symbol_path_slot


class TestTheGrammarsColonTolerantPathSlot:
    """ADR-0036 Ruling 1: ``path`` is colon-tolerant; every other slot is not."""

    @pytest.mark.parametrize(("symbol_id", "expected"), [
        ("python:os:0-0:mkdir:unresolved", "os"),
        ("go:net/http:0-0:Do:unresolved", "net/http"),
        # The ADR's own worked example.
        ("dart:dart:io:0-0:module:module", "dart:io"),
        # Every Rust sink module is colon-bearing; this is the filed defect.
        ("rust:std::fs:0-0:write:external_symbol", "std::fs"),
        ("rust:std::env:0-0:var:external_symbol", "std::env"),
        ("rust:std::process::Command:0-0:new:external_symbol", "std::process::Command"),
        # A resolved first-party dst: an absolute path, colons irrelevant.
        ("python:/stdlib/os.py:100-102:os.listdir:function", "/stdlib/os.py"),
    ])
    def test_path_slot(self, symbol_id: str, expected: str) -> None:
        assert symbol_path_slot(symbol_id) == expected

    @pytest.mark.parametrize(("symbol_id", "expected"), [
        ("rust:std::fs:0-0:write:external_symbol", "std::fs"),
        ("rust:std::env:0-0:var:external_symbol", "std::env"),
        ("dart:dart:io:0-0:module:module", "dart:io"),
    ])
    def test_the_module_hint_agrees_with_the_chokepoint(
        self, symbol_id: str, expected: str,
    ) -> None:
        """The consumer INV-fokik names must return what the chokepoint returns."""
        assert _extract_module_hint(symbol_id) == expected


class TestTheNameSlotHasTheSameDefectInTheOtherDirection:
    """The path fix alone moves NOTHING, and the positive control is what proved it.

    With the module hint corrected to ``std::fs``, tagging a
    ``rust:std::fs:0-0:write:external_symbol`` edge still produced zero boundaries.
    ``io_boundary._extract_callee_name`` takes "everything after the first three
    fields", which assumes the path slot is colon-FREE — the mirror of the module-hint
    bug, in the same file. It returned ``fs:0-0:write``, so the lookup missed before
    the module hint could matter.

    ``taint._extract_callee_name`` has always anchored on the span and gets this right.
    Two homes for one question, one of them wrong: the same shape WI-ribuz files for the
    path slot. Both now route through :func:`ir.symbol_name_slot`.
    """

    @pytest.mark.parametrize(("symbol_id", "expected"), [
        ("rust:std::fs:0-0:write:external_symbol", "write"),
        ("rust:std::env:0-0:var:external_symbol", "var"),
        ("rust:std::process::Command:0-0:spawn:external_symbol", "spawn"),
        ("python:os:0-0:mkdir:unresolved", "mkdir"),
        ("go:net/http:0-0:Do:unresolved", "Do"),
        ("dart:dart:io:0-0:module:module", "module"),
        ("python:/stdlib/os.py:100-102:os.listdir:function", "os.listdir"),
    ])
    def test_name_slot(self, symbol_id: str, expected: str) -> None:
        from hypergumbo_core.ir import symbol_name_slot

        assert symbol_name_slot(symbol_id) == expected

    @pytest.mark.parametrize(("symbol_id", "expected"), [
        ("rust:std::fs:0-0:write:external_symbol", "write"),
        ("rust:std::env:0-0:var:external_symbol", "var"),
    ])
    def test_the_boundary_tagger_agrees(self, symbol_id: str, expected: str) -> None:
        from hypergumbo_core.io_boundary import _extract_callee_name

        assert _extract_callee_name(symbol_id) == expected

    def test_objc_selector_name_survives(self) -> None:
        """A colon-bearing NAME is what the span anchor buys on the name side."""
        from hypergumbo_core.ir import symbol_name_slot

        assert symbol_name_slot(
            "objc:/app/F.m:10-12:writeToFile:atomically::method",
        ) == "writeToFile:atomically:"

    def test_the_two_name_parsers_now_agree(self) -> None:
        """The disagreement between two parsers of one string is the tell (INV-fokik)."""
        from hypergumbo_core.io_boundary import _extract_callee_name as boundary_name
        from hypergumbo_core.taint import _extract_callee_name as taint_name

        for symbol_id in (
            "rust:std::fs:0-0:write:external_symbol",
            "rust:std::env:0-0:var:external_symbol",
            "python:os:0-0:mkdir:unresolved",
            "dart:dart:io:0-0:module:module",
            "objc:/app/F.m:10-12:writeToFile:atomically::method",
        ):
            assert boundary_name(symbol_id) == taint_name(symbol_id), symbol_id


class TestTheEndToEndBoundaryIsActuallyRecovered:
    """THE closure evidence for INV-fokik: a Rust sink tags where it did not.

    The item's failure mode is a SILENT drop, so a passing unit test on the parser is
    not enough — the assertion has to run production's tagger and see a primitive.
    """

    @pytest.mark.parametrize(("dst", "primitive"), [
        ("rust:std::fs:0-0:write:external_symbol", "std::fs.write"),
        ("rust:std::fs:0-0:create_dir_all:external_symbol", "std::fs.create_dir_all"),
        ("rust:std::env:0-0:var:external_symbol", "std::env.var"),
    ])
    def test_rust_sink_tags(self, dst: str, primitive: str) -> None:
        from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
        from hypergumbo_core.ir import Edge

        edge = Edge.create(
            src="rust:/app/src/main.rs:1-3:main:function",
            dst=dst,
            edge_type="calls",
            line=2,
            evidence_type="ast_call",
            origin="rust",
            origin_run_id="test",
            is_resolved=False,
        )
        assert tag_io_boundaries([edge], {"rust": load_catalog("rust")}) == 1
        assert (edge.meta or {}).get("io_primitive") == primitive


class TestMalformedIdsFailSafe:
    """A double-span id has no true path slot, so it must degrade, not reject.

    Measured at 483 occurrences across just + rage. ``external`` is in
    ``_UNRESOLVED_MODULE_PLACEHOLDERS``; ``external:0-0:Stdio`` is not, and a
    non-placeholder module hint is compared as a real module and rejects everything.
    """

    @pytest.mark.parametrize("symbol_id", [
        "rust:external:0-0:Stdio:0-0:null:external_symbol",
        "rust:external:0-0:fs:0-0:read_dir:external_symbol",
        "rust:external:0-0:Vec:0-0:with_capacity:external_symbol",
        "rust:external:0-0:String:0-0:from_utf8:external_symbol",
    ])
    def test_double_span_id_yields_the_placeholder_not_a_fake_module(
        self, symbol_id: str,
    ) -> None:
        assert symbol_path_slot(symbol_id) == "external"
        assert _extract_module_hint(symbol_id) == "external"

    def test_the_placeholder_is_actually_recognised_as_one(self) -> None:
        """Guard the REASON the above matters, not just its string value.

        Asserting ``== "external"`` alone would still pass if the placeholder set
        were renamed, and the silent-rejection bug would come straight back.
        """
        from hypergumbo_core.taint import _UNRESOLVED_MODULE_PLACEHOLDERS

        assert symbol_path_slot(
            "rust:external:0-0:Stdio:0-0:null:external_symbol",
        ) in _UNRESOLVED_MODULE_PLACEHOLDERS
        assert "external:0-0:Stdio" not in _UNRESOLVED_MODULE_PLACEHOLDERS


class TestNoSpanTokenFallsBackRatherThanFailing:
    """Ids whose span slot is not ``\\d+-\\d+`` must keep their current answer.

    Measured at 584 across the same maps — ``just`` recipes carry a bare line number
    (``just:examples/screenshot.just:6:build:recipe``). Right-anchoring already handles
    those correctly, so span-anchoring keeps it as the fallback instead of returning "".
    """

    @pytest.mark.parametrize(("symbol_id", "expected"), [
        ("just:examples/screenshot.just:6:build:recipe", "examples/screenshot.just"),
        ("just:examples/kitchen-sink.just:202:a:recipe", "examples/kitchen-sink.just"),
    ])
    def test_bare_line_number_span(self, symbol_id: str, expected: str) -> None:
        assert symbol_path_slot(symbol_id) == expected

    def test_too_few_parts_is_still_empty(self) -> None:
        assert symbol_path_slot("python:os:mkdir") == ""
        assert symbol_path_slot("") == ""


class TestFirstPartyPathsStillReturnNoHint:
    """``_extract_module_hint`` keeps its own contract: a file path is not a module.

    This is the INV-sapit surface (PR #256) and is unrelated to the anchor, so it is
    pinned here to prove the migration did not quietly drop it.
    """

    @pytest.mark.parametrize("symbol_id", [
        "python:/home/u/repo/app/util.py:10-12:helper:function",
        "go:/home/u/repo/cmd/main.go:1-3:Command:function",
    ])
    def test_absolute_path_yields_none(self, symbol_id: str) -> None:
        assert _extract_module_hint(symbol_id) is None

    def test_relative_first_party_path_is_still_the_disclosed_gap(self) -> None:
        """DISCLOSED, not fixed: a relative first-party path still reads as a module.

        Recorded on PR #256 with its own re-evaluation trigger. Pinned so a future
        widening has to argue with an assertion rather than discover this silently.
        """
        assert _extract_module_hint("rust:src/main.rs:1-3:helper:function") == "src/main.rs"
