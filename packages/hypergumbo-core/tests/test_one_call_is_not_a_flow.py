# SPDX-License-Identifier: AGPL-3.0-or-later
"""A value a call RETURNS cannot be an argument to that SAME call (INV-lozat).

WHY THIS EXISTS AND WHY IT DID NOT BEFORE. Until INV-lozat no shipped primitive
was a taint SOURCE and a taint SINK at the same call. ``db_read`` pairs with
``db_write``, ``net_recv`` with ``net_send`` — always two different calls. A
content-returning launch is the first primitive that is both: ``cmd.Output()``
receives the child's bytes AND is a ``subprocess`` sink, so the propagators
paired the call with ITSELF and reported "data from a subprocess reaches a
subprocess" over one call that cannot possibly feed its own arguments.

MEASURED, WHICH IS WHY THIS IS A GATE AND NOT A TIDINESS ARGUMENT. Paired A/B
over the measurement-0006 cohort (16 repositories, both arms cold), counting
hypergumbo's OWN ``walk_blocked_by`` field:

    background rate, arm A       4 of 692 rows    0.6%   sink_before_source
    the rows INV-lozat adds     18 of  51 rows   35.3%   sink_before_source

Sixty times the background rate, so this is a property of the new primitive
class rather than an inherited one, and two of the three verdicts that moved
``inconclusive -> violated`` rested on a SINGLE such row.

WHY NOT ACT ON ``walk_blocked_by`` DIRECTLY, which would catch more. Because
ADR-0017 §3a is confirm-only and taint.py says so where the field is defined:
*"NOTHING IS ACTED ON. §3a stays confirm-only ... No verdict moves because of
this field."* ``sink_before_source`` means the walk COULD NOT RUN, not that the
flow is impossible — a loop makes a textually-earlier sink genuinely reachable
from a later source. This gate is a different kind of statement, and one the
walk is not needed for: it is about a CALL SITE, the same shape as
``_sink_call_can_carry_taint`` (a site that discards anchors no sink) and
``_source_call_can_mint_taint`` (a site that reads memory mints nothing).

POSITIVE EVIDENCE IS REQUIRED, and that asymmetry is deliberate. The gate fires
only when the edge records EXACTLY ONE call line. With two or more, the caller
invokes the primitive twice and iteration N's output really can reach
iteration N+1's arguments (``out = check_output(out)``), so the flow stays.
With NONE recorded we do not know how many calls there are, and removal without
evidence is the expensive direction for a security tool — so it stays then too.
``test_two_call_sites_keep_the_flow`` and ``test_no_line_information_keeps_the_flow``
are those two controls, and they are what stops this gate from degenerating
into "a primitive that is both a source and a sink never fires".
"""

from __future__ import annotations

from hypergumbo_core import taint
from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.taint import (
    TaintSink,
    TaintSource,
    propagate_taint_ddg,
    propagate_taint_structural,
)

#: ``os/exec.Cmd.Output`` as INV-lozat now declares it: simultaneously an
#: ``ipc_recv`` source and a ``subprocess`` sink.
_SOURCE = TaintSource(
    taint_label="untrusted_input", module="os/exec.Cmd", name="Output",
    kind="method", source_boundary="ipc_recv",
)
_SINK = TaintSink(
    zone="subprocess", trust_level="untrusted", module="os/exec.Cmd",
    name="Output", kind="method",
)
#: A second, DIFFERENT sink in the same function — the shape that must keep
#: firing, because `git log` output really can become another command.
_OTHER_SINK = TaintSink(
    zone="subprocess", trust_level="untrusted", module="os/exec",
    name="Command", kind="function",
)

_CALLER = "go:cmd/bd/worktree.go:21-27:gitRevParse:function"
_OUTPUT = "go:os/exec:0-0:Output:external_symbol"
_COMMAND = "go:os/exec:0-0:Command:external_symbol"


def _edge(dst: str, line: int | None, call_lines: list[int] | None = None,
          construct: str = "method"):
    meta: dict[str, object] = {"call_construct": construct}
    if call_lines is not None:
        meta["call_lines"] = call_lines
    e: dict[str, object] = {
        "src": _CALLER, "dst": dst, "type": "calls",
        "is_resolved": False, "meta": meta,
    }
    if line is not None:
        e["line"] = line
    return e


