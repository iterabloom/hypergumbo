# SPDX-License-Identifier: AGPL-3.0-or-later
"""One symbol-id path slot, one parser — and the Rust defect that proved it.

WHY THIS FILE EXISTS. Per ADR-0036 (D1a) the path slot is the ONE colon-tolerant
slot in ``{lang}:{path}:{span}:{name}:{kind}``; lang/span/name/kind are
colon-free, so the parse must be RIGHT-ANCHORED. That fact had four homes, two
of which took ``parts[1]``:

    ir._extract_path_slot          parts[1]         WRONG
    ir._parse_dangling_id          right-anchored   correct
    verify_claims._symbol_path_slot right-anchored  correct
    taint._extract_callee_module   parts[1]         WRONG

The two in ``ir.py`` sit thirteen lines apart. ``_symbol_path_slot``'s docstring
had already named ``_extract_path_slot`` as wrong and warned "this must not
become a third naive copy" — while a fourth copy was live in ``taint.py``.

THE MEASURED CONSEQUENCE, which is why this is a defect and not tidiness. Rust
is the only language whose taint catalogue uses ``::`` module paths, and ALL
NINE of its distinct sink modules are colon-bearing (``std::fs``,
``std::process``, ``std::io``, ``std::net::TcpStream``, ...). ``parts[1]``
truncates every one of them to ``std``. ``_module_matches('std::fs', 'std')`` is
False, and ``_lookup_named_entry`` treats a present-but-mismatched module as a
REJECTION rather than a degrade. So on dev, before this fix::

    rust:std::fs:0-0:write:external_symbol        -> MATCH=None
    rust:external:0-0:write:external_symbol       -> MATCH=std::fs.write

The edge that correctly names its module is rejected; the edge carrying no
module information at all matches. The better-informed input produces the worse
answer.

WHY IT IS FILED AS LATENT RATHER THAN AS A LIVE REGRESSION. On a 9-repo cohort
the fix moves exactly one flow (+1 source, 0 lost): Rust analyzers today emit
the ``external`` sentinel for 396 of 449 unresolved taint call edges, and the 53
that do carry real module paths name non-catalogued callees (``assert_cmd::
Command``, ``std::path::PathBuf``), so their rejections are accidentally
correct. The exposure is forward-looking and large: WI-jokij raises external-id
module coverage, at which point those 396 sentinel edges start carrying
``std::fs`` — and an unfixed truncation would take Rust from 45 matched sinks to
zero. That is why WI-jokij is blocked on this.
"""

from __future__ import annotations

import pytest

from hypergumbo_core import ir
from hypergumbo_core.io_boundary import _module_matches
from hypergumbo_core.taint import (
    _build_callee_index,
    _extract_callee_module,
    _extract_callee_name,
    _match_propagation_entry,
    load_builtin_taint_catalog,
)
from hypergumbo_core.verify_claims import _symbol_path_slot

# (symbol_id, expected path slot). The colon-bearing cases are the point; the
# colon-free ones are the non-regression floor.
CASES = [
    ("rust:std::fs:0-0:write:external_symbol", "std::fs"),
    ("rust:std::net::TcpStream:0-0:connect:external_symbol", "std::net::TcpStream"),
    ("dart:dart:io:0-0:module:module", "dart:io"),
    ("javascript:node:fs/promises:0-0:readFile:external_symbol", "node:fs/promises"),
    ("bash:kube::codegen:0-0:gen_helpers:external_symbol", "kube::codegen"),
    ("python:os:0-0:getenv:external_symbol", "os"),
    ("go:net/http:0-0:Get:external_symbol", "net/http"),
    ("python:src/app/views.py:10-20:handler:function", "src/app/views.py"),
]


class TestOneParserForOneSlot:
    """Every home must return the same path slot for the same id."""

    @pytest.mark.parametrize("symbol_id,expected", CASES)
    def test_taint_extract_callee_module(self, symbol_id: str, expected: str) -> None:
        assert _extract_callee_module(symbol_id) == expected

    @pytest.mark.parametrize("symbol_id,expected", CASES)
    def test_ir_extract_path_slot(self, symbol_id: str, expected: str) -> None:
        assert ir._extract_path_slot(symbol_id) == expected

    @pytest.mark.parametrize("symbol_id,expected", CASES)
    def test_ir_parse_dangling_id(self, symbol_id: str, expected: str) -> None:
        assert ir._parse_dangling_id(symbol_id)[1] == expected

    @pytest.mark.parametrize("symbol_id,expected", CASES)
    def test_verify_claims_symbol_path_slot(
        self, symbol_id: str, expected: str,
    ) -> None:
        assert _symbol_path_slot(symbol_id) == expected

    def test_the_four_homes_agree(self) -> None:
        """The property, stated once: agreement, not four separate constants.

        A future fifth caller that re-derives the parse will disagree here for
        any colon-bearing id, which is the whole class this file guards.
        """
        for symbol_id, _ in CASES:
            got = {
                "taint._extract_callee_module": _extract_callee_module(symbol_id),
                "ir._extract_path_slot": ir._extract_path_slot(symbol_id),
                "ir._parse_dangling_id": ir._parse_dangling_id(symbol_id)[1],
                "verify_claims._symbol_path_slot": _symbol_path_slot(symbol_id),
            }
            assert len(set(got.values())) == 1, f"{symbol_id} -> {got}"

    def test_a_colon_bearing_case_is_actually_exercised(self) -> None:
        """Non-vacuity floor (L17).

        If every CASES entry were colon-free the suite above would pass on the
        unfixed ``parts[1]`` code, which is exactly the vacuous green this
        project keeps re-learning. Assert the fixture has teeth.
        """
        with_colons = [c for c in CASES if ":" in c[1]]
        assert len(with_colons) >= 4, (
            "the fixture must contain colon-bearing path slots, or it cannot "
            f"distinguish a right-anchored parse from parts[1]: {CASES}"
        )


