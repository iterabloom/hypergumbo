# SPDX-License-Identifier: AGPL-3.0-or-later
"""A sink call that passes only literals cannot carry taint (INV-fubag).

MEASURED, NOT SUPPOSED. ``docs/measurements/0003`` adjudicated every flow that
the construction-edge widening added across six repositories: 35 added, 1 true
positive, 34 false. **24 of the 34 land on ten call sites that are all
constructors taking no arguments at all**, a keyword literal, a generated temp
path, or the temp-file object::

    tempfile.TemporaryDirectory()                 no arguments
    tempfile.TemporaryFile()                      no arguments
    NamedTemporaryFile()                          no arguments
    tempfile.NamedTemporaryFile(delete=True)      keyword literal only

THE ARGUMENT IS A PROOF, NOT A HEURISTIC, which is why this gate needs no
threshold and costs no recall. Taint models a flow as the tainted value being
an ARGUMENT to the sink call or its RECEIVER. A call passing only literal
constants has no argument that could be the tainted value, and the receiver of
``tempfile.TemporaryDirectory()`` is a module. So such a flow cannot be a true
positive under the tool's own model.

WHY THE CONSTRUCTOR IS THE WRONG ANCHOR, which is the finding underneath.
``ZipFile(path,'w')`` *opens*; ``zipp.writestr(name, data)`` *writes*. Anchored
on the constructor, the sink only witnesses "an fs resource was created in this
function", so any tainted value anywhere in the function yields a flow. The
constructor IS a legitimate sink when its ARGUMENT is tainted — that is exactly
0003's single true positive, mitmproxy's ``ZipFile(path, "w")`` where ``path``
came from ``os.path.expanduser``. The defect is flagging it when the argument
is not.

ABSENCE OF THE MARKER IS THE CONSERVATIVE READING, deliberately. Producers stamp
``meta['call_arg_shape'] = 'literal_only'`` only when they can PROVE every
argument is a literal; every other call — including every edge in every behavior
map written before this key existed — carries no key and keeps flowing. A gate
whose default silences findings would be a false-negative generator on a
security analysis, which is the failure mode that matters here.
"""
from __future__ import annotations

from typing import ClassVar

from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.ir import Edge, deduplicate_edges
from hypergumbo_core.taint import (
    TaintSink,
    TaintSource,
    propagate_taint_ddg,
    propagate_taint_structural,
)


def _edge(src: str, dst: str, meta: dict | None = None) -> dict:
    e: dict = {
        "src": src, "dst": dst, "type": "calls",
        "is_resolved": not dst.endswith(":unresolved"),
    }
    if meta is not None:
        e["meta"] = meta
    return e


class TestLiteralOnlySinkCallIsNotAFlow:
    """The 0003 shape: a tainted value in scope, a sink that cannot receive it.

    Mirrors pretix's ``Renderer.render_background``, which reads ORM data and
    calls ``tempfile.TemporaryDirectory()`` — eight of the measured false
    positives land on that one site.
    """

    _SOURCES: ClassVar[list[TaintSource]] = [TaintSource(
        taint_label="untrusted_input", module="sys.stdin",
        name="read", kind="function",
    )]
    _SINKS: ClassVar[list[TaintSink]] = [TaintSink(
        zone="host_fs", trust_level="untrusted",
        module="tempfile", name="TemporaryDirectory", kind="function",
    )]
    _CALLER = "python:render.py:10-40:render_background:function"
    _SOURCE_CALL = "python:sys.stdin:0-0:read:unresolved"
    _SINK_CALL = "python:tempfile:0-0:TemporaryDirectory:unresolved"

    def _graph(self, sink_meta: dict | None) -> list[dict]:
        return [
            _edge(self._CALLER, self._SOURCE_CALL),
            _edge(self._CALLER, self._SINK_CALL, sink_meta),
        ]

    def test_literal_only_sink_call_yields_no_flow(self) -> None:
        findings = propagate_taint_structural(
            self._graph({"call_arg_shape": "literal_only"}),
            self._SOURCES, self._SINKS, [],
        )
        assert findings == []

    def test_positive_control_same_graph_without_the_marker_flows(self) -> None:
        """THE CONTROL. Without it, a gate that matched nothing and a gate that
        works are indistinguishable — and 0003's own lesson is that a control
        whose output equals the subject did not take."""
        findings = propagate_taint_structural(
            self._graph(None), self._SOURCES, self._SINKS, [],
        )
        assert len(findings) == 1
        assert findings[0].sink_zone == "host_fs"

    def test_an_unrelated_meta_key_does_not_silence_the_flow(self) -> None:
        """The gate must key on the VALUE, not on meta being present."""
        findings = propagate_taint_structural(
            self._graph({"call_construct": "method"}),
            self._SOURCES, self._SINKS, [],
        )
        assert len(findings) == 1

    def test_an_unknown_arg_shape_value_does_not_silence_the_flow(self) -> None:
        """Default-deny on the SILENCING direction: only the one value we can
        prove is safe suppresses a finding."""
        findings = propagate_taint_structural(
            self._graph({"call_arg_shape": "something_new"}),
            self._SOURCES, self._SINKS, [],
        )
        assert len(findings) == 1


