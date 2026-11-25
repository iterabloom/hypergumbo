# SPDX-License-Identifier: AGPL-3.0-or-later
"""A variable's type belongs to the function that binds it (INV-zabud).

``test_call_resolution_matrix.py`` pins the rule for every mainstream language
in its ``scope_own`` and ``scope_ext`` columns. This module holds what that
table cannot show:

* C# and Groovy bind an untyped receiver by name alone (WI-dovoz, WI-tiluh),
  so their ``scope_ext`` cell reads ``picked`` whether or not a type crossed
  functions. Here the question is asked directly: does the destination of an
  untyped ``db.execute()`` change when the FUNCTIONS around it swap places?
* What must still cross functions, because it is not a function's local: a
  constructor parameter read as ``this.<name>`` (TypeScript), a class
  parameter (Scala), a chunk-level ``local`` (Lua), and a local seen from a
  closure inside its function (JavaScript).

Every test asserts reach first: the typed callers reach the class they
construct, so an unresolved or missing edge cannot pass for "did not leak".
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/var_scope.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/csharp.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/groovy.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/lua.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/scala.py
from __future__ import annotations

from pathlib import Path

import pytest

import _call_matrix_fixture as fx


def reached(directory: Path, files: dict[str, str], *callers: str, target: str = "execute") -> list[str]:
    """Write ``files``, survey them, and say what each caller's ``target`` call reaches."""
    for rel, text in files.items():
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    behavior_map = fx.survey(directory)
    return [fx.arm_cell(behavior_map, directory, "direct", caller, target)[0] for caller in callers]


# ---------------------------------------------------------------- C#, Groovy
def _csharp(functions: list[str]) -> dict[str, str]:
    body = "\n".join(functions)
    return {"App/Program.cs": f"""using ExtLib;

namespace App
{{
    public class Conn
    {{
        public int execute() {{ return 1; }}
    }}
    public class Other
    {{
        public int execute() {{ return 2; }}
    }}
    public static class Program
    {{
{body}
    }}
}}
"""}


def _groovy(functions: list[str]) -> dict[str, str]:
    body = "\n".join(functions)
    return {"app/Main.groovy": f"""package app

import ext.Lib

class Conn {{
    int execute() {{ return 1 }}
}}
class Other {{
    int execute() {{ return 2 }}
}}
class Main {{
{body}
}}
"""}


def _same_name_program(lang: str, decoy_by_parameter: bool, decoy_first: bool) -> dict[str, str]:
    """``typed_decoy`` binds ``h`` to Other, ``untyped`` to an external value, ``typed_right`` to Conn."""
    head, own, ext, call, tail, param = fx._SCOPE[lang]

    def function(name: str, bind: str, cls: str = "", params: str = "") -> str:
        lines = [head.replace("@n", name).replace("@p", params)]
        lines += [bind.replace("@c", cls)] if bind else []
        return "\n".join([*lines, call, tail]) + "\n"

    assert param is not None
    decoy = (function("typed_decoy", "", params=param.replace("@c", "Other")) if decoy_by_parameter
             else function("typed_decoy", own, "Other"))
    right = function("typed_right", own, "Conn")
    untyped = function("untyped", ext)
    functions = [decoy, untyped, right] if decoy_first else [right, untyped, decoy]
    return {"csharp": _csharp, "groovy": _groovy}[lang](functions)


@pytest.mark.parametrize("lang", ["csharp", "groovy"])
@pytest.mark.parametrize("decoy_by_parameter", [False, True], ids=["local", "parameter"])
def test_an_untyped_local_is_not_typed_by_the_function_before_it(
    lang: str, decoy_by_parameter: bool, tmp_path: Path,
) -> None:
    seen = []
    for decoy_first in (True, False):
        files = _same_name_program(lang, decoy_by_parameter, decoy_first)
        decoy, untyped, right = reached(
            tmp_path / f"first{int(decoy_first)}", files, "typed_decoy", "untyped", "typed_right")
        assert (decoy, right) == ("decoy", "right")  # reach: a typed ``h`` resolves by its type
        seen.append(untyped)
    assert seen[0] == seen[1], f"the untyped call follows the function written before it: {seen}"


# ---------------------------------------------------------------- TypeScript, JavaScript
TS_PROPERTIES = """class Svc {
  run(): number { return 1; }
}
class Other {
  run(): number { return 2; }
}
class Injected {
  constructor(private svc: Svc) {}
  viaConstructor(): void {
    this.svc.run();
  }
}
class Setter {
  setDep(dep: Other): void {
    dep.run();
  }
  viaSetter(): void {
    this.dep.run();
  }
}
"""


def test_a_constructor_parameter_types_the_property_in_another_method(tmp_path: Path) -> None:
    constructor, setter, other = reached(
        tmp_path, {"main.ts": TS_PROPERTIES}, "viaConstructor", "setDep", "viaSetter", target="run")
    assert setter == "decoy"  # reach: a typed parameter resolves inside its own method
    assert constructor == "right"
    assert other == "unresolved"  # ``setDep``'s parameter is not a property


JS_CLOSURE = """class Conn {
  execute() { return 1; }
}
class Other {
  execute() { return 2; }
}
function outer() {
  const c = new Conn();
  const inner = () => {
    c.execute();
  };
  inner();
}
function sibling() {
  const c = extlib.make();
  c.execute();
}
"""


def test_a_closure_sees_the_local_of_the_function_around_it(tmp_path: Path) -> None:
    inner, sibling = reached(tmp_path, {"main.js": JS_CLOSURE}, "inner", "sibling")
    assert inner == "right"
    assert sibling == "unresolved"


# ---------------------------------------------------------------- Lua
LUA_UPVALUE = """local Conn = {}
Conn.__index = Conn

function Conn.new()
  return setmetatable({}, Conn)
end

function Conn:execute()
  return 1
end

local shared = Conn.new()

local function via_upvalue()
  shared:execute()
end

return { via_upvalue = via_upvalue }
"""


def test_a_chunk_level_local_is_seen_inside_a_function(tmp_path: Path) -> None:
    assert reached(tmp_path, {"main.lua": LUA_UPVALUE}, "via_upvalue") == ["right"]


# ---------------------------------------------------------------- Scala
SCALA_CLASS_PARAMETER = """package app

class Conn {
  def execute(): Int = 1
}
class Other {
  def execute(): Int = 2
}
class Holder(svc: Conn) {
  def viaClassParameter(): Unit = {
    svc.execute()
  }
}
object Main {
  def typedDecoy(s: Other): Unit = {
    s.execute()
  }
  def viaLambda(xs: Seq[Int]): Unit = {
    xs.foreach { s => s.execute() }
  }
}
"""


def test_a_class_parameter_types_a_method_and_a_function_parameter_does_not_type_a_sibling(
    tmp_path: Path,
) -> None:
    class_parameter, decoy, sibling = reached(
        tmp_path, {"app/Main.scala": SCALA_CLASS_PARAMETER}, "viaClassParameter", "typedDecoy", "viaLambda")
    assert decoy == "decoy"  # reach: a typed parameter resolves inside its own function
    assert class_parameter == "right"
    assert sibling == "unresolved"  # ``s`` there is an Int the analyzer cannot type, not ``typedDecoy``'s Other
