# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scope, emission and producer stable_ids for OCaml, F#, Elm and Haskell (WI-tusad).

These four analyzers used to leave their functions (and F# records/fields,
Elm ports, Haskell data types and instances) at ``stable_id=None`` for the
ADR-0035 backstop floor, which nulls every same-name group in a file. OCaml
also DROPPED the second same-name binding of a file (INV-kagib): a per-file set
of seen names kept ``module A``'s ``run`` and lost ``module B``'s. Each analyzer
now emits every declaration, names a binding inside a nested module with the
module chain (``A.run``), and mints the ADR-0035 producer formula through
``overload_arity.declared_arity_stable_id``.

What is pinned, per language, on the analyzer's own output:

* the exact value (the shared helper over the inputs the module docstring
  names), so a producer that drifted onto other inputs fails here;
* that every same-name declaration is emitted, each with its own non-null id
  (checked symbol by symbol, never through a dict keyed on name);
* the delete-an-earlier-sibling property: deleting an earlier same-name
  declaration leaves every survivor's id unchanged (lines are kept with blank
  lines so each survivor matches itself by line).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import (
    ARITY_KEY, PARAMETERS_KEY, declared_arity, declared_arity_stable_id,
    parameter_entry,
)
from hypergumbo_lang_common.elm import analyze_elm
from hypergumbo_lang_common.fsharp import analyze_fsharp
from hypergumbo_lang_common.haskell import analyze_haskell
from hypergumbo_lang_common.ocaml import analyze_ocaml

Analyze = Callable[[Path], AnalysisResult]


def _result(analyze: Analyze, root: Path, name: str, text: str) -> AnalysisResult:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    return analyze(root)


def _symbols(analyze: Analyze, root: Path, name: str, text: str) -> list[Symbol]:
    return list(_result(analyze, root, name, text).symbols)


def _by_line(symbols: list[Symbol], kinds: set[str]) -> dict[int, Symbol]:
    picked = [s for s in symbols if s.kind in kinds and s.span is not None]
    by_line = {s.span.start_line: s for s in picked}  # type: ignore[union-attr]
    assert len(by_line) == len(picked), "two symbols on one line"
    return by_line


def _assert_deletion_moves_nothing(
    analyze: Analyze, tmp_path: Path, name: str, text: str, deleted: str,
    kinds: set[str], survivors: int,
) -> None:
    """Delete ``deleted`` (replaced by as many blank lines) and compare."""
    assert deleted in text
    edited = text.replace(deleted, "\n" * deleted.count("\n"))
    before = _by_line(_symbols(analyze, tmp_path / "r1", name, text), kinds)
    after = _by_line(_symbols(analyze, tmp_path / "r2", name, edited), kinds)
    assert len(after) == survivors < len(before)  # reach
    for line, sym in after.items():
        assert sym.stable_id is not None, (line, sym.name)
        assert sym.stable_id == before[line].stable_id, (line, sym.name)


def _expected(
    kind: str, lang: str, path: str, name: str, qualified_name: str,
    meta: Optional[dict[str, Any]],
) -> str:
    return declared_arity_stable_id(
        kind=kind, lang=lang, path=path, name=name,
        qualified_name=qualified_name, arity=declared_arity(meta),
    )


def _edge_names(result: AnalysisResult) -> set[tuple[str, str]]:
    by_id = {s.id: s.name for s in result.symbols}
    return {
        (by_id.get(e.src, e.src), by_id.get(e.dst, e.dst))
        for e in result.edges if e.edge_type == "calls"
    }


# -- OCaml ----------------------------------------------------------------

_OCAML = (
    "module A = struct\n"
    "  let run x = x\n"
    "end\n"
    "module B = struct\n"
    "  let run x y = x + y\n"
    "  module C = struct let deep ?(o = 1) y = y end\n"
    "end\n"
    "let helper x = A.run x\n"
    "let rec f x =\n"
    "  let g z = z in\n"
    "  let v = 1 in\n"
    "  g (x + v)\n"
    "and h y = B.run y y\n"
    "let () =\n"
    "  let loc q = q in\n"
    "  ignore (loc 1)\n"
    "let k = fun a ->\n"
    "  let w =\n"
    "    let inner b = b in\n"
    "    inner a\n"
    "  in w\n"
    "let other x = Missing.run x\n"
    "type t = int\n"
    "module D = struct type t = string end\n"
)


