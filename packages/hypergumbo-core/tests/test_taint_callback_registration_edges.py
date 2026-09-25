# SPDX-License-Identifier: AGPL-3.0-or-later
"""A callback handed to a registration carries taint into the handler (WI-nisud).

THE DEFECT, measured 2026-09-14 on a seven-arm javascript fixture.
``io-boundaries`` classified every arm -- ``WebSocket.addEventListener`` and
``WebSocket.onmessage`` as ``net_recv``, ``http.createServer`` as ``net_recv``,
``process.on`` as ``ipc_recv`` -- and ``verify-claims`` returned evidence_count
2, both of them controls, ZERO from ``net_recv``, while a control placed INSIDE
the same arrow function fired.

WHAT WAS INERT WAS NOT "EVERY CALLBACK SOURCE", AND THE DIFFERENCE IS SYMBOL
STRUCTURE RATHER THAN THE REGISTRATION CONCEPT. WI-nisud was filed on the
universal claim; it is wrong in one direction, and the counterexample already
had a test in the tree (test_js_ts_handler_assignment.py). An assignment RHS
written INLINE is minted no symbol, so the handler body is attributed to the
ENCLOSING function, source and sink land on one node, and
``ws.onmessage = function (ev) { exec('ls ' + ev.data); }`` verified
``violated`` / ``net_recv`` BEFORE this change -- a single-node path that
crosses no registration edge at all. Inertness followed the handler getting its
own symbol, which is what ``addEventListener``, ``http.createServer`` and
``process.on`` all do. The third spelling, a NAMED identifier assigned to a
property, is inert for a different reason again: no edge of ANY type reaches the
handler, so there is nothing for this predicate to cross (WI-vubal). The two
compose -- WI-vubal supplies the missing edge, this supplies the crossing.

THE MECHANISM. The analyzer emits ``references`` from the enclosing function to
the callback symbol::

    references  viaAddEventListener:function -> _cb_addEventListener@33:function
    calls       _cb_addEventListener@33:function -> child_process:0-0:exec

``references`` is not in :data:`TAINT_CALL_EDGE_TYPES`, so the walk advances
only along value-carrying edges and never enters the handler. The source is
attributed to the ENCLOSING function, the sink lives in the CALLBACK, and
nothing value-carrying joins them.

THE EDGE TYPE IS THE ONLY BLOCKER, and that was measured rather than assumed,
because "the walk cannot enter the handler" would also be the reported reason if
the engine simply did not credit a callback PARAMETER. Two difference arms
settled it. A javascript arm whose callee is reached by a plain ``calls`` edge
and is passed NOTHING tainted (source in the caller, sink in the callee) DOES
produce a finding -- structural propagation is function-level and coarse, so a
call-shaped edge is sufficient on its own. And the python parity arm -- source
in the enclosing function, sink in the handler, joined by the ``dispatches_to``
edge ``argparse_dispatch`` emits for ``set_defaults(func=...)`` -- produces a
finding on the identical shape. Same shape, traversable edge type, finding.

WHY A PREDICATE AND NOT A SET MEMBER. ``references`` is OVERLOADED, carrying
four evidence types in js_ts.py alone: ``callback_argument_reference`` (this
shape), ``ast_type_ref`` (TypeScript type references -- the bespoke ``type_ref``
edge type folded onto ``references``), ``object_field_reference``
(``{onClick: handleClick}`` and the ``{handleClick}`` shorthand) and an
``ast_call_direct`` middleware chain. Putting ``references`` wholesale into
:data:`TAINT_CALL_EDGE_TYPES` would make a TYPE reference carry dataflow, which
is the precise error class INV-putug measured at 20 of 47 spurious situations on
pretix; and it would make ``module.exports = {parse, stringify}`` flow a
module-level source into every exported function. The narrowing is the
``is_grpc_rpc_implementation`` move -- one shared predicate, matched on meta, in
the one place taint asks the question.

NOT MONOTONE-ADDITIVE, unlike the ``dispatches_to`` membership INV-zuhig added.
``_register_sanitizer_callers`` asks this same predicate, so a registration
edge whose callee matches a sanitizer now installs a barrier that was not there
before: ``arr.map(escapeHtml)`` sanitizes the enclosing function. That is
semantically right and it is why this cannot be waved through as "adjacency only
grows" -- this change CAN delete a finding. The last class pins it.

THE SIBLING THIS DOES NOT SHIP. kotlin emits ``references`` +
``callable_reference`` for ``::fn``, which is the same family one language over
and is equally inert. It is NOT included here: its precision on a kotlin corpus
is unmeasured, and an unmeasured language folded into a precision-sensitive
change is what INV-putug punishes. Filed separately.
"""
from __future__ import annotations

from collections import defaultdict

from hypergumbo_core.edge_types import is_callback_registration
from hypergumbo_core.taint import (
    TAINT_CALL_EDGE_TYPES,
    TaintSink,
    TaintSource,
    _is_taint_call_edge,
    propagate_taint_structural,
)

CB = "callback_argument_reference"


def _edge(
    src: str, dst: str, edge_type: str = "references", evidence: str | None = CB
) -> dict:
    meta = {"evidence_type": evidence} if evidence is not None else {}
    return {
        "src": src,
        "dst": dst,
        "type": edge_type,
        "meta": meta,
        "is_resolved": not dst.endswith(":unresolved"),
    }


