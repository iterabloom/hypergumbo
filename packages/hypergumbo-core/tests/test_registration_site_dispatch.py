# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/_registration_sites.py
"""A registration site's node dispatches to the function the call hands over.

The IPC linker mints ``ipc:receive:<channel>`` for ``ipcMain.handle('ch', h)``
and the WebSocket linker ``ws:connection`` for ``io.on('connection', h)``;
neither linked the node to ``h`` (WI-pahom, WI-movur). Each now emits a
``dispatches_to`` edge from the node to the function a
``callback_argument_reference`` at the site's file and line points at,
preferring the one the framework row stamped with the site's concept.

The linkers read files, and their context's symbols and edges stand for the
JS/TS analyzer's output (this package's tests cannot run the analyzer): a
function symbol per handler, and the ``references`` edge the analyzer emits
for a function passed as an argument, at the call's line.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.ipc import ipc_linker
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_core.linkers.websocket import websocket_linker


def _function(path: str, line: int, name: str, concept: str | None = None) -> Symbol:
    meta = {"concepts": [{"concept": concept, "framework": "x"}]} if concept else {}
    return Symbol(
        id=f"javascript:{path}:{line}-{line}:{name}:function", name=name, kind="function",
        language="javascript", path=path, span=Span(line, line, 0, 0),
        origin="javascript", origin_run_id="r", meta=meta,
    )


def _passed(src: Symbol, dst: Symbol, line: int) -> Edge:
    return Edge.create(
        src=src.id, dst=dst.id, edge_type="references", line=line,
        origin="javascript", origin_run_id="r",
        evidence_type="callback_argument_reference",
    )


def _dispatches(result) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    names = {s.id: s.name for s in result.symbols}
    for edge in result.edges:
        if edge.edge_type == "dispatches_to":
            out.setdefault(names[edge.src], set()).add(edge.dst)
    return out


def _write(tmp_path: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (tmp_path / name).write_text(text)


MAIN = (
    "const { ipcMain } = require('electron');\n"           # 1
    "ipcMain.handle('settings:read', readSettings);\n"     # 2
    "ipcMain.on('log', (e, m) => xs.map(fmt));\n"          # 3
    "ipcMain.on('a', h1); ipcMain.on('b', h2);\n"          # 4
    "ipcMain.on('two', pick(left, right));\n"              # 5
)
PRELOAD = (
    "const { ipcRenderer } = require('electron');\n"
    "ipcRenderer.invoke('settings:read');\n"
    "ipcRenderer.send('log');\n"
    "ipcRenderer.send('a');\n"
    "ipcRenderer.send('b');\n"
    "ipcRenderer.send('two');\n"
)


class TestIpcReceiveSites:
    def _context(self, tmp_path: Path) -> LinkerContext:
        _write(tmp_path, {"main.js": MAIN, "preload.js": PRELOAD})
        module = _function("main.js", 1, "main.js")
        read = _function("main.js", 10, "readSettings")
        log_cb = _function("main.js", 3, "_cb_on", concept="ipc_handler")
        fmt = _function("main.js", 11, "fmt")
        # Both tagged: the row stamped each, yet the line cannot say which
        # function serves which of its two channels.
        h1 = _function("main.js", 12, "h1", concept="ipc_handler")
        h2 = _function("main.js", 13, "h2", concept="ipc_handler")
        left, right = _function("main.js", 14, "left"), _function("main.js", 15, "right")
        symbols = [module, read, log_cb, fmt, h1, h2, left, right]
        edges = [
            _passed(module, read, 2),
            _passed(module, log_cb, 3),
            _passed(log_cb, fmt, 3),       # passed inside the handler's body
            _passed(module, h1, 4),
            _passed(module, h2, 4),
            _passed(module, left, 5),
            _passed(module, right, 5),
            # Another kind of edge at a site's line is not read.
            Edge.create(
                src=module.id, dst=fmt.id, edge_type="calls", line=2,
                origin="javascript", origin_run_id="r",
            ),
        ]
        return LinkerContext(repo_root=tmp_path, symbols=symbols, edges=edges)

    def test_a_receive_site_dispatches_to_its_handler(self, tmp_path: Path) -> None:
        ctx = self._context(tmp_path)
        result = ipc_linker(ctx)
        dispatch = _dispatches(result)
        assert dispatch["ipc:receive:settings:read"] == {
            "javascript:main.js:10-10:readSettings:function"}
        edge = next(
            e for e in result.edges
            if e.edge_type == "dispatches_to" and e.dst.endswith(":readSettings:function")
        )
        reference = next(e for e in ctx.edges if e.dst == edge.dst)
        assert edge.meta["dispatch_kind"] == "listener"
        assert edge.meta["channel_kind"] == "ipc"
        assert edge.derived_from == [edge.src, edge.dst, reference.id]

    def test_the_tagged_outermost_function_of_a_line(self, tmp_path: Path) -> None:
        dispatch = _dispatches(ipc_linker(self._context(tmp_path)))
        assert dispatch["ipc:receive:log"] == {"javascript:main.js:3-3:_cb_on:function"}

    def test_an_ambiguous_line_links_nothing(self, tmp_path: Path) -> None:
        dispatch = _dispatches(ipc_linker(self._context(tmp_path)))
        # Two receive sites on one line, and one site passing two untagged
        # functions: which function serves the channel is not known.
        assert "ipc:receive:a" not in dispatch
        assert "ipc:receive:b" not in dispatch
        assert "ipc:receive:two" not in dispatch


SERVER = (
    "const io = require('socket.io')(3000);\n"                  # 1
    "io.on('connection', onConnection);\n"                      # 2
    "io.of('/admin').on('connection', onAdmin);\n"              # 3
    "process.on('exit', onExit);\n"                             # 4
    "io.on('connection', (s) => { s.on('ping', onPing); });\n"  # 5
)


class TestWebSocketEndpoints:
    def test_a_connection_endpoint_dispatches_to_its_handler(self, tmp_path: Path) -> None:
        _write(tmp_path, {"server.js": SERVER})
        module = _function("server.js", 1, "server.js")
        on_connection = _function("server.js", 10, "onConnection", "websocket_handler")
        on_admin = _function("server.js", 11, "onAdmin", "websocket_handler")
        on_exit = _function("server.js", 12, "onExit")
        # Line 5: the connection callback, and inside it a socket handler;
        # the row stamped both, the endpoint's handler is the outer one.
        outer = _function("server.js", 5, "_cb_on", "websocket_handler")
        on_ping = _function("server.js", 13, "onPing", "websocket_handler")
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[module, on_connection, on_admin, on_exit, outer, on_ping],
            edges=[
                _passed(module, on_connection, 2),
                _passed(module, on_admin, 3),
                _passed(module, on_exit, 4),
                _passed(module, outer, 5),
                _passed(outer, on_ping, 5),
            ],
        )
        result = websocket_linker(ctx)
        targets = {
            (s.span.start_line, e.dst)
            for e in result.edges if e.edge_type == "dispatches_to"
            for s in result.symbols if s.id == e.src
        }
        assert targets == {(2, on_connection.id), (3, on_admin.id), (5, outer.id)}
        edge = next(e for e in result.edges if e.edge_type == "dispatches_to")
        assert edge.meta["channel_kind"] == "websocket"
