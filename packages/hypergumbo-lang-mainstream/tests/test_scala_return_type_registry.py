# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-gokop: a Scala receiver bound by INFERENCE takes the declared return type.

WHAT WAS MISSING. ``scala.py`` typed a receiver only from a DECLARED type: a
parameter annotation, a ``val`` annotation or ``new``, a class parameter. Scala
code mostly writes none of these::

    val x = mk()          <- the return type of ``mk``
    mk().getEncoding()    <- a chained receiver
    a.b.close()           <- a parameterless member (``def b: T`` / ``val b: T``)
    this.helper()         <- the enclosing type

Every receiver among them fell to the ``external`` sentinel with no
``receiver_type_hint``. The base ``TreeSitterAnalyzer`` already aggregates every
file's ``FileAnalysis.method_return_types`` into ``_method_return_type_registry``
(swift and rust fill it); scala now fills it in Pass 1 and reads it in Pass 2.

WHAT IS DELIBERATELY NOT INFERRED, pinned below so a later change has to argue
with it:

* ``Option[T]`` / ``Future[T]`` / ``Try[T]`` / ``Either[L, R]`` are NOT unwrapped
  to ``T`` for a DIRECT receiver. ``opt().get`` and ``fut.map`` call the
  wrapper's own methods; typing that receiver ``T`` would hand
  ``Option.getOrElse`` to ``T`` and earn a hint that is wrong. (Rust unwraps
  because its ``?`` / ``.unwrap()`` project the value at the call site; Scala has
  no such operator.) Every name Scala imports implicitly is refused as an
  inferred type, as WI-pokam refused it for a generic base: it cannot qualify, so
  its only reachable effect is the short-name mis-bind PR #892 measured.
* The NEAREST binder of a name decides. A typed ``val`` outside a lambda does
  not type the lambda's same-named parameter. When the nearest binder's type
  cannot be said, the file-wide declared map answers as it did before -- kept on
  measurement (see ``_ScalaReceiverTyper``): the stricter reading dropped ~250
  hints per corpus repo that were mostly right by naming convention.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

_LIB = """package demo.lib

import java.io.FileWriter

class Holder {
  def writer: FileWriter = ???
  val named: FileWriter = ???
}

class Repo {
  def find(id: Int): Int = id
  def self: this.type = this
}

class BaseSvc {
  def out(): FileWriter = ???
}

class Box(val boxed: FileWriter, count: Int)
"""

_SOURCE = """package demo

import java.io.FileWriter
import demo.lib.{Holder, Repo, BaseSvc}

case class Point(x: Int) {
  def norm(): Int = x
}

class Svc extends BaseSvc {
  def mk(): FileWriter = new FileWriter("p")
  def opt(): Option[FileWriter] = None
  def name(): String = "n"
  def holder(): Holder = new Holder
  def repo(): Repo = new Repo
  def same[T](t: T): T = t
  def helper(): Int = 1

  def callResult(): Unit = {
    val x = mk()
    x.write("callResult")
  }

  def chained(): Unit = {
    mk().getEncoding()
  }

  def objectChained(): Unit = {
    Factory.make().flush()
  }

  def parameterless(h: Holder): Unit = {
    h.writer.close()
    h.named.append("named")
  }

  def thisReceiver(): Unit = {
    this.helper()
  }

  def projectChained(): Unit = {
    repo().find(1)
  }

  def fluent(): Unit = {
    repo().self.find(2)
  }

  def inherited(): Unit = {
    val o = out()
    o.write("inherited")
  }

  def caseClassApply(): Unit = {
    val p = Point(1)
    p.norm()
  }

  def optionNotUnwrapped(): Unit = {
    val maybe = opt()
    maybe.getOrElse(null)
  }

  def implicitRefused(): Unit = {
    name().trim()
  }

  def typeParamRefused(): Unit = {
    same(mk()).write("typeParam")
  }

  def castReceiver(a: Any): Unit = {
    a.asInstanceOf[FileWriter].flush()
  }

  def typedCase(a: Any): Unit = a match {
    case q: FileWriter => q.write("typedCase")
    case _ => ()
  }

  def lambdaShadow(xs: Seq[Int]): Unit = {
    val s = mk()
    xs.foreach(s => s.write("lambdaShadow"))
  }

  def leakSource(): Unit = {
    val leaky: FileWriter = mk()
    leaky.flush()
  }

  def leakTarget(): Unit = {
    val leaky = unknownThing()
    leaky.write("leakTarget")
  }
}

object Factory {
  def make(): FileWriter = ???
}
"""


