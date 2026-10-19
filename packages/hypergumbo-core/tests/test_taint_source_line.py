# SPDX-License-Identifier: AGPL-3.0-or-later
"""The §3a walk examines the source's own line (WI-jusoj).

The walk treated the source's line as a starting point and never as a use
site, so whatever else the line did with the value was invisible:

(a) A sink ON the source's line -- ``resp = requests.post(url,
    headers={"A": os.environ["K"]})`` -- was classified ``sink_before_source``
    (``sink_after_source`` was ``any(sink > source)``), and INV-muhij's rule
    then kept the row from carrying a verdict. It is now
    ``sink_on_source_line``: the walk still cannot adjudicate it, but the row
    carries the verdict.
(b) A non-sink consumer on the source's line -- ``n = acc.push(process.env.K)``
    -- was not an escape: the walk followed ``n``, exhausted, and REMOVED the
    flow while the secret went into ``acc``. Every other call on a source line
    is now asked the use-line question; one no summary says terminates is an
    escape. This can only turn ``False`` into ``None``.
"""
from __future__ import annotations

from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.taint import (
    WALK_BLOCKED_SINK_BEFORE_SOURCE,
    WALK_BLOCKED_SOURCE_LINE_SINK,
    TaintSink,
    TaintSource,
    _ddg_taint_reaches,
    _source_line_escapes,
    propagate_taint_ddg,
)

SOURCES = [TaintSource(taint_label="host_secret", module="os", name="getenv", kind="function")]
SINKS = [TaintSink(zone="network", trust_level="untrusted", module="net", name="send", kind="function")]
_FN = "python:a.py:1-9:go:function"
_GETENV = "python:os:0-0:getenv:unresolved"
_SEND = "python:net:0-0:send:unresolved"


def _call(dst: str, line: int) -> dict:
    return {"src": _FN, "dst": dst, "is_resolved": False, "type": "calls", "line": line}


def _ddg(*edges: tuple[str, int, int]) -> list[DdgEdge]:
    return [
        DdgEdge(variable=v, def_block="bb_0", def_line=d, use_block="bb_0", use_line=u, symbol_id=_FN)
        for v, d, u in edges
    ]


def _findings(ddg: list[DdgEdge], edges: list[dict], stmts: dict) -> list:
    return propagate_taint_ddg(
        ddg, edges, SOURCES, SINKS, [], ddg_symbols={_FN}, language="python",
        stmt_defuse=stmts, function_summaries={},
    )


def test_a_sink_on_the_source_line_carries_its_own_blocker() -> None:
    """``r = send(getenv("K"))`` at 2, ``r`` used at 3: the walk cannot order
    them, and the row is not ``sink_before_source``."""
    (finding,) = _findings(
        _ddg(("r", 2, 3)), [_call(_GETENV, 2), _call(_SEND, 2)],
        {_FN: [(2, ("r",), ()), (3, (), ("r",))]},
    )
    assert finding.walk_blocked_by == WALK_BLOCKED_SOURCE_LINE_SINK


def test_a_sink_strictly_before_the_source_keeps_its_old_blocker() -> None:
    (finding,) = _findings(
        _ddg(("k", 3, 4)), [_call(_SEND, 1), _call(_GETENV, 3)],
        {_FN: [(3, ("k",), ()), (4, (), ("k",))]},
    )
    assert finding.walk_blocked_by == WALK_BLOCKED_SINK_BEFORE_SOURCE


def test_another_call_on_the_source_line_keeps_the_flow() -> None:
    """``n = push(getenv("K"))`` at 2; ``n`` dies at 3; ``send(acc)`` at 4. The
    walk exhausts on ``n`` -- which REMOVED the flow -- but ``push`` may have
    taken the value, so the walk is now unknowing and the flow stays."""
    findings = _findings(
        _ddg(("n", 2, 3), ("acc", 1, 4)),
        [_call(_GETENV, 2), _call("python:external:0-0:push:unresolved", 2), _call(_SEND, 4)],
        {_FN: [(1, ("acc",), ()), (2, ("n",), ()), (3, (), ("n",)), (4, (), ("acc",))]},
    )
    assert [f.walk_verdict for f in findings] == ["escaped"]


def test_source_line_escapes_asks_every_other_call() -> None:
    from hypergumbo_core.function_summaries import FunctionSummary

    terminating = FunctionSummary(function="logging.info", side_effect=True)
    calls_at = {(_FN, 2): {_GETENV, "python:external:0-0:push:unresolved"},
                (_FN, 5): {_GETENV, "python:logging:0-0:info:unresolved"},
                (_FN, 7): {_GETENV}}
    keys = {(_FN, "python:logging:0-0:info:unresolved"): {"logging.info"}}
    escapes = _source_line_escapes(
        _FN, _GETENV, [2, 5, 7], calls_at, keys, {"logging.info": terminating},
    )
    # 2: an unkeyed call is unknown; 5: a terminating call closes; 7: only the source.
    assert escapes == frozenset({2})


def test_a_source_line_escape_turns_exhaustion_into_ignorance() -> None:
    """``n`` is consumed by a terminating call at 3, so the walk exhausts --
    unless the source line itself escapes."""
    from hypergumbo_core.function_summaries import FunctionSummary

    uses = {(_FN, "n", 2): {3}}
    defs = {(_FN, 2): {"n"}}
    callees = {(_FN, 3): {"logging.info"}}
    summaries = {"logging.info": FunctionSummary(function="logging.info", side_effect=True)}
    assert _ddg_taint_reaches(
        _FN, [2], [9], uses, callees, summaries, defs_at=defs,
    ) is False
    assert _ddg_taint_reaches(
        _FN, [2], [9], uses, callees, summaries, defs_at=defs, source_line_escapes={2},
    ) is None


def test_constructing_the_source_s_receiver_does_not_escape() -> None:
    """``pt = Fernet(key).decrypt(ct)``: the construction runs before the
    source and cannot hold its result, so the line stays closed and the
    same-function sanitizer credit (WI-fasub) can still be earned."""
    source = "python:cryptography.fernet.Fernet:0-0:decrypt:unresolved"
    receiver = "python:cryptography.fernet:0-0:Fernet:unresolved"
    calls_at = {(_FN, 3): {source, receiver}}
    assert _source_line_escapes(_FN, source, [3], calls_at, {}, {}) == frozenset()
    other = "python:cryptography.fernet:0-0:MultiFernet:unresolved"
    assert _source_line_escapes(_FN, source, [3], {(_FN, 3): {source, other}}, {}, {}) == frozenset({3})
    assert _source_line_escapes(_FN, "malformed", [3], {(_FN, 3): {"malformed", "x:y"}}, {}, {}) == frozenset({3})


def test_the_escape_is_recorded_and_no_summaries_close_nothing() -> None:
    from hypergumbo_core.taint import EscapeSite

    sites: list[EscapeSite] = []
    assert _ddg_taint_reaches(
        _FN, [2], [9], {(_FN, "n", 2): {3}}, defs_at={(_FN, 2): {"n"}},
        source_line_escapes={2}, escape_sites=sites,
    ) is None
    assert EscapeSite(_FN, 2, "call_beside_source") in sites
    # A keyed call with no summary index at all is unknown: the line stays open.
    keyed = "python:logging:0-0:info:unresolved"
    assert _source_line_escapes(
        _FN, _GETENV, [2], {(_FN, 2): {_GETENV, keyed}},
        {(_FN, keyed): {"logging.info"}}, None,
    ) == frozenset({2})
