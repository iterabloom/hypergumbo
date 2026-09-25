# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-tusav: a java READ's boundary is its RECEIVER'S ORIGIN, through nested constructors.

THE GAP. ``java.io.BufferedReader.{readLine,read}`` and
``java.util.Scanner.{next,nextLine,nextInt}`` shipped at a FIXED ``fs_read``,
and the Scanner row said so in its own note ("When reading from a file"). So

    BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
    String line = in.readLine();

reported a FILESYSTEM read. Measured on the four-method fixture this file is
built from, before the change: ``fs_read: 4`` covering both the stdin and the
file readers, with the stdin crossing visible only as the separate
``java.lang.System.in`` attribute reference.

WHAT THE ITEM EXPECTED AND WHAT IS ACTUALLY TRUE. WI-tusav asked for
``abstains_to: fs_read`` "because the constructor scope carries the unresolved
case", and asked that the constructor rows be checked FIRST -- an fs_read
fallback with no ipc_recv constructor fallback would leave the stdin crossing
represented nowhere. Checked: java emits NO ``calls`` edge for
``object_creation_expression`` at all, so there are no constructor rows in any
language-level sense and there never could be. The unresolved case is carried
instead by ``java.lang.System.in``'s ATTRIBUTE row (``ipc_recv``), which fires
on the ``module_attr_ref`` edge and is what the pre-change measurement above
shows. ``fs_read`` first is therefore still the safe abstention, but for a
different reason than the item recorded.

THE MECHANISM. The read's receiver is a bare local. Resolve ITS last binding at
or above the call's line in the enclosing method, then unwrap the decorator
chain -- ``BufferedReader`` over ``InputStreamReader`` over ``System.in`` -- until
a constructor NAMES the target: a path-taking constructor is ``host_path``, an
in-memory one is ``in_memory``, ``System.in`` is ``std_stream``. Java's wrapper
is a NESTED constructor, so this is two constructors deep where go's was one
call; the unwrap is an explicit bounded loop rather than recursion.

WHAT THIS DOES NOT DO. ``net_stream`` is NEVER stamped, and that is a
correctness requirement rather than laziness: ``_narrow_by_target_kind`` keeps
only rows whose boundary equals the resolved one, so stamping a kind the
catalogue has no row for would empty the candidate list and DELETE the
classification outright. ``new Scanner(sock.getInputStream())`` therefore
abstains to ``fs_read`` exactly as it did before, and c.yaml's stdio rows carry
the same two-row shape for the same reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.java import (
    _java_receiver_stream_kind,
    analyze_java,
)


@pytest.fixture()
def java_available():
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_java"):
        pytest.skip("Java tree-sitter grammar not installed")


_IMPORTS = (
    "import java.io.BufferedReader;\n"
    "import java.io.InputStreamReader;\n"
    "import java.io.FileReader;\n"
    "import java.io.StringReader;\n"
    "import java.io.InputStream;\n"
    "import java.io.File;\n"
    "import java.util.Scanner;\n\n"
)


def _edges(tmp_path: Path, body: str):
    src = _IMPORTS + "public class P {\n" + body + "\n}\n"
    (tmp_path / "P.java").write_text(src, encoding="utf-8")
    result = analyze_java(tmp_path)
    assert not result.skipped
    return result.edges


def _read_edges(edges, name: str, module: str):
    return [
        e for e in edges
        if e.edge_type == "calls"
        and e.dst.startswith(f"java:{module}:")
        and e.dst.split(":")[3] == name
    ]


def _stamps(edges, name: str, module: str) -> list[str]:
    return [
        (e.meta or {})["io_target_kind"]
        for e in _read_edges(edges, name, module)
        if (e.meta or {}).get("io_target_kind") is not None
    ]


_BR = "java.io.BufferedReader"
_SC = "java.util.Scanner"


