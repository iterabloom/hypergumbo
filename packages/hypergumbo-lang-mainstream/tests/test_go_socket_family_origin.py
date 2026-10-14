# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-potog: a Go connection's NETWORK decides its target kind (WI-baran's Go twin).

THE GAP. go.py stamped every ``net.Dial`` / ``net.DialTimeout`` connection
``io_target_kind=net_stream`` and every ``ln.Accept()`` connection the same,
so a Unix-domain socket was a network crossing. podman's
``pkg/machine/shim/claim_darwin.go`` dials ``net.DialTimeout("unix",
helperSock, ...)`` and ``fmt.Fprintln(con, "GO")`` reached io-boundaries as
``net_send`` and taint as the ``network`` zone. An AF_UNIX socket's far end is
another process on this host: audit-findings 0021 files that as ``ipc``.

THE MECHANISM IS WI-baran'S, not a new one. c.py decides a descriptor by the
FAMILY it was created with (``socket(AF_UNIX, ..)`` -> ``pipe``,
``socket(AF_INET, ..)`` -> ``net_stream``) and follows ``accept`` one hop to
the listener's family. Go spells the family as the network-name STRING that
``net.Dial`` / ``net.DialTimeout`` / ``net.Listen`` / ``net.ListenPacket``
take first (``"unix"``, ``"unixgram"``, ``"unixpacket"`` vs ``"tcp*"``,
``"udp*"``, ``"ip*"``), and fixes it in the function name for
``net.DialUnix`` / ``net.ListenUnix`` / ``net.ListenUnixgram``. The stamp
vocabulary, the dual catalogue rows and their selection are unchanged.

A NETWORK THAT IS NOT A LITERAL keeps TODAY'S answer, ``net_stream``. This is
the Go form of the C rows' ``abstains_to: net_*``: Go's stamp IS the row
selection, so stamping nothing would move a dial's ``fmt.Fprint*`` to its
``logging`` fallback -- a change nobody measured -- rather than keep it where
it classified before. The same default already stands for a declared
``net.Conn`` of unknown family. Pinned below so the choice is visible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.go import (
    _go_socket_family_kind,
    _go_typed_target_kind,
    analyze_go,
)


@pytest.fixture()
def go_available():
    """Skip ONLY when the Go grammar is genuinely absent (see the sibling files)."""
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_go"):
        pytest.skip("Go tree-sitter grammar not installed")


_PRELUDE = (
    "package main\n\n"
    'import (\n\t"bufio"\n\t"fmt"\n\t"io"\n\t"net"\n\t"time"\n)\n\n'
)


def _edges(tmp_path: Path, body: str):
    (tmp_path / "m.go").write_text(_PRELUDE + body, encoding="utf-8")
    result = analyze_go(tmp_path)
    assert not result.skipped
    return result.edges


def _calls(edges, name: str, module: str):
    return [
        e for e in edges
        if e.edge_type == "calls"
        and e.dst.startswith(f"go:{module}:")
        and e.dst.split(":")[3] == name
    ]


def _stamps(edges, name: str, module: str = "fmt") -> list[str]:
    """Every stamp on the named call -- ASSERTS REACH first: the call exists."""
    calls = _calls(edges, name, module)
    assert calls, f"fixture never reached {module}.{name}"
    return [(e.meta or {}).get("io_target_kind") for e in calls]


