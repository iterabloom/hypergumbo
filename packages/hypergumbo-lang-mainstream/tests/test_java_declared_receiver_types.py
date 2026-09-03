# SPDX-License-Identifier: AGPL-3.0-or-later
"""A DECLARED type is a receiver-typing source (INV-vugon), and the return-type
registry is consumed rather than only built (WI-gajuh).

INV-vugon. ``java.py`` typed a local only from its INITIALIZER — ``new T()``,
or the return type of a resolved in-repo method — and never read the
declaration itself. So ``OutputStream o = sock.getOutputStream(); o.write(b)``
emitted ``java:external:0-0:o.write:unresolved``: the receiver's type sat in
the source, one token to the left, and the analyzer glued the VARIABLE NAME
into the callee instead. On guacamole-client that was 315 typed bindings out
of ~2,180, with 1,210 call-initialised locals, 358 catch parameters and 163
for-each variables never typed. Java DECLARES its types; every one of those
declarations is stronger evidence than any inference.

WI-gajuh. ``method_return_type_registry`` was built from Pass 1 symbols and
never passed to ``_extract_edges`` — write-only, the same defect WI-doluf
found in Go. The registry is now the ONE interface a return type is read
through, keyed ``<Owner>.<method>`` → returned type, where the value is
qualified through the DECLARING file's imports (``java.io.FileWriter``) or is
the bare name of an in-repo class. WI-lalot's library-signature loader feeds
the same dict with the same key shape (``java.net.Socket.getOutputStream`` →
``java.io.OutputStream``); the consumer does not know which rows came from
where.

Assertions read the dst the edge actually carries, and where the point is
catalogue reach they go through production's own ``_match_propagation_entry``
rather than eyeballing a string — reaching the catalogue entry is the
contract, the dst spelling is not.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.java import analyze_java


def _unresolved(edges: list[Edge], method: str, *, line: int | None = None) -> Edge:
    """The unresolved edge for ``method`` (optionally at ``line``)."""
    hits = [
        e for e in edges
        if not e.is_resolved
        and e.edge_type == "calls"
        and e.dst.split(":")[-2].split(".")[-1] == method
        and (line is None or e.line == line)
    ]
    assert len(hits) == 1, [e.dst for e in hits]
    return hits[0]


def _catalogue_match(edge: Edge) -> str | None:
    """The catalogue ``qualified_name`` production's matcher returns for ``edge``."""
    from hypergumbo_core.taint import (
        _build_callee_index,
        _match_propagation_entry,
        load_builtin_taint_catalog,
    )

    catalog = load_builtin_taint_catalog()
    index = _build_callee_index(catalog.sinks_for_language("java"))
    ambiguous = catalog.ambiguous_names_for_language("java")
    matched = _match_propagation_entry(
        index, edge.dst, ambiguous,
        (edge.meta or {}).get("call_construct"),
        is_resolved=edge.is_resolved, language="java",
    )
    return None if matched is None else matched.qualified_name


