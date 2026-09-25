# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-suhug: a WRITE's boundary is its writer ARGUMENT's origin.

THE GAP. ``go.yaml`` filed ``io.WriteString`` as ``fs_write`` and
``fmt.Fprint*`` as ``logging`` -- one fixed boundary each -- although the
first argument of every one of them is any ``io.Writer``. Measurement 0012
found the vacuous class this makes: gocryptfs writes a plaintext to
``cmd.StdinPipe()``, a pipe to a child, and the tool reported a host-filesystem
crossing. INV-zumin class (b) forbids a fixed boundary for exactly this
population, and WI-vutav had already built the mechanism for the READ side.

THE MECHANISM, GENERALISED. ``_GO_TARGET_ARGUMENT_INDEX`` names, per call,
WHICH argument is the I/O target (0 for every member today). At the call site
that argument is classified: an inline producer directly (``os.Stdout``,
``os.Stderr``, ``io.Discard``, ``&bytes.Buffer{}``); a call to another member
of the table through ITS target argument (``tabwriter.NewWriter(os.Stdout,
...)``, ``bufio.NewWriter(f)``, ``io.LimitReader(&buf, n)``); a bare or
``&``-prefixed identifier through its last binding, resolved at the line the
binding is used; and, when no binding is visible -- a parameter, a
``var``-declared buffer -- through the identifier's declared TYPE via the
analyzer's own ``var_types`` (``*bytes.Buffer`` / ``strings.Builder`` never
leave the process; ``net.Conn`` / ``http.ResponseWriter`` are network
streams). Every hop is bounded; ``io.Writer`` and ``*os.File`` are DISCLOSED
abstentions (a file may be stdout, a file or a pipe), as is a struct field.

WHAT THE VOCABULARY GAINED. ``pipe`` (``cmd.Std*Pipe()``, ``os.Pipe()``) and
``net_stream`` (a connection, a response writer). ``io.Pipe()`` is
``in_memory``: its far end is a goroutine, not a process. The read side reads
the same table, so ``bufio.NewScanner(conn)`` now stamps ``net_stream`` --
the value INV-bagok's own note said its network case was waiting on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.go import (
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
    'import (\n\t"bufio"\n\t"bytes"\n\t"crypto/sha256"\n\t"fmt"\n\t"io"\n'
    '\t"net"\n\t"net/http"\n\t"os"\n\t"os/exec"\n\t"strings"\n'
    '\t"text/tabwriter"\n)\n\n'
)


def _edges(tmp_path: Path, body: str, prelude: str = _PRELUDE):
    (tmp_path / "m.go").write_text(prelude + body, encoding="utf-8")
    result = analyze_go(tmp_path)
    assert not result.skipped
    return result.edges


def _calls(edges, name: str, module: str):
    """Every ``calls`` edge into ``go:<module>:`` whose callee is EXACTLY ``name``."""
    return [
        e for e in edges
        if e.edge_type == "calls"
        and e.dst.startswith(f"go:{module}:")
        and e.dst.split(":")[3] == name
    ]


def _stamps(edges, name: str, module: str = "fmt") -> list[str]:
    return [
        (e.meta or {})["io_target_kind"]
        for e in _calls(edges, name, module)
        if (e.meta or {}).get("io_target_kind") is not None
    ]