def test_ocaml_emits_both_same_name_module_bindings(tmp_path: Path) -> None:
    """INV-kagib: ``run`` in module A and in module B are two symbols."""
    syms = _by_line(_symbols(analyze_ocaml, tmp_path, "demo.ml", _OCAML), {"function"})
    assert syms[2].name == "A.run" and syms[5].name == "B.run"
    assert syms[6].name == "B.C.deep"
    assert None not in (syms[2].stable_id, syms[5].stable_id)
    assert syms[2].stable_id != syms[5].stable_id
    assert syms[2].stable_id == _expected(
        "function", "ocaml", "demo.ml", "run", "A.run", syms[2].meta,
    )
    assert syms[6].meta == {PARAMETERS_KEY: [
        parameter_entry("?(o = 1)", default=True), parameter_entry("y"),
    ]}
    # Both type ``t``s are emitted too, under their table-formula names.
    types = [s for s in _symbols(analyze_ocaml, tmp_path / "t", "demo.ml", _OCAML)
             if s.kind == "type"]
    assert [t.name for t in types] == ["t", "t"]


def test_ocaml_and_bindings_and_locals(tmp_path: Path) -> None:
    syms = _by_line(_symbols(analyze_ocaml, tmp_path, "demo.ml", _OCAML), {"function"})
    # ``let rec f ... and h ...``: two symbols; f spans the definition, h its binding.
    f, h = syms[9], syms[13]
    assert (f.name, h.name) == ("f", "h")
    assert (f.span.start_col, h.span.start_col) == (0, 4)
    assert f.span.end_line == 13 and h.span.end_line == 13
    # A local function is emitted under its bare name with the enclosing
    # binding in its chain; a local value (``v``, ``w``) is not emitted.
    g = syms[10]
    assert g.name == "g"
    assert g.stable_id == _expected("function", "ocaml", "demo.ml", "g", "f/g", g.meta)
    inner = syms[19]
    assert inner.stable_id == _expected(
        "function", "ocaml", "demo.ml", "inner", "k/inner", inner.meta,
    )
    assert syms[17].meta == {PARAMETERS_KEY: []}
    assert 11 not in syms and 18 not in syms
    # A local of ``let () = ...`` has no named binding around it.
    loc = syms[15]
    assert loc.stable_id == _expected("function", "ocaml", "demo.ml", "loc", "loc", loc.meta)


def test_ocaml_calls_anchor_by_position_and_try_the_qualified_name(
    tmp_path: Path,
) -> None:
    result = _result(analyze_ocaml, tmp_path, "demo.ml", _OCAML)
    calls = _edge_names(result)
    assert ("helper", "A.run") in calls
    assert ("h", "B.run") in calls      # from the ``and`` binding, not f
    assert ("f", "g") in calls
    assert ("inner", "inner") not in calls
    assert ("k", "inner") in calls      # past the local value ``w``
    # ``Missing.run``: no such qualified name, falls back to the bare lookup.
    assert any(src == "other" for src, _ in calls)


def test_ocaml_deleting_an_earlier_sibling_moves_no_id(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_ocaml, tmp_path, "demo.ml", _OCAML,
        "module A = struct\n  let run x = x\nend\n", {"function"}, survivors=10,
    )


# -- F# -------------------------------------------------------------------

_FSHARP = (
    "module Demo\n"
    "\n"
    "module A =\n"
    "    let run x = x\n"
    "    type Point = { X: int; Y: int }\n"
    "    type Shape = Circle | Square\n"
    "\n"
    "module B =\n"
    "    let run x y = x + y\n"
    "    module Inner =\n"
    "        let deep () = 1\n"
    "\n"
    "type Cls(r: float) =\n"
    "    let helper x = x\n"
    "\n"
    "let caller x =\n"
    "    let inner z = z\n"
    "    inner (A.run x) + Other.run x\n"
)


