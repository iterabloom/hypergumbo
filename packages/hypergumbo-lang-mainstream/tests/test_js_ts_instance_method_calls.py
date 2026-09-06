# SPDX-License-Identifier: AGPL-3.0-or-later
"""A JavaScript instance-method call on an external receiver emits a call edge
(WI-nasuf's javascript cell), and a ``new``-constructed receiver carries its
constructor's module into the slot (INV-misup).

INV-misup. The analyzer resolved a member call only when the receiver was a
NAMED GLOBAL whose name is the module (``localStorage.setItem``). A receiver
bound by construction — ``ws = new WebSocket(u); ws.send(x)`` — landed
``WebSocket`` in ``var_types``, the in-repo lookup for ``WebSocket.send``
missed, and nothing was emitted: in JS or TS, at module or function scope,
called or assigned. Eleven catalogue rows across WebSocket, XMLHttpRequest,
EventSource and BroadcastChannel were inert, and the whole XHR egress surface
was empty. ``js_ts.py``'s own ``JS_KNOWN_GLOBALS`` comment assumed the
opposite.

WI-nasuf. An UNTYPED receiver (``obj.write(x)``) emitted nothing either,
while ``helper(x)``, ``fs.readFileSync(x)`` and ``console.log(x)`` in the
same function all emitted. python, java, go, rust, scala and swift emit the
``external`` placeholder with ``call_construct: method`` for exactly this
case; javascript now does too, and ``analyzer_disclosure`` flips its dated
declaration so a clean verdict stops carrying the ``analyzer_method_call_blind``
caveat and starts carrying the per-site ``untyped_receiver`` one instead.

The module slot for a constructed receiver is the constructor's own path:
a catalogued global constructor (``WebSocket``), or a namespace-imported
class (``new net.Socket()`` → ``net.Socket``, which the catalogue keys).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _edges(root: Path, source: str, name: str = "app.js") -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(source)
    return analyze_javascript(root).edges


def _call(edges: list[Edge], method: str) -> Edge:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.dst.endswith(f":{method}:unresolved")
    ]
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return hits[0]


def _tagged(edges: list[Edge]) -> int:
    return tag_io_boundaries(edges, {"javascript": load_catalog("javascript")})


class TestConstructedReceiverCarriesItsModule:
    def test_websocket_send_at_function_scope(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "ws",
            "function go(u, x) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.send(x);\n"
            "}\n",
        )
        edge = _call(edges, "send")
        assert edge.dst == "javascript:WebSocket:0-0:send:unresolved", edge.dst
        assert (edge.meta or {}).get("call_construct") == "method"
        assert (edge.meta or {}).get("receiver_type_hint") == "WebSocket"
        assert _tagged(edges) >= 1

    def test_websocket_at_module_scope_and_xhr(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "mod",
            "const ws = new WebSocket('wss://h');\n"
            "ws.addEventListener('message', cb);\n"
            "const x = new XMLHttpRequest();\n"
            "x.open('POST', '/u');\n"
            "x.send(body);\n",
        )
        assert _call(edges, "addEventListener").dst == (
            "javascript:WebSocket:0-0:addEventListener:unresolved"
        )
        assert _call(edges, "open").dst == "javascript:XMLHttpRequest:0-0:open:unresolved"
        assert _call(edges, "send").dst == "javascript:XMLHttpRequest:0-0:send:unresolved"
        assert _tagged(edges) >= 3

    def test_inline_construction_of_a_bare_global(self, tmp_path: Path) -> None:
        """``new XMLHttpRequest().open(..)`` -- the receiver IS the construction."""
        edges = _edges(
            tmp_path / "inl",
            "function go(u) {\n"
            "  new XMLHttpRequest().open('GET', u);\n"
            "}\n",
        )
        edge = _call(edges, "open")
        assert edge.dst == "javascript:XMLHttpRequest:0-0:open:unresolved", edge.dst
        assert (edge.meta or {}).get("call_construct") == "method"
        assert _tagged(edges) >= 1

    def test_typescript_annotated_binding(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "ts",
            "function go(u: string, x: string) {\n"
            "  const ws: WebSocket = new WebSocket(u);\n"
            "  ws.send(x);\n"
            "}\n",
            name="app.ts",
        )
        # A ``.ts`` file emits under the ``typescript`` language prefix.
        assert _call(edges, "send").dst == "typescript:WebSocket:0-0:send:unresolved"

    def test_typescript_declared_parameter_of_a_catalogued_type(
        self, tmp_path: Path,
    ) -> None:
        """``function go(ws: WebSocket)`` -- the declaration is evidence (INV-vugon)."""
        edges = _edges(
            tmp_path / "tsp",
            "function go(ws: WebSocket, x: string) {\n"
            "  ws.send(x);\n"
            "}\n",
            name="app.ts",
        )
        edge = _call(edges, "send")
        assert edge.dst == "typescript:WebSocket:0-0:send:unresolved", edge.dst
        assert (edge.meta or {}).get("receiver_type_hint") == "WebSocket"

    def test_namespace_imported_class_keeps_its_module(self, tmp_path: Path) -> None:
        """``new net.Socket()`` is ``net.Socket``, the row the catalogue keys."""
        edges = _edges(
            tmp_path / "net",
            "const net = require('net');\n"
            "function go(d) {\n"
            "  const s = new net.Socket();\n"
            "  s.write(d);\n"
            "  new net.Socket().write(d);\n"
            "}\n",
        )
        writes = sorted(
            e.dst for e in edges
            if e.edge_type == "calls" and e.dst.endswith(":write:unresolved")
        )
        assert writes == [
            "javascript:net.Socket:0-0:write:unresolved",
            "javascript:net.Socket:0-0:write:unresolved",
        ], writes

    def test_an_inline_construction_under_an_unimported_namespace_asserts_nothing(
        self, tmp_path: Path,
    ) -> None:
        """``new foo.Socket().write(d)`` with no ``foo`` import: placeholder, no module."""
        edges = _edges(
            tmp_path / "unimp",
            "function go(foo, d) {\n"
            "  new foo.Socket().write(d);\n"
            "}\n",
        )
        edge = _call(edges, "write")
        assert edge.dst == "javascript:external:0-0:write:unresolved", edge.dst
        assert _tagged(edges) == 0

    def test_a_project_class_still_resolves_in_repo(self, tmp_path: Path) -> None:
        root = tmp_path / "proj"
        root.mkdir()
        (root / "client.js").write_text(
            "class Client {\n  send(x) { return x; }\n}\nmodule.exports = { Client };\n"
        )
        (root / "app.js").write_text(
            "const { Client } = require('./client');\n"
            "function go(x) {\n"
            "  const c = new Client();\n"
            "  c.send(x);\n"
            "}\n",
        )
        edges = analyze_javascript(root).edges
        sends = [e for e in edges if e.edge_type == "calls" and "send" in e.dst]
        assert sends, [e.dst for e in edges]
        assert all(e.is_resolved for e in sends), [e.dst for e in sends]


class TestUntypedReceiverEmitsThePlaceholder:
    def test_obj_write_emits_beside_its_siblings(self, tmp_path: Path) -> None:
        """WI-nasuf's own fixture: three sibling shapes emitted, the fourth did not."""
        edges = _edges(
            tmp_path / "u",
            "const fs = require('fs');\n"
            "function helper(x) { return x; }\n"
            "function go(obj, x) {\n"
            "  helper(x);\n"
            "  fs.readFileSync(x);\n"
            "  console.log(x);\n"
            "  obj.write(x);\n"
            "}\n",
        )
        edge = _call(edges, "write")
        assert edge.dst == "javascript:external:0-0:write:unresolved", edge.dst
        assert (edge.meta or {}).get("call_construct") == "method"
        assert (edge.meta or {}).get("receiver_type_hint") is None

    def test_a_call_or_subscript_receiver_emits_too(self, tmp_path: Path) -> None:
        """``f().m()`` and ``a[i].m()``: a receiver the analyzer cannot name is still a receiver."""
        edges = _edges(
            tmp_path / "expr",
            "function go(f, a, i, x) {\n"
            "  f().write(x);\n"
            "  a[i].send(x);\n"
            "  (a).flush(x);\n"
            "}\n",
        )
        for m in ("write", "send", "flush"):
            edge = _call(edges, m)
            assert edge.dst == f"javascript:external:0-0:{m}:unresolved", edge.dst
            assert (edge.meta or {}).get("call_construct") == "method"
            assert (edge.meta or {}).get("receiver_type_hint") is None

    def test_this_receiver_keeps_its_site_one_path(self, tmp_path: Path) -> None:
        """``this.m()`` that does not resolve is Site-1 territory, not a placeholder."""
        edges = _edges(
            tmp_path / "this",
            "class A {\n"
            "  go(x) {\n"
            "    this.missing(x);\n"
            "  }\n"
            "}\n",
        )
        hits = [e for e in edges if e.edge_type == "calls" and e.dst.endswith(":missing:unresolved")]
        assert hits == [], [e.dst for e in hits]

    def test_an_untyped_placeholder_reaches_no_row(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "n",
            "function go(obj, x) {\n"
            "  obj.write(x);\n"
            "}\n",
        )
        assert _tagged(edges) == 0

    def test_the_declaration_flips(self) -> None:
        from hypergumbo_core.analyzer_disclosure import emits_external_method_call_edges

        assert emits_external_method_call_edges("javascript") is True


