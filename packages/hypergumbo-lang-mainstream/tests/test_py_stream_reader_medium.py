# SPDX-License-Identifier: AGPL-3.0-or-later
"""asyncio.StreamReader's reads cross the medium the reader was made on (WI-foloh).

``asyncio.StreamReader.read*`` was rowed ``net_recv`` only, but one
StreamReader class is fed by a TCP socket (``open_connection``), an AF_UNIX
socket (``open_unix_connection``) or a child's pipe (``create_subprocess_*``):
whether the bytes came from the network or another local process is a fact of
the CALL that made the reader. WI-himim made py.py stamp it
(``io_target_kind``: ``net_stream`` / ``pipe``); this is the row that reads the
stamp -- WI-kalol's ``net.Conn`` dual-row shape: the ``net_recv`` row and an
``ipc_recv`` twin, both ``call_site_undecidable``, abstaining to ``net_recv``,
so an unstamped reader (an annotated parameter) is still a network read.

ADR-0049 ruling 3 at the finding level: both boundaries derive
``untrusted_input``, so no taint source is lost; what moves is WHICH claim a
pipe read meets (an "never receives from the network" claim is no longer
violated by a child's stdout).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.io_boundary import boundaries_of, load_catalog, tag_io_boundaries
from hypergumbo_core.taint import AUTO_SOURCE_LABEL_MAP
from hypergumbo_lang_mainstream.py import analyze_python


def _read_boundaries(tmp_path: Path, source: str) -> dict[str, list[str]]:
    """{caller function: boundaries of its StreamReader read edge}."""
    (tmp_path / "app.py").write_text(source)
    result = analyze_python(tmp_path)
    tag_io_boundaries(result.edges, {"python": load_catalog("python")})
    by_id = {s.id: s for s in result.symbols}
    return {
        by_id[e.src].name: boundaries_of(e.meta)
        for e in result.edges
        if e.edge_type == "calls" and "asyncio.StreamReader" in e.dst and e.src in by_id
    }


_SOURCE = (
    "import asyncio\n\n\n"
    "async def tcp(host):\n"
    "    reader, writer = await asyncio.open_connection(host, 80)\n"
    "    return await reader.read(100)\n\n\n"
    "async def unix(path):\n"
    "    reader, writer = await asyncio.open_unix_connection(path)\n"
    "    return await reader.readline()\n\n\n"
    "async def child(cmd):\n"
    "    proc = await asyncio.create_subprocess_exec(cmd, stdout=asyncio.subprocess.PIPE)\n"
    "    return await proc.stdout.readexactly(4)\n\n\n"
    "async def given(reader: asyncio.StreamReader):\n"
    "    return await reader.readuntil(b'\\n')\n"
)


def test_a_tcp_reader_is_a_network_read(tmp_path: Path) -> None:
    assert _read_boundaries(tmp_path, _SOURCE)["tcp"] == ["net_recv"]


def test_a_unix_socket_reader_is_ipc(tmp_path: Path) -> None:
    assert _read_boundaries(tmp_path, _SOURCE)["unix"] == ["ipc_recv"]


def test_a_child_s_stdout_is_ipc(tmp_path: Path) -> None:
    assert _read_boundaries(tmp_path, _SOURCE)["child"] == ["ipc_recv"]


def test_an_unstamped_reader_abstains_to_the_network(tmp_path: Path) -> None:
    """An annotated parameter came from anywhere: today's answer, net_recv."""
    assert _read_boundaries(tmp_path, _SOURCE)["given"] == ["net_recv"]


def test_both_media_are_untrusted_input() -> None:
    """ADR-0049 ruling 3: the move loses no taint source."""
    assert AUTO_SOURCE_LABEL_MAP["net_recv"] == AUTO_SOURCE_LABEL_MAP["ipc_recv"] == "untrusted_input"
