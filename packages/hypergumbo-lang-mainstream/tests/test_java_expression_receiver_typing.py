# SPDX-License-Identifier: AGPL-3.0-or-later
"""A java receiver that is an EXPRESSION, or a parameter spelled with its package,
is typed like a named, imported one (WI-halin).

Measured 2026-09-25 on the production path (``~/hypergumbo_lab_notebook/
hidar_09252026``): a declared parameter, a declared local, a field, ``this.field``
and a wildcard import each typed ``c.doFinal(ct)`` as ``javax.crypto.Cipher``,
while four ordinary spellings emitted the ``external`` module placeholder, so no
catalogued method row could match them:

1. a chain on a static factory -- ``Cipher.getInstance("AES").doFinal(ct)``;
2. ``var`` bound from a static factory -- ``var c = Cipher.getInstance("AES")``;
3. a fully-qualified PARAMETER type -- ``void dec(javax.crypto.Cipher c, ..)``;
4. a constructor-expression receiver -- ``new FileOutputStream(p).write(pt)``.

Two defects sat under shapes 1 and 2 besides the missing lookup arms, and both
are pinned here because each alone leaves the shapes broken:

- THE SHIPPED LIBRARY ROWS WERE INERT. ``library_signatures/java.yaml`` keyed its
  rows by the SHORT owner (``Socket.getOutputStream``) while every java lookup
  asks with the owner the file establishes (``java.net.Socket.getOutputStream``),
  so not one of its rows typed anything -- not even the shapes the registry's
  own consumer test pins with a hand-built registry.
- A RECEIVER EXPRESSION WAS READ AS AN IMPLICIT ``this``. Any receiver that is not
  an identifier or field access left ``receiver_name`` at ``None``, which the
  current-class lookup took to mean ``this``: ``new FileOutputStream(p).write(pt)``
  inside a class declaring its own ``write`` became a RESOLVED call to that
  ``write``, and the sink edge was never emitted.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.java import analyze_java


def _calls(tmp_path: Path, method: str) -> list[Edge]:
    return [
        e for e in analyze_java(tmp_path).edges
        if e.edge_type == "calls" and e.dst.split(":")[-2].split(".")[-1] == method
    ]


def _module_of(tmp_path: Path, method: str, *, line: int | None = None) -> str:
    """The module slot of the ONE unresolved edge for ``method`` (at ``line``)."""
    hits = [
        e for e in _calls(tmp_path, method)
        if not e.is_resolved and (line is None or e.line == line)
    ]
    assert len(hits) == 1, [(e.line, e.dst) for e in hits]
    return hits[0].dst.split(":")[1]


def _sink_match(edge: Edge) -> str | None:
    """The sink row production's matcher returns for ``edge``."""
    from hypergumbo_core.taint import (
        _build_callee_index,
        _match_propagation_entry,
        load_builtin_taint_catalog,
    )

    catalog = load_builtin_taint_catalog()
    matched = _match_propagation_entry(
        _build_callee_index(catalog.sinks_for_language("java")),
        edge.dst,
        catalog.ambiguous_names_for_language("java"),
        (edge.meta or {}).get("call_construct"),
        is_resolved=edge.is_resolved, language="java",
    )
    return None if matched is None else matched.qualified_name


_CIPHER = "javax.crypto.Cipher"


class TestTheFiledShapes:
    def test_shape_1_a_chain_on_a_static_factory(self, tmp_path: Path) -> None:
        (tmp_path / "A.java").write_text("""
import javax.crypto.Cipher;
public class A {
    byte[] dec(byte[] ct) throws Exception {
        return Cipher.getInstance("AES").doFinal(ct);
    }
}
""")
        assert _module_of(tmp_path, "doFinal") == _CIPHER

    def test_shape_2_var_bound_from_a_static_factory(self, tmp_path: Path) -> None:
        (tmp_path / "A.java").write_text("""
import javax.crypto.Cipher;
public class A {
    byte[] dec(byte[] ct) throws Exception {
        var c = Cipher.getInstance("AES");
        return c.doFinal(ct);
    }
}
""")
        assert _module_of(tmp_path, "doFinal") == _CIPHER

    def test_shape_3_a_fully_qualified_parameter_type(self, tmp_path: Path) -> None:
        (tmp_path / "A.java").write_text("""
public class A {
    byte[] dec(javax.crypto.Cipher c, byte[] ct) throws Exception {
        return c.doFinal(ct);
    }
}
""")
        assert _module_of(tmp_path, "doFinal") == _CIPHER

    def test_shape_3_a_qualified_generic_parameter_binds_its_base(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "A.java").write_text("""
public class A {
    void m(java.util.List<String> xs) { xs.clear(); }
}
""")
        assert _module_of(tmp_path, "clear") == "java.util.List"

    def test_shape_4_a_constructor_expression_receiver_reaches_the_sink(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "A.java").write_text("""
import java.io.FileOutputStream;
public class A {
    void w(byte[] pt) throws Exception {
        new FileOutputStream("out.bin").write(pt);
    }
}
""")
        [edge] = [e for e in _calls(tmp_path, "write") if not e.is_resolved]
        assert edge.dst.split(":")[1] == "java.io.FileOutputStream", edge.dst
        assert (edge.meta or {}).get("call_construct") == "method"
        assert _sink_match(edge) == "java.io.FileOutputStream.write"

    def test_shape_4_a_package_qualified_constructor(self, tmp_path: Path) -> None:
        (tmp_path / "A.java").write_text("""
public class A {
    void w(byte[] pt) throws Exception {
        new java.io.FileOutputStream("out.bin").write(pt);
    }
}
""")
        assert _module_of(tmp_path, "write") == "java.io.FileOutputStream"


