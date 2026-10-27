# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Unix-domain socket server is ``ipc_listen``, not ``net_listen`` (WI-gaziv).

ADR-0050 §1 names a row's primary boundary by the medium data crosses, and a
Unix socket's medium is local IPC. With no IPC-side deferred-crossing value,
Go's ``net.ListenUnix`` and Python's ``asyncio.start_unix_server`` were rowed
``net_listen`` and read as network ingress; the owner chose a new value
(2026-10-26). Through the production pipeline (``run_behavior_map`` then
``io-boundaries --format json``): each Unix listener is counted in
``ipc_listen_edges``, and its TCP twin in the same file stays in
``net_listen_edges`` -- the control that the move did not take the network
listeners with it.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from hypergumbo_core.cli import build_parser, cmd_io_boundaries, run_behavior_map

_GO = {
    "go.mod": "module example.com/srv\n\ngo 1.21\n",
    "main.go": (
        'package main\n\nimport "net"\n\n'
        "func serveUnix() error {\n"
        '\tl, err := net.ListenUnix("unix", &net.UnixAddr{Name: "/tmp/s", Net: "unix"})\n'
        "\tif err != nil {\n\t\treturn err\n\t}\n\treturn l.Close()\n}\n\n"
        "func serveUnixgram() error {\n"
        '\tc, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: "/tmp/g", Net: "unixgram"})\n'
        "\tif err != nil {\n\t\treturn err\n\t}\n\treturn c.Close()\n}\n\n"
        "func serveTCP() error {\n"
        '\tl, err := net.ListenTCP("tcp", nil)\n'
        "\tif err != nil {\n\t\treturn err\n\t}\n\treturn l.Close()\n}\n\n"
        "func main() { serveUnix(); serveUnixgram(); serveTCP() }\n"
    ),
}

_PY = {
    "srv.py": (
        "import asyncio\nimport socketserver\n\n\n"
        "async def handle(reader, writer):\n    writer.close()\n\n\n"
        "async def unix_main():\n"
        '    return await asyncio.start_unix_server(handle, path="/tmp/s")\n\n\n'
        "async def tcp_main():\n"
        '    return await asyncio.start_server(handle, "127.0.0.1", 8888)\n\n\n'
        "class Handler(socketserver.StreamRequestHandler):\n"
        "    def handle(self):\n        self.rfile.readline()\n\n\n"
        "def unix_stream():\n"
        '    server = socketserver.UnixStreamServer("/tmp/u", Handler)\n'
        "    server.serve_forever()\n\n\n"
        "def tcp_stream():\n"
        '    server = socketserver.TCPServer(("127.0.0.1", 9999), Handler)\n'
        "    server.serve_forever()\n"
    ),
}


def _io_boundaries(repo: Path, files: dict[str, str]) -> dict:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    map_file = repo / "map.json"
    run_behavior_map(repo_root=repo, out_path=map_file, include_sketch_precomputed=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        args = build_parser().parse_args([
            "io-boundaries", str(repo), "--input", str(map_file), "--format", "json",
        ])
        assert cmd_io_boundaries(args) == 0
    return json.loads(buf.getvalue())


def _primitives(data: dict, boundary: str) -> set[str]:
    entry = data["boundaries"].get(boundary, {"chains": []})
    return {chain["primitive"] for chain in entry["chains"]}


def test_go_unix_listeners_are_ipc_listen(tmp_path: Path) -> None:
    data = _io_boundaries(tmp_path, _GO)
    assert _primitives(data, "ipc_listen") == {"net.ListenUnix", "net.ListenUnixgram"}
    assert _primitives(data, "net_listen") == {"net.ListenTCP"}
    assert (data["ipc_listen_edges"], data["net_listen_edges"]) == (2, 1)
    assert data["total_io_edges"] == 0


def test_python_unix_servers_are_ipc_listen(tmp_path: Path) -> None:
    data = _io_boundaries(tmp_path, _PY)
    assert _primitives(data, "ipc_listen") == {
        "asyncio.start_unix_server", "socketserver.UnixStreamServer.serve_forever",
    }
    assert _primitives(data, "net_listen") == {
        "asyncio.start_server", "socketserver.TCPServer.serve_forever",
    }
    assert (data["ipc_listen_edges"], data["net_listen_edges"]) == (2, 2)