@pytest.fixture(scope="module")
def edges(tmp_path_factory: pytest.TempPathFactory) -> list:
    from hypergumbo_lang_mainstream.scala import analyze_scala

    root = tmp_path_factory.mktemp("gokop")
    (root / "lib").mkdir()
    (root / "lib" / "Lib.scala").write_text(_LIB)
    (root / "Main.scala").write_text(_SOURCE)
    result = analyze_scala(root)
    assert not result.skipped, "scala grammar unavailable -- refusing a vacuous pass"
    calls = [e for e in result.edges if e.edge_type == "calls"]
    assert calls, "no call edges at all -- the fixture is wrong"
    return calls


def _line_of(needle: str) -> int:
    hits = [i for i, line in enumerate(_SOURCE.splitlines(), start=1) if needle in line]
    assert len(hits) == 1, f"{needle!r} must occur exactly once in the fixture, found {hits}"
    return hits[0]


def _edge(edges: list, needle: str, callee: str):
    line = _line_of(needle)
    hits = [e for e in edges if e.line == line
            and e.dst.split(":")[3].rsplit(".", 1)[-1] == callee]
    assert len(hits) == 1, f"expected one {callee} edge at {needle!r}, got {[e.dst for e in hits]}"
    return hits[0]


def _hint(edge) -> "str | None":
    return (edge.meta or {}).get("receiver_type_hint")


def _slot(edge) -> str:
    return edge.dst.split(":")[1]


class TestInferredReceiverIsTyped:
    """The four shapes the item names, plus the members a chain walks through."""

    def test_val_bound_to_a_call_result(self, edges: list) -> None:
        e = _edge(edges, 'x.write("callResult")', "write")
        assert _hint(e) == "java.io.FileWriter"
        assert _slot(e) == "java.io.FileWriter"

    def test_chained_receiver(self, edges: list) -> None:
        e = _edge(edges, "mk().getEncoding()", "getEncoding")
        assert _hint(e) == "java.io.FileWriter"
        assert _slot(e) == "java.io.FileWriter"

    def test_chained_through_an_object(self, edges: list) -> None:
        e = _edge(edges, "Factory.make().flush()", "flush")
        assert _slot(e) == "java.io.FileWriter"

    def test_parameterless_def_member(self, edges: list) -> None:
        e = _edge(edges, "h.writer.close()", "close")
        assert _slot(e) == "java.io.FileWriter"

    def test_typed_val_member(self, edges: list) -> None:
        e = _edge(edges, 'h.named.append("named")', "append")
        assert _slot(e) == "java.io.FileWriter"

    def test_return_type_inherited_from_a_base_class(self, edges: list) -> None:
        e = _edge(edges, 'o.write("inherited")', "write")
        assert _slot(e) == "java.io.FileWriter"

    def test_cast_receiver(self, edges: list) -> None:
        e = _edge(edges, "a.asInstanceOf[FileWriter].flush()", "flush")
        assert _slot(e) == "java.io.FileWriter"

    def test_typed_case_pattern(self, edges: list) -> None:
        e = _edge(edges, 'q.write("typedCase")', "write")
        assert _slot(e) == "java.io.FileWriter"


class TestProjectReceiverResolves:
    """A PROJECT return type takes the type-qualified path and binds the method."""

    def test_this_receiver_binds_the_enclosing_method(self, edges: list) -> None:
        e = _edge(edges, "this.helper()", "helper")
        assert e.is_resolved and e.dst.endswith(":Svc.helper:method"), e.dst

    def test_chained_project_type(self, edges: list) -> None:
        e = _edge(edges, "repo().find(1)", "find")
        assert e.is_resolved and e.dst.endswith(":Repo.find:method"), e.dst

    def test_this_type_return_is_the_owner(self, edges: list) -> None:
        e = _edge(edges, "repo().self.find(2)", "find")
        assert e.is_resolved and e.dst.endswith(":Repo.find:method"), e.dst

    def test_case_class_apply_types_the_val(self, edges: list) -> None:
        e = _edge(edges, "p.norm()", "norm")
        assert e.is_resolved and e.dst.endswith(":Point.norm:method"), e.dst