class TestDeduplicationCannotMaskADynamicCallSite:
    """The gate's soundness depends on how collapsed call sites merge.

    ``deduplicate_edges`` keeps ONE edge per ``(src, dst, edge_type)`` and, by
    construction, the FIRST one encountered — later duplicates contribute only
    their ``call_lines``. So a function containing both::

        tempfile.TemporaryDirectory()            # line 100, literal_only
        tempfile.TemporaryDirectory(dir=tainted) # line 200, dynamic

    collapses to a single edge, and if the survivor kept line 100's marker the
    gate would silence a REAL flow. That is a false negative on a security
    analysis — strictly worse than the false positives this gate removes, and
    the reason the marker must merge conservatively rather than survive by
    arrival order.
    """

    _SRC = "python:render.py:10-40:render_background:function"
    _DST = "python:tempfile:0-0:TemporaryDirectory:unresolved"

    def _pair(self, first: dict | None, second: dict | None) -> Edge:
        edges = [
            Edge.create(src=self._SRC, dst=self._DST, edge_type="calls",
                        line=100, evidence_type="ast_call", meta=first,
                        origin="test", origin_run_id="run"),
            Edge.create(src=self._SRC, dst=self._DST, edge_type="calls",
                        line=200, evidence_type="ast_call", meta=second,
                        origin="test", origin_run_id="run"),
        ]
        kept = deduplicate_edges(edges)
        assert len(kept) == 1
        return kept[0]

    def test_a_dynamic_site_clears_a_literal_only_marker(self) -> None:
        survivor = self._pair({"call_arg_shape": "literal_only"}, None)
        assert (survivor.meta or {}).get("call_arg_shape") is None

    def test_order_does_not_decide_it(self) -> None:
        """The defect this pins is arrival-order-dependent, so both orders
        must agree — otherwise the guard passes on whichever order the fixture
        happens to use."""
        survivor = self._pair(None, {"call_arg_shape": "literal_only"})
        assert (survivor.meta or {}).get("call_arg_shape") is None

    def test_positive_control_all_literal_sites_keep_the_marker(self) -> None:
        """Without this the conservative merge could simply delete the key
        always, which would pass the two tests above while making the gate
        inert."""
        survivor = self._pair(
            {"call_arg_shape": "literal_only"},
            {"call_arg_shape": "literal_only"},
        )
        assert (survivor.meta or {}).get("call_arg_shape") == "literal_only"

    def test_collapsed_sites_are_still_recorded(self) -> None:
        """The merge must not damage what dedup already guarantees."""
        survivor = self._pair({"call_arg_shape": "literal_only"}, None)
        assert (survivor.meta or {}).get("call_lines") == [100, 200]


_FN = "python:render.py:1-9:render_background:function"


def _call(dst_name: str, line: int, meta: dict | None = None) -> dict:
    e: dict = {
        "src": _FN,
        "dst": f"python:external:0-0:{dst_name}:unresolved", "is_resolved": False,
        "type": "calls",
        "line": line,
    }
    if meta is not None:
        e["meta"] = meta
    return e


class TestTheDdgArmAppliesTheIdenticalGate:
    """Both propagation arms must agree, through the SHARED predicate.

    The structural and DDG passes each build their own ``sink_callers`` map
    from near-identical blocks. Gating only one would leave the other reporting
    exactly the flows this key exists to remove — and a rule implemented twice
    is the shape that let the call-family set drift across three consumers
    (taint, verify-claims, dead-code reachability) until INV-lalad found it.

    The fixture is a real DDG flow, not a structural bridge::

        1  data = read()
        3  TemporaryDirectory(data)

    with a data dependence carrying ``data`` from line 1 to line 3. The first
    attempt at this class used a bridge fixture that produced no finding in
    either arm, so the gate assertion passed while exercising nothing — the
    non-vacuity control below is what caught it.
    """

    _SOURCES: ClassVar[list[TaintSource]] = [TaintSource(
        taint_label="untrusted_input", module="external",
        name="read", kind="function",
    )]
    _SINKS: ClassVar[list[TaintSink]] = [TaintSink(
        zone="host_fs", trust_level="untrusted", module="external",
        name="TemporaryDirectory", kind="function",
    )]

    def _findings(self, sink_meta: dict | None) -> list:
        return propagate_taint_ddg(
            [DdgEdge(variable="data", def_block="bb_0", def_line=1,
                     use_block="bb_0", use_line=3, symbol_id=_FN)],
            [_call("read", 1), _call("TemporaryDirectory", 3, sink_meta)],
            self._SOURCES, self._SINKS, [],
            ddg_symbols={_FN},
            stmt_defuse={_FN: [(3, (), ("data",))]},
        )

    def test_literal_only_is_gated_in_the_ddg_arm_too(self) -> None:
        assert self._findings({"call_arg_shape": "literal_only"}) == []

    def test_non_vacuity_the_same_fixture_reports_without_the_marker(self) -> None:
        """The floor this class needs: the fixture must reach its sink, or the
        assertion above is satisfied by a walk that found nothing anyway."""
        findings = self._findings(None)
        assert len(findings) == 1
        assert findings[0].sink_zone == "host_fs"
