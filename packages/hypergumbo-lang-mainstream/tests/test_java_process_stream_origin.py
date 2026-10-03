# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kanor: a read over a ``Process``'s output stream reads a PIPE.

``ProcessBuilder.start()`` and ``Runtime.exec(..)`` return a ``Process`` -- a
HANDLE the child fills later (ADR-0049 ruling 1: the launch returns nothing the
far side chose). The child's bytes cross at the READ through
``p.getInputStream()`` / ``p.getErrorStream()``, and java.yaml already rows that
read twice (``fs_read`` first, ``ipc_recv`` twin) for WI-tusav's receiver-origin
stamp. What was missing is the origin: ``_java_stream_kind_of`` stopped at the
``getInputStream()`` call and abstained, so the read fell back to ``fs_read``,
which mints nothing, and a program that executed its child's output reported
no flow.

``pipe`` reads as ``ipc_recv`` (``io_boundary._READ_TARGET_KIND_BOUNDARY``), a
kind java.yaml HAS a row for, so stamping it cannot empty the candidate list --
the reason ``net_stream`` is never stamped (a ``Socket``'s stream still
abstains, pinned below).

A ``Process`` is a ``Process`` however it was obtained, so its DECLARED type is
a proof even for a parameter, where a wrapper's origin would not be.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.java import (
    _java_declared_type_of,
    _java_is_process,
    _java_stream_kind_of,
    analyze_java,
)

_IMPORTS = (
    "import java.io.BufferedReader;\n"
    "import java.io.InputStreamReader;\n"
    "import java.io.InputStream;\n\n"
)
_BR = "java.io.BufferedReader"
_IS = "java.io.InputStream"


@pytest.fixture()
def java_available():
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_java"):
        pytest.skip("Java tree-sitter grammar not installed")


def _stamps(tmp_path: Path, body: str, name: str, module: str) -> list:
    src = _IMPORTS + "public class P {\n" + body + "\n}\n"
    (tmp_path / "P.java").write_text(src, encoding="utf-8")
    result = analyze_java(tmp_path)
    assert not result.skipped
    edges = [
        e for e in result.edges
        if e.edge_type == "calls"
        and e.dst.startswith(f"java:{module}:")
        and e.dst.split(":")[3] == name
    ]
    assert edges, f"no {module}.{name} call edge -- the fixture cannot reach"
    return [(e.meta or {}).get("io_target_kind") for e in edges]


class TestAProcessStreamIsAPipe:
    def test_a_reader_over_a_started_process(self, tmp_path, java_available) -> None:
        assert _stamps(tmp_path, """
    void f() throws Exception {
        Process p = new ProcessBuilder("git", "log").start();
        BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()));
        String line = r.readLine();
    }
""", "readLine", _BR) == ["pipe"]

    def test_a_raw_stream_from_runtime_exec(self, tmp_path, java_available) -> None:
        assert _stamps(tmp_path, """
    void f() throws Exception {
        var p = Runtime.getRuntime().exec("git log");
        InputStream in = p.getInputStream();
        int n = in.read(new byte[8]);
    }
""", "read", _IS) == ["pipe"]

    def test_the_error_stream_inline(self, tmp_path, java_available) -> None:
        assert _stamps(tmp_path, """
    void f() throws Exception {
        BufferedReader r = new BufferedReader(new InputStreamReader(
            new ProcessBuilder("make").start().getErrorStream()));
        String line = r.readLine();
    }
""", "readLine", _BR) == ["pipe"]

    def test_a_process_parameter(self, tmp_path, java_available) -> None:
        """The declared type proves it; no binding is needed."""
        assert _stamps(tmp_path, """
    void f(Process p) throws Exception {
        BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()));
        String line = r.readLine();
    }
""", "readLine", _BR) == ["pipe"]

    def test_start_on_a_declared_builder_and_exec_on_a_declared_runtime(
        self, tmp_path, java_available,
    ) -> None:
        assert _stamps(tmp_path, """
    void f(ProcessBuilder pb, Runtime rt) throws Exception {
        var a = pb.start();
        BufferedReader r = new BufferedReader(new InputStreamReader(a.getInputStream()));
        String x = r.readLine();
        var b = rt.exec("ls");
        BufferedReader s = new BufferedReader(new InputStreamReader(b.getInputStream()));
        String y = s.readLine();
    }
""", "readLine", _BR) == ["pipe", "pipe"]