class TestDeliberatelyNotInferred:
    def test_option_is_not_unwrapped(self, edges: list) -> None:
        e = _edge(edges, "maybe.getOrElse(null)", "getOrElse")
        assert _hint(e) is None and _slot(e) == "external"

    def test_implicitly_imported_return_type_is_refused(self, edges: list) -> None:
        e = _edge(edges, "name().trim()", "trim")
        assert _hint(e) is None and _slot(e) == "external"

    def test_type_parameter_return_is_refused(self, edges: list) -> None:
        e = _edge(edges, 'same(mk()).write("typeParam")', "write")
        assert _hint(e) is None and _slot(e) == "external"

    def test_lambda_parameter_shadows_an_outer_val(self, edges: list) -> None:
        e = _edge(edges, 's => s.write("lambdaShadow")', "write")
        assert _hint(e) is None and _slot(e) == "external"

    def test_an_untyped_binder_defers_to_the_file_wide_map(self, edges: list) -> None:
        """PINNED PRE-EXISTING BEHAVIOUR, kept on measurement. ``leaky`` here is
        an inferred val whose type cannot be said; the file-wide map still answers
        from ``leakSource``'s annotated val, as it always has (ADR-0006 "Scope
        Handling"). The map is neither widened nor narrowed by this change."""
        control = _edge(edges, "leaky.flush()", "flush")
        assert _hint(control) == "FileWriter"
        e = _edge(edges, 'leaky.write("leakTarget")', "write")
        assert _hint(e) == "FileWriter"


class TestNestedTypeReference:
    """A return type written as a TYPE MEMBER of a project object
    (``Outer.Inner``) is the type ``Inner``: its members are looked up by the
    leaf. Measured on sbt: ``NetworkClient.parseArgs`` returns
    ``NetworkClient.Arguments``, and ``parseArgs(..).withBaseDirectory(..)``
    stayed unresolved -- and a parameter typed ``Arguments`` that the registry
    re-typed lost the bind it had before."""

    _FILES: ClassVar[dict[str, str]] = {
        "lib/Lib.scala": (
            "package demo.lib\n\n"
            "object Outer {\n"
            "  class Inner {\n"
            "    def ping(): Int = 1\n"
            "  }\n"
            "  def make(): Outer.Inner = new Inner\n"
            "}\n"
        ),
        "lib/File.scala": (
            "package demo.lib\n\n"
            "class File {\n"
            "  def exists(): Boolean = true\n"
            "}\n"
        ),
        "Main.scala": (
            "package demo\n\n"
            "import demo.lib.Outer\n\n"
            "object Use {\n"
            "  def nestedType(): Unit = {\n"
            "    Outer.make().ping()\n"
            "  }\n"
            "  def jdk(f: java.io.File): Unit = {\n"
            "    f.exists()\n"
            "  }\n"
            "}\n"
        ),
    }

    @pytest.fixture(scope="class")
    def nested_edges(self, tmp_path_factory: pytest.TempPathFactory) -> list:
        from hypergumbo_lang_mainstream.scala import analyze_scala

        root = tmp_path_factory.mktemp("gokop_nested")
        for rel, text in self._FILES.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        result = analyze_scala(root)
        assert not result.skipped
        return [e for e in result.edges if e.edge_type == "calls"]

    def test_a_type_member_binds_through_its_leaf(self, nested_edges: list) -> None:
        pings = [e for e in nested_edges if e.dst.split(":")[3].endswith("ping")]
        assert len(pings) == 1, [e.dst for e in nested_edges]
        assert pings[0].is_resolved and pings[0].dst.endswith(":Inner.ping:method"), pings[0].dst

    def test_a_library_path_ending_in_a_project_type_name_stays_the_library(
        self, nested_edges: list,
    ) -> None:
        """``java.io.File`` is not the project's ``demo.lib.File``: neither a
        project package nor rooted at a project type, so it keeps its path."""
        hits = [e for e in nested_edges if e.dst.split(":")[3].endswith("exists")]
        assert len(hits) == 1, [e.dst for e in nested_edges]
        assert not hits[0].is_resolved
        assert hits[0].dst == "scala:java.io.File:0-0:exists:unresolved"
        assert _hint(hits[0]) == "java.io.File"