def _structural(edges, sinks=(_SINK,)):
    return list(propagate_taint_structural(
        edges, [_SOURCE], list(sinks), [], language="go",
    ))


def _ddg(edges, sinks=(_SINK,)):
    """The DDG arm over the same edges.

    ``propagate_taint_ddg`` returns ``[]`` on empty ``ddg_edges`` before it
    reaches any sink, so a real one is supplied — otherwise this arm would
    "pass" for a reason that has nothing to do with the gate.
    """
    return list(propagate_taint_ddg(
        [DdgEdge(variable="out", def_block="b0", def_line=22,
                 use_block="b0", use_line=22, symbol_id=_CALLER)],
        edges, [_SOURCE], list(sinks), [], ddg_symbols={_CALLER},
        language="go",
    ))


class TestOneCallIsNotAFlow:
    def test_structural_refuses_the_pair(self) -> None:
        """THE DEFECT, at the resolution that makes it undeniable: one call,
        reported as a source->sink pair with itself."""
        assert _structural([_edge(_OUTPUT, 22)]) == [], (
            "one `cmd.Output()` was reported as data from a subprocess "
            "reaching a subprocess. The bytes it returns cannot be arguments "
            "to the call that returned them."
        )

    def test_ddg_refuses_the_pair(self) -> None:
        """BOTH ARMS. The structural and ddg passes select on the repository,
        not on the claim, so a gate on one is a gate half the corpus skips."""
        assert _ddg([_edge(_OUTPUT, 22)]) == []

    def test_a_different_sink_in_the_same_function_still_fires(self) -> None:
        """NON-DESTRUCTION, and the finding this whole item exists to produce:
        `git log` output handed to a second command."""
        edges = [_edge(_OUTPUT, 22), _edge(_COMMAND, 23, construct="function")]
        got = _structural(edges, sinks=(_SINK, _OTHER_SINK))
        assert [f.sink_primitive for f in got] == ["Command"], (
            f"the second command's flow was lost with the self-pair: "
            f"{[f.sink_primitive for f in got]}"
        )

    def test_two_call_sites_keep_the_flow(self) -> None:
        """CONTROL WITH TEETH. Two calls to the same primitive in one function
        is a shape where the pair is REAL — `out = check_output(out)` in a
        loop — so the gate must not fire on it. Without this the gate would be
        "a source that is also a sink never fires", which is a different and
        wrong rule."""
        got = _structural([_edge(_OUTPUT, 22, call_lines=[22, 40])])
        assert len(got) == 1, (
            "two recorded call sites were suppressed as one call; the gate has "
            "stopped being about a single invocation."
        )

    def test_no_line_information_keeps_the_flow(self) -> None:
        """The direction discipline, asserted rather than described. With no
        recorded line we do not KNOW there is one call, and removing a finding
        on an assumption is the expensive direction."""
        assert len(_structural([_edge(_OUTPUT, None)])) == 1

    def test_both_propagators_consult_the_shared_predicate(self) -> None:
        """ONE FACT, ONE HOME. Two copies of this question is how the
        call-family set drifted across three consumers (taint.py's own words at
        the structural call site), so the clause lives in one predicate and
        this records that both arms reach it."""
        seen: list[tuple[str, str]] = []
        real = taint._source_and_sink_are_one_call

        def recorder(sc, sk, scallee, skcallee, call_lines):
            seen.append((scallee, skcallee))
            return real(sc, sk, scallee, skcallee, call_lines)

        edges = [_edge(_OUTPUT, 22)]
        taint._source_and_sink_are_one_call = recorder  # type: ignore[assignment]
        try:
            propagate_taint_structural(
                edges, [_SOURCE], [_SINK], [], language="go",
            )
            assert seen, "the structural arm never asked"
            seen.clear()
            _ddg(edges)
            assert seen, "the ddg arm never asked"
        finally:
            taint._source_and_sink_are_one_call = real  # type: ignore[assignment]
