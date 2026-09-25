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


class TestTheImplicitImportDenylist:
    """A generic base this change would INVENT is refused when it is a name
    Scala imports implicitly.

    MEASURED, and the measurement is the whole justification. Without this,
    `val m: Map[String, X]` contributes the base `Map`, which cannot qualify --
    there is no import line to look it up in -- so it adds nothing to the module
    slot and reaches only the bare short-name bind. On sbt that produced 44
    bindings of a standard-library `Map[K,V]` to the PROJECT's `Map.get` in
    `sbt/SessionVar.scala`: 24% of every edge the change newly resolved, and
    exactly the "arbitrary same-named internal def" funnel this file's external
    branch refuses for untyped receivers.

    THE COST IS ZERO WHERE IT COUNTS. Denying them drops the HINTED share
    (28.07% -> 23.43% on sbt) and leaves the MODULE-SLOT share alone
    (18.07% -> 18.19%, marginally up) -- because an unqualifiable name never
    filled a slot in the first place. The item's own acceptance metric is the
    hinted share, so that metric would have preferred the WORSE version.
    """

    def test_a_generic_over_an_implicit_import_name_is_refused(
        self, tmp_path: Path,
    ) -> None:
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "M.scala").write_text(
            "package demo\n\nobject M {\n  def mk() = ???\n"
            "  def run(): Unit = {\n    val m: Map[String, Int] = mk()\n"
            "    m.get(\"k\")\n  }\n}\n"
        )
        result = analyze_scala(tmp_path)
        assert not result.skipped
        hits = [e for e in result.edges
                if (e.meta or {}).get("call_construct") == "method"
                and not e.is_resolved]
        assert hits, "no method edge emitted"
        assert (hits[0].meta or {}).get("receiver_type_hint") is None
        assert hits[0].dst.split(":")[1] == "external"

    def test_an_explicitly_annotated_bare_name_is_untouched(
        self, tmp_path: Path,
    ) -> None:
        """Scoped to the base this change INVENTS from a type argument. A bare
        `val x: Map = ...` is the pre-existing INV-fahub / WI-bihit population --
        the programmer wrote that name -- and narrowing it is a separate change
        with its own measurement."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "N.scala").write_text(
            "package demo\n\nobject N {\n  def mk() = ???\n"
            "  def run(): Unit = {\n    val m: Map = mk()\n"
            "    m.get(\"k\")\n  }\n}\n"
        )
        result = analyze_scala(tmp_path)
        assert not result.skipped
        hits = [e for e in result.edges
                if (e.meta or {}).get("call_construct") == "method"
                and not e.is_resolved]
        assert hits, "no method edge emitted"
        assert (hits[0].meta or {}).get("receiver_type_hint") == "Map"

    def test_a_qualified_generic_over_an_implicit_name_still_qualifies(
        self, tmp_path: Path,
    ) -> None:
        """`scala.collection.immutable.Map[K,V]` is not the ambiguous case: the
        file spells the path, so there is nothing to collide with."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Q.scala").write_text(
            "package demo\n\nobject Q {\n  def mk() = ???\n"
            "  def run(): Unit = {\n"
            "    val m: scala.collection.immutable.Map[String, Int] = mk()\n"
            "    m.get(\"k\")\n  }\n}\n"
        )
        result = analyze_scala(tmp_path)
        assert not result.skipped
        hits = [e for e in result.edges
                if (e.meta or {}).get("call_construct") == "method"
                and not e.is_resolved]
        assert hits, "no method edge emitted"
        assert (hits[0].meta or {}).get("receiver_type_hint") == \
            "scala.collection.immutable.Map"
