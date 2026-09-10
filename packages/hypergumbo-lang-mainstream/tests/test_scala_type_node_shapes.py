# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-pokam: a Scala receiver's type survives a QUALIFIED or GENERIC type node.

WHAT WAS BROKEN. ``scala.py`` read a declared type with
``find_child_by_type(node, "type_identifier")``, which names ONLY a bare type.
Tree-sitter gives a qualified type its own node kind (``stable_type_identifier``)
and a generic applied type another (``generic_type``), so both produced NO
``receiver_type_hint`` at all and the receiver fell to the sentinel.

THE FILED ITEM NAMED ONE SHAPE; A PROBE FOUND THREE, AND THEY ARE ONE BUG
(``~/hypergumbo_lab_notebook/pokam_scala_09102026/PROBE_RESULT.md``). Each pair
below differs in exactly one thing:

    A1 val x: File            hint File     A2 val x: java.io.File     NONE
    B1 val x = new File("p")  hint File     B2 val x = new java.io.File("p") NONE
    C2 val x: Buffer          hint Buffer   C1 val x: Buffer[String]   NONE

So it is not the annotation path — ``new`` loses it identically (B2), though
WI-pokam lists ``val x = new Foo()`` among the shapes that WORK. And a GENERIC
APPLIED type (C1) is not mentioned in the filing at all, while being pervasive in
idiomatic Scala (``List[X]``, ``Option[X]``, ``Future[X]``, ``Seq[X]``).

WHAT THE SLOT DOES WITH EACH, and why they differ. WI-sigog's discipline is that
the module slot only ever carries a path THE FILE ITSELF DECLARES — a bare name
is looked up in ``import_aliases`` and left alone when it misses, because a
simple name in the module slot asserts a module that does not exist (INV-fazim).
That discipline is KEPT and extended by exactly one case:

* a GENERIC type contributes its BASE name (``Buffer[String]`` → ``Buffer``),
  which then qualifies through ``import_aliases`` like any bare name — the
  already-proven path, reused rather than duplicated;
* a QUALIFIED type IS the path, spelled by the file at the use site. Nothing is
  invented and nothing is looked up: ``java.io.File`` qualifies to itself. This
  is the same fact ``import java.io.File`` establishes, written inline instead of
  at the top, and it is what makes an inline-qualified receiver reach the
  catalogue at all — an inline qualification is precisely the case where there is
  NO import line to consult.

NOT IN SCOPE, and not overlooked: ``val x = mk()``, where the type must come from
the return type of ``mk``. That is the return-type-registry shape objc and swift
already have and is WI-pokam's own harder half.
"""

from pathlib import Path

_SOURCE = """package demo

import java.io.File
import scala.collection.mutable.Buffer

object Shapes {
  def mk() = ???

  def annotatedBare(): Unit = {
    val a: File = mk()
    a.createNewFile()
  }

  def annotatedQualified(): Unit = {
    val b: java.io.File = mk()
    b.createNewFile()
  }

  def annotatedGeneric(): Unit = {
    val c: Buffer[String] = mk()
    c.append("x")
  }

  def newBare(): Unit = {
    val d = new File("p")
    d.createNewFile()
  }

  def newQualified(): Unit = {
    val e = new java.io.File("p")
    e.createNewFile()
  }

  def newGeneric(): Unit = {
    val f = new Buffer[String]()
    f.append("x")
  }

  def paramQualified(x: java.io.File): Unit = {
    x.createNewFile()
  }

