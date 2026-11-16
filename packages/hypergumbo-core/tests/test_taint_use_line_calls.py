# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every call on a use line is one the §3a walk asks about (WI-tinav).

``t = getenv("K"); n = acc.push(t); log(n); send(acc[0])``: the walk followed
the heir ``n``, which a terminating ``log`` closed, and REMOVED the flow while
the secret went into ``acc``. The use line was asked the escape question, but
the walk's per-line call index held only calls with a catalogue key, so an
unresolved ``acc.push`` (or any resolved first-party call) was not there and
the line read as "no call at all", the one case that skips the question.

The index now records every call at a line; a call with no catalogue key is
:data:`UNCATALOGUED_CALL`, which no summary describes, so it never closes a
branch. That is the rule :func:`_source_line_escapes` already applies to the
source's own line (WI-jusoj b). This can only turn ``False`` into ``None``.
"""
from __future__ import annotations

from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.function_summaries import FunctionSummary
from hypergumbo_core.taint import (
    UNCATALOGUED_CALL,
    TaintSink,
    TaintSource,
    _walk_indexes,
    propagate_taint_ddg,
)

SOURCES = [TaintSource(taint_label="host_secret", module="os", name="getenv", kind="function")]
SINKS = [TaintSink(zone="network", trust_level="untrusted", module="net", name="send", kind="function")]
_FN = "python:a.py:1-9:go:function"
_GETENV = "python:os:0-0:getenv:unresolved"
_SEND = "python:net:0-0:send:unresolved"
_INFO = "python:logging:0-0:info:unresolved"
_PUSH = "python:external:0-0:push:unresolved"
_HELPER = "python:a.py:11-12:helper:function"
_SUMMARIES = {"logging.info": FunctionSummary(function="logging.info", side_effect=True)}


def _call(dst: str, line: int, *, resolved: bool = False) -> dict:
    return {"src": _FN, "dst": dst, "is_resolved": resolved, "type": "calls", "line": line}


def _ddg(*edges: tuple[str, int, int]) -> list[DdgEdge]:
    return [
        DdgEdge(variable=v, def_block="bb_0", def_line=d, use_block="bb_0", use_line=u, symbol_id=_FN)
        for v, d, u in edges
    ]


def _verdicts(line_3_call: dict, *, heir: bool = True) -> list[str]:
    """``acc`` at 1, ``t = getenv()`` at 2, line 3 consumes ``t`` (defining
    ``n`` when *heir*), ``info(n)`` at 4, ``send(acc)`` at 5."""
    line_3 = (3, ("n",), ("t", "acc")) if heir else (3, (), ("t", "acc"))
    ddg = _ddg(("acc", 1, 3), ("acc", 1, 5), ("t", 2, 3), *([("n", 3, 4)] if heir else []))
    findings = propagate_taint_ddg(
        ddg,
        [_call(_GETENV, 2), line_3_call, _call(_INFO, 4), _call(_SEND, 5)],
        SOURCES, SINKS, [], ddg_symbols={_FN}, language="python",
        stmt_defuse={_FN: [(1, ("acc",), ()), (2, ("t",), ()), line_3, (4, (), ("n",)), (5, (), ("acc",))]},
        function_summaries=_SUMMARIES,
    )
    return [f.walk_verdict for f in findings]


def test_an_unresolved_call_whose_result_is_the_heir_keeps_the_flow() -> None:
    """The filed shape: ``n = acc.push(t)``. It was removed."""
    assert _verdicts(_call(_PUSH, 3)) == ["escaped"]


def test_a_first_party_call_whose_result_is_the_heir_keeps_the_flow() -> None:
    """``n = helper(t, acc)``: a resolved in-repo callee has no catalogue key
    (WI-zumud) and no inferred summary, so what it did with ``t`` is unknown,
    as it already is on the source's own line."""
    assert _verdicts(_call(_HELPER, 3, resolved=True)) == ["escaped"]


def test_a_catalogued_terminating_call_still_closes_the_line() -> None:
    """The control: ``n = info(t)`` consumes ``t`` and the heir dies at a
    terminating call, so the walk exhausts and the flow is removed."""
    assert _verdicts(_call(_INFO, 3)) == []


def test_an_uncatalogued_call_beside_a_terminating_one_keeps_the_line_open() -> None:
    """``info(acc.push(t))`` with no heir: the line held only ``logging.info``,
    so the all-or-nothing rule read it as terminating and closed it."""
    line_3 = [_call(_PUSH, 3), {**_call(_INFO, 3), "line": 3}]
    ddg = _ddg(("acc", 1, 3), ("acc", 1, 5), ("t", 2, 3))
    findings = propagate_taint_ddg(
        ddg, [_call(_GETENV, 2), *line_3, _call(_SEND, 5)],
        SOURCES, SINKS, [], ddg_symbols={_FN}, language="python",
        stmt_defuse={_FN: [(1, ("acc",), ()), (2, ("t",), ()), (3, (), ("t", "acc")), (5, (), ("acc",))]},
        function_summaries=_SUMMARIES,
    )
    assert [f.walk_verdict for f in findings] == ["escaped"]


def test_the_index_marks_an_uncatalogued_call_and_only_a_taint_call() -> None:
    edges = [
        _call(_PUSH, 3),
        _call(_HELPER, 4, resolved=True),
        _call(_INFO, 5),
        {"src": _FN, "dst": "python:a.py:20-30:T:class", "is_resolved": True, "type": "references", "line": 6},
    ]
    ix = _walk_indexes([], edges, {}, "python", False, {})
    assert ix.callee_names[(_FN, 3)] == {UNCATALOGUED_CALL}
    assert ix.callee_names[(_FN, 4)] == {UNCATALOGUED_CALL}
    assert ix.callee_names[(_FN, 5)] == {"logging.info"}
    assert not ix.callee_names.get((_FN, 6))