class TestTheWriterArgumentDecides:
    def test_stdout_is_a_standard_stream(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, 'func f() {\n\tfmt.Fprintf(os.Stdout, "x")\n}\n')
        assert _stamps(edges, "Fprintf") == ["std_stream"]

    def test_stderr_is_a_standard_stream(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, 'func f() {\n\tfmt.Fprintln(os.Stderr, "x")\n}\n')
        assert _stamps(edges, "Fprintln") == ["std_stream"]

    def test_a_response_writer_parameter_is_a_network_stream(
        self, tmp_path, go_available,
    ) -> None:
        """The type decides when no binding is visible: ``http.ResponseWriter``."""
        edges = _edges(tmp_path, """
func h(w http.ResponseWriter, r *http.Request) {
\tfmt.Fprint(w, "x")
}
""")
        assert _stamps(edges, "Fprint") == ["net_stream"]

    def test_a_declared_builder_by_address_is_in_memory(
        self, tmp_path, go_available,
    ) -> None:
        """``&sb`` over ``var sb strings.Builder`` -- 96 of the 105 ``&ident`` sites."""
        edges = _edges(tmp_path, """
func f() string {
\tvar sb strings.Builder
\tfmt.Fprintf(&sb, "x")
\treturn sb.String()
}
""")
        assert _stamps(edges, "Fprintf") == ["in_memory"]

    def test_a_declared_buffer_by_address_is_in_memory(
        self, tmp_path, go_available,
    ) -> None:
        edges = _edges(tmp_path, """
func f() {
\tvar buf bytes.Buffer
\tfmt.Fprintf(&buf, "x")
}
""")
        assert _stamps(edges, "Fprintf") == ["in_memory"]

    def test_a_buffer_literal_is_in_memory(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
func f() {
\tbuf := &bytes.Buffer{}
\tfmt.Fprintf(buf, "x")
}
""")
        assert _stamps(edges, "Fprintf") == ["in_memory"]

    def test_a_hash_is_in_memory(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
func f() {
\th := sha256.New()
\tfmt.Fprintf(h, "x")
}
""")
        assert _stamps(edges, "Fprintf") == ["in_memory"]

    def test_a_tabwriter_over_stdout_is_a_standard_stream(
        self, tmp_path, go_available,
    ) -> None:
        """The largest resolvable shape: a writer WRAPPER over stdout."""
        edges = _edges(tmp_path, """
func f() {
\ttw := tabwriter.NewWriter(os.Stdout, 0, 8, 1, ' ', 0)
\tfmt.Fprintf(tw, "x")
}
""")
        assert _stamps(edges, "Fprintf") == ["std_stream"]

    def test_a_buffered_writer_over_a_created_file_is_a_host_path(
        self, tmp_path, go_available,
    ) -> None:
        """Two hops: ``bw`` <- ``bufio.NewWriter(f)`` <- ``os.Create``."""
        edges = _edges(tmp_path, """
func f(p string) {
\tf, _ := os.Create(p)
\tbw := bufio.NewWriter(f)
\tfmt.Fprintf(bw, "x")
}
""")
        assert _stamps(edges, "Fprintf") == ["host_path"]

    def test_a_childs_stdin_pipe_is_a_pipe(self, tmp_path, go_available) -> None:
        """Measurement 0012's vacuous row: gocryptfs writing to ``StdinPipe``."""
        edges = _edges(tmp_path, """
func f() {
\tcmd := exec.Command("sh")
\tstdin, _ := cmd.StdinPipe()
\tio.WriteString(stdin, "x")
}
""")
        assert _stamps(edges, "WriteString", "io") == ["pipe"]

    def test_an_in_process_pipe_is_in_memory(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
func f() {
\t_, pw := io.Pipe()
\tfmt.Fprintf(pw, "x")
}
""")
        assert _stamps(edges, "Fprintf") == ["in_memory"]

    def test_discard_is_the_null_device(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, 'func f() {\n\tfmt.Fprintf(io.Discard, "x")\n}\n')
        assert _stamps(edges, "Fprintf") == ["null_device"]

    def test_a_connection_parameter_is_a_network_stream(
        self, tmp_path, go_available,
    ) -> None:
        edges = _edges(tmp_path, """
func h(conn net.Conn) {
\tfmt.Fprintf(conn, "x")
}
""")
        assert _stamps(edges, "Fprintf") == ["net_stream"]

    def test_an_accepted_connection_is_a_network_stream(
        self, tmp_path, go_available,
    ) -> None:
        """INV-bagok's caddy case, pointed at the writer: ``c, _ := ln.Accept()``."""
        edges = _edges(tmp_path, """
func h(ln net.Listener) {
\tc, _ := ln.Accept()
\tfmt.Fprintf(c, "x")
}
""")
        assert _stamps(edges, "Fprintf") == ["net_stream"]

    def test_the_last_binding_wins(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
func f(p string) {
\tw := os.Stdout
\tf, _ := os.Create(p)
\tw = f
\tfmt.Fprintf(w, "x")
}
""")
        assert _stamps(edges, "Fprintf") == ["host_path"]

    def test_a_multi_line_call_reads_the_argument_not_the_text(
        self, tmp_path, go_available,
    ) -> None:
        edges = _edges(tmp_path, """
func f() {
\tfmt.Fprintf(
\t\tos.Stderr,
\t\t"x",
\t)
}
""")
        assert _stamps(edges, "Fprintf") == ["std_stream"]

    def test_an_aliased_import_still_names_the_package(
        self, tmp_path, go_available,
    ) -> None:
        prelude = 'package main\n\nimport (\n\tf "fmt"\n\t"os"\n)\n\n'
        edges = _edges(
            tmp_path, 'func g() {\n\tf.Fprintf(os.Stdout, "x")\n}\n', prelude,
        )
        assert _stamps(edges, "Fprintf") == ["std_stream"]


class TestTheDisclosedAbstentions:
    """One answer per call site or none (INV-zumin); these are the nones."""

    def test_an_io_writer_parameter_stamps_nothing(
        self, tmp_path, go_available,
    ) -> None:
        """172 of the 749 bare-identifier sites: the origin is the caller's."""
        edges = _edges(tmp_path, 'func f(out io.Writer) {\n\tfmt.Fprintf(out, "x")\n}\n')
        assert _stamps(edges, "Fprintf") == []
        assert len(_calls(edges, "Fprintf", "fmt")) == 1

    def test_an_os_file_parameter_stamps_nothing(
        self, tmp_path, go_available,
    ) -> None:
        """An ``*os.File`` may be stdout, a file or a pipe end."""
        edges = _edges(tmp_path, 'func f(f *os.File) {\n\tfmt.Fprintf(f, "x")\n}\n')
        assert _stamps(edges, "Fprintf") == []

    def test_a_struct_field_stamps_nothing(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
type T struct{ out io.Writer }

func (t *T) f() {
\tfmt.Fprintf(t.out, "x")
}
""")
        assert _stamps(edges, "Fprintf") == []

    def test_only_the_target_argument_is_read(self, tmp_path, go_available) -> None:
        """``os.Stdout`` in argument TWO is data, not the writer."""
        edges = _edges(tmp_path, """
func f(out io.Writer) {
\tfmt.Fprintf(out, "%v", os.Stdout)
}
""")
        assert _stamps(edges, "Fprintf") == []

    def test_a_buffered_writer_over_a_parameter_stamps_nothing(
        self, tmp_path, go_available,
    ) -> None:
        edges = _edges(tmp_path, """
func f(w io.Writer) {
\tbw := bufio.NewWriter(w)
\tfmt.Fprintf(bw, "x")
}
""")
        assert _stamps(edges, "Fprintf") == []

    def test_the_hop_limit_ends_a_chain_of_rebindings(
        self, tmp_path, go_available,
    ) -> None:
        """Four plain rebindings of an ``io.Writer`` parameter exceed the cap
        and abstain; the cap is what stops a self-referential ``w = w`` from
        looping, and three hops still reach ``w = f`` <- ``os.Create``."""
        edges = _edges(tmp_path, """
func f(w0 io.Writer) {
\tw1 := w0
\tw2 := w1
\tw3 := w2
\tw4 := w3
\tfmt.Fprintf(w4, "x")
}
""")
        assert _stamps(edges, "Fprintf") == []

    def test_a_call_with_no_argument_at_the_index_stamps_nothing(
        self, tmp_path, go_available,
    ) -> None:
        """Not valid Go, but it parses, and an analyzer must not crash on it."""
        edges = _edges(tmp_path, 'func f() {\n\tfmt.Fprintf()\n}\n')
        assert _stamps(edges, "Fprintf") == []
        assert len(_calls(edges, "Fprintf", "fmt")) == 1


class TestTheReadSideReadsTheSameTable:
    def test_a_reader_over_a_connection_is_a_network_stream(
        self, tmp_path, go_available,
    ) -> None:
        """INV-bagok's network case: wrapper AND read both stamp it."""
        edges = _edges(tmp_path, """
func h(conn net.Conn) {
\tr := bufio.NewReader(conn)
\tline, _ := r.ReadString('\\n')
\t_ = line
}
""")
        assert _stamps(edges, "NewReader", "bufio") == ["net_stream"]
        assert _stamps(edges, "ReadString", "bufio") == ["net_stream"]

    def test_a_limit_reader_over_a_buffer_is_in_memory(
        self, tmp_path, go_available,
    ) -> None:
        """cilium's ``NewScanner(io.LimitReader(&stderr, n))``: through the wrapper."""
        edges = _edges(tmp_path, """
func f() {
\tvar buf bytes.Buffer
\tsc := bufio.NewScanner(io.LimitReader(&buf, 10))
\tfor sc.Scan() {
\t}
}
""")
        assert _stamps(edges, "NewScanner", "bufio") == ["in_memory"]
        assert _stamps(edges, "Scan", "bufio") == ["in_memory"]

    def test_a_childs_stdout_pipe_is_a_pipe(self, tmp_path, go_available) -> None:
        edges = _edges(tmp_path, """
func f() {
\tcmd := exec.Command("sh")
\tout, _ := cmd.StdoutPipe()
\tsc := bufio.NewScanner(out)
\tfor sc.Scan() {
\t}
}
""")
        assert _stamps(edges, "NewScanner", "bufio") == ["pipe"]
        assert _stamps(edges, "Scan", "bufio") == ["pipe"]


class TestTheTypeTable:
    """Unit-level: the declared type's package is resolved through the imports."""

    def test_a_buffer_is_in_memory(self, go_available) -> None:
        assert _go_typed_target_kind("bytes.Buffer", {"bytes": "bytes"}) == "in_memory"

    def test_a_builder_is_in_memory(self, go_available) -> None:
        assert _go_typed_target_kind(
            "strings.Builder", {"strings": "strings"},
        ) == "in_memory"

    def test_a_response_writer_is_a_network_stream(self, go_available) -> None:
        assert _go_typed_target_kind(
            "http.ResponseWriter", {"http": "net/http"},
        ) == "net_stream"

    def test_an_aliased_package_resolves_through_its_import_path(
        self, go_available,
    ) -> None:
        assert _go_typed_target_kind(
            "h.ResponseWriter", {"h": "net/http"},
        ) == "net_stream"

    def test_an_unqualified_type_is_in_repo_and_abstains(self, go_available) -> None:
        assert _go_typed_target_kind("Buffer", {"bytes": "bytes"}) is None

    def test_a_package_that_is_not_imported_abstains(self, go_available) -> None:
        """A local package named ``bytes`` is not the stdlib's."""
        assert _go_typed_target_kind("bytes.Buffer", {}) is None

    def test_a_file_abstains(self, go_available) -> None:
        assert _go_typed_target_kind("os.File", {"os": "os"}) is None

    def test_an_io_writer_abstains(self, go_available) -> None:
        assert _go_typed_target_kind("io.Writer", {"io": "io"}) is None


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

    def test_a_print_to_stdout_is_logging(self, tmp_path, go_available) -> None:
        got = self._classified(
            tmp_path, 'func f() {\n\tfmt.Fprintf(os.Stdout, "x")\n}\n', "Fprintf", "fmt",
        )
        assert got == ("logging", "fmt", "Fprintf")

    def test_a_print_to_a_connection_is_a_network_send(
        self, tmp_path, go_available,
    ) -> None:
        got = self._classified(
            tmp_path, 'func h(c net.Conn) {\n\tfmt.Fprintf(c, "x")\n}\n', "Fprintf", "fmt",
        )
        assert got == ("net_send", "fmt", "Fprintf")

    def test_a_write_to_a_childs_stdin_is_an_ipc_send(
        self, tmp_path, go_available,
    ) -> None:
        got = self._classified(tmp_path, """
func f() {
\tcmd := exec.Command("sh")
\tstdin, _ := cmd.StdinPipe()
\tio.WriteString(stdin, "x")
}
""", "WriteString", "io")
        assert got == ("ipc_send", "io", "WriteString")

    def test_a_print_to_a_created_file_is_a_filesystem_write(
        self, tmp_path, go_available,
    ) -> None:
        got = self._classified(tmp_path, """
func f(p string) {
\tf, _ := os.Create(p)
\tfmt.Fprintf(f, "x")
}
""", "Fprintf", "fmt")
        assert got == ("fs_write", "fmt", "Fprintf")

    def test_an_unstamped_print_keeps_todays_answer(
        self, tmp_path, go_available,
    ) -> None:
        got = self._classified(
            tmp_path, 'func f(out io.Writer) {\n\tfmt.Fprintf(out, "x")\n}\n', "Fprintf", "fmt",
        )
        assert got == ("logging", "fmt", "Fprintf")

    def test_an_unstamped_write_string_keeps_todays_answer(
        self, tmp_path, go_available,
    ) -> None:
        got = self._classified(
            tmp_path, 'func f(out io.Writer) {\n\tio.WriteString(out, "x")\n}\n',
            "WriteString", "io",
        )
        assert got == ("fs_write", "io", "WriteString")

    def test_a_read_over_a_connection_is_a_network_receive(
        self, tmp_path, go_available,
    ) -> None:
        got = self._classified(tmp_path, """
func h(conn net.Conn) {
\tr := bufio.NewReader(conn)
\tline, _ := r.ReadString('\\n')
\t_ = line
}
""", "ReadString", "bufio")
        assert got == ("net_recv", "bufio.Reader", "ReadString")