class TestImplicitNameCollider:
    """A project type NESTED in an object, which shares a name Scala imports
    implicitly, does not capture the standard library's: outside that object
    and with no import, ``Map(pairs*)`` is ``scala.Predef.Map``.

    Measured, not hypothetical: sbt declares ``SessionVar.Map`` (a case class
    with a ``get``), and reading ``val mappings = Map(mappingList*)`` and
    ``Map[String, X](..)`` as that case class bound two ``.get`` calls to it --
    the collider WI-pokam measured, reached through a case-class ``apply``. The
    companion-style owner path (``List.empty``) is refused by the same rule.
    """

    _SRC = """package demo

class Box {
  def get(k: Int): Int = k
}

object SessionVar {
  case class Map(n: Int) {
    def get(k: Int): Int = n
  }
  object List {
    def empty: Box = new Box
  }
}

object Use {
  def viaApply(): Unit = {
    val m = Map(1 -> 2)
    m.get(1)
  }

  def viaTypedApply(): Unit = {
    val t = Map[Int, Int](1 -> 2)
    t.get(1)
  }

  def viaOwner(): Unit = {
    List.empty.get(1)
  }
}
"""

    @pytest.fixture(scope="class")
    def collider_edges(self, tmp_path_factory: pytest.TempPathFactory) -> list:
        from hypergumbo_lang_mainstream.scala import analyze_scala

        root = tmp_path_factory.mktemp("gokop_collider")
        (root / "Use.scala").write_text(self._SRC)
        result = analyze_scala(root)
        assert not result.skipped
        lines = self._SRC.splitlines()
        targets = {lines.index(f"    {s}") + 1
                   for s in ("m.get(1)", "t.get(1)", "List.empty.get(1)")}
        found = [e for e in result.edges if e.edge_type == "calls" and e.line in targets
                 and e.dst.split(":")[3].rsplit(".", 1)[-1] == "get"]
        assert len(found) == 3, [e.dst for e in result.edges]
        return found

    def test_get_on_the_standard_library_does_not_bind_the_project_type(
        self, collider_edges: list,
    ) -> None:
        for e in collider_edges:
            assert not e.is_resolved, e.dst
            assert _hint(e) is None, e.dst


class TestSameNamedTypesInTwoPackages:
    """A qualified type decides WHICH same-named type a bind may land on.

    Measured on lila, where 74 files declare a class ``Env``:
    ``env.tournament.version(id)`` -- typed ``lila.tournament.Env`` through the
    registry -- bound to ``lila.challenge.Env.version``, because members are
    looked up by the SIMPLE name and the resolver keeps one ``Env.version``.
    The qualified path now has to agree with the symbol (the WI-tipoh check), so
    a call binds into its own package or stays unresolved -- never across.
    """

    _FILES: ClassVar[dict[str, str]] = {
        "challenge/Env.scala": "package lila.challenge\n\nclass Env {\n  def version(): Int = 1\n}\n",
        "tournament/Env.scala": "package lila.tournament\n\nclass Env {\n  def version(): Int = 2\n}\n",
        "app/Root.scala": (
            "package lila.app\n\n"
            "class Root {\n"
            "  val tournament: lila.tournament.Env = ???\n"
            "  val challenge: lila.challenge.Env = ???\n"
            "}\n\n"
            "class Ctl(root: Root) {\n"
            "  def tour(): Unit = root.tournament.version()\n"
            "  def chal(): Unit = root.challenge.version()\n"
            "}\n"
        ),
    }

    def test_a_bind_never_crosses_into_another_package(
        self, tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        from hypergumbo_lang_mainstream.scala import analyze_scala

        root = tmp_path_factory.mktemp("gokop_env")
        for rel, text in self._FILES.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        result = analyze_scala(root)
        assert not result.skipped
        by_caller = {
            e.src.split(":")[3]: e for e in result.edges
            if e.edge_type == "calls" and e.dst.split(":")[3].endswith("version")
        }
        assert set(by_caller) == {"Ctl.tour", "Ctl.chal"}, by_caller
        expected = {"Ctl.tour": "tournament/", "Ctl.chal": "challenge/"}
        resolved = 0
        for caller, edge in by_caller.items():
            if edge.is_resolved:
                resolved += 1
                assert edge.dst.split(":")[1].startswith(expected[caller]), edge.dst
            else:
                assert _hint(edge) == "Env", edge.dst
        # One of the two is reachable at all: the resolver keeps ONE
        # ``Env.version``. Asserting it here keeps this a control that can fail.
        assert resolved == 1


class TestPassOneRegistry:
    """The producer side, read straight off ``FileAnalysis.method_return_types``."""

    @pytest.fixture(scope="class")
    def registry(self) -> dict:
        import tree_sitter
        import tree_sitter_scala

        from hypergumbo_lang_mainstream.scala import _extract_symbols_from_file

        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_scala.language()))
        out: dict = {}
        for name, text in (("Lib.scala", _LIB), ("Main.scala", _SOURCE)):
            src = text.encode()
            analysis = _extract_symbols_from_file(parser.parse(src), src, name, "run")
            out.update(analysis.method_return_types)
        return out

    def test_library_type_is_qualified_through_the_declaring_files_import(
        self, registry: dict,
    ) -> None:
        assert registry["Svc.mk"] == "java.io.FileWriter"
        assert registry["Holder.writer"] == "java.io.FileWriter"

    def test_typed_member_val_and_class_parameter_register(self, registry: dict) -> None:
        assert registry["Holder.named"] == "java.io.FileWriter"
        assert registry["Box.boxed"] == "java.io.FileWriter"

    def test_project_type_registers(self, registry: dict) -> None:
        assert registry["Svc.repo"] == "demo.lib.Repo"

    def test_this_type_registers_the_owner(self, registry: dict) -> None:
        assert registry["Repo.self"] == "Repo"

    @pytest.mark.parametrize(
        "key", ["Svc.opt", "Svc.name", "Svc.same", "Svc.helper", "Box.count", "Point.x"])
    def test_refused_return_types_are_absent(self, registry: dict, key: str) -> None:
        assert key not in registry


