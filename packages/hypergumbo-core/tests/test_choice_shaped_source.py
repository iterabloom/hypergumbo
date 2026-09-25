# SPDX-License-Identifier: AGPL-3.0-or-later
"""The choice-shaped source blindness, DECLARED rather than fixed (WI-jivih, arc T8).

WI-jivih pre-registered its own decision rule before any number existed: if the
finding-weighted share of choice-shaped sources is under 5%, the source half is
not worth a per-row `value_shape` field and the honest output is a dated
declared blindness. A 23-repository cohort sampled deliberately for socket
ownership — chosen to be maximally favourable to the property — measured
**2 of 568 findings, 0.35%**, while plainly seeing the population (87% of it
reached a choice-shaped chain). So the field is not built, and this is the
disclosure instead.
"""
import pytest

from hypergumbo_core.taint import TaintFlowFinding
from hypergumbo_core.verify_claims import (
    CAVEAT_CHOICE_SHAPED_SOURCE,
    CHOICE_SHAPED_SOURCE_NAMES,
    Claim,
    TaintFlowConstraint,
    is_choice_shaped_source,
    verify_taint_claim,
)


def _finding(source_primitive, source_module="socket"):
    return TaintFlowFinding(
        taint_label="untrusted_input",
        source_symbol="s", source_primitive=source_primitive,
        source_module=source_module, sink_symbol="k",
        sink_primitive="run", sink_module="subprocess",
        sink_zone="subprocess", sanitized=False,
        confidence="approximate", analysis_method="structural",
    )


def _claim():
    return Claim(id="C1", text="untrusted input never reaches a subprocess",
                 constraint_taint_flow=TaintFlowConstraint(
                     source_taint="untrusted_input",
                     prohibited_sink_zone="subprocess"))


class TestTheRegistry:
    def test_the_three_families_are_declared(self):
        """accept (a peer the far side did not author), the erlang inet DNS
        answers, and the exit status collected by wait*."""
        for name in ("accept", "gethostbyname", "waitpid"):
            assert name in CHOICE_SHAPED_SOURCE_NAMES

    def test_matching_is_on_the_last_segment(self):
        """The catalogue spells one concept with different module prefixes per
        language, so a qualified name must resolve by its terminal segment."""
        assert is_choice_shaped_source("gen_tcp.accept")
        assert is_choice_shaped_source("net.Listener.Accept")
        assert is_choice_shaped_source("sys/socket.accept")

    def test_a_content_shaped_source_is_not_matched(self):
        assert not is_choice_shaped_source("builtins.input")
        assert not is_choice_shaped_source("socket.socket.recv")

    def test_a_bare_name_still_resolves(self):
        assert is_choice_shaped_source("accept")

    def test_empty_is_not_choice_shaped(self):
        assert not is_choice_shaped_source("")


class TestTheCaveat:
    def test_fires_on_a_violated_verdict_rooted_at_a_choice_shaped_source(self):
        v = verify_taint_claim(_claim(), [_finding("accept")])
        assert v.verdict == "violated"
        kinds = {c["kind"] for c in v.caveats}
        assert CAVEAT_CHOICE_SHAPED_SOURCE in kinds

    def test_does_not_fire_on_a_content_shaped_source(self):
        v = verify_taint_claim(_claim(), [_finding("input", "builtins")])
        assert v.verdict == "violated"
        kinds = {c["kind"] for c in v.caveats}
        assert CAVEAT_CHOICE_SHAPED_SOURCE not in kinds

    def test_the_detail_is_DATED_and_carries_the_number(self):
        """A blindness without its measurement reads as an apology; with one it
        reads as a decision. Both must survive an edit."""
        v = verify_taint_claim(_claim(), [_finding("gethostbyname")])
        detail = next(c["detail"] for c in v.caveats
                      if c["kind"] == CAVEAT_CHOICE_SHAPED_SOURCE)
        assert "2026-09-13" in detail, "the blindness must be DATED"
        assert "0.35%" in detail, "the blindness must carry its measurement"
        assert "not a per-row field" in detail or "declined" in detail

    def test_it_names_which_sources_it_is_about(self):
        v = verify_taint_claim(_claim(), [_finding("accept"),
                                          _finding("waitpid")])
        entry = next(c for c in v.caveats
                     if c["kind"] == CAVEAT_CHOICE_SHAPED_SOURCE)
        assert sorted(entry["entries"]) == ["accept", "waitpid"]

    def test_the_verdict_VALUE_is_unchanged(self):
        """P3: this is a DISCLOSURE, not a filter. Nothing moves between
        confirmed and violated, and evidence_count is untouched."""
        plain = verify_taint_claim(_claim(), [_finding("input", "builtins")])
        choice = verify_taint_claim(_claim(), [_finding("accept")])
        assert plain.verdict == choice.verdict == "violated"
        assert plain.evidence_count == choice.evidence_count == 1

    def test_a_confirmed_verdict_gets_no_such_caveat(self):
        """No findings means no source to qualify."""
        v = verify_taint_claim(_claim(), [])
        kinds = {c["kind"] for c in v.caveats}
        assert CAVEAT_CHOICE_SHAPED_SOURCE not in kinds
