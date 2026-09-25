# SPDX-License-Identifier: AGPL-3.0-or-later
"""``instantiates`` edges are call-shaped for taint (INV-lalad).

A constructor call IS a call: data passed to it crosses into a callable
exactly as it does through ``calls``. ``TAINT_CALL_EDGE_TYPES`` did not say
so, and ``taint.py`` did not contain the string ``instantiates`` anywhere, so
the taint walk could not traverse a construction edge at all.

THE MEASURED CONSEQUENCE (the A/B filed on INV-lalad). Two fixtures identical
but for the SPELLING of the sink::

    data = sys.stdin.read()      # ipc_recv -> untrusted_input
    subprocess.run(data)         # CONTROL  -> violated, exit 1, 2 ddg findings
    subprocess.Popen(data)       # SUBJECT  -> confirmed_with_caveats, exit 3

``py.py`` types ``module.Attr()`` as ``instantiates`` when the attribute is
PascalCase and ``calls`` otherwise (the WI-jubag heuristic). ``Popen`` is
PascalCase and ``run`` is not, so one capital letter decided whether a
command-injection flow was visible at all. The subject arm did not merely go
quiet — it stated "No unsanitized untrusted_input data reaches subprocess
zone" about a two-line function in which it does.

WHY THE SET IS NOW DERIVED RATHER THAN EXTENDED. Appending the literal
``"instantiates"`` here would have made a fourth hand-maintained copy of "what
counts as the call family", and disagreeing copies of one set are the defect
this whole audit chased (INV-nosoz is five copies of the inheritance family).
WI-toruz declared the call family ONCE, on ``call_construct``'s
``applicable_edge_types``; the call-family half of this set now reads that
declaration.

WHAT MUST NOT HAPPEN, and is pinned below. The four sets that look like they
name the call family do NOT name the same concept. This one also carries
``module_attr_ref`` (WI-lokuv — attribute-kind io_primitives such as
``os.environ`` / ``sys.argv``; without it their auto-imported TaintSource
records never match in structural propagation) and ``dispatches_to``
(INV-zuhig — framework dispatch). Deriving the set WHOLESALE from the registry
would delete both. The regression guards in
:class:`TestTaintSpecificExtrasSurvive` exist because that mistake is the
obvious one, and it reads as a cleanup.
"""
from __future__ import annotations

from typing import ClassVar

from hypergumbo_core.axis_meta_keys import call_family_edge_types
from hypergumbo_core.taint import (
    TAINT_CALL_EDGE_TYPES,
    TaintSink,
    TaintSource,
    _is_taint_call_edge,
    propagate_taint_structural,
)


def _make_edge(src: str, dst: str, edge_type: str = "calls") -> dict:
    return {
        "src": src,
        "dst": dst,
        "type": edge_type,
        "is_resolved": not dst.endswith(":unresolved"),
    }


class TestInstantiatesIsCallShaped:
    def test_membership(self) -> None:
        assert "instantiates" in TAINT_CALL_EDGE_TYPES

    def test_predicate(self) -> None:
        assert _is_taint_call_edge({"type": "instantiates"}) is True

    def test_call_family_half_derives_from_the_registry(self) -> None:
        """The call-family half is the registry's, not a local literal."""
        assert call_family_edge_types() <= TAINT_CALL_EDGE_TYPES

    def test_structural_edges_still_excluded(self) -> None:
        """Widening to the call family must not admit structural edges.

        ``contains`` is the control: it is a structural containment
        relationship, not a call, and taint must not cross it.
        """
        assert _is_taint_call_edge({"type": "contains"}) is False
        assert _is_taint_call_edge({"type": "extends"}) is False


class TestTaintSpecificExtrasSurvive:
    """Regression guards for the wholesale-derive mistake (see module doc)."""

    def test_module_attr_ref_survives(self) -> None:
        assert "module_attr_ref" in TAINT_CALL_EDGE_TYPES

    def test_dispatches_to_survives(self) -> None:
        assert "dispatches_to" in TAINT_CALL_EDGE_TYPES

    def test_extras_are_not_in_the_call_family(self) -> None:
        """Pins WHY they must be listed explicitly: the registry does not
        carry them, so a wholesale derive would silently drop them."""
        family = call_family_edge_types()
        assert "module_attr_ref" not in family
        assert "dispatches_to" not in family


class TestInstantiatesPropagation:
    """The INV-lalad shape: construction is the only edge into the sink.

    Mirrors the filed A/B fixture exactly — ``handle_request`` calls the
    source (``sys.stdin.read``, so the CALLER is seeded, the default
    ``start_at``) and then reaches ``subprocess.Popen``. The only difference
    between the two tests below is the edge type on that second hop, which
    is the whole defect.
    """

    _SOURCES: ClassVar[list[TaintSource]] = [TaintSource(
        taint_label="untrusted_input",
        module="sys.stdin",
        name="read",
        kind="function",
    )]
    _SINKS: ClassVar[list[TaintSink]] = [TaintSink(
        zone="subprocess", trust_level="untrusted",
        module="subprocess", name="Popen", kind="function",
    )]

    _CALLER = "python:app.py:6-8:handle_request:function"
    _SOURCE_CALL = "python:sys.stdin:0-0:read:unresolved"
    _SINK_CALL = "python:subprocess:0-0:Popen:unresolved"

    def _graph(self, sink_edge_type: str) -> list[dict]:
        return [
            _make_edge(self._CALLER, self._SOURCE_CALL),
            _make_edge(self._CALLER, self._SINK_CALL, edge_type=sink_edge_type),
        ]

    def test_taint_reaches_a_sink_through_a_construction_edge(self) -> None:
        findings = propagate_taint_structural(
            self._graph("instantiates"), self._SOURCES, self._SINKS, [],
        )
        assert len(findings) == 1
        assert findings[0].sink_zone == "subprocess"

    def test_the_same_graph_on_a_calls_edge_agrees(self) -> None:
        """The control arm of the filed A/B: spelled ``run`` instead of
        ``Popen`` the flow was always visible. Both spellings must now agree,
        which is the actual acceptance criterion — not merely that
        ``instantiates`` produces SOME finding."""
        via_calls = propagate_taint_structural(
            self._graph("calls"), self._SOURCES, self._SINKS, [],
        )
        via_instantiates = propagate_taint_structural(
            self._graph("instantiates"), self._SOURCES, self._SINKS, [],
        )
        assert len(via_calls) == len(via_instantiates) == 1
        assert via_calls[0].sink_zone == via_instantiates[0].sink_zone

    def test_contains_edge_does_not_reach_the_sink(self) -> None:
        """Control: a structural ``contains`` edge on the same graph yields
        nothing, so the finding above is attributable to the call-family
        widening rather than to the source minting on its own."""
        findings = propagate_taint_structural(
            self._graph("contains"), self._SOURCES, self._SINKS, [],
        )
        assert findings == []