class TestMalformedIdsDegradeRatherThanThrow:
    """An unparseable id is not evidence; it must not raise either."""

    @pytest.mark.parametrize("bad", ["", "nocolons", "a:b", "a:b:c", "a:b:c:d"])
    def test_short_ids_return_empty(self, bad: str) -> None:
        assert _extract_callee_module(bad) == ""
        assert _symbol_path_slot(bad) == ""
        assert ir._extract_path_slot(bad) is None

    def test_parse_dangling_id_keeps_its_unknown_sentinel(self) -> None:
        """``_parse_dangling_id``'s <5-part fallback is a documented contract.

        It returns the ``<unknown>`` path sentinel rather than "", and
        ``_derive_dst_ref_from_id`` branches on exactly that value, so folding
        it to "" here would silently change which edges get a ``dst_ref``.
        """
        assert ir._parse_dangling_id("a:b:c")[1] == "<unknown>"


class TestRustSinksMatchThroughTheirRealModule:
    """The behavioural defect, end to end through production's own matcher."""

    def _rust_sink_index(self):
        cat = load_builtin_taint_catalog()
        return _build_callee_index(cat.sinks_for_language("rust"))

    def test_rust_catalogue_is_entirely_colon_bearing(self) -> None:
        """The premise, asserted rather than assumed (L1).

        If a future catalogue edit made Rust's modules colon-free, the test
        below would pass for a reason unrelated to this fix.
        """
        cat = load_builtin_taint_catalog()
        modules = {s.module for s in cat.sinks_for_language("rust") if s.module}
        assert modules, "rust declares no sink modules — fixture is inert"
        assert all("::" in m for m in modules), (
            f"expected every rust sink module to be colon-bearing, got {modules}"
        )

    def test_truncated_hint_does_not_match_the_catalogued_module(self) -> None:
        """Why truncation is fatal here rather than merely lossy.

        ``_lookup_named_entry`` rejects on a present-but-mismatched module, so
        a truncated hint is not a degrade — it is a refusal.
        """
        assert not _module_matches("std::fs", "std")
        assert _module_matches("std::fs", "std::fs")

    def test_module_bearing_rust_sink_id_matches(self) -> None:
        """The regression. Fails on dev with MATCH=None."""
        idx = self._rust_sink_index()
        sid = "rust:std::fs:0-0:write:external_symbol"
        matched = _match_propagation_entry(
            idx, sid, frozenset(), is_resolved=False,
        )
        assert matched is not None, (
            "a rust edge naming its real module std::fs was rejected; the "
            "module hint was truncated to 'std'"
        )
        assert matched.module == "std::fs"

    def test_the_sentinel_form_still_matches(self) -> None:
        """Non-destructiveness (L57): the path that worked must keep working.

        396 of 449 rust unresolved taint call edges on the cohort arrive via
        this sentinel form. The correct outcome of this fix is that they are
        UNAFFECTED, and a break here would look like a precision improvement.
        """
        idx = self._rust_sink_index()
        matched = _match_propagation_entry(
            idx, "rust:external:0-0:write:external_symbol", frozenset(),
            is_resolved=False,
        )
        assert matched is not None
        assert matched.module == "std::fs"

    def test_the_name_half_is_unchanged_for_both_forms(self) -> None:
        """The name parse serves 100% of the population and must not move.

        Re-keying the NAME onto ``dst_ref`` was the WI-lonad remedy as filed;
        measurement showed it would drop 60.5% of taint call edges. This pins
        the name half against that.
        """
        for sid in ("rust:std::fs:0-0:write:external_symbol",
                    "rust:external:0-0:write:external_symbol",
                    "javascript:node:fs/promises:0-0:readFile:external_symbol"):
            assert _extract_callee_name(sid) == sid.split(":")[-2]
