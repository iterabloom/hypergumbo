# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every parent-type form Scala's grammar parses lands in ``base_classes``.

A parent written as a stable path (``IOApp.Simple``, ``a.b.C``), with type
arguments on a path (``a.B[T]``), with constructor arguments on a path
(``a.B(x)``) or as a projection (``Outer#Inner``) was dropped: only a bare
``type_identifier`` and a ``generic_type`` over one were read. So
``object Server extends IOApp.Simple`` carried no base class, and http4s.yaml's
``^IOApp\\.Simple$`` entrypoint row could not fire. The name is recorded as
written, type and constructor arguments dropped; ``derives`` and self-types
are not parents and stay out.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.framework_patterns import enrich_symbols
from hypergumbo_core.linkers.inheritance import link_inheritance
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_lang_mainstream.scala import analyze_scala


def _bases(tmp_path: Path, code: str, name: str) -> list[str]:
    (tmp_path / "Forms.scala").write_text(code)
    result = analyze_scala(tmp_path)
    sym = next(s for s in result.symbols if s.name == name)
    return list((sym.meta or {}).get("base_classes", []))


@pytest.mark.parametrize(
    ("code", "name", "expected"),
    [
        ("class C extends A", "C", ["A"]),
        ("class C extends a.b.C2", "C", ["a.b.C2"]),
        ("object O extends IOApp.Simple", "O", ["IOApp.Simple"]),
        ("case object O extends IOApp.Simple", "O", ["IOApp.Simple"]),
        ("object O extends A.B.C", "O", ["A.B.C"]),
        ("class C extends A with B", "C", ["A", "B"]),
        ("class C extends A[T]", "C", ["A"]),
        ("class C extends a.B[T]", "C", ["a.B"]),
        ("class C extends A(1, 2)", "C", ["A"]),
        ("class C extends a.B(x)", "C", ["a.B"]),
        ("class C extends A[T](x)", "C", ["A"]),
        ("case class C(x: Int) extends a.B[T](x) with D", "C", ["a.B", "D"]),
        ("class C extends A, B", "C", ["A", "B"]),
        ("class C extends A, b.C2", "C", ["A", "b.C2"]),
        ("class C extends a.B with E.D[T]", "C", ["a.B", "E.D"]),
        ("trait T extends a.B with c.D", "T", ["a.B", "c.D"]),
        ("class C extends Outer#Inner", "C", ["Outer.Inner"]),
        ("class C extends (A @ann)", "C", ["A"]),
        ("class C extends (A with b.B)", "C", ["A", "b.B"]),
        # Not a parent class: a tuple type and a singleton type name none.
        ("class C extends (A, B)", "C", []),
        ("class C extends a.type", "C", []),
        ("enum E extends A.B { case X }", "E", ["A.B"]),
        ("enum E extends A, B[Int] { case X }", "E", ["A", "B"]),
    ],
)
def test_parent_type_form_is_recorded(
    tmp_path: Path, code: str, name: str, expected: list[str],
) -> None:
    assert _bases(tmp_path, code, name) == expected


def test_derives_and_self_type_are_not_parents(tmp_path: Path) -> None:
    code = (
        "case class C(x: Int) extends A derives Eq, Show\n"
        "trait T { self: Foo with a.Bar => def f = 1 }\n"
    )
    assert _bases(tmp_path, code, "C") == ["A"]
    assert _bases(tmp_path, code, "T") == []


def test_dotted_parent_keeps_no_import_hint(tmp_path: Path) -> None:
    """``base_class_imports`` stays for BARE bases only (the inheritance
    linker reads a dotted base's qualifier from the base itself)."""
    (tmp_path / "Main.scala").write_text(
        "import cats.effect.IOApp\n"
        "object Main extends IOApp.Simple\n"
        "object Other extends IOApp\n"
    )
    result = analyze_scala(tmp_path)
    by_name = {s.name: s for s in result.symbols}
    assert "base_class_imports" not in (by_name["Main"].meta or {})
    assert by_name["Other"].meta["base_class_imports"] == {"IOApp": "cats.effect.IOApp"}


def test_ioapp_simple_gets_the_http4s_entrypoint(tmp_path: Path) -> None:
    """End to end: the http4s row ``^IOApp\\.Simple$`` lands, and the
    ``^IOApp$`` row does not claim the dotted parent."""
    (tmp_path / "Server.scala").write_text(
        "import cats.effect.{IO, IOApp}\n"
        "object Simple extends IOApp.Simple {\n"
        "  val run: IO[Unit] = IO.unit\n"
        "}\n"
    )
    result = analyze_scala(tmp_path)
    enrich_symbols(result.symbols, {"http4s"})
    sym = next(s for s in result.symbols if s.name == "Simple" and s.kind == "object")
    concepts = (sym.meta or {}).get("concepts", [])
    assert [c.get("matched_base_class") for c in concepts if c["concept"] == "entrypoint"] == [
        "IOApp.Simple"
    ]


def _inheritance_edges(tmp_path: Path) -> list:
    result = analyze_scala(tmp_path)
    ctx = LinkerContext(repo_root=tmp_path, symbols=result.symbols, edges=result.edges)
    by_id = {s.id: s for s in result.symbols}
    return [
        (by_id[e.src].name, by_id[e.dst].qualified_name if e.dst in by_id else e.dst)
        for e in link_inheritance(ctx).edges
        if e.edge_type in ("extends", "implements")
    ]


@pytest.mark.parametrize("package", ["package app\n", ""])
def test_dotted_parent_binds_the_nested_type(tmp_path: Path, package: str) -> None:
    """``extends Outer.Inner`` binds the nested ``Outer.Inner``, not an
    unrelated top-level ``Inner``, with and without a package clause."""
    (tmp_path / "Outer.scala").write_text(
        package + "object Outer {\n  trait Inner\n}\n"
    )
    (tmp_path / "Inner.scala").write_text(package + "trait Inner\n")
    (tmp_path / "Impl.scala").write_text(package + "class Impl extends Outer.Inner\n")
    prefix = "app." if package else ""
    assert _inheritance_edges(tmp_path) == [("Impl", f"{prefix}Outer.Inner")]


def test_dotted_parent_with_no_in_repo_type_is_external(tmp_path: Path) -> None:
    """No in-repo ``Simple``: the linker's external stand-in, as for a bare
    external base; an in-repo ``Simple`` whose qualified name does not fit
    ``IOApp.Simple`` is not bound, and the qualifier becomes the module."""
    (tmp_path / "Main.scala").write_text(
        "package app\nobject Main extends IOApp.Simple\n"
    )
    assert _inheritance_edges(tmp_path) == [
        ("Main", "scala:external:0-0:Simple:unresolved"),
    ]
    (tmp_path / "Simple.scala").write_text("package other\ntrait Simple\n")
    assert _inheritance_edges(tmp_path) == [
        ("Main", "scala:IOApp:0-0:Simple:unresolved"),
    ]
