# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0017 §3a stops collapsing ``False`` into ``None`` (WI-kabif + WI-joluk).

WHAT CHANGES. The §3a arm used to read ``adjudicated = walk_result is True``,
so "the walk exhausted every route and found no dependence" (``False``) and
"the walk lost the value" (``None``) were the same event and neither removed
anything. ``False`` now REMOVES the flow. That is the one verdict class
removal may act on, and the distinction is the whole reason
``walk_verdict_for`` has three names for two negatives.

WHY THE TWO ITEMS SHIP TOGETHER. Since PR #214 an unearned ``False`` is a live
falsehood in a security tool: on the barrier arm it earns ``sanitized`` and
drops a flow. WI-joluk's forfeit gate — downgrade ``False`` to ``None`` for any
function whose CFG statement extents miss a call node in its body — is what
keeps a ``False`` earned. It was wired to this arm on 2026-08-26; the pairing is
asserted here rather than assumed, because the gate protecting an arm that did
not consume ``False`` was previously a no-op and could have been removed by
anyone tidying up without a test failing.

THE FIXTURE NEEDS A TERMINATING SUMMARY, WHICH IS NOT AN ACCIDENT. A walk can
only reach ``False`` if every route it followed ended in something accounted
for. Every chain ends at a variable with no further uses, and that is an ESCAPE
(``no_heir``) unless §4 says the callee at that line consumed the value. So
``print`` at line 3 is load-bearing: without a terminating summary this fixture
returns ``None`` and asserts nothing. That is also why WI-vosug is a real
prerequisite rather than a courtesy.

