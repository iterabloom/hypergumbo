# SPDX-License-Identifier: AGPL-3.0-or-later
"""The PATH slot is colon-tolerant and must be read through the chokepoint; the
NAME slot is colon-FREE BY RULE and a colon there is a producer defect
(INV-divuf, reduced).

WHAT THIS FILE CLAIMED FIRST, AND WHY IT WAS WRONG. The first draft asserted
that "only ``parts[0]`` and ``parts[-1]`` are colon-free by the grammar" and
built a shrink-only gate on top of it. That premise is FALSE, and a failing test
elsewhere in the suite is what refuted it:
``test_id_format_validator_names_the_name_slot_colon_not_the_span``.

ADR-0036 Ruling 1 makes the NAME slot colon-free BY RULE.
:func:`analyze.base.sanitize_id_name_segment` folds ``:`` to ``.`` and
:func:`analyze.base.make_symbol_id` applies it to every name slot (WI-sikar). So
``parts[-2]`` is CORRECT for a well-formed id, and the ~13 positional name-slot
reads scattered through the tree are not defects — they are the grammar being
relied on. Only the PATH slot is colon-tolerant (ADR-0036 D1a): ``dart:io``,
``std::io``.

WHERE THE COLONS ACTUALLY COME FROM, then. ``make_unresolved_edge`` builds its
dst with a raw f-string::

    dst_id = f"{lang}:{module_hint}:0-0:{callee_name}:unresolved"   # base.py

and never applies the sanitizer — which is precisely what ``spec_validator``'s
own error message warns against ("Use make_symbol_id(...) rather than
constructing IDs with f-strings"). An Objective-C selector
``writeToFile:atomically:`` therefore lands in the name slot verbatim, in
violation of Ruling 1. THAT is the root cause of WI-nakut's empty method name,
and it is a PRODUCER defect at one line — not a parsing defect at fifteen.

WHY THE FIX IS NOT SIMPLY "SANITIZE IT", measured before recommending. 107 of
the 128 primitives in ``io_primitives/objc.yaml`` carry a colon in their name
(``fileExistsAtPath:``, ``writeToFile:atomically:``), so sanitizing the edge to
``writeToFile.atomically.`` would break catalogue matching unless the qualified
index registers the folded form too. That is a real architectural fork — restore
the grammar at the producer, or amend the grammar to admit the colon — and it is
recorded on INV-divuf rather than decided here.

WHAT SURVIVES AND IS TESTED BELOW: the PATH-slot half, which was never in doubt,
and the name-slot READS in ``verify_claims`` that a reader sees — the caveat
named ``writeToFile`` for ``writeToFile:atomically:``, a method that does not
exist. Reading through :func:`ir.symbol_name_slot` returns whatever the producer
actually emitted, whole, so the disclosure is honest about the id it has rather
than truncating it. That is right under EITHER arm of the fork.
"""

import pytest

from hypergumbo_core import ir
from hypergumbo_core.ir import symbol_name_slot, symbol_path_slot

# Real production id shapes. Not invented: each was observed in a survey or a
# live analyzer run during the INV-divuf measurement.
OBJC_SELECTOR = "objc:external:0-0:writeToFile:atomically::unresolved"
RUST_QUALIFIED = "rust:std::io:0-0:Error::new:external_symbol"
DART_COLON_PATH = "dart:dart:io:0-0:module:module"
PLAIN = "python:app/config.py:3-9:load:function"


class TestThePathSlotIsColonTolerant:
    """ADR-0036 D1a. This half was never in question and is what the surviving
    conversions rest on."""

    @pytest.mark.parametrize("sid,expected", [
        (RUST_QUALIFIED, "std::io"),
        (DART_COLON_PATH, "dart:io"),
        (PLAIN, "app/config.py"),
        (OBJC_SELECTOR, "external"),
    ])
    def test_the_chokepoint_recovers_the_whole_path(
        self, sid: str, expected: str,
    ) -> None:
        assert symbol_path_slot(sid) == expected

    @pytest.mark.parametrize("sid", [RUST_QUALIFIED, DART_COLON_PATH])
    def test_a_positional_read_truncates_it(self, sid: str) -> None:
        """The discriminating cases for the conversions in ``verify_claims`` and
        ``io_boundary``: ``parts[1]`` is a truncation on any colon-bearing path,
        and both languages ship one."""
        assert sid.split(":")[1] != symbol_path_slot(sid)