class TestTheShippedLibraryRowsReach:
    """Each arm uses a row ``library_signatures/java.yaml`` already shipped."""

    def test_an_instance_producer_types_the_var_and_the_chain(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "A.java").write_text("""
import java.net.Socket;
public class A {
    void w(Socket s, byte[] b) throws Exception {
        var o = s.getOutputStream();
        o.write(b);
        s.getOutputStream().write(b);
    }
}
""")
        assert _module_of(tmp_path, "write", line=6) == "java.io.OutputStream"
        assert _module_of(tmp_path, "write", line=7) == "java.io.OutputStream"

    def test_a_static_producer_types_the_var_and_the_chain(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "A.java").write_text("""
import java.nio.file.Files;
import java.nio.file.Path;
public class A {
    void w(Path p, byte[] b) throws Exception {
        var o = Files.newOutputStream(p);
        o.write(b);
        Files.newOutputStream(p).write(b);
        java.nio.file.Files.newOutputStream(p).flush();
    }
}
""")
        assert _module_of(tmp_path, "write", line=7) == "java.io.OutputStream"
        assert _module_of(tmp_path, "write", line=8) == "java.io.OutputStream"
        assert _module_of(tmp_path, "flush") == "java.io.OutputStream"

    def test_a_java_lang_owner_needs_no_import_and_chains_twice(
        self, tmp_path: Path,
    ) -> None:
        """``Runtime.getRuntime()`` -> ``Runtime``, ``.exec(..)`` -> ``Process``."""
        (tmp_path / "A.java").write_text("""
public class A {
    void r(String cmd) throws Exception {
        Runtime.getRuntime().exec(cmd).waitFor();
    }
}
""")
        assert _module_of(tmp_path, "exec") == "java.lang.Runtime"
        assert _module_of(tmp_path, "waitFor") == "java.lang.Process"

    def test_a_constructor_rooted_chain_reaches_its_row(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "A.java").write_text("""
import java.net.Socket;
public class A {
    void w(String h, byte[] b) throws Exception {
        new Socket(h, 80).getOutputStream().write(b);
    }
}
""")
        assert _module_of(tmp_path, "write") == "java.io.OutputStream"

    def test_a_wildcard_owner_reaches_its_row(self, tmp_path: Path) -> None:
        (tmp_path / "A.java").write_text("""
import java.nio.file.*;
public class A {
    void w(Path p, byte[] b) throws Exception {
        Files.newOutputStream(p).write(b);
    }
}
""")
        assert _module_of(tmp_path, "write") == "java.io.OutputStream"


class TestWhatMustNotBeTyped:
    def test_an_untyped_lowercase_receiver_is_not_read_as_a_type(
        self, tmp_path: Path,
    ) -> None:
        """Under a wildcard a capitalised name may be a class; ``files`` may not."""
        (tmp_path / "A.java").write_text("""
import java.nio.file.*;
public class A {
    void w(Path p, byte[] b) throws Exception {
        var files = make();
        files.newOutputStream(p).write(b);
        var o = files.newOutputStream(p);
        o.write(b);
    }
}
""")
        assert _module_of(tmp_path, "write", line=6) == "external"
        assert _module_of(tmp_path, "write", line=8) == "external"

    def test_a_typed_variable_is_never_read_as_its_types_static_owner(
        self, tmp_path: Path,
    ) -> None:
        """A local named like a class is the local (``Files`` bound to a ``Box``)."""
        (tmp_path / "A.java").write_text("""
import java.nio.file.Files;
import java.nio.file.Path;
public class A {
    void w(Path p, byte[] b, Box Files) throws Exception {
        Files.newOutputStream(p).write(b);
    }
}
class Box {}
""")
        assert _module_of(tmp_path, "write") == "external"

    def test_a_type_parameter_is_never_a_static_owner(self, tmp_path: Path) -> None:
        """A ``<Files>`` type parameter under ``java.nio.file.*`` is not that class."""
        (tmp_path / "A.java").write_text("""
import java.nio.file.*;
public class A<Files> {
    void w(Path p, byte[] b) throws Exception {
        Files.newOutputStream(p).write(b);
    }
}
""")
        assert _module_of(tmp_path, "write") == "external"

    def test_an_in_repo_producer_wins_over_the_library_row(
        self, tmp_path: Path,
    ) -> None:
        """A project ``Files`` is described by its own source, not by java.yaml."""
        (tmp_path / "Files.java").write_text("""
public class Files {
    public static Sink newOutputStream(String p) { return new Sink(); }
}
""")
        (tmp_path / "Sink.java").write_text("""
public class Sink {
}
""")
        (tmp_path / "A.java").write_text("""
public class A {
    void w(String p, byte[] b) throws Exception {
        Files.newOutputStream(p).write(b);
        var o = Files.newOutputStream(p);
        o.write(b);
    }
}
""")
        writes = [e for e in _calls(tmp_path, "write") if not e.is_resolved]
        assert len(writes) == 2, [e.dst for e in writes]
        for edge in writes:
            assert "java.io.OutputStream" not in edge.dst, edge.dst
            assert (edge.meta or {}).get("receiver_type_hint") == "Sink", edge.meta

    def test_a_declared_type_still_beats_the_inference(self, tmp_path: Path) -> None:
        (tmp_path / "A.java").write_text("""
import java.io.Closeable;
import java.nio.file.Files;
import java.nio.file.Path;
public class A {
    void w(Path p) throws Exception {
        Closeable o = Files.newOutputStream(p);
        o.close();
    }
}
""")
        assert _module_of(tmp_path, "close") == "java.io.Closeable"