class TestDeclaredLocalType:
    """``T x = <anything>`` binds ``x`` to ``T``."""

    def test_call_initialised_local_names_the_declared_type(
        self, tmp_path: Path,
    ) -> None:
        """The 1,210-site shape: ``Connection c = DriverManager.getConnection(u)``."""
        (tmp_path / "Db.java").write_text("""
import java.sql.Connection;
import java.sql.DriverManager;

public class Db {
    void q(String u, String s) throws Exception {
        Connection c = DriverManager.getConnection(u);
        c.prepareStatement(s);
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "prepareStatement")
        assert edge.dst == "java:java.sql.Connection:0-0:prepareStatement:unresolved", edge.dst
        meta = edge.meta or {}
        assert meta.get("receiver_type_hint") == "Connection"
        assert meta.get("call_construct") == "method"
        assert edge.dst_ref is not None
        assert edge.dst_ref.module_path == "java.sql.Connection"
        assert edge.dst_ref.name == "prepareStatement"

    def test_declared_local_reaches_the_catalogue(self, tmp_path: Path) -> None:
        """The point of the module slot: production's matcher accepts it.

        ``commit`` rather than ``prepareStatement``: the latter is a
        ``db_read`` row, which the taint catalogue indexes as a SOURCE, and
        the sink matcher is the one this asserts through.
        """
        (tmp_path / "Db.java").write_text("""
import java.sql.Connection;
import java.sql.DriverManager;

public class Db {
    void q(String u) throws Exception {
        Connection c = DriverManager.getConnection(u);
        c.commit();
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "commit")
        assert edge.dst == "java:java.sql.Connection:0-0:commit:unresolved", edge.dst
        assert _catalogue_match(edge) == "java.sql.Connection.commit"

    def test_null_field_and_later_assignment_initialisers_are_all_typed(
        self, tmp_path: Path,
    ) -> None:
        """The initializer's SHAPE is irrelevant; the declaration is the evidence."""
        (tmp_path / "V.java").write_text("""
import java.io.OutputStream;
import java.net.Socket;

public class V {
    OutputStream field;
    Socket sock;

    void declaredNull() throws Exception {
        OutputStream o = null;
        o.write(1);
    }
    void declaredField() throws Exception {
        OutputStream o = this.field;
        o.write(2);
    }
    void declaredThenAssigned() throws Exception {
        OutputStream o;
        o = sock.getOutputStream();
        o.write(4);
    }
    void declaredCast(Object x) throws Exception {
        OutputStream o = (OutputStream) x;
        o.write(5);
    }
}
""")
        edges = analyze_java(tmp_path).edges
        for line in (11, 15, 20, 24):
            edge = _unresolved(edges, "write", line=line)
            assert edge.dst == "java:java.io.OutputStream:0-0:write:unresolved", (
                line, edge.dst,
            )

    def test_constructor_initialiser_still_narrows_the_declared_type(
        self, tmp_path: Path,
    ) -> None:
        """``OutputStream o = new FileOutputStream(p)`` keeps the CONCRETE type.

        The declaration is a baseline, not a ceiling: an initializer that
        names a subtype is more specific and is what the catalogue rows are
        written against (``java.io.FileOutputStream.write`` is catalogued;
        ``java.io.OutputStream.write`` is not). This is the behaviour the
        analyzer had before the declaration was read at all; pinned so
        reading the declaration cannot regress it.
        """
        (tmp_path / "W.java").write_text("""
import java.io.OutputStream;
import java.io.FileOutputStream;

public class W {
    void w(String p, byte[] b) throws Exception {
        OutputStream o = new FileOutputStream(p);
        o.write(b);
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "write")
        assert edge.dst == "java:java.io.FileOutputStream:0-0:write:unresolved", edge.dst
        assert _catalogue_match(edge) == "java.io.FileOutputStream.write"

    def test_a_return_type_inference_does_not_override_a_declared_type(
        self, tmp_path: Path,
    ) -> None:
        """``Writer w = F.makeWriter()`` stays ``java.io.Writer``.

        The return-type lookup is name-keyed, so it can answer for an
        overload, a covariant override, or (through a chained receiver
        landing in the current-class lookup) an unrelated method of the same
        name; the declaration is the one fact the source states. A
        constructor initializer is different -- the object IS that class --
        and keeps narrowing (pinned above).
        """
        (tmp_path / "F.java").write_text("""
import java.io.FileWriter;
import java.io.IOException;

public class F {
    static FileWriter makeWriter() throws IOException { return new FileWriter("/tmp/x"); }
}
""")
        (tmp_path / "M.java").write_text("""
import java.io.Writer;

public class M {
    void m() throws Exception {
        Writer w = F.makeWriter();
        w.write("x");
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "write")
        assert edge.dst == "java:java.io.Writer:0-0:write:unresolved", edge.dst
        assert (edge.meta or {}).get("receiver_type_hint") == "Writer"

    def test_a_chained_receiver_landing_on_a_same_named_method_cannot_retype_a_local(
        self, tmp_path: Path,
    ) -> None:
        """Read back from guacamole-client: ``String s = x.attrs().get(k); s.trim()``.

        The chained ``.get(k)`` has no receiver name, so the analyzer's
        current-class lookup resolves it to the anonymous ``Supplier``'s
        ``get()`` in the same class, whose return type is ``Future``; before
        the declared-type rule that inference retyped ``s`` and ``s.trim()``
        was emitted as ``java.util.concurrent.Future.trim``.
        """
        (tmp_path / "K.java").write_text("""
import java.util.Map;
import java.util.concurrent.Future;
import java.util.function.Supplier;

public class K {
    Map<String, String> attrs;

    Supplier<Future<String>> supplier() {
        return new Supplier<Future<String>>() {
            public Future<String> get() { return null; }
        };
    }

    String pick(K other, String k) {
        String s = other.attrs().get(k);
        return s.trim();
    }

    Map<String, String> attrs() { return attrs; }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "trim")
        assert edge.dst == "java:java.lang.String:0-0:trim:unresolved", edge.dst

    def test_var_declaration_binds_nothing_by_itself(self, tmp_path: Path) -> None:
        """``var`` carries no type; only an inference can bind it (unchanged)."""
        (tmp_path / "X.java").write_text("""
import java.io.OutputStream;

public class X {
    OutputStream field;
    void m() throws Exception {
        var o = field;
        o.write(1);
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "write")
        assert edge.dst == "java:external:0-0:o.write:unresolved", edge.dst


class TestCatchAndForEachBindings:
    def test_catch_parameter_is_typed(self, tmp_path: Path) -> None:
        (tmp_path / "C.java").write_text("""
import java.io.IOException;

public class C {
    void m() {
        try {
            run();
        } catch (IOException e) {
            e.getMessage();
        }
    }
    void run() throws IOException {}
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "getMessage")
        assert edge.dst == "java:java.io.IOException:0-0:getMessage:unresolved", edge.dst

    def test_multi_catch_parameter_is_left_untyped(self, tmp_path: Path) -> None:
        """``catch (A | B e)`` has no single declared type; nothing is asserted."""
        (tmp_path / "C.java").write_text("""
import java.io.IOException;
import java.sql.SQLException;

public class C {
    void m() {
        try {
            run();
        } catch (IOException | SQLException e) {
            e.getMessage();
        }
    }
    void run() throws IOException, SQLException {}
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "getMessage")
        assert edge.dst == "java:external:0-0:e.getMessage:unresolved", edge.dst

    def test_try_with_resources_declaration_is_typed(self, tmp_path: Path) -> None:
        """``try (RandomAccessFile f = new RandomAccessFile(p, "rw"))`` binds ``f``.

        Read back from keycloak: every ``LDAPQuery`` and ``RandomAccessFile``
        there is declared as a resource, and the old arm had typed them only
        by a leak from another method's parameter.
        """
        (tmp_path / "R.java").write_text("""
import java.io.RandomAccessFile;
import java.sql.Connection;

public class R {
    void m(String p, Connection outer) throws Exception {
        try (RandomAccessFile f = new RandomAccessFile(p, "rw"); Connection c = outer) {
            f.getChannel();
            c.commit();
        }
    }
}
""")
        edges = analyze_java(tmp_path).edges
        assert _unresolved(edges, "getChannel").dst == (
            "java:java.io.RandomAccessFile:0-0:getChannel:unresolved"
        )
        assert _unresolved(edges, "commit").dst == (
            "java:java.sql.Connection:0-0:commit:unresolved"
        )

    def test_a_resource_that_names_an_existing_variable_declares_nothing(
        self, tmp_path: Path,
    ) -> None:
        """Java 9 ``try (conn)``: no type at the resource; the earlier binding stands."""
        (tmp_path / "R9.java").write_text("""
import java.sql.Connection;

public class R9 {
    void m(Connection conn) throws Exception {
        try (conn) {
            conn.commit();
        }
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "commit")
        assert edge.dst == "java:java.sql.Connection:0-0:commit:unresolved", edge.dst

    def test_a_lambda_parameter_is_typed_only_when_written(self, tmp_path: Path) -> None:
        (tmp_path / "Lam.java").write_text("""
import java.io.File;
import java.util.List;

public class Lam {
    void m(List<File> xs) {
        xs.forEach((File f) -> f.delete());
        xs.forEach(g -> g.exists());
    }
}
""")
        edges = analyze_java(tmp_path).edges
        assert _unresolved(edges, "delete").dst == "java:java.io.File:0-0:delete:unresolved"
        assert _unresolved(edges, "exists").dst == "java:external:0-0:g.exists:unresolved"

    def test_for_each_variable_is_typed_and_reaches_the_catalogue(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "L.java").write_text("""
import java.io.File;
import java.util.List;

public class L {
    void clean(List<File> files) {
        for (File f : files) {
            f.delete();
        }
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "delete")
        assert edge.dst == "java:java.io.File:0-0:delete:unresolved", edge.dst
        assert _catalogue_match(edge) == "java.io.File.delete"


class TestFieldShapes:
    def test_a_generic_or_inline_qualified_field_binds_its_base_type(
        self, tmp_path: Path,
    ) -> None:
        """Only a bare ``type_identifier`` field child was read before."""
        (tmp_path / "Fd.java").write_text("""
import java.util.Map;

public class Fd {
    private Map<String, String> attrs;
    private java.sql.Connection conn;

    void m(String k) throws Exception {
        attrs.get(k);
        conn.commit();
    }
}
""")
        edges = analyze_java(tmp_path).edges
        assert _unresolved(edges, "get").dst == "java:java.util.Map:0-0:get:unresolved"
        assert _unresolved(edges, "commit").dst == (
            "java:java.sql.Connection:0-0:commit:unresolved"
        )

    def test_a_record_component_binds_like_a_field(self, tmp_path: Path) -> None:
        """Read back from keycloak: ``record Connector(CountDownLatch latch, ..)``."""
        (tmp_path / "Rec.java").write_text("""
import java.util.concurrent.CountDownLatch;

public class Rec {
    record Connector(CountDownLatch latch, String name) implements Runnable {
        public void run() {
            latch.countDown();
        }
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "countDown")
        assert edge.dst == (
            "java:java.util.concurrent.CountDownLatch:0-0:countDown:unresolved"
        ), edge.dst

    def test_an_inherited_library_field_is_qualified_through_the_parents_imports(
        self, tmp_path: Path,
    ) -> None:
        """``protected Connection conn`` in Base, ``conn.commit()`` in Child.

        Read back from keycloak: every provider subclass uses ``realm``,
        ``session`` and ``event`` fields declared in an abstract parent, and
        the old arm had typed them only by a leak from a sibling method. The
        child file does NOT import ``Connection``; the parent's imports are
        the ones that name it.
        """
        (tmp_path / "Base.java").write_text("""
import java.sql.Connection;

public abstract class Base {
    protected Connection conn;
}
""")
        (tmp_path / "Child.java").write_text("""
public class Child extends Base {
    void m() throws Exception {
        conn.commit();
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "commit")
        assert edge.dst == "java:java.sql.Connection:0-0:commit:unresolved", edge.dst
        # Site 3 for the linker is untouched: the field name and the class
        # are still handed over.
        assert (edge.meta or {}).get("inherited_field_receiver") == "conn"
        assert (edge.meta or {}).get("enclosing_class") == "Child"

    def test_the_inherited_field_walk_stops_at_its_depth_cap(self) -> None:
        """A chain deeper than ``depth_cap`` answers ``None`` rather than walking on."""
        from hypergumbo_lang_mainstream.java import _inherited_field_module

        parents = {"C": "B", "B": "A"}
        fields = {"A": {"conn": "java.sql.Connection"}}
        assert _inherited_field_module(
            "C", "conn", parents, fields, {}, None, frozenset(), depth_cap=1,
        ) is None
        assert _inherited_field_module(
            "C", "conn", parents, fields, {}, None, frozenset(), depth_cap=2,
        ) == "java.sql.Connection"

    def test_an_inherited_project_class_field_is_left_to_the_linker(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "Base.java").write_text("""
public abstract class Base {
    protected Helper helper;
}
""")
        (tmp_path / "Helper.java").write_text("""
public class Helper {
    void run() {}
}
""")
        (tmp_path / "Child.java").write_text("""
public class Child extends Base {
    void m() {
        helper.run();
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "run")
        assert edge.dst == "java:external:0-0:helper.run:unresolved", edge.dst
        assert (edge.meta or {}).get("receiver_type_hint") is None
        assert (edge.meta or {}).get("inherited_field_receiver") == "helper"


class TestLocalScope:
    def test_a_local_does_not_leak_into_the_next_method(self, tmp_path: Path) -> None:
        """A local named like a field must not retype the field elsewhere.

        ``var_types`` is file-scoped. Before this, ``File f = new File(p)`` in
        one method left ``f`` bound to ``File`` for every LATER method in the
        file, so a sibling method's use of the ``Socket f`` FIELD was typed
        ``java.io.File``. Locals (declared, constructed, or inferred from a
        return) are now unbound when the next method begins; fields and
        parameters keep their file-wide binding.
        """
        (tmp_path / "S.java").write_text("""
import java.io.File;
import java.net.Socket;

public class S {
    Socket f;

    void a(String p) throws Exception {
        File f = new File(p);
        f.delete();
    }
    void b() throws Exception {
        f.getInputStream();
    }
}
""")
        edges = analyze_java(tmp_path).edges
        assert _unresolved(edges, "delete").dst == (
            "java:java.io.File:0-0:delete:unresolved"
        )
        assert _unresolved(edges, "getInputStream").dst == (
            "java:java.net.Socket:0-0:getInputStream:unresolved"
        )


    def test_an_anonymous_class_inside_the_method_does_not_close_its_scope(
        self, tmp_path: Path,
    ) -> None:
        """A method nested in a method (anonymous class body) is its own scope.

        Found by reading the guacamole-client churn back: ``new
        FilenameFilter() { public boolean accept(..) {..} }`` carries a
        ``method_declaration``, and closing the enclosing scope there unbound
        ``protocol_directory`` for the ``getAbsolutePath()`` call after it.
        The inner method's own locals must also not leak into the outer one.
        """
        (tmp_path / "A.java").write_text("""
import java.io.File;
import java.io.FilenameFilter;
import java.net.Socket;

public class A {
    void outer(String p) throws Exception {
        File dir = new File(p);
        File[] files = dir.listFiles(new FilenameFilter() {
            public boolean accept(File f, String s) {
                Socket dir = null;
                dir.getInputStream();
                return f.exists();
            }
        });
        dir.getAbsolutePath();
    }
}
""")
        edges = analyze_java(tmp_path).edges
        assert _unresolved(edges, "listFiles").dst == (
            "java:java.io.File:0-0:listFiles:unresolved"
        )
        assert _unresolved(edges, "getInputStream").dst == (
            "java:java.net.Socket:0-0:getInputStream:unresolved"
        )
        assert _unresolved(edges, "exists").dst == "java:java.io.File:0-0:exists:unresolved"
        assert _unresolved(edges, "getAbsolutePath").dst == (
            "java:java.io.File:0-0:getAbsolutePath:unresolved"
        )


class TestTypeQualification:
    def test_java_lang_type_qualifies_without_an_import(self, tmp_path: Path) -> None:
        """``Runtime rt = Runtime.getRuntime(); rt.exec(cmd)`` is a subprocess sink.

        JLS 7.3 imports ``java.lang.*`` into every compilation unit, so a bare
        ``Runtime`` that is neither imported nor a project class names
        ``java.lang.Runtime`` — the same implicit-import rule the wildcard
        slot already applies to a capitalised static receiver.
        """
        (tmp_path / "R.java").write_text("""
public class R {
    void run(String cmd) throws Exception {
        Runtime rt = Runtime.getRuntime();
        rt.exec(cmd);
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "exec")
        assert "java.lang.Runtime" in edge.dst, edge.dst
        assert _catalogue_match(edge) == "java.lang.Runtime.exec"

    def test_java_lang_is_a_closed_list(self, tmp_path: Path) -> None:
        """``Object`` IS ``java.lang.Object``; an unimported ``InputStream`` is NOT.

        A file that forgot ``import java.io.InputStream`` still declares
        ``InputStream``; the only honest slot for it is ``external``. Writing
        ``java.lang.InputStream`` would be a present-but-wrong hint, worse
        than untyped (INV-kotob).
        """
        (tmp_path / "O.java").write_text("""
public class O {
    void a(Object o, InputStream in) throws Exception {
        o.hashCode();
        in.read();
    }
}
""")
        edges = analyze_java(tmp_path).edges
        assert _unresolved(edges, "hashCode").dst == (
            "java:java.lang.Object:0-0:hashCode:unresolved"
        )
        assert _unresolved(edges, "read").dst == "java:external:0-0:in.read:unresolved"

    def test_a_type_parameter_is_never_a_java_lang_class(self, tmp_path: Path) -> None:
        """``<T> void m(T x) { x.run(); }`` -- ``T`` is in no package at all."""
        (tmp_path / "G.java").write_text("""
public class G<E> {
    E field;
    <T> void m(T x) {
        x.run();
        field.go();
    }
}
""")
        edges = analyze_java(tmp_path).edges
        assert _unresolved(edges, "run").dst == "java:external:0-0:x.run:unresolved"
        assert _unresolved(edges, "go").dst == "java:external:0-0:field.go:unresolved"

    def test_a_bare_type_under_a_wildcard_import_names_the_wildcard_package(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "Sc.java").write_text("""
import java.util.*;

public class Sc {
    void s() {
        Scanner sc = new Scanner(System.in);
        sc.nextLine();
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "nextLine")
        assert edge.dst == "java:java.util.Scanner:0-0:nextLine:unresolved", edge.dst

    def test_a_bare_type_that_is_a_nested_project_class_is_not_sent_to_java_lang(
        self, tmp_path: Path,
    ) -> None:
        """``Entry e`` where ``Entry`` is ``Outer.Entry`` in this repo is not ``java.lang.Entry``."""
        (tmp_path / "Outer.java").write_text("""
public class Outer {
    static class Entry {
        void ping() {}
    }
    Entry make() { return new Entry(); }
    void m() {
        Entry e = make();
        e.pong();
    }
}
""")
        edges = analyze_java(tmp_path).edges
        pong = [e for e in edges if e.dst.split(":")[-2].endswith("pong")]
        assert pong, [e.dst for e in edges]
        assert all("java.lang" not in e.dst for e in pong), [e.dst for e in pong]

    def test_a_nested_type_on_an_unimported_outer_is_left_alone(
        self, tmp_path: Path,
    ) -> None:
        """``Outer.Entry e`` with ``Outer`` neither imported nor a package path: no slot."""
        (tmp_path / "Outer.java").write_text("""
public class Outer {
    static class Entry {
        void ping() {}
    }
    Entry make() { return new Entry(); }
    void m() {
        Outer.Entry e = make();
        e.pong();
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "pong")
        assert edge.dst == "java:external:0-0:e.pong:unresolved", edge.dst
        assert (edge.meta or {}).get("receiver_type_hint") == "Outer.Entry"

    def test_inline_fully_qualified_type_is_its_own_module(self, tmp_path: Path) -> None:
        (tmp_path / "Q.java").write_text("""
public class Q {
    void q(String s) throws Exception {
        java.sql.Connection c = null;
        c.prepareStatement(s);
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "prepareStatement")
        assert edge.dst == "java:java.sql.Connection:0-0:prepareStatement:unresolved", edge.dst

    def test_nested_type_qualifies_through_the_outer_import(self, tmp_path: Path) -> None:
        (tmp_path / "N.java").write_text("""
import java.util.Map;

public class N {
    void n(Map<String, String> m) {
        for (Map.Entry<String, String> en : m.entrySet()) {
            en.getKey();
        }
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "getKey")
        assert edge.dst == "java:java.util.Map.Entry:0-0:getKey:unresolved", edge.dst


class TestReturnTypeRegistryConsumer:
    """WI-gajuh: the registry built in Pass 1 is read in Pass 2."""

    def test_var_bound_to_a_cross_file_in_repo_return_is_typed(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "F.java").write_text("""
import java.io.FileWriter;
import java.io.IOException;

public class F {
    static FileWriter makeWriter() throws IOException { return new FileWriter("/tmp/x"); }
}
""")
        (tmp_path / "M2.java").write_text("""
import java.io.FileWriter;
import java.io.IOException;

public class M2 {
    void crossFileVar() throws IOException {
        var w = F.makeWriter();
        w.write("z");
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "write")
        assert edge.dst == "java:java.io.FileWriter:0-0:write:unresolved", edge.dst
        assert _catalogue_match(edge) == "java.io.FileWriter.write"

    def test_registry_value_is_qualified_through_the_declaring_files_imports(
        self, tmp_path: Path,
    ) -> None:
        """The caller need not import the type; the declaring file did."""
        (tmp_path / "F.java").write_text("""
import java.io.FileWriter;
import java.io.IOException;

public class F {
    static FileWriter makeWriter() throws IOException { return new FileWriter("/tmp/x"); }
}
""")
        (tmp_path / "M3.java").write_text("""
public class M3 {
    void crossFileVar() throws Exception {
        var w = F.makeWriter();
        w.write("q");
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "write")
        assert edge.dst == "java:java.io.FileWriter:0-0:write:unresolved", edge.dst

    def test_in_repo_return_type_still_hands_the_project_class_to_the_linker(
        self, tmp_path: Path,
    ) -> None:
        """An in-repo return type keeps chaining to the project class (unchanged).

        The analyzer does not emit the resolved edge itself — WI-puvil lifted
        Site-2 resolution into the ``inherited_calls`` linker — it stashes the
        inferred type as ``receiver_type_hint`` and leaves the module slot
        alone (a project class is not a module). Pinned so the registry
        consumer cannot start writing a bare project name into the slot.
        """
        (tmp_path / "F.java").write_text("""
public class F {
    static Client makeClient() { return new Client(); }
}
""")
        (tmp_path / "Client.java").write_text("""
public class Client {
    void send() {}
}
""")
        (tmp_path / "M4.java").write_text("""
public class M4 {
    void go() {
        var c = F.makeClient();
        c.send();
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "send")
        assert (edge.meta or {}).get("receiver_type_hint") == "Client"
        assert edge.dst == "java:external:0-0:c.send:unresolved", edge.dst

    def test_a_chain_on_a_bare_or_static_in_repo_call_is_typed(
        self, tmp_path: Path,
    ) -> None:
        """``makeWriter().write(x)`` and ``F.makeWriter().write(x)`` both reach the registry."""
        (tmp_path / "F.java").write_text("""
import java.io.FileWriter;
import java.io.IOException;

public class F {
    static FileWriter makeWriter() throws IOException { return new FileWriter("/tmp/x"); }
    void own() throws IOException {
        makeWriter().write("a");
    }
}
""")
        (tmp_path / "M5.java").write_text("""
public class M5 {
    void other() throws Exception {
        F.makeWriter().write("b");
    }
}
""")
        edges = analyze_java(tmp_path).edges
        writes = sorted(
            (e.src.split(":")[-2], e.dst) for e in edges
            if not e.is_resolved and e.edge_type == "calls" and e.dst.endswith(":write:unresolved")
        )
        assert writes == [
            ("F.own", "java:java.io.FileWriter:0-0:write:unresolved"),
            ("M5.other", "java:java.io.FileWriter:0-0:write:unresolved"),
        ], writes

    def test_library_return_rows_type_the_assignment_and_the_chain(
        self, tmp_path: Path,
    ) -> None:
        """The WI-lalot interface: a library row keyed ``<fqn-owner>.<method>``.

        The registry has no library rows today; this drives ``_extract_edges``
        with one so the consumer's contract is pinned before the loader
        exists. Both binding forms must read it: the assignment
        (``var o = s.getOutputStream(); o.write(b)``) and the chain
        (``s.getOutputStream().write(b)``).
        """
        from hypergumbo_core.ir import AnalysisRun
        from hypergumbo_lang_mainstream.java import (
            PASS_ID,
            PASS_VERSION,
            _extract_edges,
            _extract_imports,
            _extract_symbols,
            _get_java_parser,
        )

        src = b"""
import java.net.Socket;

public class T {
    void t(Socket s, byte[] b) throws Exception {
        var o = s.getOutputStream();
        o.write(b);
        s.getOutputStream().write(b);
    }
}
"""
        path = tmp_path / "T.java"
        path.write_bytes(src)
        parser = _get_java_parser()
        assert parser is not None
        tree = parser.parse(src)
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
        symbols = _extract_symbols(tree, src, path, run, repo_root=tmp_path)
        global_symbols = {s.name: s for s in symbols}
        class_symbols = {s.name: s for s in symbols if s.kind == "class"}
        edges = _extract_edges(
            tree, src, path, run, global_symbols, class_symbols,
            _extract_imports(tree, src),
            method_return_type_registry={
                "java.net.Socket.getOutputStream": "java.io.OutputStream",
            },
        )
        assigned = _unresolved(edges, "write", line=7)
        chained = _unresolved(edges, "write", line=8)
        assert assigned.dst == "java:java.io.OutputStream:0-0:write:unresolved", assigned.dst
        assert chained.dst == "java:java.io.OutputStream:0-0:write:unresolved", chained.dst
        assert (chained.meta or {}).get("call_construct") == "method"
