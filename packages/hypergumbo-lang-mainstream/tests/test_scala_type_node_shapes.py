# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Scala receiver's type survives a QUALIFIED or GENERIC type node (WI-pokam).

Tree-sitter gives a bare type a ``type_identifier``, a qualified type its own
node kind (``stable_type_identifier``) and a generic applied type another
(``generic_type``). Reading a declared type with
``find_child_by_type(node, "type_identifier")`` names ONLY the bare one, so the
other two would produce no ``receiver_type_hint`` and the receiver would fall
to the sentinel. The hazard is the same on every path that reads a type node --
annotation, ``new`` and parameter -- so each shape is tested on each path:

    val x: File                     hint File          slot java.io.File
    val x: java.io.File             hint java.io.File  slot java.io.File
    val x = new java.io.File("p")   hint java.io.File  slot java.io.File
    val x: Buffer[String]           hint Buffer        slot scala.collection.mutable.Buffer

Generic applied types are pervasive in idiomatic Scala (``List[X]``,
``Option[X]``, ``Future[X]``, ``Seq[X]``).

WHAT THE SLOT DOES WITH EACH, and why they differ. The module slot only ever
carries a path THE FILE ITSELF DECLARES (WI-sigog) — a bare name is looked up
in ``import_aliases`` and left alone when it misses, because a simple name in
the module slot asserts a module that does not exist (INV-fazim). Both shapes
keep that discipline:

* a GENERIC type contributes its BASE name (``Buffer[String]`` → ``Buffer``),
  which then qualifies through ``import_aliases`` like any bare name — the
  already-proven path, reused rather than duplicated;
* a QUALIFIED type IS the path, spelled by the file at the use site. Nothing is
  invented and nothing is looked up: ``java.io.File`` qualifies to itself. This
  is the same fact ``import java.io.File`` establishes, written inline instead of
  at the top, and it is what makes an inline-qualified receiver reach the
  catalogue at all — an inline qualification is precisely the case where there is
  NO import line to consult.

NOT EXERCISED HERE: ``val x = mk()``, where the type must come from the return
type of ``mk``. That shape is typed through the return-type registry
(``method_return_type_registry``, WI-gokop), not from a type node, and the
fixture's ``mk() = ???`` declares no return type.
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
    """Bare-type controls: if these go red, qualified/generic handling has
    broken the bare-name path it extends."""

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
        """``new`` reads its type node apart from an annotation, so handling
        qualified types on the annotation path alone would leave this shape at
        the sentinel while the annotation tests pass."""
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
    """A generic base INVENTED from a type argument is refused when it is a
    name Scala imports implicitly.

    Without this, `val m: Map[String, X]` contributes the base `Map`, which
    cannot qualify -- there is no import line to look it up in -- so it adds
    nothing to the module slot and reaches only the bare short-name bind,
    binding a standard-library `Map[K,V]` to a same-named PROJECT `Map.get`.
    That is the "arbitrary same-named internal def" funnel `scala.py`'s
    external branch refuses for untyped receivers.

    The refusal costs nothing in the module slot, because an unqualifiable
    name never fills one; it lowers only the share of edges carrying a hint.
    Judge a change here by the module-slot share: the hinted share prefers the
    WORSE version.
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
        """The denylist applies only to a base INVENTED from a type argument. A
        bare `val x: Map = ...` names a type the programmer wrote, and keeps
        its hint (INV-fahub, WI-bihit)."""
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
