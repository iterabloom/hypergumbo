# SPDX-License-Identifier: AGPL-3.0-or-later
"""The name slot of an unresolved java call names the CALLEE, not the receiver
it was called through (WI-nakut, java half).

WHAT WAS BROKEN. When java could not type a receiver it glued the receiver
IDENTIFIER into the callee name -- ``java:external:0-0:u.mkdirs:unresolved``
rather than ``mkdirs`` -- so every consumer that asks the catalogue about a
name got a string no catalogue has ever keyed. The visible cost is the
boundary-scoped ``untyped_receiver`` caveat, which matches the callee name
against the catalogue's METHOD-KIND rows and therefore could never fire for
java: a clean java verdict said only "N of M method call sites had an untypable
receiver" and never "and those names are catalogued for fs_write".

PARITY, MEASURED RATHER THAN ASSERTED. One fixture per language, same
construct (an untypable local receiver calling a catalogued method), run
through the shipped survey on 2026-09-07:

    go       go:external:0-0:Write:external_symbol
    kotlin   kotlin:external:0-0:write:external_symbol
    python   python:external:0-0:sendall:external_symbol
    rust     rust:external:0-0:write_all:external_symbol
    scala    scala:external:0-0:write:external_symbol
    java     java:external:0-0:u.write:external_symbol      <- the outlier

java was also inconsistent with ITSELF: the static-import and typed-receiver
branches already shortened the name, the explicit-FQ and ``java.lang`` branches
kept the prefix and left ``strip_redundant_module_qualifier`` to remove it
downstream, and the placeholder branch kept it with nothing anywhere able to
remove it. The name slot's content was a function of which resolution branch
fired -- a resolution fact filed under a naming slot.

WHY SHORTENING CANNOT CHANGE A VERDICT, and it is the guard the project already
built rather than a new argument. ``receiver_name`` is assigned only inside
``if object_node is not None``, so every edge this shortens carries
``call_construct="method"``; ``io_boundary.gate_named_entry`` opens with
``if call_construct == "method": return None`` for EVERY kind, and
``taint._register_sanitizer_callers`` refuses an unresolved bare-name sanitizer
match on the same stamp. ``make_unresolved_edge``'s own docstring names this as
the guard that stops "a name-shortening improvement" from becoming a phantom
barrier. Both directions are pinned below as refutation cells.

WHAT IT BUYS, measured on three repositories the same day (edges whose short
name is a catalogued method-kind row, and so become attributable to a
boundary):

    sherpa-onnx     10 edges /  4 names /  3 boundaries
    jenkins        974 edges / 43 names /  9 boundaries   (over 650 already matching)
    cassandra    4,708 edges / 53 names / 10 boundaries   (over 4,953 already matching)

and it corrects a second, quieter falsehood: the UNSCOPED caveat prints a
distinct-method COUNT, and the prefixed spellings inflated it by 29% / 102% /
176% on those repos -- cassandra reported 22,584 distinct methods where there
are 8,191, with ``get`` alone printed as 427 separate "methods".

NO CAPITALISATION EXEMPTION, AND THAT IS MEASURED. Sparing a Capitalised
receiver (on the theory that it names a TYPE rather than a variable) was
considered and refuted on the data: jenkins' capitalised bucket is dominated by
SCREAMING_CASE static FIELDS -- ``ALL.getName``, ``CONFIG.getName``,
``BAD_FILTERS.get``, ``DESCRIPTOR.get``, ``INSTANCE.add`` -- which are receiver
variables by every criterion that matters, and the exemption would have thrown
away 51% of the win there while keeping 0% of a real type distinction.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.java import analyze_java


def _raw(edges: list[Edge]) -> list[dict]:
    """The serialized shape ``verify_claims`` walks."""
    return [
        {"src": e.src, "dst": e.dst, "type": e.edge_type,
         "line": e.line, "meta": dict(e.meta or {}),
         "is_resolved": e.is_resolved}
        for e in edges
    ]


def _unresolved(edges: list[Edge], method: str) -> Edge:
    """The one unresolved edge whose callee SHORT name is ``method``."""
    hits = [
        e for e in edges
        if not e.is_resolved
        and e.edge_type == "calls"
        and e.dst.split(":")[-2].split(".")[-1] == method
    ]
    assert len(hits) == 1, [e.dst for e in hits]
    return hits[0]


class TestTheNameSlotNamesTheCallee:
    def test_an_untypable_local_receiver_yields_the_bare_method_name(
        self, tmp_path: Path,
    ) -> None:
        """The filed shape: ``u.mkdirs()`` where ``u``'s type resolves to nothing."""
        (tmp_path / "A.java").write_text("""
package app;

public class A {
    void run() {
        var u = pick();
        u.mkdirs();
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "mkdirs")
        assert edge.dst == "java:external:0-0:mkdirs:unresolved", edge.dst
        assert (edge.meta or {})["callee_name"] == "mkdirs"

    def test_a_screaming_case_constant_receiver_is_not_a_type(
        self, tmp_path: Path,
    ) -> None:
        """The refutation of the capitalisation exemption, in one file.

        ``CONFIG`` is capitalised and is a static FIELD, not a type; jenkins'
        whole capitalised bucket looks like this.

        ``Cfg`` is deliberately a type the file cannot qualify. Declaring the
        field ``Object`` instead types the receiver as ``java.lang.Object``
        (INV-suril's implicit-import list) and the edge never reaches the
        placeholder branch at all -- which is a fine outcome, and not the cell
        under test.
        """
        (tmp_path / "B.java").write_text("""
package app;

public class B {
    static final Cfg CONFIG = pick();

    void run() {
        CONFIG.getProperty("k");
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "getProperty")
        assert edge.dst == "java:external:0-0:getProperty:unresolved", edge.dst

    def test_a_field_access_receiver_keeps_only_the_method(
        self, tmp_path: Path,
    ) -> None:
        """``System.out.println`` kept the MIDDLE token (``out.println``), which
        names neither the receiver chain nor the callee."""
        (tmp_path / "C.java").write_text("""
package app;

public class C {
    void run() {
        System.out.println("hi");
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "println")
        assert edge.dst.split(":")[-2] == "println", edge.dst

    def test_no_unresolved_java_callee_carries_a_receiver_prefix(
        self, tmp_path: Path,
    ) -> None:
        """The invariant, over a file mixing every receiver shape."""
        (tmp_path / "D.java").write_text("""
package app;

import java.io.File;

public class D {
    Object field = pick();

    void run(Object p) {
        var u = pick();
        u.mkdirs();
        this.field.hashCode();
        p.toString();
        File f = new File("/tmp");
        f.createNewFile();
        pick().flush();
    }
}
""")
        for e in analyze_java(tmp_path).edges:
            if e.is_resolved or e.edge_type != "calls":
                continue
            name = e.dst.split(":")[-2]
            assert "." not in name, e.dst


class TestTheTypedPathIsUnchanged:
    def test_a_declared_receiver_still_names_its_module(
        self, tmp_path: Path,
    ) -> None:
        """CONTROL: shortening must not be mistaken for typing. The typed branch
        already emitted the bare name and must keep naming the module."""
        (tmp_path / "E.java").write_text("""
import java.io.File;

public class E {
    void m() throws Exception {
        File f = new File("/tmp/x");
        f.createNewFile();
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "createNewFile")
        assert edge.dst == "java:java.io.File:0-0:createNewFile:unresolved", edge.dst


class TestTheScopedDisclosureCanNowFire:
    def test_the_boundary_scoped_caveat_attributes_the_site(
        self, tmp_path: Path,
    ) -> None:
        """The item's unmeasured question, answered through production code:
        does shortening let the SCOPED caveat ATTRIBUTE the site to a boundary
        rather than only count it?"""
        from hypergumbo_core.io_boundary import load_catalog
        from hypergumbo_core.verify_claims import untyped_receiver_sites

        (tmp_path / "F.java").write_text("""
package app;

public class F {
    void run() {
        var u = pick();
        u.mkdirs();
    }
}
""")
        sites = untyped_receiver_sites(
            _raw(analyze_java(tmp_path).edges), {"java": load_catalog("java")},
        )
        assert "fs_write" in sites, sites
        assert sites["fs_write"], sites


class TestRefutationCells:
    def test_shortening_does_not_create_a_boundary_classification(
        self, tmp_path: Path,
    ) -> None:
        """The recall/precision guard. ``gate_named_entry`` refuses a
        method-construct call with no module hint for EVERY kind, so the shorter
        name must classify exactly as the longer one did: not at all."""
        from hypergumbo_core.io_boundary import classify_call, load_catalog

        (tmp_path / "G.java").write_text("""
package app;

public class G {
    void run() {
        var u = pick();
        u.mkdirs();
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "mkdirs")
        catalogs = {"java": load_catalog("java")}
        assert classify_call(catalogs, edge.dst, edge.meta,
                             dst_ref=edge.dst_ref) is None

    def test_shortening_does_not_register_a_phantom_sanitizer_barrier(
        self, tmp_path: Path,
    ) -> None:
        """The fail-OPEN direction, which is the one that deletes findings.
        ``doFinal`` on an untypable receiver must not bind the catalogued
        ``javax.crypto.Cipher.doFinal`` -- a barrier earns ``sanitized``, which
        drops the flow from the claim's violation set (#214)."""
        from hypergumbo_core.taint import (
            _build_callee_index,
            _match_propagation_entry,
            load_builtin_taint_catalog,
        )

        (tmp_path / "H.java").write_text("""
package app;

public class H {
    void run(byte[] plain) throws Exception {
        var w = pick();
        w.doFinal(plain);
    }
}
""")
        edge = _unresolved(analyze_java(tmp_path).edges, "doFinal")
        assert edge.dst == "java:external:0-0:doFinal:unresolved", edge.dst
        catalog = load_builtin_taint_catalog()
        matched = _match_propagation_entry(
            _build_callee_index(catalog.sinks_for_language("java")),
            edge.dst, catalog.ambiguous_names_for_language("java"),
            (edge.meta or {}).get("call_construct"),
            is_resolved=edge.is_resolved, language="java",
        )
        assert matched is None, matched