  def paramGeneric(y: Buffer[String]): Unit = {
    y.append("x")
  }
}
"""


def _by_line(tmp_path: Path) -> dict[int, object]:
    from hypergumbo_lang_mainstream.scala import analyze_scala

    (tmp_path / "Shapes.scala").write_text(_SOURCE)
    result = analyze_scala(tmp_path)
    assert not result.skipped, "scala grammar unavailable — refusing a vacuous pass"
    out: dict[int, object] = {}
    for edge in result.edges:
        if edge.edge_type != "calls" or edge.is_resolved:
            continue
        if (edge.meta or {}).get("call_construct") != "method":
            continue
        out[edge.line] = edge
    assert out, "no unresolved method-call edges at all — the fixture is wrong"
    return out


def _line_of(needle: str) -> int:
    for i, line in enumerate(_SOURCE.splitlines(), start=1):
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} not in the fixture")


def _hint(edges: dict[int, object], needle: str) -> "str | None":
    edge = edges.get(_line_of(needle))
    assert edge is not None, f"no method edge at {needle!r}"
    return (edge.meta or {}).get("receiver_type_hint")  # type: ignore[attr-defined]


def _slot(edges: dict[int, object], needle: str) -> str:
    edge = edges.get(_line_of(needle))
    assert edge is not None, f"no method edge at {needle!r}"
    return edge.dst.split(":")[1]  # type: ignore[attr-defined]


class TestTheControlsThatAlreadyWorked:
    """If these ever go red the fix has broken the path it was extending."""

    def test_bare_annotation(self, tmp_path: Path) -> None:
        edges = _by_line(tmp_path)
        assert _hint(edges, "a.createNewFile()") == "File"
        assert _slot(edges, "a.createNewFile()") == "java.io.File"

    def test_bare_new(self, tmp_path: Path) -> None:
        edges = _by_line(tmp_path)
        assert _hint(edges, "d.createNewFile()") == "File"
        assert _slot(edges, "d.createNewFile()") == "java.io.File"


class TestAQualifiedTypeIsThePath:
    """An inline qualification is the one case with NO import line to consult,
    so the name written at the use site is the only evidence there is — and it
    is sufficient evidence, being the static owner path itself."""

    def test_qualified_annotation(self, tmp_path: Path) -> None:
        edges = _by_line(tmp_path)
        assert _hint(edges, "b.createNewFile()") == "java.io.File"
        assert _slot(edges, "b.createNewFile()") == "java.io.File"

    def test_qualified_new(self, tmp_path: Path) -> None:
        """WI-pokam lists ``val x = new Foo()`` among the shapes that WORK; the
        qualified form does not, and a fix scoped to the annotation branch would
        leave this broken while looking like a fix."""
        edges = _by_line(tmp_path)
        assert _hint(edges, "e.createNewFile()") == "java.io.File"
        assert _slot(edges, "e.createNewFile()") == "java.io.File"

    def test_qualified_parameter(self, tmp_path: Path) -> None:
        edges = _by_line(tmp_path)
        assert _hint(edges, "x.createNewFile()") == "java.io.File"
        assert _slot(edges, "x.createNewFile()") == "java.io.File"


class TestAGenericTypeContributesItsBase:
    """The receiver's methods live on the BASE; the type argument is not the
    receiver. Emitting the base reuses the proven bare-name path rather than
    inventing a second one."""

    def test_generic_annotation(self, tmp_path: Path) -> None:
        edges = _by_line(tmp_path)
        assert _hint(edges, "c.append(") == "Buffer"
        assert _slot(edges, "c.append(") == "scala.collection.mutable.Buffer"

    def test_generic_new(self, tmp_path: Path) -> None:
        edges = _by_line(tmp_path)
        assert _hint(edges, "f.append(") == "Buffer"
        assert _slot(edges, "f.append(") == "scala.collection.mutable.Buffer"

    def test_generic_parameter(self, tmp_path: Path) -> None:
        edges = _by_line(tmp_path)
        assert _hint(edges, "y.append(") == "Buffer"
        assert _slot(edges, "y.append(") == "scala.collection.mutable.Buffer"


class TestTheDisciplineIsKept:
    """WI-sigog / INV-fazim: the slot carries only a path the file declares."""

    def test_an_unimported_bare_name_still_keeps_the_sentinel(
        self, tmp_path: Path,
    ) -> None:
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "S.scala").write_text(
            "package demo\n\nobject S {\n  def mk() = ???\n"
            "  def run(): Unit = {\n    val z: Mystery = mk()\n"
            "    z.createNewFile()\n  }\n}\n"
        )
        result = analyze_scala(tmp_path)
        assert not result.skipped
        hits = [e for e in result.edges
                if (e.meta or {}).get("call_construct") == "method"
                and not e.is_resolved]
        assert hits, "no method edge emitted"
        assert hits[0].dst.split(":")[1] == "external"
        assert (hits[0].meta or {}).get("receiver_type_hint") == "Mystery"