def test_fsharp_scope_chains_and_ids(tmp_path: Path) -> None:
    syms = _by_line(
        _symbols(analyze_fsharp, tmp_path, "Demo.fs", _FSHARP),
        {"function", "record", "union"},
    )
    assert syms[4].name == "A.run" and syms[9].name == "B.run"
    assert syms[4].stable_id != syms[9].stable_id
    assert syms[9].stable_id == _expected(
        "function", "fsharp", "Demo.fs", "run", "B.run",
        {PARAMETERS_KEY: [parameter_entry("x"), parameter_entry("y")]},
    )
    assert syms[11].name == "B.Inner.deep"
    assert syms[11].meta == {PARAMETERS_KEY: [parameter_entry("()")]}
    assert syms[14].name == "Cls.helper"
    inner = syms[17]
    assert inner.name == "inner"
    assert inner.stable_id == _expected(
        "function", "fsharp", "Demo.fs", "inner", "caller/inner", inner.meta,
    )
    assert syms[5].name == "A.Point" and syms[6].name == "A.Shape"
    assert syms[5].stable_id == _expected(
        "record", "fsharp", "Demo.fs", "Point", "A.Point", None,
    )
    fields = [s for s in _symbols(analyze_fsharp, tmp_path / "f", "Demo.fs", _FSHARP)
              if s.kind == "field"]
    assert [f.name for f in fields] == ["A.Point.X", "A.Point.Y"]
    assert fields[0].stable_id == _expected(
        "field", "fsharp", "Demo.fs", "X", "A.Point.X", None,
    )


def test_fsharp_dotted_call_resolves_the_qualified_function(tmp_path: Path) -> None:
    result = _result(analyze_fsharp, tmp_path, "Demo.fs", _FSHARP)
    calls = _edge_names(result)
    assert ("caller", "A.run") in calls
    assert ("caller", "inner") in calls


def test_fsharp_deleting_an_earlier_sibling_moves_no_id(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_fsharp, tmp_path, "Demo.fs", _FSHARP,
        "module A =\n    let run x = x\n    type Point = { X: int; Y: int }\n"
        "    type Shape = Circle | Square\n",
        {"function", "record", "union"}, survivors=5,
    )


# -- Elm ------------------------------------------------------------------

_ELM = (
    "port module Demo exposing (..)\n"
    "\n"
    "first : Int\n"
    "first =\n"
    "    1\n"
    "\n"
    "run : Int -> ( Int, Int ) -> Int\n"
    "run x ( a, b ) =\n"
    "    x\n"
    "\n"
    "port send : String -> Cmd msg\n"
)


def test_elm_function_and_port_ids_are_the_producer_formula(tmp_path: Path) -> None:
    syms = _by_line(
        _symbols(analyze_elm, tmp_path, "Demo.elm", _ELM), {"function", "port"},
    )
    assert syms[4].meta == {PARAMETERS_KEY: []}
    assert syms[8].meta == {PARAMETERS_KEY: [
        parameter_entry("x"), parameter_entry("( a, b )"),
    ]}
    assert syms[8].stable_id == _expected(
        "function", "elm", "Demo.elm", "run", "run", syms[8].meta,
    )
    assert syms[11].stable_id == _expected(
        "port", "elm", "Demo.elm", "send", "send", None,
    )


def test_elm_deleting_an_earlier_declaration_moves_no_id(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_elm, tmp_path, "Demo.elm", _ELM, "first : Int\nfirst =\n    1\n",
        {"function", "port"}, survivors=2,
    )


# -- Haskell --------------------------------------------------------------

_HASKELL = (
    "module Demo where\n"
    "\n"
    "data T = A | B\n"
    "\n"
    "class Shape a where\n"
    "  area :: a -> Int\n"
    "\n"
    "instance Shape Int where\n"
    "  area x = x\n"
    "\n"
    "f :: Int -> Int -> Int\n"
    "f 0 y = y\n"
    "f x y = x\n"
    "\n"
    "g = \\x -> x\n"
)


def test_haskell_ids_carry_equation_arity(tmp_path: Path) -> None:
    syms = _by_line(
        _symbols(analyze_haskell, tmp_path, "Demo.hs", _HASKELL),
        {"function", "data", "instance", "class"},
    )
    f, g = syms[12], syms[15]
    assert f.meta == {ARITY_KEY: 2} and g.meta == {ARITY_KEY: 0}
    assert f.stable_id == _expected("function", "haskell", "Demo.hs", "f", "f", f.meta)
    assert syms[3].stable_id == _expected("data", "haskell", "Demo.hs", "T", "T", None)
    assert syms[8].stable_id == _expected(
        "instance", "haskell", "Demo.hs", "Shape Int", "Shape Int", None,
    )
    # A class keeps its table-formula id, computed downstream.
    assert syms[5].stable_id is None


def test_haskell_deleting_an_earlier_declaration_moves_no_id(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_haskell, tmp_path, "Demo.hs", _HASKELL, "data T = A | B\n",
        {"function", "data", "instance"}, survivors=3,
    )
