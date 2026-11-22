# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py
"""A WebSocket route the analyzer already marked is not materialized twice.

``materialize_route_symbols`` skips a ``route`` concept whose (method, path)
an analyzer's marker already holds. A transport marker (WebSocket) keeps its
sentinel in ``route_protocol`` and leaves ``http_method`` empty
(``make_route_symbol``, INV-tibap), so the dedupe read no method for it and
a ``route`` concept with method ``WS`` on the handler minted a second marker
at the handler's line. Found with the @fastify/websocket route
``fastify.get('/ws', { websocket: true }, onSocket)`` (WI-movur).
"""
from __future__ import annotations

from hypergumbo_core.analyze.base import make_route_symbol
from hypergumbo_core.framework_patterns import materialize_route_symbols
from hypergumbo_core.ir import Span, Symbol


def _handler(method: str, path: str) -> Symbol:
    return Symbol(
        id="javascript:server.js:18-18:onSocket:function",
        name="onSocket", kind="function", language="javascript", path="server.js",
        span=Span(start_line=18, end_line=18, start_col=0, end_col=0),
        meta={"concepts": [{"concept": "route", "method": method, "path": path}]},
    )


def _marker(method: str, path: str) -> Symbol:
    return make_route_symbol(
        language="javascript", path="server.js",
        span=Span(start_line=85, end_line=85, start_col=0, end_col=0),
        method=method, route_path=path, origin="javascript", origin_run_id="r",
        handler_ref="onSocket",
    )


def test_a_websocket_marker_suppresses_the_same_route_concept() -> None:
    marker = _marker("WS", "/ws")
    assert marker.meta["http_method"] is None  # the transport lives elsewhere
    assert materialize_route_symbols([marker, _handler("WS", "/ws")]) == []


def test_another_path_or_method_is_still_materialized() -> None:
    marker = _marker("WS", "/ws")
    assert len(materialize_route_symbols([marker, _handler("WS", "/other")])) == 1
    assert len(materialize_route_symbols([marker, _handler("GET", "/ws")])) == 1