class TestTheReceiversOriginDecides:
    def test_the_filed_idiom_reads_a_standard_stream(self, tmp_path, java_available) -> None:
        """The exact two lines WI-tusav was filed on, two constructors deep."""
        edges = _edges(tmp_path, """
    void fromStdin() throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
        String line = in.readLine();
    }
""")
        assert _stamps(edges, "readLine", _BR) == ["std_stream"]

    def test_a_reader_over_a_file_reads_a_host_path(self, tmp_path, java_available) -> None:
        edges = _edges(tmp_path, """
    void fromFile(String p) throws Exception {
        BufferedReader fr = new BufferedReader(new FileReader(p));
        String line = fr.readLine();
    }
""")
        assert _stamps(edges, "readLine", _BR) == ["host_path"]

    def test_a_scanner_over_stdin_reads_a_standard_stream(self, tmp_path, java_available) -> None:
        """One constructor deep, and the row whose own note said 'when reading
        from a file'."""
        edges = _edges(tmp_path, """
    void scanStdin() {
        Scanner sc = new Scanner(System.in);
        String s = sc.nextLine();
    }
""")
        assert _stamps(edges, "nextLine", _SC) == ["std_stream"]

    def test_a_scanner_over_a_new_file_reads_a_host_path(self, tmp_path, java_available) -> None:
        edges = _edges(tmp_path, """
    void scanFile(String p) throws Exception {
        Scanner sc = new Scanner(new File(p));
        String s = sc.nextLine();
    }
""")
        assert _stamps(edges, "nextLine", _SC) == ["host_path"]

    def test_an_in_memory_reader_is_still_in_memory(self, tmp_path, java_available) -> None:
        edges = _edges(tmp_path, """
    void fromString(String s) throws Exception {
        BufferedReader r = new BufferedReader(new StringReader(s));
        String line = r.readLine();
    }
""")
        assert _stamps(edges, "readLine", _BR) == ["in_memory"]

    def test_a_bare_input_stream_bound_to_stdin(self, tmp_path, java_available) -> None:
        """``InputStream.read`` is the row WI-tusav noted was absent entirely."""
        edges = _edges(tmp_path, """
    void raw() throws Exception {
        InputStream is = System.in;
        int c = is.read();
    }
""")
        assert _stamps(edges, "read", "java.io.InputStream") == ["std_stream"]


class TestAbstentionIsTheDefault:
    def test_a_parameter_receiver_stamps_nothing(self, tmp_path, java_available) -> None:
        """The origin is in another scope. The CALL must survive the abstention."""
        edges = _edges(tmp_path, """
    void handle(BufferedReader r) throws Exception {
        String line = r.readLine();
    }
""")
        assert _stamps(edges, "readLine", _BR) == []
        assert len(_read_edges(edges, "readLine", _BR)) == 1

    def test_a_wrapper_over_an_unbound_identifier_stamps_nothing(
        self, tmp_path, java_available,
    ) -> None:
        edges = _edges(tmp_path, """
    void wrap(InputStreamReader isr) throws Exception {
        BufferedReader r = new BufferedReader(isr);
        String line = r.readLine();
    }
""")
        assert _stamps(edges, "readLine", _BR) == []

    def test_a_socket_stream_is_not_claimed(self, tmp_path, java_available) -> None:
        """NEVER stamp a kind the catalogue has no row for: ``net_stream``
        would empty the candidate list and delete the classification."""
        edges = _edges(tmp_path, """
    void fromSocket(java.net.Socket sock) throws Exception {
        BufferedReader r = new BufferedReader(
            new InputStreamReader(sock.getInputStream()));
        String line = r.readLine();
    }
""")
        assert _stamps(edges, "readLine", _BR) == []

    def test_a_field_receiver_stamps_nothing(self, tmp_path, java_available) -> None:
        edges = _edges(tmp_path, """
    BufferedReader shared;
    void useField() throws Exception {
        String line = shared.readLine();
    }
""")
        assert _stamps(edges, "readLine", _BR) == []


class TestOrderIsTheWholePoint:
    def test_the_last_binding_before_the_read_wins(self, tmp_path, java_available) -> None:
        edges = _edges(tmp_path, """
    void rebound(String p) throws Exception {
        BufferedReader r = new BufferedReader(new InputStreamReader(System.in));
        r = new BufferedReader(new FileReader(p));
        String line = r.readLine();
    }
""")
        assert _stamps(edges, "readLine", _BR) == ["host_path"]

    def test_a_rebinding_below_the_read_is_not_used(self, tmp_path, java_available) -> None:
        edges = _edges(tmp_path, """
    void later(String p) throws Exception {
        BufferedReader r = new BufferedReader(new InputStreamReader(System.in));
        String line = r.readLine();
        r = new BufferedReader(new FileReader(p));
    }
""")
        assert _stamps(edges, "readLine", _BR) == ["std_stream"]


