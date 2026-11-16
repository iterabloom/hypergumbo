# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Go reader wrapper's boundary is its ARGUMENT'S ORIGIN (WI-lipis).

`bufio.{NewScanner,NewReader}` wrap an arbitrary `io.Reader`, so which boundary
a read crosses is decided by the ARGUMENT, which no catalogue row can see — a
row sees only the CALLEE. `go.py` therefore stamps the argument's kind as
`io_target_kind` (`os.Stdin` -> `std_stream`, an `os.Open` handle ->
`host_path`, `strings.NewReader` -> `in_memory`, a `net.Conn` ->
`net_stream`), and the catalogue's dual bufio rows narrow on it. An unstamped
call falls back to `ipc_recv`, which is in `AUTO_SOURCE_LABEL_MAP` and mints an
`untrusted_input` source.

These tests cover an argument that is a bare LOCAL, whose origin is the binding
that reaches the call. Its commonest origin is a file handle, `f, _ :=
os.Open(p)` — a host-filesystem read, not stdin, and not a source.

WHY THE ORIGIN LOOKUP IS SYNTACTIC HERE AND NOT A SECOND DATAFLOW ENGINE. The
analyzer runs before any DDG exists, so it cannot ask the reaching-def solver.
What it CAN see is the enclosing function's own text:
`f, _ := os.Open(p)` five lines above `bufio.NewScanner(f)`. This resolves the
LAST binding of the name before the use, in the enclosing function only, and
abstains on everything else — a deliberately smaller instrument than the DDG,
whose answers are a subset of the DDG's and never a superset.

ONE ANSWER PER CALL SITE, OR NONE (INV-zumin). A bare local whose binding this
cannot find stamps NOTHING and takes the unstamped fallback. A stamp can
REMOVE a source, so every case below that stops minting is paired with a
control that must keep minting.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.go import analyze_go

_PRELUDE = (
    "package main\n\n"
    'import (\n\t"bufio"\n\t"net"\n\t"net/http"\n'
    '\t"os"\n\t"strings"\n)\n\n'
)


def _edges(tmp_path: Path, body: str):
    (tmp_path / "m.go").write_text(_PRELUDE + body, encoding="utf-8")
    result = analyze_go(tmp_path)
    assert not result.skipped
    return result.edges


def _target_kinds(edges, name: str) -> list[str]:
    """Every stamped `io_target_kind` on a `calls` edge into `bufio`.

    Keyed on the bufio destination and restricted to `calls`, because one
    line can carry the wrapper AND its argument's call, and `import "bufio"`
    produces a `go:bufio:` destination of its own with no meta.
    """
    return [
        (e.meta or {}).get("io_target_kind")
        for e in edges
        if e.edge_type == "calls"
        and e.dst.startswith("go:bufio:")
        and name in e.dst
        and (e.meta or {}).get("io_target_kind") is not None
    ]


