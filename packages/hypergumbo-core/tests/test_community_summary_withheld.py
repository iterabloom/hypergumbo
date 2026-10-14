# SPDX-License-Identifier: AGPL-3.0-or-later
"""A community terminating summary does not close a taint branch (WI-tigud).

ADR-0061 ruling 2: a community row may add a finding but never remove one. A
TERMINATING function summary removes: it tells the §3a walk the value stops at
that callee, so a flow that would otherwise be reported is closed. The shipped
community summaries (``function_summaries/go_testify_zap.yaml`` -- testify's
``require.*`` terminate) therefore never close a branch; a flow they would have
closed is reported, and the violated verdicts of the run name them
(``withheld_community_summary``). A propagating community summary changes
nothing either way -- the walk reads only termination.
"""

from __future__ import annotations

from hypergumbo_core.function_summaries import (
    FunctionSummary,
    clear_summary_cache,
    load_function_summaries,
)
from hypergumbo_core.taint import (
    TaintSink,
    TaintSource,
    _use_site_terminates,
    propagate_taint_structural,
    withheld_community_summaries,
)
from hypergumbo_core.verify_claims import (
    CAVEAT_WITHHELD_COMMUNITY_SUMMARY,
    Claim,
    TaintFlowConstraint,
    verify_taint_claim,
)

_TESTIFY = "github.com/stretchr/testify/require.NoError"


def _summaries() -> dict[str, FunctionSummary]:
    clear_summary_cache()
    try:
        return load_function_summaries()
    finally:
        clear_summary_cache()


def _terminating(name: str, *, community: bool) -> FunctionSummary:
    return FunctionSummary(function=name, side_effect=True, community=community)


class TestTheCatalogue:
    def test_the_shipped_third_party_summaries_load_as_community(self) -> None:
        rows = _summaries()
        assert rows[_TESTIFY].community is True
        assert rows["fmt.Printf"].community is False


class TestTheWalk:
    def _site(self, summary: FunctionSummary) -> bool:
        return _use_site_terminates(
            "fn", 3, {("fn", 3): {summary.function}}, {summary.function: summary},
        )

    def test_a_vouched_terminating_summary_closes_the_branch(self) -> None:
        """THE CONTROL."""
        assert self._site(_terminating("fmt.Printf", community=False)) is True

    def test_a_community_one_does_not(self) -> None:
        assert self._site(_terminating(_TESTIFY, community=True)) is False


class TestTheDisclosure:
    def _edge(self, dst: str) -> dict[str, str]:
        return {"src": "go:a.go:1-9:TestX:function", "dst": dst, "type": "calls"}

    def test_it_names_the_community_terminating_summaries_the_run_calls(self) -> None:
        edges = [
            self._edge("go:github.com/stretchr/testify/require:0-0:NoError:external_symbol"),
            self._edge("go:go.uber.org/zap:0-0:String:external_symbol"),
            self._edge("go:fmt:0-0:Printf:external_symbol"),
        ]
        # zap.String is community but PROPAGATES; fmt.Printf terminates but
        # is built-in: neither is withheld.
        assert withheld_community_summaries(edges, _summaries()) == [_TESTIFY]

    def test_a_run_that_calls_none_names_none(self) -> None:
        edges = [self._edge("go:fmt:0-0:Printf:external_symbol")]
        assert withheld_community_summaries(edges, _summaries()) == []

    def test_without_community_summaries_there_is_nothing_to_withhold(self) -> None:
        edges = [self._edge(
            "go:github.com/stretchr/testify/require:0-0:NoError:external_symbol")]
        rows = {k: v for k, v in _summaries().items() if not v.community}
        assert withheld_community_summaries(edges, rows) == []


class TestTheVerdict:
    def _claim(self) -> Claim:
        return Claim(
            id="HS-LOG", text="host secrets never reach a log",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="host_secret", prohibited_sink_zone="logging",
            ),
        )

    def _findings(self) -> list:
        fn = "py:a.py:1-5:handler:function"
        edges = [
            {"src": fn, "dst": "py:os:0-0:getenv:unresolved", "type": "calls",
             "is_resolved": False, "meta": {"evidence_type": "ast_call_direct"}},
            {"src": fn, "dst": "py:logging:0-0:info:unresolved", "type": "calls",
             "is_resolved": False, "meta": {"evidence_type": "ast_call_direct"}},
        ]
        return propagate_taint_structural(
            edges,
            [TaintSource(taint_label="host_secret", module="os", name="getenv",
                         kind="function")],
            [TaintSink(zone="logging", trust_level="untrusted",
                       module="logging", name="info", kind="function")],
            [],
        )

    def test_it_rides_a_violated_verdict(self) -> None:
        verdict = verify_taint_claim(
            self._claim(), self._findings(),
            withheld_community_summaries=[_TESTIFY],
        )
        assert verdict.verdict == "violated"
        (caveat,) = [c for c in verdict.caveats
                     if c["kind"] == CAVEAT_WITHHELD_COMMUNITY_SUMMARY]
        assert caveat["entries"] == [_TESTIFY]
        assert "function_summaries.d" in caveat["detail"]

    def test_a_violated_verdict_in_a_run_with_none_carries_none(self) -> None:
        """THE CONTROL: the caveat is not decoration on every violated verdict."""
        verdict = verify_taint_claim(self._claim(), self._findings())
        assert verdict.verdict == "violated"
        assert not any(c["kind"] == CAVEAT_WITHHELD_COMMUNITY_SUMMARY
                       for c in verdict.caveats)

    def test_a_clean_verdict_does_not_carry_it(self) -> None:
        verdict = verify_taint_claim(
            self._claim(), [], withheld_community_summaries=[_TESTIFY],
        )
        assert verdict.verdict == "confirmed"
        assert not any(c["kind"] == CAVEAT_WITHHELD_COMMUNITY_SUMMARY
                       for c in verdict.caveats)