class TestHandlerAssignmentEmitsNoEdge:
    """WI-zumoz. ``ws.onmessage = handler`` is a property ASSIGNMENT, not a
    call, so no call edge exists for the method-kind rows ``WebSocket.onmessage``,
    ``WebSocket.onclose`` and ``EventSource.onmessage`` to match. The
    limitation is DECLARED in ``hypergumbo_core.analyzer_disclosure``
    (``CONSTRUCT_BLIND_ROWS``, dated, disclosed on a clean verdict). This test
    pins it so it cannot change silently: when the analyzer starts emitting a
    registration edge for the assignment it fails, and the declaration must
    flip in the same change.
    """

    def test_assignment_emits_nothing_while_the_call_on_the_same_receiver_emits(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(
            tmp_path / "handlers",
            "function listen(u) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.onmessage = function (ev) { return ev.data; };\n"
            "  ws.onclose = function (ev) { return ev.code; };\n"
            "  ws.addEventListener('message', function (ev) { return ev.data; });\n"
            "  const es = new EventSource(u);\n"
            "  es.onmessage = (ev) => ev.data;\n"
            "}\n",
        )
        calls = {e.dst for e in edges if e.edge_type == "calls"}
        assert calls == {"javascript:WebSocket:0-0:addEventListener:unresolved"}, calls
        assert _tagged(edges) == 1