class TestTheArgumentsOriginDecides:
    def test_a_file_handle_bound_above_the_call_is_a_host_path(self, tmp_path) -> None:
        """The commonest origin. `f` is a FILE, not stdin."""
        edges = _edges(tmp_path, """
func fromFile(p string) {
\tf, _ := os.Open(p)
\tsc := bufio.NewScanner(f)
\t_ = sc
}
""")
        assert _target_kinds(edges, "NewScanner") == ["host_path"]

    def test_a_stdin_handle_bound_above_the_call_still_reads_a_stream(
        self, tmp_path,
    ) -> None:
        """THE CONTROL THAT COSTS SOMETHING. The row's own stated condition,
        reached through a local instead of inline. If this stopped resolving,
        the one population the `ipc_recv` row is written for would go
        unrecognised."""
        edges = _edges(tmp_path, """
func fromStdin() {
\th := os.Stdin
\tsc := bufio.NewScanner(h)
\t_ = sc
}
""")
        assert _target_kinds(edges, "NewScanner") == ["std_stream"]

    def test_an_in_memory_reader_bound_above_the_call_is_still_in_memory(
        self, tmp_path,
    ) -> None:
        """The INLINE spelling needs no origin lookup; through a local it does,
        or the same in-memory reader goes unrecognised one hop away."""
        edges = _edges(tmp_path, """
func fromString(s string) {
\tr := strings.NewReader(s)
\tsc := bufio.NewScanner(r)
\t_ = sc
}
""")
        assert _target_kinds(edges, "NewScanner") == ["in_memory"]

    def test_an_unresolvable_origin_stamps_nothing(self, tmp_path) -> None:
        """THE ABSTENTION, and INV-zumin's ruling in force: a parameter has no
        binding in this function, so the analyzer has no answer and must not
        invent one. Stamping nothing leaves the unstamped fallback in charge."""
        edges = _edges(tmp_path, """
func fromParam(r *strings.Reader) {
\tsc := bufio.NewScanner(r)
\t_ = sc
}
""")
        assert _target_kinds(edges, "NewScanner") == []

    def test_a_network_conn_is_not_claimed_as_a_file(self, tmp_path) -> None:
        """NON-DESTRUCTION on the source side. `net.Dial` returns a Conn; a
        read off it IS a network crossing and must keep minting. The stamp
        names a network stream and selects the `net_recv` row -- still
        minting, at the boundary it actually crosses (WI-suhug)."""
        edges = _edges(tmp_path, """
func fromConn(addr string) {
\tc, _ := net.Dial("tcp", addr)
\tsc := bufio.NewScanner(c)
\t_ = sc
}
""")
        assert _target_kinds(edges, "NewScanner") == ["net_stream"]

    def test_the_last_binding_before_the_call_wins(self, tmp_path) -> None:
        """REBINDING. A name bound twice takes the binding that reaches the
        use, not the first one in the file — the shape a text scan gets wrong
        and the reason this reads the enclosing function rather than grepping.
        """
        edges = _edges(tmp_path, """
func rebound(p string, s string) {
\tvar f interface{}
\tf = strings.NewReader(s)
\tf, _ = os.Open(p)
\tsc := bufio.NewScanner(f)
\t_ = sc
}
""")
        assert _target_kinds(edges, "NewScanner") == ["host_path"]

    def test_a_binding_below_the_call_is_not_used(self, tmp_path) -> None:
        """The other half of the ordering rule, and the one a naive
        last-match-in-file scan fails."""
        edges = _edges(tmp_path, """
func later(p string, s string) {
\tf := strings.NewReader(s)
\tsc := bufio.NewScanner(f)
\tf2, _ := os.Open(p)
\t_, _ = sc, f2
}
""")
        assert _target_kinds(edges, "NewScanner") == ["in_memory"]

    def test_an_http_body_is_left_to_the_catalogue(self, tmp_path) -> None:
        """`resp.Body` is a `net_recv` crossing. Nothing is stamped, so the
        unstamped `ipc_recv` fallback decides -- the right answer (it mints)
        for the wrong reason; either way it must keep minting."""
        edges = _edges(tmp_path, """
func fromBody(u string) {
\tresp, _ := http.Get(u)
\tsc := bufio.NewScanner(resp.Body)
\t_ = sc
}
""")
        assert _target_kinds(edges, "NewScanner") == []

    def test_a_declaration_without_an_initialiser_is_not_a_binding(
        self, tmp_path,
    ) -> None:
        """`var f *os.File` NAMES the variable and binds nothing to it.

        A `var_spec` with no `value` field reaches the same place a
        well-formed binding does, and reading a type annotation as an origin
        would report every declared handle as whatever its TYPE mentions —
        `*os.File` contains neither `os.Open(` nor `os.Stdin`, so the wrong
        answer here would be silent rather than loud. Nothing bound, nothing
        stamped.
        """
        edges = _edges(tmp_path, """
func fromDeclared() {
\tvar f *os.File
\tsc := bufio.NewScanner(f)
\t_ = sc
}
""")
        assert _target_kinds(edges, "NewScanner") == []
