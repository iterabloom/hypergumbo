# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-vutav: a READ's boundary is its RECEIVER'S ORIGIN, one binding after the wrapper.

THE GAP. WI-lipis made ``bufio.NewReader(f)`` follow its argument: the analyzer
resolves ``f``'s last binding in the enclosing function and stamps
``io_target_kind`` on the CONSTRUCTOR's call edge, and the catalogue's dual
``bufio.{NewReader,NewScanner}`` rows narrow on that stamp. But the constructor
transfers nothing (ADR-0049); the bytes cross at ``reader.ReadString('\\n')``,
one line later, on a RECEIVER whose type WI-vutav's first half taught the
analyzer to name (``bufio.Reader``, so the edge lands in the ``bufio`` slot).
Nothing stamped that edge, so a read row could only ever have been declared at
a fixed boundary -- which INV-zumin class (b) rules out for exactly this
population: "undecidable at the call site ... the honest fix is fd-type
inference, not row reordering."

THE MECHANISM. The read's receiver is a bare local. Resolve ITS last binding;
when that binding is a handle-wrapper call, classify the wrapper's argument
exactly as the constructor site does -- an inline producer directly, a bare
identifier through ONE more binding hop, taken AT THE WRAPPER'S LINE so a
rebinding of the handle between the wrapper and the read is not misread.
Anything else abstains: a parameter, a struct field, a non-wrapper binding, a
wrapper over an argument the file does not bind. INV-zumin's rule is one
answer per call site or none, and this direction (selecting ``ipc_recv`` over
``fs_read``) mints a source, so abstention is the only safe default.

WHAT THIS DOES NOT DO. It is not a second dataflow engine: same enclosing
function, textual line order, no branch reasoning -- ``_go_last_binding``'s
contract. ``bufio.NewReaderSize`` and ``bufio.NewReadWriter`` are not in
``_GO_TARGET_ARGUMENT_INDEX`` and stamp nothing here either; they are disclosed on
the catalogue rows, not claimed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.go import (
    _go_receiver_handle_kind,
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
    'import (\n\t"bufio"\n\t"net"\n\t"net/http"\n'
    '\t"os"\n\t"strings"\n)\n\n'
)


def _edges(tmp_path: Path, body: str, prelude: str = _PRELUDE):
    (tmp_path / "m.go").write_text(prelude + body, encoding="utf-8")
    result = analyze_go(tmp_path)
    assert not result.skipped
    return result.edges


def _read_edges(edges, name: str, module: str = "bufio"):
    """Every ``calls`` edge into ``go:<module>:`` whose callee is EXACTLY ``name``.

    Exact on the callee segment: ``Read`` must not match ``ReadString``.
    """
    return [
        e for e in edges
        if e.edge_type == "calls"
        and e.dst.startswith(f"go:{module}:")
        and e.dst.split(":")[3] == name
    ]


def _stamps(edges, name: str, module: str = "bufio") -> list[str]:
    return [
        (e.meta or {})["io_target_kind"]
        for e in _read_edges(edges, name, module)
        if (e.meta or {}).get("io_target_kind") is not None
    ]