class TestElementRegistry:
    """The element registry's producer side, read off ``element_types``."""

    @pytest.fixture(scope="class")
    def produced(self) -> "tuple[dict, dict]":
        import tree_sitter
        import tree_sitter_scala

        from hypergumbo_lang_mainstream.scala import _extract_symbols_from_file

        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_scala.language()))
        types: dict = {}
        elements: dict = {}
        src = _EDGES.encode()
        analysis = _extract_symbols_from_file(
            parser.parse(src), src, "Edges.scala", "run", element_types=elements)
        types.update(analysis.method_return_types)
        return types, elements

    def test_a_container_return_registers_its_element(self, produced: "tuple[dict, dict]") -> None:
        types, elements = produced
        assert "Edges.many" not in types  # ``Seq`` is implicit: no TYPE
        assert elements["Edges.many"] == "java.io.FileWriter"

    @pytest.mark.parametrize("key", ["Edges.single", "Edges.table"])
    def test_a_singleton_or_a_map_registers_nothing(
        self, produced: "tuple[dict, dict]", key: str,
    ) -> None:
        """``Maker.type`` is a singleton of a VALUE, not ``this``; a ``Map`` hands
        out pairs, so it has no single element."""
        types, elements = produced
        assert key not in types and key not in elements


# --- ELEMENT TYPES: the for-binder and lambda-parameter half -----------------

_ELEMS = """package demo

import java.io.FileWriter
import scala.concurrent.Future

class Elems {
  def writers(): Seq[FileWriter] = ???
  def maybe(): Option[FileWriter] = ???
  def later(): Future[FileWriter] = ???
  def either(): Either[String, FileWriter] = ???

  def forGenerator(ws: List[FileWriter]): Unit = {
    for (w <- ws) w.write("forGenerator")
  }

  def forOverCall(): Unit = {
    for (w <- writers()) w.append("forOverCall")
  }

  def lambdaParam(ws: Seq[FileWriter]): Unit = {
    ws.foreach(w => w.write("lambdaParam"))
  }

  def braceLambda(): Unit = {
    writers().map { w => w.getEncoding() }
  }

  def caseBlock(): Unit = {
    writers().foreach { case w => w.write("caseBlock") }
  }

  def placeholder(): Unit = {
    writers().foreach(_.write("placeholder"))
  }

  def preserved(): Unit = {
    writers().filter(_ != null).foreach(_.write("preserved"))
  }

  def projection(): Unit = {
    maybe().get.append("projection")
  }

  def getOrElseProjection(): Unit = {
    val m = maybe()
    m.getOrElse(null).append("getOrElse")
  }

  def futureMap(): Unit = {
    later().map(w => w.write("futureMap"))
  }

  def futureItself(): Unit = {
    later().onComplete(println)
  }

  def eitherRight(): Unit = {
    either().map(w => w.write("eitherRight"))
  }

  def localVal(): Unit = {
    val ws = writers()
    ws.foreach(w => w.write("localVal"))
  }

  def twoParams(ws: Seq[FileWriter]): Unit = {
    ws.foldLeft(0)((acc, w) => { w.write("foldLeft"); acc })
  }

  def matchIsNotAnElement(a: Any): Unit = a match {
    case w => w.write("matchIsNot")
  }

  def tupleIsNotAnElement(ps: Seq[(FileWriter, Int)]): Unit = {
    for ((w, i) <- ps) w.write("tupleIsNot")
  }
}
"""


