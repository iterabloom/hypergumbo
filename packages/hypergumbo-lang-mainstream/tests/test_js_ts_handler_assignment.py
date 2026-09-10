# SPDX-License-Identifier: AGPL-3.0-or-later
"""A handler ASSIGNMENT on a catalogued receiver emits a registration edge
(``ws.onmessage = h``), so the three rows ``WebSocket.onmessage``,
``WebSocket.onclose`` and ``EventSource.onmessage`` stop being unreachable.

WI-dosuh. ``ws.onmessage = handler`` is the browser receive surface short of
``addEventListener``, and it is a property assignment, not a call. Every
branch of the member-call cascade in ``js_ts.py`` keys on a
``call_expression``, so the assignment produced NOTHING -- not a
low-confidence edge, not the ``external`` placeholder -- and the three
catalogue rows above could never match. The limitation was declared in
writing (``analyzer_disclosure.CONSTRUCT_BLIND_ROWS``) rather than fixed,
because the remedy is a third kind of thing: a suppressed name is a policy,
an unemitted method call is a missing edge, and this is a construct the
analyzer does not model at all.

WHAT THIS BUYS, STATED HONESTLY: ZERO SECURITY FINDINGS TODAY. Classifying
the site is only the first half. For a finding, taint must also ENTER the
handler, and the enclosing-function-to-callback edge is ``references``,
which is not in ``TAINT_CALL_EDGE_TYPES`` -- so no javascript
callback-registration source can carry taint yet, ``addEventListener``
included, and that half is WI-nisud. Neither item subsumes the other; the
measurement is in ~/hypergumbo_lab_notebook/dosuh_js_09102026/. This file
therefore asserts CLASSIFICATION -- the edge exists, names the right row and
tags the right boundary -- and asserts nothing about findings.

SCOPE IS SET BY PARITY WITH THE CALL PATH, not by the construct. The
2026-09-10 probe (~/hypergumbo_lab_notebook/dosuh_family_09102026/) asked
every receiver spelling twice, once as ``ws.send(x)`` and once as
``ws.onmessage = h``. Four spellings resolve the receiver to its catalogue
module for a CALL and emitted nothing for an ASSIGNMENT; those four are this
change's work and each has a test below. Three more resolve for NEITHER --
they are receiver-typing gaps that predate this construct and would need a
second copy of receiver resolution to fix here, so they are filed instead
(WI-ponid: assignment-bound and field-bound receivers land on ``external``;
WI-vipos: a computed-property call emits no edge at all) and pinned below as
KNOWN GAPS so they cannot be mistaken for this change's failures.

THE PROPERTY MUST BE ONE THE CATALOGUE ROWS. ``ws.onopen = h`` and
``ws.binaryType = 'arraybuffer'`` emit nothing: emitting for every property
write on a catalogued receiver would put plain data assignments in the graph
as ``calls`` edges. Deriving the set from the catalogue (rather than listing
it) is the ``_derive_js_constructor_types`` precedent -- a row added
tomorrow is reachable without a code change.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.js_ts import analyze_javascript

_CATS = {
    "javascript": load_catalog("javascript"),
    "typescript": load_catalog("typescript"),
}


def _edges(root: Path, source: str, name: str = "app.js") -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(source)
    return analyze_javascript(root).edges


def _dsts(edges: list[Edge]) -> set[str]:
    return {e.dst for e in edges if e.edge_type == "calls"}


def _tagged(edges: list[Edge]) -> int:
    return tag_io_boundaries(edges, _CATS)


def _registration(edges: list[Edge], prop: str) -> Edge:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.dst.endswith(f":{prop}:unresolved")
    ]
    assert len(hits) == 1, [e.dst for e in edges]
    return hits[0]


class TestTheFiledSpelling:
    """``const ws = new WebSocket(u); ws.onmessage = h`` -- the shape
    WI-zumoz measured and CONSTRUCT_BLIND_ROWS declared."""

    def test_it_names_the_catalogue_row_and_tags_net_recv(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(
            tmp_path / "filed",
            "function listen(u, h) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.onmessage = h;\n"
            "}\n",
        )
        assert "javascript:WebSocket:0-0:onmessage:unresolved" in _dsts(edges)
        assert _tagged(edges) == 1

    def test_the_edge_declares_the_construct(self, tmp_path: Path) -> None:
        """``call_construct`` is what tells the io-boundary layer this was a
        registration and not a bare name, and both taint gates read it."""
        edges = _edges(
            tmp_path / "construct",
            "function listen(u, h) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.onmessage = h;\n"
            "}\n",
        )
        assert _registration(edges, "onmessage").meta["call_construct"] == "assignment"

    def test_the_value_shape_does_not_matter(self, tmp_path: Path) -> None:
        """A registration is a registration whether the handler is named,
        a function expression or an arrow -- the row is chosen by the
        RECEIVER and the PROPERTY, and reading the value would add a third
        thing to get wrong."""
        for i, value in enumerate(
            ("h", "function (ev) { return ev.data; }", "(ev) => ev.data"),
        ):
            edges = _edges(
                tmp_path / f"value{i}",
                "function listen(u, h) {\n"
                "  const ws = new WebSocket(u);\n"
                f"  ws.onmessage = {value};\n"
                "}\n",
            )
            assert _tagged(edges) == 1, value


class TestTheOtherTwoRows:
    def test_onclose(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "onclose",
            "function listen(u, h) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.onclose = h;\n"
            "}\n",
        )
        assert "javascript:WebSocket:0-0:onclose:unresolved" in _dsts(edges)
        assert _tagged(edges) == 1

    def test_eventsource_onmessage(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "es",
            "function listen(u, h) {\n"
            "  const es = new EventSource(u);\n"
            "  es.onmessage = h;\n"
            "}\n",
        )
        assert "javascript:EventSource:0-0:onmessage:unresolved" in _dsts(edges)
        assert _tagged(edges) == 1


class TestTheOtherThreeReceiverSpellings:
    """The three spellings beyond the filed one where the CALL path already
    reaches the catalogue, so the assignment path owes parity."""

    def test_inline_new_receiver(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "inline",
            "function listen(u, h) {\n"
            "  new WebSocket(u).onmessage = h;\n"
            "}\n",
        )
        assert "javascript:WebSocket:0-0:onmessage:unresolved" in _dsts(edges)
        assert _tagged(edges) == 1

    def test_module_scope(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "modscope",
            "const ws = new WebSocket(u);\nws.onmessage = h;\n",
        )
        assert "javascript:WebSocket:0-0:onmessage:unresolved" in _dsts(edges)
        assert _tagged(edges) == 1

    def test_typescript_declared_parameter(self, tmp_path: Path) -> None:
        """java's INV-vugon rule: a declaration is evidence. The call path
        already reads ``var_types`` for this; the assignment path reads the
        same map through the same helper."""
        edges = _edges(
            tmp_path / "tsparam",
            "function listen(ws: WebSocket, h) {\n  ws.onmessage = h;\n}\n",
            name="app.ts",
        )
        assert "typescript:WebSocket:0-0:onmessage:unresolved" in _dsts(edges)
        assert _tagged(edges) == 1


class TestLogicalAssignmentRegistersToo:
    """``ws.onmessage ??= h`` registers a handler exactly as ``=`` does; it is
    the same mechanism under a different operator, and leaving it out would
    be a blind spot that then has to be disclosed."""

    def test_logical_or_assign(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "orassign",
            "function listen(u, h) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.onmessage ||= h;\n"
            "}\n",
        )
        assert _tagged(edges) == 1

    def test_nullish_assign(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "nullish",
            "function listen(u, h) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.onmessage ??= h;\n"
            "}\n",
        )
        assert _tagged(edges) == 1


class TestWhatMustNotEmit:
    """CONTROLS. A rule that fires on everything classifies nothing, and the
    first version of this probe used a control that could not fail."""

    def test_an_uncatalogued_property_emits_nothing(self, tmp_path: Path) -> None:
        """``onopen`` was REMOVED from the catalogue deliberately (INV-nular:
        it is a connection-lifecycle callback carrying no peer data). The
        analyzer must not re-add it as an edge."""
        edges = _edges(
            tmp_path / "onopen",
            "function listen(u, h) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.onopen = h;\n"
            "}\n",
        )
        assert _dsts(edges) == set()

    def test_a_data_property_emits_nothing(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "binarytype",
            "function listen(u) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.binaryType = 'arraybuffer';\n"
            "}\n",
        )
        assert _dsts(edges) == set()

    def test_a_project_class_receiver_emits_nothing(self, tmp_path: Path) -> None:
        """The module slot must never be filled with a fictional module: a
        project class named like nothing in the catalogue is not a row."""
        edges = _edges(
            tmp_path / "projclass",
            "class Thing { }\n"
            "function listen(h) {\n"
            "  const t = new Thing();\n"
            "  t.onmessage = h;\n"
            "}\n",
        )
        assert _dsts(edges) == set()

    def test_an_untyped_receiver_emits_nothing(self, tmp_path: Path) -> None:
        """WI-nasuf gave untyped METHOD CALLS the ``external`` placeholder so
        the call is disclosed. An assignment gets no such placeholder: with no
        receiver type there is no row to name, and an ``external`` assignment
        edge would assert a call that never happens."""
        edges = _edges(
            tmp_path / "untyped",
            "function listen(obj, h) {\n  obj.onmessage = h;\n}\n",
        )
        assert _dsts(edges) == set()


class TestTheCallPathIsUnchanged:
    """REGRESSION CONTROL. The assignment branch shares receiver resolution
    with the call cascade, so a change there is a change to every member
    call in every javascript repo."""

    def test_add_event_listener_still_resolves(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "ael",
            "function listen(u, h) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.addEventListener('message', h);\n"
            "}\n",
        )
        assert _dsts(edges) == {
            "javascript:WebSocket:0-0:addEventListener:unresolved"}
        assert _tagged(edges) == 1

    def test_send_still_resolves(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "send",
            "function listen(u, x) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.send(x);\n"
            "}\n",
        )
        assert _dsts(edges) == {"javascript:WebSocket:0-0:send:unresolved"}
        assert _tagged(edges) == 1

    def test_an_untyped_method_call_still_gets_its_placeholder(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(
            tmp_path / "untypedcall",
            "function go(obj, x) {\n  obj.write(x);\n}\n",
        )
        assert _dsts(edges) == {"javascript:external:0-0:write:unresolved"}


class TestKnownGapsStayGaps:
    """PINNED, NOT FIXED. These are receiver-typing failures the CALL path
    shares (measured 2026-09-10, dosuh_family_09102026/probe_parity.py), so
    fixing them inside this construct would mean a second, divergent copy of
    receiver resolution. When either is fixed, the fix belongs in the shared
    helper and BOTH constructs gain it at once -- at which point these two
    tests fail and get re-pointed, which is the signal they exist for.
    """

    def test_a_receiver_bound_by_assignment_is_still_unresolved(
        self, tmp_path: Path,
    ) -> None:
        """WI-ponid. ``var_ctor_modules`` is populated only under
        ``variable_declarator``."""
        edges = _edges(
            tmp_path / "gap_letassign",
            "function listen(u, h) {\n"
            "  let ws;\n"
            "  ws = new WebSocket(u);\n"
            "  ws.onmessage = h;\n"
            "}\n",
        )
        assert _dsts(edges) == set()

    def test_a_field_bound_receiver_is_still_unresolved(
        self, tmp_path: Path,
    ) -> None:
        """WI-ponid, second spelling: the ordinary class-based WebSocket
        client. ``this.ws.send(x)`` lands on ``external`` today, so the
        assignment has no module to name either."""
        edges = _edges(
            tmp_path / "gap_field",
            "class C {\n"
            "  constructor(u) { this.ws = new WebSocket(u); }\n"
            "  go(h) { this.ws.onmessage = h; }\n"
            "}\n",
        )
        assert _dsts(edges) == set()


class TestTheHandlerBodyStaysWithItsEnclosingFunction:
    """THE MECHANISM BEHIND A LIVE FINDING, pinned because it is load-bearing
    and was discovered by refutation rather than designed.

    WI-dosuh was predicted to be correct-but-INERT: classifying the site was
    said to buy no finding, because taint must also ENTER the handler and the
    enclosing-function-to-callback edge is ``references``, which is not in
    ``TAINT_CALL_EDGE_TYPES``. Measured on the production path, that prediction
    is REFUTED for one spelling of three, and the difference is symbol
    structure, not danger:

      * ``addEventListener`` passes its callback as an ARGUMENT, and WI-zavad's
        ``_emit_anon_callback_reference_edges`` mints that callback its own
        symbol (``_cb_addEventListener@<n>``). A sink inside the callback hangs
        off THAT symbol, behind the uncrossable ``references`` edge -- inert,
        as predicted.
      * An ASSIGNMENT right-hand side is minted no symbol at all, so an inline
        handler's body is attributed to the ENCLOSING function. Source and sink
        land on one node, and the structural arm reports the flow:
        ``ws.onmessage = function (ev) { exec('ls ' + ev.data); }`` verifies as
        ``violated / untrusted-input-no-subprocess`` with origin ``net_recv``.
        It is a TRUE finding -- network bytes really do reach a shell.
      * A NAMED handler (``ws.onmessage = handle``) stays inert exactly as
        predicted, and for a stronger reason than expected: there is no edge of
        any type from the registering function to ``handle`` (filed as
        WI-vubal).

    THIS IS NOT AN ARGUMENT FOR MINTING A SYMBOL ON THE ASSIGNMENT SIDE. Doing
    so would make the inline spelling inert too -- reproducing the very defect
    WI-nisud exists to remove, and trading a true finding for symmetry. The
    collapse is accidentally CORRECT here: the handler body genuinely executes
    with the received value in scope, so a sink in it is genuinely reachable
    from the source.

    So this pins the structure, not the verdict: if a future change gives the
    assignment RHS its own symbol, the live finding disappears silently, and
    this test is what says so. Verdicts and fixtures:
    ~/hypergumbo_lab_notebook/dosuh_family_09102026/.
    """

    _SRC = (
        "const { exec } = require('child_process');\n"
        "function viaAssignment(url) {\n"
        "  const ws = new WebSocket(url);\n"
        "  ws.onmessage = function (ev) { exec('ls ' + ev.data); };\n"
        "}\n"
        "function viaListener(url) {\n"
        "  const ws = new WebSocket(url);\n"
        "  ws.addEventListener('message', function (ev) { exec('ls ' + ev.data); });\n"
        "}\n"
    )

    def _src_of(self, edges: list[Edge], dst_suffix: str, near: str) -> str:
        hits = [
            e for e in edges
            if e.edge_type == "calls" and e.dst.endswith(dst_suffix)
            and near in e.src
        ]
        assert len(hits) == 1, [(e.src, e.dst) for e in edges]
        return hits[0].src

    def test_the_assignment_sink_shares_the_source_symbol(
        self, tmp_path: Path,
    ) -> None:
        """One node holds both, which is what lets the structural arm see a
        flow with no callback edge to cross."""
        edges = _edges(tmp_path / "collapse", self._SRC)
        source_src = self._src_of(edges, ":onmessage:unresolved", "viaAssignment")
        sink_src = self._src_of(edges, ":exec:unresolved", "viaAssignment")
        assert source_src == sink_src

    def test_the_listener_sink_does_not(self, tmp_path: Path) -> None:
        """THE CONTROL that makes the assertion above mean something: the
        identical program written with ``addEventListener`` puts the sink on a
        different symbol, which is why it stays inert."""
        edges = _edges(tmp_path / "collapse2", self._SRC)
        source_src = self._src_of(edges, ":addEventListener:unresolved", "viaListener")
        sink_hits = [
            e for e in edges
            if e.edge_type == "calls" and e.dst.endswith(":exec:unresolved")
            and "_cb_addEventListener" in e.src
        ]
        assert len(sink_hits) == 1, [(e.src, e.dst) for e in edges]
        assert sink_hits[0].src != source_src
        assert any(
            e.edge_type == "references" and e.src == source_src
            and "_cb_addEventListener" in e.dst
            for e in edges
        ), "the callback should be linked by a references edge"