class TestThePredicateIsTheSingleSourceOfTruth:
    """``is_callback_registration`` lives beside ``is_grpc_rpc_implementation``
    for the same stated reason: so consumers cannot drift about what the
    folded/overloaded form means."""

    def test_the_registration_shape_matches(self) -> None:
        assert is_callback_registration("references", {"evidence_type": CB}) is True

    def test_a_bare_references_edge_does_not(self) -> None:
        assert is_callback_registration("references", None) is False
        assert is_callback_registration("references", {}) is False

    def test_a_type_reference_does_not(self) -> None:
        # INV-putug's class. A TypeScript type reference is reachability, not
        # dataflow, and it rides the SAME edge type.
        assert (
            is_callback_registration("references", {"evidence_type": "ast_type_ref"})
            is False
        )

    def test_an_object_field_reference_does_not(self) -> None:
        # ``module.exports = {parse, stringify}`` is a reference, not a dispatch.
        assert (
            is_callback_registration(
                "references", {"evidence_type": "object_field_reference"}
            )
            is False
        )

    def test_the_evidence_type_alone_is_not_enough(self) -> None:
        # The pair is load-bearing: the edge TYPE still has to be ``references``.
        assert is_callback_registration("contains", {"evidence_type": CB}) is False


class TestTaintReadsTheRegistrationEdgeAsCallShaped:
    def test_the_predicate_accepts_it(self) -> None:
        assert _is_taint_call_edge(_edge("a:f:function", "b:g:function")) is True

    def test_references_is_still_not_a_set_member(self) -> None:
        # The boundary test_taint_dispatches_to_edges.py pins, restated rather
        # than eroded: bare ``references`` stays inert, and the SET is unchanged.
        assert "references" not in TAINT_CALL_EDGE_TYPES
        assert _is_taint_call_edge({"type": "references"}) is False

    def test_a_type_reference_stays_inert_through_taint(self) -> None:
        assert (
            _is_taint_call_edge(
                _edge("a:f:function", "b:T:class", evidence="ast_type_ref")
            )
            is False
        )


class TestTheWalkNowEntersTheHandler:
    """The end-to-end shape from the fixture: source attributed to the enclosing
    function, sink inside the callback, joined only by the registration edge."""

    ENCLOSING = "javascript:app.js:12-17:viaAddEventListener:function"
    HANDLER = "javascript:app.js:14-16:_cb_addEventListener@33:function"
    SOURCE = "javascript:WebSocket:0-0:addEventListener:external_symbol"
    SINK = "javascript:child_process:0-0:exec:external_symbol"

    def _edges(self, registration_type: str, evidence: str | None) -> list[dict]:
        return [
            {
                "src": self.ENCLOSING,
                "dst": self.SOURCE,
                "type": "calls",
                "meta": {},
                "is_resolved": True,
            },
            _edge(self.ENCLOSING, self.HANDLER, registration_type, evidence),
            {
                "src": self.HANDLER,
                "dst": self.SINK,
                "type": "calls",
                "meta": {},
                "is_resolved": True,
            },
        ]

    @staticmethod
    def _run(edges: list[dict]) -> list:
        return list(
            propagate_taint_structural(
                edges,
                [
                    TaintSource(
                        taint_label="untrusted_input",
                        module="WebSocket",
                        name="addEventListener",
                        kind="method",
                    )
                ],
                [
                    TaintSink(
                        zone="subprocess",
                        trust_level="untrusted",
                        module="child_process",
                        name="exec",
                        kind="function",
                    )
                ],
                [],
            )
        )

    def test_the_registration_edge_carries_the_flow(self) -> None:
        flows = self._run(self._edges("references", CB))
        assert flows, "the walk did not enter the handler"
        assert any(self.HANDLER in str(f) for f in flows)

    def test_the_same_graph_without_the_evidence_type_is_inert(self) -> None:
        # The CONTROL. Identical graph, identical source and sink; only the
        # evidence type differs. If this also produced a flow the test above
        # would be proving nothing.
        assert not self._run(self._edges("references", None))


class TestThisCanDeleteAFindingAndThatIsDeliberate:
    """``_register_sanitizer_callers`` asks the same predicate, so a callback
    handed to a sanitizer installs a barrier. Named because it makes this change
    non-additive, unlike the ``dispatches_to`` membership."""

    def test_a_sanitizer_passed_as_a_callback_registers_as_a_barrier(self) -> None:
        from hypergumbo_core.taint import TaintSanitizer, _register_sanitizer_callers

        sanitizer = TaintSanitizer(
            input_taint="untrusted_input",
            output_taint="",
            qualified_name="escapeHtml",
        )
        caller = "javascript:app.js:1-9:render:function"
        registered: dict[str, dict[str, list[TaintSanitizer]]] = defaultdict(dict)
        _register_sanitizer_callers(
            [_edge(caller, "javascript:app.js:20-22:escapeHtml:function")],
            {"escapeHtml": [sanitizer]},
            registered,
        )
        assert caller in registered, "arr.map(escapeHtml) did not register a barrier"

    def test_a_type_reference_to_a_sanitizer_name_registers_nothing(self) -> None:
        # The control: the barrier comes from the REGISTRATION shape, not from
        # any ``references`` edge that happens to name a sanitizer.
        from hypergumbo_core.taint import TaintSanitizer, _register_sanitizer_callers

        sanitizer = TaintSanitizer(
            input_taint="untrusted_input",
            output_taint="",
            qualified_name="escapeHtml",
        )
        registered: dict[str, dict[str, list[TaintSanitizer]]] = defaultdict(dict)
        _register_sanitizer_callers(
            [
                _edge(
                    "javascript:app.js:1-9:render:function",
                    "javascript:app.js:20-22:escapeHtml:function",
                    evidence="ast_type_ref",
                )
            ],
            {"escapeHtml": [sanitizer]},
            registered,
        )
        assert not registered