class TestTheHelperAbstainsOnEveryUnprovableShape:
    """Unit level, so each abstention branch is pinned by its own input."""

    def _kind(self, rhs: str):
        src = (
            _IMPORTS
            + "public class P {\n    void f() throws Exception {\n"
            + f"        BufferedReader r = {rhs};\n"
            + "        r.readLine();\n    }\n}\n"
        )
        node, raw = _invocation(src, "readLine")
        return _java_receiver_stream_kind(node, raw, "r")

    def test_a_nested_wrapper_over_stdin(self, java_available) -> None:
        assert self._kind(
            "new BufferedReader(new InputStreamReader(System.in))"
        ) == "std_stream"

    def test_a_binding_that_is_not_a_constructor(self, java_available) -> None:
        assert self._kind("other") is None

    def test_a_wrapper_over_nothing(self, java_available) -> None:
        assert self._kind("new BufferedReader()") is None

    def test_a_constructor_that_is_not_a_stream_type(self, java_available) -> None:
        assert self._kind("new StringBuilder()") is None

    def test_a_wrapper_chain_deeper_than_the_budget_abstains(self, java_available) -> None:
        deep = "System.in"
        for _ in range(12):
            deep = f"new InputStreamReader({deep})"
        assert self._kind(f"new BufferedReader({deep})") is None

    def test_a_declaration_without_an_initialiser_is_skipped(
        self, tmp_path, java_available,
    ) -> None:
        """``BufferedReader r;`` binds nothing -- the declarator has a name and
        no value, and the walk must step over it to reach the assignment."""
        edges = _edges(tmp_path, """
    void split() throws Exception {
        BufferedReader r;
        r = new BufferedReader(new InputStreamReader(System.in));
        String line = r.readLine();
    }
""")
        assert _stamps(edges, "readLine", _BR) == ["std_stream"]

    def test_a_call_outside_any_method_body_abstains(self, java_available) -> None:
        """An instance-initialiser block has no enclosing method or constructor,
        so there is no scope to resolve the binding in."""
        src = (
            _IMPORTS
            + "public class P {\n    {\n"
            + "        BufferedReader r = new BufferedReader("
            + "new InputStreamReader(System.in));\n"
            + "        r.readLine();\n    }\n}\n"
        )
        node, raw = _invocation(src, "readLine")
        assert _java_receiver_stream_kind(node, raw, "r") is None

    def test_a_receiver_with_no_binding_at_all(self, java_available) -> None:
        src = (
            _IMPORTS
            + "public class P {\n    void f(BufferedReader r) throws Exception {\n"
            + "        r.readLine();\n    }\n}\n"
        )
        node, raw = _invocation(src, "readLine")
        assert _java_receiver_stream_kind(node, raw, "r") is None


def _invocation(source: str, callee: str):
    """The ``method_invocation`` node whose name is ``callee``, plus the bytes."""
    import tree_sitter
    import tree_sitter_java
    from hypergumbo_core.analyze.base import find_child_by_field, iter_tree, node_text

    raw = source.encode("utf-8")
    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_java.language()))
    tree = parser.parse(raw)
    for n in iter_tree(tree.root_node):
        if n.type != "method_invocation":
            continue
        name = find_child_by_field(n, "name")
        if name is not None and node_text(name, raw) == callee:
            return n, raw
    raise AssertionError(f"no call to {callee} in fixture")  # pragma: no cover


class TestTheStampReachesTheCatalogue:
    """End to end through production's classifier, with the shipped catalogue."""

    def _classified(self, tmp_path, body: str, name: str, module: str):
        from hypergumbo_core.io_boundary import classify_call, load_catalog

        edges = _edges(tmp_path, body)
        (edge,) = _read_edges(edges, name, module)
        prim = classify_call(
            {"java": load_catalog("java")}, edge.dst, edge.meta,
            dst_ref=edge.dst_ref,
        )
        return None if prim is None else (prim.boundary, prim.module, prim.name)

    def test_a_stdin_read_is_an_ipc_receive(self, tmp_path, java_available) -> None:
        got = self._classified(tmp_path, """
    void fromStdin() throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
        String line = in.readLine();
    }
""", "readLine", _BR)
        assert got == ("ipc_recv", _BR, "readLine")

    def test_a_file_read_is_still_a_filesystem_read(self, tmp_path, java_available) -> None:
        got = self._classified(tmp_path, """
    void fromFile(String p) throws Exception {
        BufferedReader fr = new BufferedReader(new FileReader(p));
        String line = fr.readLine();
    }
""", "readLine", _BR)
        assert got == ("fs_read", _BR, "readLine")

    def test_an_unstamped_read_falls_back_to_the_declared_row(
        self, tmp_path, java_available,
    ) -> None:
        """``fs_read`` is declared FIRST, so it is what an unresolvable
        receiver keeps -- and it mints no taint source."""
        got = self._classified(tmp_path, """
    void handle(BufferedReader r) throws Exception {
        String line = r.readLine();
    }
""", "readLine", _BR)
        assert got == ("fs_read", _BR, "readLine")