@pytest.fixture(scope="module")
def element_edges(tmp_path_factory: pytest.TempPathFactory) -> list:
    from hypergumbo_lang_mainstream.scala import analyze_scala

    root = tmp_path_factory.mktemp("gokop_elems")
    (root / "Elems.scala").write_text(_ELEMS)
    result = analyze_scala(root)
    assert not result.skipped, "scala grammar unavailable -- refusing a vacuous pass"
    return [e for e in result.edges if e.edge_type == "calls"]


def _elem_edge(edges: list, needle: str, callee: str):
    hits = [i for i, line in enumerate(_ELEMS.splitlines(), start=1) if needle in line]
    assert len(hits) == 1, f"{needle!r} must occur exactly once in _ELEMS, found {hits}"
    found = [e for e in edges if e.line == hits[0]
             and e.dst.split(":")[3].rsplit(".", 1)[-1] == callee]
    assert len(found) == 1, f"expected one {callee} edge at {needle!r}, got {[e.dst for e in found]}"
    return found[0]


class TestElementTypedBinders:
    """A binder that receives ONE ELEMENT of a container whose element type is
    declared -- on a parameter, or on the return type of the call that built the
    container -- takes that element type."""

    @pytest.mark.parametrize("needle,callee", [
        ('w.write("forGenerator")', "write"),
        ('w.append("forOverCall")', "append"),
        ('w.write("lambdaParam")', "write"),
        ("w.getEncoding()", "getEncoding"),
        ('w.write("caseBlock")', "write"),
        ('_.write("placeholder")', "write"),
        ('_.write("preserved")', "write"),
        ('w.write("eitherRight")', "write"),
        ('w.write("localVal")', "write"),
        ('w.write("futureMap")', "write"),
    ])
    def test_binder_takes_the_element_type(
        self, element_edges: list, needle: str, callee: str,
    ) -> None:
        e = _elem_edge(element_edges, needle, callee)
        assert _slot(e) == "java.io.FileWriter", e.dst

    @pytest.mark.parametrize("needle,callee", [
        ('maybe().get.append("projection")', "append"),
        ('m.getOrElse(null).append("getOrElse")', "append"),
    ])
    def test_projection_unwraps_at_the_call_site(
        self, element_edges: list, needle: str, callee: str,
    ) -> None:
        e = _elem_edge(element_edges, needle, callee)
        assert _slot(e) == "java.io.FileWriter", e.dst

    def test_the_wrapper_itself_keeps_its_own_type(self, element_edges: list) -> None:
        """``Future``'s own methods are called on a ``Future``: the registry
        records the wrapper, qualified through the declaring file's import."""
        e = _elem_edge(element_edges, "later().onComplete(println)", "onComplete")
        assert _hint(e) == "scala.concurrent.Future"

    @pytest.mark.parametrize("needle", [
        'w.write("foldLeft")', 'w.write("matchIsNot")', 'w.write("tupleIsNot")',
    ])
    def test_not_an_element_binder(self, element_edges: list, needle: str) -> None:
        """Two-parameter lambda, a ``match`` case, a tuple pattern: none of them
        binds ONE element, so the receiver stays untyped."""
        e = _elem_edge(element_edges, needle, "write")
        assert _hint(e) is None and _slot(e) == "external"


# --- The shapes at the edge of each rule, typed or refused -------------------

