# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/scala.py
"""A method or field passed as a value reaches what it names.

Scala hands code to a library by NAME far more often than by ``f _``:
``handleWith(createItem)`` (automatic eta-expansion), ``handleExceptions(
apiExceptionHandler)`` and ``handleWebSocketMessages(echoFlow)`` (a field
holding a handler value), Play's ``mapping(...)(SignupData.fromForm)(
SignupData.toForm)``. The analyzer emitted a ``references`` edge only for an
explicit ``f _`` inside a function, so each of these handlers had no caller.

The rule: an argument of a call that is a bare name, ``Obj.member`` or
``this.member``, and that names a function, method or field of the project,
gets a ``references`` edge from the code holding the call (the function, else
the class or object whose body holds it, as calls are anchored). A bare name
is the enclosing type's member, or a top-level function when exactly one in
the project has that name. A name a parameter, a lambda, a pattern or a local
``val`` binds is that local value and gets no edge. Evidence ``eta_expansion``
for a method or function that takes parameters (the name stands for the
function), ``ast_name_read`` for a field or a parameterless method (the name
stands for its value). The explicit ``f _`` keeps its own emitter, which now
also runs in a class or object body.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Symbol
from hypergumbo_lang_mainstream.scala import analyze_scala

_ROUTES = """\
package example

object SignupData {
  def fromForm(name: String, age: Int): String = name
  def toForm(d: String): Option[(String, Int)] = None
}

def topLevelHandler(x: Int): Int = x

class ApiRoutes {
  def createItem(body: String): String = body
  def timeoutResponse(r: String): String = r
  val apiExceptionHandler: String = "handler"
  val echoFlow: String = "flow"
  def eagerValue: Int = 3
  def unused(x: Int): Int = x
  def named(x: Int): Int = x

  val routes: String = concat(
    handleWith(createItem),
    handleExceptions(apiExceptionHandler),
    handleWebSocketMessages(echoFlow),
    withRequestTimeoutResponse(this.timeoutResponse),
    mapping("a")(SignupData.fromForm)(SignupData.toForm),
    register(topLevelHandler),
    lift(eagerValue),
    bind(handler = named)
  )

  def methodRoutes(createItem: String): String =
    handleWith(createItem)

  def lambdas(): Seq[String] =
    Seq("a").map(echoFlow => handleWith(echoFlow))

  def localVal(): String = {
    val apiExceptionHandler = "local"
    handleExceptions(apiExceptionHandler)
  }

  def eta(): String = handleWith(createItem _)

  def qualifiedLocals(registry: Other): String =
    concat(handleWith(registry.call), handleWith(Deep.Inner.fn), handleWith(SignupData.missing))
}

object Deep {
  object Inner {
    def fn(x: Int): Int = x
  }
}

class Other {
  val unused: Int = 1
  def call(): Int = handle(unused)
  def literal(): String = handle("createItem")
}
"""


def _analyze(tmp_path: Path) -> tuple[dict[str, Symbol], list[Edge]]:
    (tmp_path / "A.scala").write_text(_ROUTES)
    result = analyze_scala(tmp_path)
    return {s.name: s for s in result.symbols}, list(result.edges)


def _refs(symbols: dict[str, Symbol], edges: list[Edge]) -> dict[tuple[str, str], str]:
    by_id = {s.id: s.name for s in symbols.values()}
    return {
        (by_id[e.src], by_id[e.dst]): e.evidence_type
        for e in edges
        if e.edge_type == "references" and e.src in by_id and e.dst in by_id
    }


class TestNamedValueArguments:
    def test_a_method_passed_by_name(self, tmp_path: Path) -> None:
        refs = _refs(*_analyze(tmp_path))
        assert refs[("ApiRoutes", "ApiRoutes.createItem")] == "eta_expansion"

    def test_a_field_holding_a_handler(self, tmp_path: Path) -> None:
        symbols, edges = _analyze(tmp_path)
        assert symbols["ApiRoutes.echoFlow"].kind == "field"
        refs = _refs(symbols, edges)
        assert refs[("ApiRoutes", "ApiRoutes.apiExceptionHandler")] == "ast_name_read"
        assert refs[("ApiRoutes", "ApiRoutes.echoFlow")] == "ast_name_read"

    def test_this_member(self, tmp_path: Path) -> None:
        refs = _refs(*_analyze(tmp_path))
        assert refs[("ApiRoutes", "ApiRoutes.timeoutResponse")] == "eta_expansion"

    def test_an_objects_member(self, tmp_path: Path) -> None:
        refs = _refs(*_analyze(tmp_path))
        assert refs[("ApiRoutes", "SignupData.fromForm")] == "eta_expansion"
        assert refs[("ApiRoutes", "SignupData.toForm")] == "eta_expansion"

    def test_a_top_level_function(self, tmp_path: Path) -> None:
        refs = _refs(*_analyze(tmp_path))
        assert refs[("ApiRoutes", "topLevelHandler")] == "eta_expansion"

    def test_a_parameterless_method_is_a_value_read(self, tmp_path: Path) -> None:
        refs = _refs(*_analyze(tmp_path))
        assert refs[("ApiRoutes", "ApiRoutes.eagerValue")] == "ast_name_read"

    def test_a_named_argument(self, tmp_path: Path) -> None:
        refs = _refs(*_analyze(tmp_path))
        assert refs[("ApiRoutes", "ApiRoutes.named")] == "eta_expansion"

    def test_explicit_eta_expansion_still_one_edge(self, tmp_path: Path) -> None:
        symbols, edges = _analyze(tmp_path)
        eta = symbols["ApiRoutes.eta"]
        target = symbols["ApiRoutes.createItem"]
        hits = [e for e in edges if e.edge_type == "references"
                and e.src == eta.id and e.dst == target.id]
        assert len(hits) == 1


class TestLocalValuesAreNotMembers:
    def test_a_parameter_of_the_same_name(self, tmp_path: Path) -> None:
        refs = _refs(*_analyze(tmp_path))
        assert ("ApiRoutes.methodRoutes", "ApiRoutes.createItem") not in refs

    def test_a_lambda_parameter_of_the_same_name(self, tmp_path: Path) -> None:
        refs = _refs(*_analyze(tmp_path))
        assert ("ApiRoutes.lambdas", "ApiRoutes.echoFlow") not in refs

    def test_a_local_val_of_the_same_name(self, tmp_path: Path) -> None:
        refs = _refs(*_analyze(tmp_path))
        assert ("ApiRoutes.localVal", "ApiRoutes.apiExceptionHandler") not in refs

    def test_a_member_of_a_local_value_or_a_path_or_unknown(self, tmp_path: Path) -> None:
        refs = _refs(*_analyze(tmp_path))
        # ``registry.call``: registry is a parameter; ``Deep.Inner.fn``: a
        # path of two qualifiers is not read; ``SignupData.missing``: no such
        # member.
        assert not [k for k in refs if k[0] == "ApiRoutes.qualifiedLocals"]

    def test_another_classs_member_and_a_string(self, tmp_path: Path) -> None:
        symbols, edges = _analyze(tmp_path)
        refs = _refs(symbols, edges)
        # ``handle(unused)`` in Other is Other's field, not ApiRoutes.unused.
        assert refs[("Other.call", "Other.unused")] == "ast_name_read"
        assert ("Other.call", "ApiRoutes.unused") not in refs
        assert not [k for k in refs if k[0] == "Other.literal"]
        assert not [k for k in refs if k[1] == "ApiRoutes.unused"]