class TestTheReceiversBindingDecides:
    def test_a_reader_over_stdin_reads_a_stream(self, tmp_path, go_available) -> None:
        """THE IDIOM the item was filed on: ``reader := bufio.NewReader(os.Stdin)``."""
        edges = _edges(tmp_path, """
func fromStdin() {
\treader := bufio.NewReader(os.Stdin)
\tline, _ := reader.ReadString('\\n')
\t_ = line
}
""")
        assert _stamps(edges, "ReadString") == ["std_stream"]

    def test_a_scanner_over_a_file_handle_reads_a_host_path(
        self, tmp_path, go_available,
    ) -> None:
        """Two hops: ``sc`` <- ``NewScanner(f)`` <- ``os.Open``. The 74.1% case."""
        edges = _edges(tmp_path, """
func fromFile(p string) {
\tf, _ := os.Open(p)
\tsc := bufio.NewScanner(f)
\tfor sc.Scan() {
\t\t_ = sc.Text()
\t}
}
""")
        assert _stamps(edges, "Scan") == ["host_path"]
        assert _stamps(edges, "Text") == ["host_path"]

    def test_an_in_memory_reader_is_still_in_memory(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
func fromString(s string) {
\tr := bufio.NewReader(strings.NewReader(s))
\tline, _ := r.ReadString('\\n')
\t_ = line
}
""")
        assert _stamps(edges, "ReadString") == ["in_memory"]

    def test_a_parameter_receiver_stamps_nothing(self, tmp_path, go_available) -> None:
        """The abstention that costs something: the origin is in another scope."""
        edges = _edges(tmp_path, """
func handle(r *bufio.Reader) {
\tline, _ := r.ReadString('\\n')
\t_ = line
}
""")
        assert _stamps(edges, "ReadString") == []
        # The edge itself is still there, in the slot the row is keyed on --
        # abstaining from the STAMP must not lose the CALL.
        assert len(_read_edges(edges, "ReadString")) == 1

    def test_a_wrapper_over_a_parameter_handle_stamps_nothing(
        self, tmp_path, go_available,
    ) -> None:
        """One hop resolves (``r`` <- ``NewReader(f)``); the second (``f``) does not."""
        edges = _edges(tmp_path, """
func wrap(f *os.File) {
\tr := bufio.NewReader(f)
\tline, _ := r.ReadString('\\n')
\t_ = line
}
""")
        assert _stamps(edges, "ReadString") == []

    def test_a_wrapper_over_an_unprovable_argument_stamps_nothing(
        self, tmp_path, go_available,
    ) -> None:
        """``resp.Body`` is a field access, not a bare identifier: no hop, no claim."""
        edges = _edges(tmp_path, """
func fromHTTP(u string) {
\tresp, _ := http.Get(u)
\tr := bufio.NewReader(resp.Body)
\tline, _ := r.ReadString('\\n')
\t_ = line
}
""")
        assert _stamps(edges, "ReadString") == []

    def test_a_receiver_bound_to_a_non_wrapper_is_not_claimed(
        self, tmp_path, go_available,
    ) -> None:
        """``net.Dial`` is not a handle wrapper; its ``Read`` keeps its own row.

        The receiver is DECLARED so the edge takes the typed-receiver path
        (``go:net:``) and the binding is consulted; a ``:=`` from ``net.Dial``
        would leave ``c`` untyped and never reach that path at all."""
        edges = _edges(tmp_path, """
func fromConn(a string) {
\tvar c net.Conn
\tc, _ = net.Dial("tcp", a)
\tbuf := make([]byte, 8)
\tc.Read(buf)
}
""")
        assert _stamps(edges, "Read", module="net") == []
        assert len(_read_edges(edges, "Read", module="net")) == 1


class TestOrderIsTheWholePoint:
    def test_the_last_binding_before_the_read_wins(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
func rebound(p string) {
\tr := bufio.NewReader(os.Stdin)
\tf, _ := os.Open(p)
\tr = bufio.NewReader(f)
\tline, _ := r.ReadString('\\n')
\t_ = line
}
""")
        assert _stamps(edges, "ReadString") == ["host_path"]

    def test_a_rebinding_below_the_read_is_not_used(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
func later(p string) {
\tr := bufio.NewReader(os.Stdin)
\tline, _ := r.ReadString('\\n')
\tf, _ := os.Open(p)
\tr = bufio.NewReader(f)
\t_ = line
}
""")
        assert _stamps(edges, "ReadString") == ["std_stream"]

    def test_the_handle_is_resolved_at_the_wrappers_line_not_the_reads(
        self, tmp_path, go_available,
    ) -> None:
        """``f`` is rebound BETWEEN the wrapper and the read. The reader still
        wraps the file it was built over; a hop taken at the read's line would
        say stdin."""
        edges = _edges(tmp_path, """
func between(p string) {
\tf, _ := os.Open(p)
\tr := bufio.NewReader(f)
\tf = os.Stdin
\tline, _ := r.ReadString('\\n')
\t_ = line
}
""")
        assert _stamps(edges, "ReadString") == ["host_path"]


class TestTheSurroundingsAreUnchanged:
    def test_an_aliased_import_still_names_the_wrapper(self, tmp_path, go_available) -> None:
        prelude = 'package main\n\nimport (\n\tb "bufio"\n\t"os"\n)\n\n'
        edges = _edges(tmp_path, """
func aliased() {
\tr := b.NewReader(os.Stdin)
\tline, _ := r.ReadString('\\n')
\t_ = line
}
""", prelude=prelude)
        assert _stamps(edges, "ReadString") == ["std_stream"]

    def test_the_constructors_own_stamp_is_unchanged(self, tmp_path, go_available) -> None:
        """WI-lipis's stamp on the wrapper call is the CONTROL, and it must survive."""
        edges = _edges(tmp_path, """
func both() {
\treader := bufio.NewReader(os.Stdin)
\tline, _ := reader.ReadString('\\n')
\t_ = line
}
""")
        assert _stamps(edges, "NewReader") == ["std_stream"]
        assert _stamps(edges, "ReadString") == ["std_stream"]


def _read_call(source: str, callee: str):
    """The ``call_expression`` node whose selector field is ``callee``, plus the bytes."""
    import tree_sitter
    import tree_sitter_go
    from hypergumbo_core.analyze.base import find_child_by_field, iter_tree, node_text

    raw = source.encode("utf-8")
    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_go.language()))
    tree = parser.parse(raw)
    for n in iter_tree(tree.root_node):
        if n.type != "call_expression":
            continue
        fn = find_child_by_field(n, "function")
        if fn is None or fn.type != "selector_expression":
            continue
        field = find_child_by_field(fn, "field")
        if field is not None and node_text(field, raw) == callee:
            return n, raw
    raise AssertionError(f"no call to {callee} in fixture")  # pragma: no cover