_EDGES = """package demo

import java.io.FileWriter

object Maker {
  def apply(): FileWriter = ???
}

trait Plain

class Edges extends Plain {
  def mk(): FileWriter = ???
  def pair(): (FileWriter, Int) = ???
  def many(): Seq[FileWriter] = ???
  def single: Maker.type = Maker
  def table(): Map[String, FileWriter] = ???

  def forValueCollection(): Unit = {
    for { a <- Seq(1); ws = many(); w <- ws } yield w.write("forValueCollection")
  }

  def nestedLambda(xss: Seq[Seq[FileWriter]]): Unit = {
    xss.foreach(ys => ys.foreach(y => y.write("nestedLambda")))
  }

  def diamond(): Unit = {
    new Diamond().nothing().write("diamond")
  }

  def constantPattern(a: Any): Unit = a match {
    case Maker => Maker().flush()
  }

  def nestedPattern(a: Option[FileWriter]): Unit = a match {
    case Some(x) => x.write("nestedPattern")
  }

  def typedLambda(ws: Seq[Any]): Unit = {
    ws.foreach((w: FileWriter) => w.write("typedLambda"))
  }

  def notPassing(ws: Seq[FileWriter]): Unit = {
    ws.zipWith(w => w.write("notPassing"))
  }

  def bareHolder(): Unit = {
    run(w => w.write("bareHolder"))
  }

  def valLambda(): Unit = {
    val f = w => w.write("valLambda")
  }

  def generatorCall(): Unit = {
    for (w <- mk().items()) w.flush()
  }

  def forValue(): Unit = {
    for { a <- Seq(1); w = mk() } yield w.write("forValue")
  }

  def laterVal(lw: FileWriter): Unit = {
    lw.write("laterVal")
    val lw = 3
  }

  def ownInit(ow: FileWriter): Unit = {
    val ow = ow.append("ownInit")
  }

  def memoTwice(): Unit = {
    val m = mk()
    m.write("memoOne")
    m.write("memoTwo")
  }

  val cycA = cycB
  val cycB = cycA
  def cycle(): Unit = {
    cycA.write("cycle")
  }

  def destructured(): Unit = {
    val (d, n) = pair()
    d.write("destructured")
  }

  def refusedAnnotation(): Unit = {
    val r: Map[String, Int] = mk()
    r.write("refusedAnnotation")
  }

  def anonymous(): Unit = {
    new Plain { def g(): Unit = this.write("anonymous") }
  }

  def anonymousOwner(): Unit = {
    new Edges { def g(): Unit = mk().write("anonymousOwner") }
  }

  def placeholderNowhere(): Unit = {
    val g = _.write("placeholderNowhere")
  }

  def placeholderNotPassing(): Unit = {
    run(_.write("placeholderNotPassing"))
  }

  def placeholderInLambda(ws: Seq[FileWriter]): Unit = {
    ws.map(x => _.write("placeholderInLambda"))
  }

  def placeholderBlock(ws: Seq[FileWriter]): Unit = {
    ws.foreach { _.write("placeholderBlock") }
  }

  def parenthesised(): Unit = {
    (mk()).write("parenthesised")
  }

  def constructed(): Unit = {
    new FileWriter("p").write("constructed")
  }

  def genericNotCast(): Unit = {
    mk[Int].write("genericNotCast")
  }

  def curried(): Unit = {
    mk()(1).write("curried")
  }

  def literal(): Unit = {
    "s".write("literal")
  }

  def valueCall(): Unit = {
    val f = mk
    f().write("valueCall")
  }

  def applyCall(): Unit = {
    Maker().write("applyCall")
  }

  def traitCall(): Unit = {
    Plain().write("traitCall")
  }

  def lowerUnbound(): Unit = {
    nothing.thing().write("lowerUnbound")
  }

  def elementOfThis(): Unit = {
    this.foreach(w => w.write("elementOfThis"))
  }

  def elementOfParen(ws: Seq[FileWriter]): Unit = {
    (ws).foreach(w => w.write("elementOfParen"))
  }

  def elementOfUnbound(): Unit = {
    nobody.foreach(w => w.write("elementOfUnbound"))
  }

  def elementOfApply(): Unit = {
    Maker().foreach(w => w.write("elementOfApply"))
  }

  def elementOfLiteral(): Unit = {
    "ab".foreach(w => w.write("elementOfLiteral"))
  }

  def elementNotPreserved(ws: Seq[FileWriter]): Unit = {
    ws.grouped(2).foreach(w => w.write("elementNotPreserved"))
  }

  def elementOfCurried(): Unit = {
    mk()(1).foreach(w => w.write("elementOfCurried"))
  }

  def indentedMatch(a: Any): Unit =
    a match
      case w => w.write("indentedMatch")

  def elementOfValue(): Unit = {
    val p = pair()
    p.foreach(w => w.write("elementOfValue"))
  }
}

def topLevel(): FileWriter = ???

trait DBase
trait DLeft extends DBase
trait DRight extends DBase
class Diamond extends DLeft with DRight

def useTop(): Unit = {
  topLevel().write("useTop")
  this.write("topThis")
}

enum Color {
  case Red
  def f(): Unit = this.write("enumThis")
}
"""


@pytest.fixture(scope="module")
def edge_case_edges(tmp_path_factory: pytest.TempPathFactory) -> list:
    from hypergumbo_lang_mainstream.scala import analyze_scala

    root = tmp_path_factory.mktemp("gokop_edges")
    (root / "Edges.scala").write_text(_EDGES)
    result = analyze_scala(root)
    assert not result.skipped, "scala grammar unavailable -- refusing a vacuous pass"
    return [e for e in result.edges if e.edge_type == "calls"]


def _edge_case(edges: list, needle: str, callee: str):
    hits = [i for i, line in enumerate(_EDGES.splitlines(), start=1) if needle in line]
    assert len(hits) == 1, f"{needle!r} must occur exactly once in _EDGES, found {hits}"
    found = [e for e in edges if e.line == hits[0]
             and e.dst.split(":")[3].rsplit(".", 1)[-1] == callee]
    assert len(found) == 1, f"expected one {callee} edge at {needle!r}, got {[e.dst for e in found]}"
    return found[0]