MEASURED SCOPE, RECORDED SO NOBODY READS THESE TESTS AS A PAYOFF. On
hypergumbo-core at dev 54edacb18c the §3a census is 15 confirmed / 12 escaped /
**0 unconfirmed** across 27 walks, and the 11-repo cohort reports the same zero
(153 ``ddg_mixed`` rows: 0 unconfirmed / 14 escaped / 139 not_attempted). So
this removes nothing on today's corpus. It becomes live exactly as escape sites
close, and the remaining ones have named owners (INV-mumov, INV-linub).
"""
from __future__ import annotations

import pytest

from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.taint import (
    TaintSink,
    TaintSource,
    propagate_taint_ddg,
)

SOURCES = [TaintSource(
    taint_label="plaintext", module="crypto", name="decrypt", kind="function",
)]
SINKS = [TaintSink(
    zone="relay", trust_level="untrusted", module="net", name="send",
    kind="function",
)]

_FN = "python:a.py:1-9:handler:function"


def _ext(name: str, line: int) -> dict:
    return {"src": _FN, "dst": f"python:external:0-0:{name}:unresolved",
            "is_resolved": False, "type": "calls", "line": line}


def _builtin(name: str, line: int) -> dict:
    """A callee whose module slot RESOLVES, so §4 can be looked up at all.

    An ``external`` slot yields an empty catalogue key by design (ADR-0051), so
    a summary could never be consulted for it — the fixture would escape and
    the test would assert nothing.
    """
    return {"src": _FN, "dst": f"python:builtins:0-0:{name}:external_symbol",
            "is_resolved": False, "type": "calls", "line": line}


def _edge(var: str, dline: int, uline: int) -> DdgEdge:
    return DdgEdge(variable=var, def_block="bb_0", def_line=dline,
                   use_block="bb_0", use_line=uline, symbol_id=_FN)


#   1  data = decrypt(token)   <- source
#   2  copy = data             <- heir, itself tracked
#   3  print(copy)             <- TERMINATING (§4): the value stops here
#   4  send("literal")         <- sink, reached by no tainted route
_REFUTED_DDG = [_edge("data", 1, 2), _edge("copy", 2, 3)]
_REFUTED_STMTS = {_FN: [(1, ("data",), ()), (2, ("copy",), ("data",)),
                        (3, (), ("copy",)), (4, (), ())]}
_REFUTED_CALLS = [_ext("decrypt", 1), _builtin("print", 3), _ext("send", 4)]


class TestRefutedFlowsAreRemoved:
    """The grant itself."""

    def test_a_refuted_flow_is_removed(self) -> None:
        findings = propagate_taint_ddg(
            _REFUTED_DDG, _REFUTED_CALLS, SOURCES, SINKS, [],
            ddg_symbols={_FN}, stmt_defuse=_REFUTED_STMTS,
        )
        assert findings == []

    def test_the_fixture_reaches_its_sink_when_the_route_exists(self) -> None:
        """Non-vacuity floor. Same fixture, one edge added so the value DOES
        reach line 4 — it must then be reported, and reported as confirmed.

        Without this a fixture that could not produce a finding under any
        circumstances would satisfy the assertion above.
        """
        findings = propagate_taint_ddg(
            [*_REFUTED_DDG, _edge("copy", 2, 4)], _REFUTED_CALLS,
            SOURCES, SINKS, [], ddg_symbols={_FN},
            stmt_defuse={_FN: [(1, ("data",), ()), (2, ("copy",), ("data",)),
                               (3, (), ("copy",)), (4, (), ("copy",))]},
        )
        assert len(findings) == 1
        assert findings[0].walk_verdict == "confirmed"

    def test_the_removed_flow_is_reported_and_not_silent(self) -> None:
        """A deletion a caller cannot observe is the wrong shape for this.

        The out-param is how ``verify-claims`` can say how many flows the walk
        removed; a security tool that silently drops findings gives a reader no
        way to tell "nothing was wrong" from "something was deleted".
        """
        removed: list = []
        findings = propagate_taint_ddg(
            _REFUTED_DDG, _REFUTED_CALLS, SOURCES, SINKS, [],
            ddg_symbols={_FN}, stmt_defuse=_REFUTED_STMTS,
            refuted_flows=removed,
        )
        assert findings == []
        assert len(removed) == 1
        assert removed[0].walk_verdict == "unconfirmed"
        assert removed[0].sink_primitive == "send"


class TestOnlyRefutationRemoves:
    """``None`` is not ``False``, and the whole grant rests on that."""

    def test_an_escaped_walk_keeps_its_flow(self) -> None:
        """Drop ``print``'s summary reach and the chain escapes instead.

        Identical fixture except the terminating callee is now on the
        ``external`` sentinel, whose catalogue key is empty by design — so the
        walk loses the value rather than accounting for it.
        """
        findings = propagate_taint_ddg(
            _REFUTED_DDG,
            [_ext("decrypt", 1), _ext("print", 3), _ext("send", 4)],
            SOURCES, SINKS, [], ddg_symbols={_FN},
            stmt_defuse=_REFUTED_STMTS,
        )
        assert len(findings) == 1
        assert findings[0].walk_verdict == "escaped"

    def test_a_walk_that_never_ran_keeps_its_flow(self) -> None:
        """No DDG definition at the source line: nothing was ever adjudicated."""
        findings = propagate_taint_ddg(
            [_edge("other", 2, 3)],
            [_ext("decrypt", 1), _ext("send", 3)],
            SOURCES, SINKS, [], ddg_symbols={_FN},
            stmt_defuse={_FN: [(2, ("other",), ()), (3, (), ("other",))]},
        )
        assert len(findings) == 1
        assert findings[0].walk_verdict == "not_attempted"


class TestTheForfeitGateStillProtects:
    """WI-joluk. The reason these two items are one PR."""

    def test_a_forfeited_function_keeps_its_flow(self) -> None:
        """The same refuted fixture, with the function's coverage forfeited.

        The CFG demonstrably did not record every call node in this function's
        body, so an exhausted walk is a walk over an incomplete graph. ``False``
        is downgraded to ``None`` and the flow SURVIVES — the safe direction,
        and the property that makes granting removal authority defensible at
        all.
        """
        findings = propagate_taint_ddg(
            _REFUTED_DDG, _REFUTED_CALLS, SOURCES, SINKS, [],
            ddg_symbols={_FN}, stmt_defuse=_REFUTED_STMTS,
            forfeit_refutation={_FN},
        )
        assert len(findings) == 1
        assert findings[0].walk_verdict == "escaped"

    def test_forfeiting_a_different_function_does_not_protect_this_one(
        self,
    ) -> None:
        """Vacuity guard: the gate must key on the SOURCE function.

        A gate that forfeited unconditionally would make the test above pass
        while protecting nothing.
        """
        findings = propagate_taint_ddg(
            _REFUTED_DDG, _REFUTED_CALLS, SOURCES, SINKS, [],
            ddg_symbols={_FN}, stmt_defuse=_REFUTED_STMTS,
            forfeit_refutation={"python:b.py:1-9:other:function"},
        )
        assert findings == []


class TestThePublishedClaimMovedWithTheCode:
    """One fact, two homes. The disclosure is read by consumers, not by us."""

    def test_inclusion_is_no_longer_decided_by_reachability_alone(self) -> None:
        from hypergumbo_core.dataflow_scope import INCLUSION_DECIDED_BY
        assert INCLUSION_DECIDED_BY != "call_graph_reachability"

    def test_the_rendered_disclosure_does_not_claim_confirm_only(self) -> None:
        from hypergumbo_core.dataflow_scope import (
            LanguageDataflowScope,
            render_dataflow_scope_text,
        )
        rows = [LanguageDataflowScope(
            language="python", catalog_sources=1, catalog_sinks=1,
            catalog_sanitizers=0,
            cfg_mapping=True, atomic_statement=True, def_use_extractor=True,
            ddg_spec=True,
        )]
        text = "\n".join(render_dataflow_scope_text(rows, {"ddg": 1}))
        assert "never removes a flow" not in text
        assert "confirm-only" not in text


@pytest.mark.parametrize("forfeited", [True, False])
def test_a_confirmation_is_never_removed_either_way(forfeited: bool) -> None:
    """``True`` is positive evidence and the gate blocks ``False`` ONLY.

    Parametrised over the gate because "the gate downgrades" and "the gate
    downgrades only the negative" are different claims, and only the second one
    keeps recall intact.
    """
    findings = propagate_taint_ddg(
        [*_REFUTED_DDG, _edge("copy", 2, 4)], _REFUTED_CALLS,
        SOURCES, SINKS, [], ddg_symbols={_FN},
        stmt_defuse={_FN: [(1, ("data",), ()), (2, ("copy",), ("data",)),
                           (3, (), ("copy",)), (4, (), ("copy",))]},
        forfeit_refutation={_FN} if forfeited else None,
    )
    assert len(findings) == 1
    assert findings[0].walk_verdict == "confirmed"
