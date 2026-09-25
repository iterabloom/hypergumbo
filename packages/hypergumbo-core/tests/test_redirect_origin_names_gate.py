# SPDX-License-Identifier: AGPL-3.0-or-later
"""A redirect no externally-derived name can reach is not a taint sink.

WI-zovuz, the consuming half. bash carries no dataflow, so a redirect-sink
finding was call-graph reachability alone: "this file reads the environment
somewhere AND reaches a function that writes somewhere". The analyzer now
stamps ``redirect_origin_names`` — the externally-derived names that can reach
what the SHELL ITSELF contributes at that site (target operand, heredoc body
it expands, and every producing stage's arguments). An EMPTY stamp is a proof,
not an absence of evidence: nothing this program holds can be the value that
crossed there, so no name-based source can pair with it.

Measured over 15 cohort repos: of 186 environment names read in the 69 files
that also carry a write redirect, 28 can reach what the shell writes. 48 of
those files have none at all.

DEFAULT-DENY ON THE SILENCING DIRECTION, inherited from this gate's other two
arms: only an explicitly-present empty list suppresses. An absent key — the
state of every behavior map written before this key existed — keeps the
finding.
"""
from __future__ import annotations

from hypergumbo_core.taint import _sink_call_can_carry_taint


def _redirect_edge(**meta) -> dict:
    base = {"io_primitive": "redirect.>", "io_mode": "w",
            "redirect_target": "/tmp/out", "redirect_target_resolved": True}
    base.update(meta)
    return {"src": "bash:s.sh:1-1:file:file",
            "dst": "bash:redirect:0-0:>:external_symbol", "meta": base}


class TestTheEmptyStampIsAProof:

    def test_a_redirect_no_name_reaches_is_not_a_sink(self):
        assert not _sink_call_can_carry_taint(
            _redirect_edge(redirect_origin_names=[]))

    def test_a_redirect_a_name_reaches_stays_a_sink(self):
        assert _sink_call_can_carry_taint(
            _redirect_edge(redirect_origin_names=["DB_PASSWORD"]))


class TestTheDefaultIsToKeep:
    """Absence must never silence — it is the state of every older map."""

    def test_an_absent_stamp_keeps_the_finding(self):
        assert _sink_call_can_carry_taint(_redirect_edge())

    def test_a_non_bash_edge_is_untouched(self):
        assert _sink_call_can_carry_taint(
            {"src": "python:a.py:1-2:f:function",
             "dst": "python:os:0-0:os.remove:function",
             "meta": {"io_primitive": "os.remove"}})

    def test_an_unrecognised_stamp_value_keeps_the_finding(self):
        # A string rather than a list: unrecognised, so it must not suppress.
        assert _sink_call_can_carry_taint(
            _redirect_edge(redirect_origin_names="DB_PASSWORD"))


class TestItComposesWithTheOtherArms:
    """The three proofs are independent; any one of them suffices."""

    def test_a_discarding_target_still_suppresses_with_names_present(self):
        # INV-nular's arm: `echo "$API_KEY" > /dev/null` discards, and that is
        # true whether or not a name reaches it.
        assert not _sink_call_can_carry_taint(
            _redirect_edge(io_target_kind="null_device",
                           redirect_origin_names=["API_KEY"]))