class TestTheDialNetworkDecides:
    def test_podmans_unix_dial_timeout_is_a_pipe(self, tmp_path, go_available) -> None:
        """The filed instance's shape: claim_darwin.go."""
        edges = _edges(tmp_path, """
func claim(helperSock string) {
\tcon, _ := net.DialTimeout("unix", helperSock, time.Second)
\tfmt.Fprintln(con, "GO")
}
""")
        assert _stamps(edges, "Fprintln") == ["pipe"]

    @pytest.mark.parametrize("network", ["unix", "unixgram", "unixpacket"])
    def test_every_unix_network_is_a_pipe(
        self, tmp_path, go_available, network: str,
    ) -> None:
        edges = _edges(tmp_path, f"""
func f(p string) {{
\tc, _ := net.Dial("{network}", p)
\tio.WriteString(c, "x")
}}
""")
        assert _stamps(edges, "WriteString", "io") == ["pipe"]

    def test_a_raw_string_network_is_read_too(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
func f(p string) {
\tc, _ := net.Dial(`unix`, p)
\tfmt.Fprintln(c, "x")
}
""")
        assert _stamps(edges, "Fprintln") == ["pipe"]

    def test_a_tcp_dial_is_still_a_network_stream(self, tmp_path, go_available) -> None:
        """The control: the same shape over ``"tcp"`` must NOT move."""
        edges = _edges(tmp_path, """
func f(a string) {
\tc, _ := net.Dial("tcp", a)
\tfmt.Fprintln(c, "x")
}
""")
        assert _stamps(edges, "Fprintln") == ["net_stream"]

    def test_a_variable_network_keeps_todays_answer(
        self, tmp_path, go_available,
    ) -> None:
        """NOT A LITERAL -> ``net_stream``, exactly what it stamped before WI-potog."""
        edges = _edges(tmp_path, """
func f(network, a string) {
\tc, _ := net.Dial(network, a)
\tfmt.Fprintln(c, "x")
}
""")
        assert _stamps(edges, "Fprintln") == ["net_stream"]

    def test_a_rebound_connection_keeps_its_family(
        self, tmp_path, go_available,
    ) -> None:
        """Two binding hops (``w := c`` <- the dial) still reach the network name."""
        edges = _edges(tmp_path, """
func f(p string) {
\tc, _ := net.Dial("unix", p)
\tw := c
\tfmt.Fprintln(w, "x")
}
""")
        assert _stamps(edges, "Fprintln") == ["pipe"]

    def test_the_last_binding_wins(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
func f(p, a string) {
\tc, _ := net.Dial("unix", p)
\tc, _ = net.Dial("tcp", a)
\tfmt.Fprintln(c, "x")
}
""")
        assert _stamps(edges, "Fprintln") == ["net_stream"]

    def test_dial_unix_is_a_pipe(self, tmp_path, go_available) -> None:
        """The family is in the NAME: before WI-potog this stamped nothing (logging)."""
        edges = _edges(tmp_path, """
func f(addr *net.UnixAddr) {
\tc, _ := net.DialUnix("unix", nil, addr)
\tfmt.Fprintln(c, "x")
}
""")
        assert _stamps(edges, "Fprintln") == ["pipe"]

    def test_a_unix_conn_parameter_is_a_pipe(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
func f(c *net.UnixConn) {
\tfmt.Fprintln(c, "x")
}
""")
        assert _stamps(edges, "Fprintln") == ["pipe"]


class TestTheAcceptHopReadsTheListener:
    """c.py's one ``accept`` hop to the listener's family, in Go."""

    def test_a_unix_listeners_connection_is_a_pipe(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
func f(p string) {
\tln, _ := net.Listen("unixpacket", p)
\tc, _ := ln.Accept()
\tr := bufio.NewReader(c)
\tline, _ := r.ReadString('\\n')
\tfmt.Fprintln(c, line)
}
""")
        assert _stamps(edges, "Fprintln") == ["pipe"]
        assert _stamps(edges, "NewReader", "bufio") == ["pipe"]
        assert _stamps(edges, "ReadString", "bufio") == ["pipe"]

    def test_a_listen_unix_listeners_connection_is_a_pipe(
        self, tmp_path, go_available,
    ) -> None:
        edges = _edges(tmp_path, """
func f(addr *net.UnixAddr) {
\tln, _ := net.ListenUnix("unix", addr)
\tc, _ := ln.Accept()
\tfmt.Fprintln(c, "x")
}
""")
        assert _stamps(edges, "Fprintln") == ["pipe"]

    def test_a_tcp_listeners_connection_is_a_network_stream(
        self, tmp_path, go_available,
    ) -> None:
        edges = _edges(tmp_path, """
func f(a string) {
\tln, _ := net.Listen("tcp", a)
\tc, _ := ln.Accept()
\tfmt.Fprintln(c, "x")
}
""")
        assert _stamps(edges, "Fprintln") == ["net_stream"]

    def test_a_variable_network_listener_keeps_todays_answer(
        self, tmp_path, go_available,
    ) -> None:
        edges = _edges(tmp_path, """
func f(network, a string) {
\tln, _ := net.Listen(network, a)
\tc, _ := ln.Accept()
\tfmt.Fprintln(c, "x")
}
""")
        assert _stamps(edges, "Fprintln") == ["net_stream"]

    def test_a_listener_parameter_keeps_todays_answer(
        self, tmp_path, go_available,
    ) -> None:
        """No binding for ``ln``: INV-bagok's caddy case is unchanged."""
        edges = _edges(tmp_path, """
func f(ln net.Listener) {
\tc, _ := ln.Accept()
\tfmt.Fprintln(c, "x")
}
""")
        assert _stamps(edges, "Fprintln") == ["net_stream"]