_ALIASES = {"bufio": "bufio", "os": "os", "net": "net"}


class TestTheHelperAbstainsOnEveryUnprovableShape:
    """Unit-level, so each abstention branch is pinned by its own input."""

    def _kind(self, rhs: str, aliases: dict[str, str] = _ALIASES):
        src = f"package main\n\nfunc f() {{\n\tr := {rhs}\n\tr.ReadString('x')\n}}\n"
        node, raw = _read_call(src, "ReadString")
        return _go_receiver_handle_kind(node, raw, "r", aliases)

    def test_a_wrapper_over_an_inline_producer(self, go_available) -> None:
        assert self._kind("bufio.NewReader(os.Stdin)") == "std_stream"

    def test_a_binding_that_is_not_a_call(self, go_available) -> None:
        assert self._kind("other") is None

    def test_a_call_with_no_package_qualifier(self, go_available) -> None:
        """A dot-import or an in-repo ``NewReader`` is not the stdlib wrapper."""
        assert self._kind("NewReader(os.Stdin)") is None

    def test_a_call_on_a_package_that_is_not_a_wrapper(self, go_available) -> None:
        assert self._kind('net.Dial("tcp", a)') is None

    def test_a_wrapper_whose_package_is_not_imported(self, go_available) -> None:
        assert self._kind("bufio.NewReader(os.Stdin)", aliases={}) is None

    def test_a_receiver_with_no_binding_at_all(self, go_available) -> None:
        src = "package main\n\nfunc f(r *bufio.Reader) {\n\tr.ReadString('x')\n}\n"
        node, raw = _read_call(src, "ReadString")
        assert _go_receiver_handle_kind(node, raw, "r", _ALIASES) is None


class TestTheStampReachesTheCatalogue:
    """End to end through production's classifier, with the shipped go catalogue."""

    def _classified(self, tmp_path, body: str, name: str):
        from hypergumbo_core.io_boundary import classify_call, load_catalog

        edges = _edges(tmp_path, body)
        (edge,) = _read_edges(edges, name)
        prim = classify_call(
            {"go": load_catalog("go")}, edge.dst, edge.meta, dst_ref=edge.dst_ref,
        )
        return None if prim is None else (prim.boundary, prim.module, prim.name)

    def test_a_stdin_read_is_an_ipc_receive(self, tmp_path, go_available) -> None:
        got = self._classified(tmp_path, """
func fromStdin() {
\treader := bufio.NewReader(os.Stdin)
\tline, _ := reader.ReadString('\\n')
\t_ = line
}
""", "ReadString")
        assert got == ("ipc_recv", "bufio.Reader", "ReadString")

    def test_a_file_scan_is_a_filesystem_read(self, tmp_path, go_available) -> None:
        got = self._classified(tmp_path, """
func fromFile(p string) {
\tf, _ := os.Open(p)
\tsc := bufio.NewScanner(f)
\tfor sc.Scan() {
\t}
}
""", "Scan")
        assert got == ("fs_read", "bufio.Scanner", "Scan")

    def test_an_unstamped_read_falls_back_to_the_declared_row(
        self, tmp_path, go_available,
    ) -> None:
        """``abstains_to: fs_read`` -- mints nothing; the constructor in the
        caller's scope carries the unresolved case (see the core test)."""
        got = self._classified(tmp_path, """
func handle(r *bufio.Reader) {
\tline, _ := r.ReadString('\\n')
\t_ = line
}
""", "ReadString")
        assert got == ("fs_read", "bufio.Reader", "ReadString")
