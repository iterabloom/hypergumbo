# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-lipis, second deliverable: the wrapper's boundary is its ARGUMENT'S ORIGIN.

`go.yaml` files `bufio.{NewScanner,NewReader}` as `ipc_recv` on the note *"When
wrapping os.Stdin"* — a condition no catalogue row can enforce, because the row
sees the CALLEE and the answer is in the ARGUMENT. `ipc_recv` is in
`AUTO_SOURCE_LABEL_MAP`, so every site mints an `untrusted_input` source.

THE MEASUREMENT THIS ACTS ON, published on the item before any design was
chosen. Of the 163 bare-local `bufio.New*` sites across the ADR-0049 cohort's Go
repositories, hypergumbo's own reaching-def solver resolves **83** (50.9%
pooled, 25-91% per repo). Of those 83:

    63 (74.1%)  wrap an `os.Open` handle   -> fs_read, deliberately NOT a source
    18 (21.2%)  other RHS
     3 ( 3.5%)  an HTTP response body      -> net_recv, correctly a source
     1 ( 1.2%)  an in-memory buffer        -> no crossing (shipped already)
     0 ( 0.0%)  wrap `os.Stdin`            -> the row's own stated condition,
                                             true NOWHERE in the population

So the file-handle case is the LARGER false source and the first deliverable's
19.1% estimate omitted it.

WHY THE ORIGIN LOOKUP IS SYNTACTIC HERE AND NOT A SECOND DATAFLOW ENGINE. The
analyzer runs before any DDG exists, so it cannot ask the reaching-def solver
that measured the 83. What it CAN see is the enclosing function's own text:
`f, _ := os.Open(p)` five lines above `bufio.NewScanner(f)`. This resolves the
LAST binding of the name before the use, in the enclosing function only, and
abstains on everything else — a deliberately smaller instrument than the DDG,
whose answers are a subset of the DDG's and never a superset.

INV-zumin's RULING IS OBSERVED: one answer per call site, or none. A bare local
whose binding this cannot find stamps NOTHING and classifies exactly as before.
The direction is REMOVAL, so every case below that stops minting is paired with
a control that must keep minting.
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

    Keyed on the bufio destination and restricted to `calls`, for the two
    reasons the first deliverable's test records: one line can carry the
    wrapper AND its argument's call, and `import "bufio"` produces a
    `go:bufio:` destination of its own with no meta.
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
        """THE DEFECT, and the 74.1% case. `f` is a FILE; the row says stdin."""
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
        the change would be deleting the one population the row was written
        for."""
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
        """The first deliverable saw this only INLINE. Through a local it was
        invisible, which is the same blind spot one hop shorter."""
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
        invent one. Stamping nothing classifies exactly as before."""
        edges = _edges(tmp_path, """
func fromParam(r *strings.Reader) {
\tsc := bufio.NewScanner(r)
\t_ = sc
}
""")
        assert _target_kinds(edges, "NewScanner") == []

    def test_a_network_conn_is_not_claimed_as_a_file(self, tmp_path) -> None:
        """NON-DESTRUCTION on the source side. `net.Dial` returns a Conn; a
        read off it IS a network crossing and must keep minting. Nothing is
        stamped, so the catalogue's `ipc_recv` row still decides — the
        conservative direction."""
        edges = _edges(tmp_path, """
func fromConn(addr string) {
\tc, _ := net.Dial("tcp", addr)
\tsc := bufio.NewScanner(c)
\t_ = sc
}
""")
        assert _target_kinds(edges, "NewScanner") == []

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
        """3 of the 83 resolved sites. `resp.Body` is a `net_recv` crossing and
        the row's `ipc_recv` is the right answer for the wrong reason; either
        way it must keep minting, so nothing is stamped."""
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
