# SPDX-License-Identifier: AGPL-3.0-or-later
"""A hint-less short-name fallback must prefer a VOUCHED row over an UNVOUCHED one.

THE DEFECT, reproduced live before the fix. ``load_catalog("haskell")`` merges
the default overlays IN FRONT of the base catalogue ("self's entries take
precedence"), and :func:`gate_named_entry` -- the only path a call with no
module hint can take -- returns ``non_method[0]``. So a bare ``readProcess``
after ``import System.Process (readProcess)`` was attributed to
``System.Process.Typed.readProcess``, a ``provenance: community`` row for a
Hackage package the program never imported. The overlay contract (ADR-0047)
says an unvouched row "can still produce a DETECTION (that direction only
ever adds findings) but must never license a CLEAN verdict". This is neither:
it is a SUBSTITUTION -- the vouched detection is replaced by an unvouched one
carrying the wrong module, and because the substitute row does not carry the
base row's ``simultaneous`` marker, the second boundary (``subprocess``) is
silently dropped with it.

THE CLASS, measured across every shipped overlay before this fix: 74
(overlay row, base row) pairs share a short name across different modules --
elixir 10, go 33, haskell 1, python 30. Method-kind rows are already refused
here by F3, so the live members are the function-kind ones: haskell's
``readProcess`` (typed-process), go's ``execabs.Command`` / ``LookPath``
(x/sys), python's ``httpx.get`` and friends. Adding the WI-zozun overlay
would have added 30 more, which is how it was found: an old-tree/new-tree
A/B on haskell-language-server lost eight ``subprocess`` chains that arm 2
had nothing to do with.

THE RULE. Order the candidates vouched-first before taking the head. It is a
sort, not a filter: an unvouched row still wins when it is the ONLY row for
the name, which is the detection direction the overlay is for.
"""
from __future__ import annotations

from hypergumbo_core.io_boundary import IoPrimitive, gate_named_entry, load_catalog


def _row(module: str, name: str, boundary: str, *, unvouched: bool) -> IoPrimitive:
    return IoPrimitive(boundary=boundary, module=module, name=name,
                       kind="function", unvouched=unvouched)


class TestTheGateOrdersVouchedFirst:
    def test_an_unvouched_row_ahead_in_the_list_does_not_win(self) -> None:
        hits = [
            _row("System.Process.Typed", "readProcess", "subprocess", unvouched=True),
            _row("System.Process", "readProcess", "ipc_recv", unvouched=False),
        ]
        hit = gate_named_entry(hits, "readProcess", None, frozenset())
        assert hit is not None
        assert hit.module == "System.Process"
        assert not hit.unvouched

    def test_a_lone_unvouched_row_still_detects(self) -> None:
        """The overlay's whole purpose: a row nobody else has still fires."""
        hits = [_row("httpx", "stream", "net_send", unvouched=True)]
        hit = gate_named_entry(hits, "stream", "external", frozenset())
        assert hit is not None and hit.module == "httpx"

    def test_vouched_declaration_order_is_preserved_among_vouched_rows(self) -> None:
        """A stable sort: the first-declared VOUCHED row is still the fallback
        (INV-fatok's abstention depends on declaration order)."""
        hits = [
            _row("x", "open", "fs_read", unvouched=False),
            _row("x", "open", "fs_write", unvouched=False),
        ]
        assert gate_named_entry(hits, "open", None, frozenset()).boundary == "fs_read"

    def test_a_hit_without_the_attribute_is_treated_as_vouched(self) -> None:
        """Duck-typed callers (taint's TaintSink/TaintSource) carry no
        ``unvouched`` field; they must not start sorting last."""
        class Bare:
            module, name, kind = "os", "getenv", "function"
        assert gate_named_entry([Bare()], "getenv", None, frozenset()) is not None

    def test_method_and_ambiguity_rules_still_run_first(self) -> None:
        hits = [_row("System.Process", "readProcess", "ipc_recv", unvouched=False)]
        assert gate_named_entry(hits, "readProcess", None, frozenset(),
                                call_construct="method") is None
        assert gate_named_entry(hits, "readProcess", None,
                                frozenset({"readProcess"})) is None


class TestTheLiveRepro:
    """Through the production loader, with every default overlay merged."""

    def test_hintless_haskell_readprocess_is_the_boot_library(self) -> None:
        cat = load_catalog("haskell")
        assert cat is not None
        for name in ("readProcess", "readProcessWithExitCode",
                     "readCreateProcessWithExitCode"):
            for hint in (None, "external"):
                hit = cat.lookup_with_module(name, hint)
                assert hit is not None, (name, hint)
                assert hit.module == "System.Process", (name, hint, hit.module)
                assert not hit.unvouched

    def test_a_hinted_call_to_the_hackage_twin_still_reaches_its_row(self) -> None:
        """The sort must not cost the overlay its OWN calls: with the hint
        present, the module-filter branch runs first and arm 1 is exact."""
        cat = load_catalog("haskell")
        assert cat is not None
        hit = cat.lookup_with_module("readProcessWithExitCode", "System.Process.Text")
        assert hit is not None and hit.module == "System.Process.Text"
        hit = cat.lookup_with_module("readProcess", "System.Process.Typed")
        assert hit is not None and hit.module == "System.Process.Typed"
