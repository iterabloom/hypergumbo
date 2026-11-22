# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/scala.py
"""A bare call in a class or object BODY reaches the type's own method.

A call that sits in no function -- a ``val``'s initialiser in a class body
(an akka-http ``val routes: Route = path("x") { complete(search(q)) }``, a
Play ``val form = Form(... verifying(a => isAdult(a)))``), or a statement in
an ``object X extends App`` body -- is anchored on the type that holds it
(INV-bamij). The type's own methods were not tried for it: the implicit-this
check takes the owner from the anchor's name as if it were a method
(``Owner.member``), and a type's name has no member part, so every bare call
to a sibling method was withheld as a possible cross-class magnet and stayed
an external symbol. The method looked unused.

Now the owner of a call anchored on a type is that type. A bare call to a
method of a DIFFERENT class (the magnet INV-fahub withholds) stays withheld.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Symbol
from hypergumbo_lang_mainstream.scala import analyze_scala

_SOURCE = """\
package example

class ApiRoutes {
  def search(q: String): String = q
  def listAll(): String = "all"

  val routes: String = complete(search("q")) + listAll()

  def methodRoutes(): String = search("m")
}

object Server extends App {
  def appRoutes(): String = "r"
  println(appRoutes())
}

class Outer {
  class Inner {
    def helper(): Int = 1
    val value: Int = helper()
  }
}

class Exporter {
  def export(): Int = 1
}

class Unrelated {
  val v: Int = export()
}
"""


def _calls(tmp_path: Path) -> tuple[dict[str, Symbol], list[Edge]]:
    (tmp_path / "A.scala").write_text(_SOURCE)
    result = analyze_scala(tmp_path)
    return {s.name: s for s in result.symbols}, [e for e in result.edges if e.edge_type == "calls"]


def _resolved(symbols: dict[str, Symbol], edges: list[Edge]) -> set[tuple[str, str]]:
    by_id = {s.id: s.name for s in symbols.values()}
    return {(by_id[e.src], by_id[e.dst]) for e in edges if e.src in by_id and e.dst in by_id}


class TestClassBodyCalls:
    def test_a_val_initialiser_reaches_the_classs_methods(self, tmp_path: Path) -> None:
        symbols, edges = _calls(tmp_path)
        assert symbols["ApiRoutes.routes"].kind == "field"
        resolved = _resolved(symbols, edges)
        assert ("ApiRoutes", "ApiRoutes.search") in resolved
        assert ("ApiRoutes", "ApiRoutes.listAll") in resolved

    def test_an_object_body_reaches_the_objects_methods(self, tmp_path: Path) -> None:
        symbols, edges = _calls(tmp_path)
        assert ("Server", "Server.appRoutes") in _resolved(symbols, edges)

    def test_a_nested_class_body(self, tmp_path: Path) -> None:
        symbols, edges = _calls(tmp_path)
        # The analyzer names a nested class by its own name (``Inner``).
        assert symbols["Inner"].kind == "class"
        assert ("Inner", "Inner.helper") in _resolved(symbols, edges)

    def test_a_method_body_is_unchanged(self, tmp_path: Path) -> None:
        symbols, edges = _calls(tmp_path)
        assert ("ApiRoutes.methodRoutes", "ApiRoutes.search") in _resolved(symbols, edges)


class TestAnotherClassesMethodStaysWithheld:
    def test_no_edge_to_a_different_classs_method(self, tmp_path: Path) -> None:
        symbols, edges = _calls(tmp_path)
        unrelated = symbols["Unrelated"]
        from_unrelated = [e for e in edges if e.src == unrelated.id]
        assert from_unrelated, "the call in Unrelated's body was not seen"
        assert symbols["Exporter.export"].id not in {e.dst for e in from_unrelated}