class TestTheNameSlotIsColonFreeByRule:
    """ADR-0036 Ruling 1 — asserted here because the first draft of this file
    claimed the opposite and built a gate on it."""

    def test_the_sanitizer_is_what_makes_positional_name_reads_correct(
        self,
    ) -> None:
        from hypergumbo_core.analyze.base import sanitize_id_name_segment

        assert sanitize_id_name_segment("writeToFile:atomically:") == (
            "writeToFile.atomically."
        )
        well_formed = "objc:external:0-0:writeToFile.atomically.:unresolved"
        assert well_formed.split(":")[-2] == symbol_name_slot(well_formed)

    def test_the_unresolved_edge_producer_bypasses_it(self) -> None:
        """THE ROOT CAUSE, pinned as an xfail-shaped assertion of CURRENT
        behaviour rather than of desired behaviour, because which way it gets
        fixed is an open architectural question (INV-divuf).

        ``make_unresolved_edge`` hand-rolls the id with an f-string, so an objc
        selector reaches the name slot unsanitised and violates Ruling 1. When
        the fork is settled this test changes with it; until then it documents
        that the violation is real and where it originates."""
        from hypergumbo_core.analyze.base import make_unresolved_edge

        edge = make_unresolved_edge(
            src_id="objc:w.m:1-5:save:method",
            callee_name="writeToFile:atomically:",
            lang="objc",
            line=2,
            pass_id="objc",
            run_id="test",
        )
        assert edge.dst == OBJC_SELECTOR
        assert ":" in symbol_name_slot(edge.dst), (
            "the name slot carries a raw colon — ADR-0036 Ruling 1 violated at "
            "the producer, which is where INV-divuf says the fix belongs"
        )


class TestTheDisclosureNamesTheWholeCallee:
    """WI-nakut's objc symptom, at the layer a reader sees. Correct under either
    arm of the fork: whatever the producer emitted, the disclosure reports it
    whole instead of truncating it at the first colon."""

    def test_the_span_anchored_read_recovers_the_full_selector(self) -> None:
        assert symbol_name_slot(OBJC_SELECTOR) == "writeToFile:atomically:"

    def test_a_positional_read_names_a_method_that_does_not_exist(self) -> None:
        """``writeToFile`` is not an Objective-C method; ``writeToFile:atomically:``
        is. Naming the truncation is worse than naming nothing, because a reader
        can act on it and find no such call."""
        assert OBJC_SELECTOR.split(":")[3] == "writeToFile"
        assert OBJC_SELECTOR.split(":")[3] != symbol_name_slot(OBJC_SELECTOR)

    def test_the_caveat_reports_the_whole_selector(self) -> None:
        from hypergumbo_core.io_boundary import load_catalog
        from hypergumbo_core.verify_claims import unknown_receiver_scope

        edges = [{
            "src": "objc:w.m:4-8:save:method", "dst": OBJC_SELECTOR,
            "type": "calls", "line": 6, "meta": {"call_construct": "method"},
        }]
        _sites, _total, names = unknown_receiver_scope(
            edges, {"objc": load_catalog("objc")},
        )
        assert names == ["writeToFile:atomically:"], names


class TestTheSpanAnchorHasOneHome:
    """A plain DRY refactor, kept because the two chokepoints carried an
    identical copy of the span-locating loop. It changes no behaviour, and the
    VALIDATORS deliberately keep their own ``parts[-3]`` — locating the span
    "correctly" on a malformed id silences ``colon_in_name_slot``, the
    diagnosis INV-dulah paid an investigation to get right."""

    @pytest.mark.parametrize("sid", [OBJC_SELECTOR, RUST_QUALIFIED, DART_COLON_PATH, PLAIN])
    def test_both_chokepoints_agree_with_the_shared_anchor(self, sid: str) -> None:
        parts = sid.split(":")
        idx = ir._span_token_index(parts)
        assert idx is not None, sid
        assert ":".join(parts[1:idx]) == symbol_path_slot(sid)
        assert ":".join(parts[idx + 1:-1]) == symbol_name_slot(sid)

    def test_the_validator_still_diagnoses_a_colon_in_the_name_slot(self) -> None:
        """The regression this reduction reverted. A span-anchored validator
        parses a malformed maven id CLEAN and reports nothing."""
        from hypergumbo_core.spec_validator import _classify_id_format_problem

        bad = "xml:pom.xml:2-2:org.springframework.boot:spring-boot-starter-web:dependency"
        assert "colon_in_name_slot" in _classify_id_format_problem(bad)
