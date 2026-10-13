# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nested same-name scopes: ids fixed in the WI-tusad stable_id scheme bump
(INV-fabih's sweep; ADR-0035 §1, §6), plus the F1 cousins of this package.

A local function hashed none of the functions enclosing it (Dart, Julia,
MATLAB, Starlark), a Julia struct/module/variable took the backstop's bare-name
table key, and an OCaml ``type t`` / ``module C`` or an F# ``let mutable x``
kept a bare name inside ``module A`` and ``module B``: the twins tied, the
per-file split pass numbered them by emission order, and deleting the first
outer scope moved the survivor's id. Each case runs the analyzer directly,
with repo-relative paths and the backstop's table formula applied as the
pipeline does; survivors are matched by their own position.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import populate_kind_stable_ids
from hypergumbo_lang_common.dart import analyze_dart
from hypergumbo_lang_common.fsharp import analyze_fsharp
from hypergumbo_lang_common.julia import analyze_julia
from hypergumbo_lang_common.matlab import analyze_matlab
from hypergumbo_lang_common.ocaml import analyze_ocaml
from hypergumbo_lang_common.purescript import analyze_purescript
from hypergumbo_lang_common.scheme import analyze_scheme
from hypergumbo_lang_common.starlark import analyze_starlark


def _symbols(analyze: Callable, root: Path, rel: str, text: str) -> list:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    symbols = [s for s in analyze(root).symbols if s.kind != "file" and s.path.endswith(rel)]
    for sym in symbols:
        sym.path = rel
    populate_kind_stable_ids(symbols)
    return symbols


def _position(sym) -> tuple:
    return (sym.kind, sym.name, sym.span.start_line, sym.span.start_col)


def _check(
    analyze: Callable, tmp_path: Path, rel: str, earlier: str, rest: str, head: str = "",
) -> list:
    """No two symbols share a stable_id, and blanking ``earlier`` (lines keep
    their numbers) leaves every survivor's id alone."""
    with_sibling = _symbols(analyze, tmp_path / "with", rel, head + earlier + rest)
    ids = [s.stable_id for s in with_sibling]
    assert None not in ids, [s.name for s in with_sibling if s.stable_id is None]
    assert len(set(ids)) == len(ids), sorted(
        (s.kind, s.name) for s in with_sibling if ids.count(s.stable_id) > 1)
    before: dict[tuple, list] = {}
    for s in with_sibling:
        before.setdefault(_position(s), []).append(s.stable_id)
    after = _symbols(analyze, tmp_path / "without", rel,
                     head + "\n" * earlier.count("\n") + rest)
    for s in after:
        assert s.stable_id in before.get(_position(s), []), (s.kind, s.name, s.span.start_line)
    return after


CASES = {
    "dart": (analyze_dart, "lib/nested.dart",
             "int a() {\n  int inner() => 1;\n  return inner();\n}\n",
             "int b() {\n  int inner() => 2;\n  return inner();\n}\n"
             "class C {\n  int m() {\n    int inner() => 3;\n    return inner();\n  }\n}\n"
             "mixin M {\n  int n() {\n    int inner() => 4;\n    return inner();\n  }\n}\n"),
    "fsharp": (analyze_fsharp, "src/Nested.fs",
               "module A =\n    let mutable x = 1\n\n", "module B =\n    let mutable x = 1\n",
               "module Nested\n\n"),
    "julia": (analyze_julia, "src/nested.jl",
              "module A\nstruct Inner\n    v::Int\nend\nlimit = 1\nmodule Sub\nend\n"
              "function run()\n    function inner()\n        1\n    end\n    inner()\nend\nend\n",
              "module B\nstruct Inner\n    v::Int\nend\nlimit = 2\nmodule Sub\nend\n"
              "function run()\n    function inner()\n        2\n    end\n    inner()\nend\n"
              "short() = 1\nend\n"),
    "matlab": (analyze_matlab, "src/nested.m",
               "function a()\n  function inner()\n  end\nend\n",
               "function b()\n  function inner()\n  end\nend\n"),
    "matlab-class": (analyze_matlab, "src/Shape.m",
                     "    function a(obj)\n      function inner()\n      end\n    end\n",
                     "    function b(obj)\n      function inner()\n      end\n    end\n  end\nend\n",
                     "classdef Shape\n  methods\n"),
    "ocaml": (analyze_ocaml, "src/nested.ml",
              "module A = struct\n  type t = int\n  module C = struct\n    let v = 1\n  end\nend\n",
              "module B = struct\n  type t = int\n  module C = struct\n    let v = 2\n  end\nend\n"),
    "starlark": (analyze_starlark, "build/nested.bzl",
                 "def a():\n    def inner():\n        return 1\n    return inner()\n\n",
                 "def b():\n    def inner():\n        return 2\n    return inner()\n"),
}


@pytest.mark.parametrize("lang", sorted(CASES))
def test_deleting_an_earlier_same_name_scope_moves_no_id(tmp_path: Path, lang: str) -> None:
    analyze, rel, earlier, rest, *head = CASES[lang]
    assert _check(analyze, tmp_path, rel, earlier, rest, *head)


def test_ocaml_and_fsharp_name_table_kinds_by_their_module(tmp_path: Path) -> None:
    """The table kinds are named like the bindings beside them (``A.t``,
    ``A.C``, ``A.x``); a top-level one keeps its bare name."""
    ocaml = _symbols(analyze_ocaml, tmp_path / "o", "m.ml",
                     "type top = int\nmodule A = struct\n  type t = int\n  module C = struct end\nend\n")
    assert sorted((s.kind, s.name) for s in ocaml) == [
        ("module", "A"), ("module", "A.C"), ("type", "A.t"), ("type", "top")]
    fsharp = _symbols(analyze_fsharp, tmp_path / "f", "M.fs",
                      "module M\n\nlet top = 1\nmodule A =\n    let mutable x = 1\n")
    assert sorted(s.name for s in fsharp if s.kind == "variable") == ["A.x", "top"]


def test_scheme_param_count_does_not_count_the_rest_dot(tmp_path: Path) -> None:
    """``(f a . rest)`` takes two parameters; the grammar spells ``.`` as a
    symbol, which ``param_count`` used to count as a third."""
    syms = _symbols(analyze_scheme, tmp_path, "src/m.scm",
                    "(define (f a . rest) a)\n(define (g a b) a)\n")
    counts = {s.name: s.meta["param_count"] for s in syms}
    assert counts == {"f": 2, "g": 2}


def test_purescript_bare_type_signatures_are_read(tmp_path: Path) -> None:
    """``helper :: Int`` is a bare ``type_name``: the function lost its
    signature, and a class member declared that way emitted nothing."""
    syms = _symbols(analyze_purescript, tmp_path, "src/M.purs",
                    "module M where\n\nclass Default a where\n  def :: a\n  size :: Int\n\n"
                    "helper :: Int\nhelper = 2\n\nr :: { a :: Int }\nr = { a: 1 }\n")
    sigs = {s.name: s.signature for s in syms if s.kind == "function"}
    assert sigs == {
        "M.def": "a", "M.size": "Int", "M.helper": "Int", "M.r": "{ a :: Int }",
    }
