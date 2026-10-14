# SPDX-License-Identifier: AGPL-3.0-or-later
"""A computed-property call (``obj['m'](x)``) emits a call edge (WI-vipos).

The call parses as a ``call_expression`` whose function is a
``subscript_expression``, and the member-call cascade in ``js_ts.py`` claimed
only ``member_expression`` callees, so ``ws['send'](x)`` produced NOTHING --
not the ``external`` placeholder an untyped ``obj.write(x)`` gets since
WI-nasuf, not a low-confidence edge. The call site was absent from the graph,
which a closed-world verdict over "the constructs the analysis emits call edges
for" silently excludes.

A STRING-LITERAL key names the property exactly as the dotted spelling does,
so it goes through the SAME cascade: ``ws['send'](x)`` and ``ws.send(x)`` emit
the same edge and reach the same row. A NON-LITERAL key (``obj[k](x)``) names
nothing; it still emits a call, to the ``external`` placeholder named
``<computed>`` (never a valid identifier, so it cannot collide with a real
method name) with ``call_construct="method"``, which both catalogue gates
refuse without a module -- a disclosed unknown, never a row.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _edges(root: Path, source: str, name: str = "app.js") -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(source)
    return analyze_javascript(root).edges


def _calls(edges: list[Edge]) -> list[Edge]:
    return [e for e in edges if e.edge_type == "calls"]


def _tagged(edges: list[Edge]) -> int:
    return tag_io_boundaries(edges, {"javascript": load_catalog("javascript")})


class TestLiteralKeyIsTheDottedCall:
    @pytest.mark.parametrize("callee", [
        "ws['send']", 'ws["send"]', "ws?.['send']", "ws[`send`]",
    ])
    def test_websocket_send(self, tmp_path: Path, callee: str) -> None:
        edges = _edges(tmp_path, (
            "function go(u, x) {\n"
            "  const ws = new WebSocket(u);\n"
            f"  {callee}(x);\n"
            "}\n"
        ))
        [edge] = _calls(edges)
        assert edge.dst == "javascript:WebSocket:0-0:send:unresolved"
        assert (edge.meta or {}).get("call_construct") == "method"
        assert _tagged(edges) == 1

    def test_dotted_control(self, tmp_path: Path) -> None:
        """The spelling the literal key must match, emitted by the old path."""
        edges = _edges(tmp_path, (
            "function go(u, x) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.send(x);\n"
            "}\n"
        ))
        assert [e.dst for e in _calls(edges)] == [
            "javascript:WebSocket:0-0:send:unresolved",
        ]

    def test_namespace_import_member(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, (
            "const fs = require('fs');\n"
            "function go(p) { return fs['readFileSync'](p); }\n"
        ))
        assert [e.dst for e in _calls(edges)] == [
            "javascript:fs:0-0:readFileSync:unresolved",
        ]
        assert _tagged(edges) == 1

    def test_this_member_resolves_in_repo(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, (
            "class C {\n"
            "  m() { return 1; }\n"
            "  n() { return this['m'](); }\n"
            "}\n"
        ))
        dsts = [e.dst for e in _calls(edges)]
        assert len(dsts) == 1 and dsts[0].endswith(":C.m:method"), dsts

    def test_untyped_receiver_gets_the_placeholder(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, "function go(obj, x) { obj['write'](x); }\n")
        assert [e.dst for e in _calls(edges)] == [
            "javascript:external:0-0:write:unresolved",
        ]


class TestNonLiteralKeyIsADisclosedUnknown:
    @pytest.mark.parametrize("callee", [
        "obj[k]", "obj?.[k]", "obj[`se${k}`]", "obj[0]", "obj['a' + k]",
    ])
    def test_emits_the_computed_placeholder(
        self, tmp_path: Path, callee: str,
    ) -> None:
        edges = _edges(tmp_path, f"function go(obj, k, x) {{ {callee}(x); }}\n")
        [edge] = _calls(edges)
        assert edge.dst == "javascript:external:0-0:<computed>:unresolved"
        assert (edge.meta or {}).get("call_construct") == "method"
        assert edge.is_resolved is False

    def test_it_never_reaches_a_row(self, tmp_path: Path) -> None:
        """Even on a receiver typed to a catalogue module: a key nobody can
        read cannot name the row."""
        edges = _edges(tmp_path, (
            "function go(u, k, x) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws[k](x);\n"
            "}\n"
        ))
        assert [e.dst for e in _calls(edges)] == [
            "javascript:external:0-0:<computed>:unresolved",
        ]
        assert _tagged(edges) == 0
