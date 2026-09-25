# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fumod: an I/O primitive's return does not inherit its argument's taint.

THE RULE IS THE 0001 RUBRIC'S OWN TIE-BREAK MADE EXECUTABLE — *taint flows
through in-program computation, not through an external resource selected by the
tainted value*. An I/O primitive's return value comes from the OTHER SIDE of the
boundary: a handle on a resource the argument merely NAMED, or bytes the argument
merely ADDRESSED. It is not a computation on that argument.

MEASURED, WITH A CONTROL THAT DISCRIMINATES, on the shipped CLI::

    # tainted PATH, constant payload            # fixed path, tainted PAYLOAD
    args = ArgumentParser().parse_args()        args = ArgumentParser().parse_args()
    out = open(args.outfile, "w")               out = open("/tmp/fixed.txt", "w")
    out.write("a constant banner\\n")            out.write(args.payload)

    before:  open -> ddg                        before:  file.write -> ddg
             file.write -> ddg                  after:   file.write -> ddg
    after:   open -> ddg
             file.write -> ddg_MIXED

Nothing tainted is written on the left, and the tool was already internally
inconsistent about it — naming `builtins.open` correctly (the path really does
select the file) and *then* crediting `file.write` as well, through a handle the
tainted value only named. On the right the tainted value reaches the write's own
argument, and that finding must keep its confirmation. It does.

DERIVED FROM THE CATALOGUE, NOT CURATED. Every I/O primitive is already
enumerated per language, so a hand-written list of "opening" calls would be a
second home for that fact and wrong the moment a row was added.

WHAT THIS DOES NOT DO, and the honest statement matters more than the fix. §3a is
CONFIRM-ONLY: the walk raises confidence and never removes a flow. So the left
finding is still REPORTED — its inclusion rests on call-graph reachability, which
this rule does not touch. What the rule withdraws is the false claim of
PRECISION (INV-sadah's concern), not the finding. Removing it needs inclusion to
stop resting on reachability (INV-karud), or §7a removal authority — which
measurement 0007 shows has no evidence-backed domain at all.
"""

from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.taint import (
    TaintSink,
    TaintSource,
    propagate_taint_ddg,
)

_FN = "python:m.py:4-8:main:function"
_PARSE = "python:argparse.ArgumentParser:0-0:parse_args:unresolved"
_OPEN = "python:builtins:0-0:open:unresolved"
_WRITE = "python:file:0-0:write:unresolved"

_SOURCES = [TaintSource(
    taint_label="host_secret", module="argparse.ArgumentParser",
    name="parse_args", kind="method", source_boundary="env_read",
)]
_SINKS = [TaintSink(
    zone="host_fs", trust_level="untrusted", module="file",
    name="write", kind="method",
)]


def _call(dst: str, line: int, meta: dict | None = None) -> dict:
    e: dict = {"src": _FN, "dst": dst, "type": "calls",
               "is_resolved": False, "line": line}
    if meta:
        e["meta"] = meta
    return e


def _run(stmt_defuse, ddg_edges):
    return propagate_taint_ddg(
        ddg_edges=ddg_edges,
        stmt_defuse={_FN: stmt_defuse},
        call_edges=[
            _call(_PARSE, 5, {"call_construct": "method"}),
            _call(_OPEN, 6, {"io_mode": "w"}),
            _call(_WRITE, 7, {"call_construct": "method"}),
        ],
        sources=_SOURCES, sinks=_SINKS, sanitizers=[],
        ddg_symbols={_FN}, language="python",
    )


def _ddg(var: str, def_line: int, use_line: int) -> DdgEdge:
    return DdgEdge(symbol_id=_FN, variable=var, def_block="bb_0",
                   def_line=def_line, use_block="bb_0", use_line=use_line)


# ``args`` defined at 5 and used at 6; ``out`` defined at 6 and used at 7.
_TAINTED_PATH = (
    [(5, ("args",), ()), (6, ("out",), ("args",)), (7, (), ("out",))],
    [_ddg("args", 5, 6), _ddg("out", 6, 7)],
)
# The control: ``args`` reaches the WRITE's own argument at line 7.
_TAINTED_PAYLOAD = (
    [(5, ("args",), ()), (6, ("out",), ()), (7, (), ("args", "out"))],
    [_ddg("args", 5, 7), _ddg("out", 6, 7)],
)


def test_a_tainted_path_does_not_confirm_the_write():
    """The handle was NAMED by the tainted value, not computed from it."""
    findings = _run(*_TAINTED_PATH)
    writes = [f for f in findings if f.sink_primitive == "write"]
    assert writes, "the flow is still REPORTED — §3a is confirm-only"
    assert [f.analysis_method for f in writes] == ["ddg_mixed"]
    assert [f.confidence for f in writes] == ["approximate"]


def test_a_tainted_payload_still_confirms_the_write():
    """THE NON-VACUITY FLOOR. Without this the rule is just suppression."""
    findings = _run(*_TAINTED_PAYLOAD)
    writes = [f for f in findings if f.sink_primitive == "write"]
    assert [f.analysis_method for f in writes] == ["ddg"]
    assert [f.confidence for f in writes] == ["precise"]


def test_a_non_io_call_still_propagates_into_its_result():
    """`p = os.path.join(tainted, "x")` is in-program computation and survives.

    The rule keys on the catalogue, so a call the catalogue does not classify as
    an I/O primitive is untouched — which is what keeps the refuters' surviving
    true positive (`path.with_name(path.name + '.bak')`) a true positive.
    """
    join = "python:os.path:0-0:join:unresolved"
    findings = propagate_taint_ddg(
        ddg_edges=[_ddg("args", 5, 6), _ddg("p", 6, 7)],
        call_edges=[
            _call(_PARSE, 5, {"call_construct": "method"}),
            _call(join, 6),
            _call(_WRITE, 7, {"call_construct": "method"}),
        ],
        sources=_SOURCES, sinks=_SINKS, sanitizers=[],
        ddg_symbols={_FN}, language="python",
        stmt_defuse={_FN: [
            (5, ("args",), ()), (6, ("p",), ("args",)), (7, (), ("p",)),
        ]},
    )
    writes = [f for f in findings if f.sink_primitive == "write"]
    assert [f.analysis_method for f in writes] == ["ddg"]


def test_the_rule_is_inert_without_a_language():
    """No language, no catalogue, no barrier — and no crash."""
    findings = propagate_taint_ddg(
        ddg_edges=_TAINTED_PATH[1],
        call_edges=[
            _call(_PARSE, 5, {"call_construct": "method"}),
            _call(_OPEN, 6, {"io_mode": "w"}),
            _call(_WRITE, 7, {"call_construct": "method"}),
        ],
        sources=_SOURCES, sinks=_SINKS, sanitizers=[],
        ddg_symbols={_FN},
        stmt_defuse={_FN: _TAINTED_PATH[0]},
    )
    assert findings