class TestAnythingElseAbstains:
    def test_a_socket_stream_still_abstains(self, tmp_path, java_available) -> None:
        assert _stamps(tmp_path, """
    void f(java.net.Socket sock) throws Exception {
        BufferedReader r = new BufferedReader(new InputStreamReader(sock.getInputStream()));
        String line = r.readLine();
    }
""", "readLine", _BR) == [None]

    def test_start_on_something_that_is_not_a_builder(self, tmp_path, java_available) -> None:
        assert _stamps(tmp_path, """
    void f(Launcher l) throws Exception {
        var p = l.start();
        BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()));
        String line = r.readLine();
    }
""", "readLine", _BR) == [None]

    def test_exec_on_something_that_is_not_a_runtime(self, tmp_path, java_available) -> None:
        assert _stamps(tmp_path, """
    void f(Shell sh) throws Exception {
        var p = sh.exec("ls");
        BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()));
        String line = r.readLine();
    }
""", "readLine", _BR) == [None]

    def test_a_non_process_accessor_on_a_process(self, tmp_path, java_available) -> None:
        """``getOutputStream`` is the child's STDIN: a write end, not a read."""
        assert _stamps(tmp_path, """
    void f(Process p) throws Exception {
        BufferedReader r = new BufferedReader(new InputStreamReader(p.getOutputStreamish()));
        String line = r.readLine();
    }
""", "readLine", _BR) == [None]


def _parse(src: str):
    import tree_sitter
    import tree_sitter_java

    raw = src.encode("utf-8")
    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_java.language()))
    return parser.parse(raw).root_node, raw


def _first(node, node_type: str, text: str, raw: bytes):
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type == node_type and raw[cur.start_byte:cur.end_byte].decode() == text:
            return cur
        stack.extend(reversed(cur.children))
    raise AssertionError(f"no {node_type} {text!r}")  # pragma: no cover


class TestTheHelpersUnit:
    """Each refusal branch pinned by its own input."""

    def test_a_name_outside_any_method_has_no_declared_type(self, java_available) -> None:
        root, raw = _parse("public class P { Process q = null; { q.getInputStream(); } }")
        node = _first(root, "identifier", "q", raw)
        assert _java_declared_type_of(node, raw, "q") is None

    def test_a_rebound_name_after_the_use_is_ignored(self, java_available) -> None:
        root, raw = _parse(
            "public class P { void f() {\n"
            "  Process p = null;\n  p.getInputStream();\n  String p2 = null;\n"
            "  Thread t = null;\n} }")
        node = _first(root, "identifier", "p", raw)
        assert _java_declared_type_of(node, raw, "p") == "Process"
        later = _first(root, "identifier", "t", raw)
        assert _java_declared_type_of(node, raw, "t") is None
        assert _java_declared_type_of(later, raw, "t") == "Thread"

    def test_a_qualified_declared_type_is_shortened(self, java_available) -> None:
        root, raw = _parse(
            "public class P { void f(java.lang.Process p) { p.waitFor(); } }")
        node = _first(root, "identifier", "p", raw)
        assert _java_is_process(node, raw)

    def test_a_var_bound_to_a_var_is_one_hop_too_many(self, java_available) -> None:
        root, raw = _parse(
            "public class P { void f() {\n"
            "  var a = new ProcessBuilder(\"x\").start();\n"
            "  var b = a;\n  var c = b;\n  c.getInputStream();\n} }")
        node = _first(root, "identifier", "c", raw)
        assert not _java_is_process(node, raw)

    def test_shapes_that_prove_nothing(self, java_available) -> None:
        root, raw = _parse(
            "public class P { void f() {\n"
            "  Object o = start();\n  Object q = this.field;\n"
            "  Object r = new Thread().start();\n  Object u = unbound;\n"
            "  Object m = foo.make();\n} }")
        bare = _first(root, "method_invocation", "start()", raw)
        assert not _java_is_process(bare, raw)
        field = _first(root, "field_access", "this.field", raw)
        assert not _java_is_process(field, raw)
        thread = _first(root, "method_invocation", "new Thread().start()", raw)
        assert not _java_is_process(thread, raw)
        unbound = _first(root, "identifier", "unbound", raw)
        assert not _java_is_process(unbound, raw)
        other = _first(root, "method_invocation", "foo.make()", raw)
        assert not _java_is_process(other, raw)

    def test_a_stream_expression_that_is_neither_a_call_nor_a_creation(
        self, java_available,
    ) -> None:
        """A field read names no producer, so the unwrap abstains on it --
        the branch the ``method_invocation`` case now sits beside."""
        root, raw = _parse("public class P { void f() { Object x = this.in; } }")
        field = _first(root, "field_access", "this.in", raw)
        assert _java_stream_kind_of(field, raw) is None