class TestRuleBoundaries:
    """Each rule's boundary, both sides. TYPED: the rule applies. UNTYPED: a
    pattern compares rather than binds, an annotation is authoritative, or the
    expression names no single type. No name in this fixture is in the file-wide
    declared map at a site asserted UNTYPED, so each such assertion tests the
    rule and not the map's fallback."""

    @pytest.mark.parametrize("needle,callee", [
        ("case Maker => Maker().flush()", "flush"),          # capitalised pattern binds nothing
        ('ws.foreach((w: FileWriter) => w.write("typedLambda"))', "write"),
        ("for (w <- mk().items()) w.flush()", "items"),      # call inside an enumerator
        ('yield w.write("forValue")', "write"),              # for { w = expr }
        ('lw.write("laterVal")', "write"),                   # a later val is not yet in scope
        ('val ow = ow.append("ownInit")', "append"),         # nor is the val being defined
        ('m.write("memoOne")', "write"),
        ('m.write("memoTwo")', "write"),
        ('mk().write("anonymousOwner")', "write"),           # anonymous class's parent owns mk
        ('(mk()).write("parenthesised")', "write"),
        ('new FileWriter("p").write("constructed")', "write"),
        ('Maker().write("applyCall")', "write"),             # object apply's return type
        ('ws.foreach { _.write("placeholderBlock") }', "write"),
        ('(ws).foreach(w => w.write("elementOfParen"))', "write"),
        ('topLevel().write("useTop")', "write"),             # a top-level def
        ('yield w.write("forValueCollection")', "write"),    # element of a for { ws = .. }
    ])
    def test_typed(self, edge_case_edges: list, needle: str, callee: str) -> None:
        e = _edge_case(edge_case_edges, needle, callee)
        assert _slot(e) == "java.io.FileWriter", e.dst

    @pytest.mark.parametrize("needle,callee", [
        ('case Some(x) => x.write("nestedPattern")', "write"),
        ('ws.zipWith(w => w.write("notPassing"))', "write"),
        ('run(w => w.write("bareHolder"))', "write"),
        ('val f = w => w.write("valLambda")', "write"),
        ("for (w <- mk().items()) w.flush()", "flush"),
        ('cycA.write("cycle")', "write"),
        ('d.write("destructured")', "write"),
        ('r.write("refusedAnnotation")', "write"),
        ('this.write("anonymous")', "write"),
        ('val g = _.write("placeholderNowhere")', "write"),
        ('run(_.write("placeholderNotPassing"))', "write"),
        ('ws.map(x => _.write("placeholderInLambda"))', "write"),
        ('mk[Int].write("genericNotCast")', "write"),
        ('mk()(1).write("curried")', "write"),
        ('"s".write("literal")', "write"),
        ('f().write("valueCall")', "write"),
        ('Plain().write("traitCall")', "write"),
        ('nothing.thing().write("lowerUnbound")', "write"),
        ('this.foreach(w => w.write("elementOfThis"))', "write"),
        ('nobody.foreach(w => w.write("elementOfUnbound"))', "write"),
        ('Maker().foreach(w => w.write("elementOfApply"))', "write"),
        ('"ab".foreach(w => w.write("elementOfLiteral"))', "write"),
        ('ws.grouped(2).foreach(w => w.write("elementNotPreserved"))', "write"),
        ('mk()(1).foreach(w => w.write("elementOfCurried"))', "write"),
        ('case w => w.write("indentedMatch")', "write"),
        ('p.foreach(w => w.write("elementOfValue"))', "write"),
        ('this.write("topThis")', "write"),
        ('this.write("enumThis")', "write"),
        ('y => y.write("nestedLambda")', "write"),           # an element's element
        ('new Diamond().nothing().write("diamond")', "write"),  # base walk visits DBase once
    ])
    def test_untyped(self, edge_case_edges: list, needle: str, callee: str) -> None:
        e = _edge_case(edge_case_edges, needle, callee)
        assert _hint(e) is None and _slot(e) == "external", e.dst

    def test_this_receiver_of_an_element_call_is_typed(self, edge_case_edges: list) -> None:
        """``this`` is a type for the call ON it, though it has no element."""
        e = _edge_case(edge_case_edges, 'this.foreach(w => w.write("elementOfThis"))', "foreach")
        assert _hint(e) == "Edges"