class TestAReceiverExpressionIsNotAnImplicitThis:
    """The current-class lookup belongs to ``m()`` and ``this.m()`` alone."""

    _SRC = """
import java.io.FileOutputStream;
import java.net.Socket;
public class A {
    void write(byte[] b) {}
    void go(Socket s, byte[] pt) throws Exception {
        new FileOutputStream("out.bin").write(pt);
        s.getOutputStream().write(pt);
        ("x" + pt.length).trim().write(pt);
        this.write(pt);
        write(pt);
    }
}
"""

    def test_expression_receivers_do_not_bind_the_enclosing_classes_method(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "A.java").write_text(self._SRC)
        writes = _calls(tmp_path, "write")
        resolved_lines = sorted(e.line for e in writes if e.is_resolved)
        assert resolved_lines == [10, 11], [(e.line, e.dst) for e in writes]
        by_line = {e.line: e for e in writes if not e.is_resolved}
        assert by_line[7].dst.split(":")[1] == "java.io.FileOutputStream"
        assert by_line[8].dst.split(":")[1] == "java.io.OutputStream"
        assert by_line[9].dst.split(":")[1] == "external"
        # Nothing on an expression receiver hands the linker the ENCLOSING class.
        for line in (7, 8, 9):
            assert "enclosing_class" not in (by_line[line].meta or {}), by_line[line].meta

    def test_a_project_class_constructor_hands_the_linker_its_own_type(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "A.java").write_text("""
public class A {
    void run() {}
    void go() { new Helper().run(); }
}
class Helper { void run() {} }
""")
        runs = _calls(tmp_path, "run")
        assert not [e for e in runs if e.is_resolved and ":A.run:" in e.dst], [
            e.dst for e in runs
        ]


class TestAConstructorReceiverKeepsItsStreamOrigin:
    """WI-tusav's read narrowing applies to ``new BufferedReader(..).readLine()`` too.

    Typing the receiver selects java.yaml's dual ``fs_read`` / ``ipc_recv`` rows,
    so the origin has to be stamped or a stdin read would read as a file read.
    """

    def test_stdin_and_a_file_are_told_apart(self, tmp_path: Path) -> None:
        (tmp_path / "A.java").write_text("""
import java.io.BufferedReader;
import java.io.FileReader;
import java.io.InputStreamReader;
public class A {
    void r(String p) throws Exception {
        new BufferedReader(new InputStreamReader(System.in)).readLine();
        new BufferedReader(new FileReader(p)).readLine();
    }
}
""")
        reads = {e.line: e for e in _calls(tmp_path, "readLine") if not e.is_resolved}
        assert reads[7].dst.split(":")[1] == "java.io.BufferedReader"
        assert (reads[7].meta or {}).get("io_target_kind") == "std_stream"
        assert (reads[8].meta or {}).get("io_target_kind") == "host_path"


class TestVarFromAConstructorItCannotNameBare:
    def test_qualified_and_generic_constructors_type_a_var(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "A.java").write_text("""
import java.util.ArrayList;
public class A {
    void w(byte[] b) throws Exception {
        var f = new java.io.FileOutputStream("x");
        f.write(b);
        var xs = new ArrayList<String>();
        xs.clear();
    }
}
""")
        assert _module_of(tmp_path, "write") == "java.io.FileOutputStream"
        assert _module_of(tmp_path, "clear") == "java.util.ArrayList"


class TestAStaticImportNamesOnlyABareCall:
    def test_a_receiver_expression_is_not_the_static_import(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "A.java").write_text("""
import static org.junit.Assert.assertEquals;
public class A {
    void t(Object x) {
        assertEquals(1, 2);
        new Checker().assertEquals(1, 2);
    }
}
""")
        by_line = {
            e.line: e for e in _calls(tmp_path, "assertEquals") if not e.is_resolved
        }
        assert by_line[5].dst.split(":")[1] == "org.junit.Assert", by_line[5].dst
        assert by_line[6].dst.split(":")[1] == "external", by_line[6].dst
        assert (by_line[6].meta or {}).get("receiver_type_hint") == "Checker"