class TestTheFamilyReader:
    """The text-level reader both hops share."""

    @pytest.mark.parametrize(("text", "kind"), [
        ('net.Dial("unix", p)', "pipe"),
        ('net.DialTimeout("unixgram", p, d)', "pipe"),
        ('net.Listen("unixpacket", p)', "pipe"),
        ('net.ListenPacket("unixgram", p)', "pipe"),
        ("net.DialUnix(\"unix\", nil, a)", "pipe"),
        ("net.ListenUnix(\"unix\", a)", "pipe"),
        ("net.ListenUnixgram(\"unixgram\", a)", "pipe"),
        ('net.Dial("tcp", a)', "net_stream"),
        ('net.Dial("tcp6", a)', "net_stream"),
        ('net.Listen("udp4", a)', "net_stream"),
        ('net.Dial("ip4:icmp", a)', "net_stream"),
        ('net.DialTCP("tcp", nil, a)', "net_stream"),
        ('net.ListenUDP("udp", a)', "net_stream"),
    ])
    def test_a_decided_family(self, text: str, kind: str) -> None:
        assert _go_socket_family_kind(text) == kind

    @pytest.mark.parametrize("text", [
        "net.Dial(network, a)",          # a variable: undecided
        'net.Dial("fd", a)',             # not a network Go knows
        "net.Dial(",                     # truncated text
        "ln.Accept()",                   # not a producer at all
        'mynet.Dial("unix", p)',         # another package's Dial
        'tls.Dial("unix", p, cfg)',      # out of scope, disclosed
    ])
    def test_an_undecided_family_is_none(self, text: str) -> None:
        assert _go_socket_family_kind(text) is None

    def test_the_unix_conn_type_is_a_pipe(self, go_available) -> None:
        assert _go_typed_target_kind("net.UnixConn", {"net": "net"}) == "pipe"


class TestTheStampReachesTheCatalogue:
    """End to end through production's classifier, with the shipped go catalogue."""

    def _classified(self, tmp_path, body: str, name: str, module: str):
        from hypergumbo_core.io_boundary import classify_call, load_catalog

        edges = _edges(tmp_path, body)
        (edge,) = _calls(edges, name, module)
        prim = classify_call(
            {"go": load_catalog("go")}, edge.dst, edge.meta, dst_ref=edge.dst_ref,
        )
        return None if prim is None else (prim.boundary, prim.module, prim.name)

    def test_a_print_to_a_unix_dial_is_an_ipc_send(self, tmp_path, go_available) -> None:
        got = self._classified(tmp_path, """
func claim(p string) {
\tcon, _ := net.DialTimeout("unix", p, time.Second)
\tfmt.Fprintln(con, "GO")
}
""", "Fprintln", "fmt")
        assert got == ("ipc_send", "fmt", "Fprintln")

    def test_a_print_to_a_tcp_dial_is_a_network_send(self, tmp_path, go_available) -> None:
        got = self._classified(tmp_path, """
func f(a string) {
\tcon, _ := net.Dial("tcp", a)
\tfmt.Fprintln(con, "GO")
}
""", "Fprintln", "fmt")
        assert got == ("net_send", "fmt", "Fprintln")

    def test_a_read_from_a_unix_listener_is_an_ipc_receive(
        self, tmp_path, go_available,
    ) -> None:
        got = self._classified(tmp_path, """
func f(p string) {
\tln, _ := net.Listen("unix", p)
\tc, _ := ln.Accept()
\tr := bufio.NewReader(c)
\tline, _ := r.ReadString('\\n')
\t_ = line
}
""", "ReadString", "bufio")
        assert got == ("ipc_recv", "bufio.Reader", "ReadString")
