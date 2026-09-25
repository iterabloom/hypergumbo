# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag's cross-language gate for hypergumbo-lang-common: every analyzer that finds a
call's enclosing function is classified, so none can drift silently.

The same gate as hypergumbo-lang-mainstream's test_call_anchor_gate.py; see its
docstring for the statement and the mechanism. It enumerates every module in
this package that DEFINES an enclosing-function lookup or emits a ``calls``
edge. Each must be VERIFIED
(named containment test, which runs the analyzer on a same-named-declarations
fixture) or UNVERIFIED (the residual row). The UNVERIFIED half is bookkeeping,
not a check: it records that nobody has looked, so a new analyzer cannot arrive
unclassified and read as covered.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_lang_common"
_LOOKUP = re.compile(r"^def _(?:get|find)_enclosing_(?:function|method|callable)\w*\(", re.M)
#: A module that emits a ``calls`` edge anchors it somewhere, whatever its lookup
#: is called. The name pattern alone missed 25 such modules (WI-tosum).
_CALLS = re.compile(r"""edge_type=["']calls["']""")

#: module -> ("verified", "test_module::test_name") | ("unverified", row id)
CLASSIFIED: dict[str, tuple[str, str]] = {
    "elixir": ("verified", "test_elixir_enclosing_anchor::test_same_short_name_in_two_modules_of_one_file"),
    "erlang": ("verified", "test_erlang_enclosing_anchor::test_ifdef_alternatives_anchor_to_the_branch_that_contains_the_call"),
    "haskell": ("verified", "test_haskell_equation_span::test_every_call_is_inside_its_src"),
    "dart": ("verified", "test_call_anchor_gate_common::test_dart_same_named_callables"),
    "elm": ("verified", "test_call_anchor_gate_common::test_elm_same_named_callables"),
    "fsharp": ("verified", "test_call_anchor_gate_common::test_fsharp_same_named_callables"),
    "glsl": ("verified", "test_call_anchor_gate_common::test_glsl_same_named_callables"),
    "hlsl": ("verified", "test_call_anchor_gate_common::test_hlsl_same_named_callables"),
    "julia": ("verified", "test_call_anchor_gate_common::test_julia_same_named_callables"),
    "matlab": ("verified", "test_call_anchor_gate_common::test_matlab_same_named_callables"),
    "nix": ("verified", "test_call_anchor_gate_common::test_nix_same_named_callables"),
    "r_lang": ("verified", "test_call_anchor_gate_common::test_r_lang_same_named_callables"),
    "scheme": ("verified", "test_call_anchor_gate_common::test_scheme_same_named_callables"),
    "starlark": ("verified", "test_call_anchor_gate_common::test_starlark_same_named_callables"),
    "wgsl": ("verified", "test_call_anchor_gate_common::test_wgsl_same_named_callables"),
    "clojure": ("unverified", "WI-tosum"),
    "commonlisp": ("unverified", "WI-tosum"),
    "fortran": ("unverified", "WI-tosum"),
    "ocaml": ("unverified", "WI-tosum"),
    "purescript": ("unverified", "WI-tosum"),
    "racket": ("unverified", "WI-tosum"),
    "robot": ("unverified", "WI-tosum"),
}


def _modules_with_a_lookup() -> set[str]:
    """Every module that defines an enclosing lookup by the conventional name, or
    emits a ``calls`` edge. A literal ``edge_type="calls"`` is what the second
    pattern sees; a module that spells the type through a variable is not seen."""
    found = set()
    for p in _SRC.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        if _LOOKUP.search(text) or _CALLS.search(text):
            found.add(p.stem)
    return found


def test_every_enclosing_lookup_is_classified() -> None:
    found = _modules_with_a_lookup()
    assert "elixir" in found, "reach: the scan must see a known lookup"
    assert found == set(CLASSIFIED), (
        f"unclassified: {sorted(found - set(CLASSIFIED))}; "
        f"stale: {sorted(set(CLASSIFIED) - found)}")


@pytest.mark.parametrize("module", sorted(m for m, (s, _) in CLASSIFIED.items() if s == "verified"))
def test_a_verified_entry_names_a_real_test(module: str) -> None:
    test_module, test_name = CLASSIFIED[module][1].split("::")
    assert callable(getattr(importlib.import_module(test_module), test_name, None)), CLASSIFIED[module]


def _assert_contained(result, calls: list[int]) -> None:
    """Reach first: a call edge credited to a callable (not the file) at every
    expected line. Then containment: every call edge's src span holds its line."""
    by_id = {s.id: s for s in result.symbols}
    anchors = [
        (by_id[e.src].name, by_id[e.src].span.start_line, by_id[e.src].span.end_line, e.line)
        for e in result.edges
        if e.edge_type == "calls" and e.line and e.src in by_id and by_id[e.src].kind != "file"
    ]
    assert {a[3] for a in anchors} >= set(calls), anchors  # reach
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)


def test_dart_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: one method name in two classes. It measured correct when first
    checked (WI-mapor); this pins it."""
    from hypergumbo_lang_common.dart import analyze_dart

    (tmp_path / "a.dart").write_text("""\
class A {
  void one() {}
  void run() {
    one();
  }
}
class B {
  void two() {}
  void run() {
    two();
  }
}
""")
    _assert_contained(analyze_dart(tmp_path), [4, 10])


def test_elm_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: none: a name is unique per module, so this pins containment in a
    ``let`` body. It measured correct when first checked (WI-mapor); this pins it."""
    from hypergumbo_lang_common.elm import analyze_elm

    (tmp_path / "Main.elm").write_text("""\
module Main exposing (..)

one x = x

two x = x

run x =
    let
        y = one x
    in
    two y
""")
    _assert_contained(analyze_elm(tmp_path), [9, 11])


def test_fsharp_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: ``let run`` in two nested modules. Its calls went to the last
    same-named declaration until the lookup keyed on position (WI-mapor)."""
    from hypergumbo_lang_common.fsharp import analyze_fsharp

    (tmp_path / "M.fs").write_text("""\
module M

module X =
    let one () = 1
    let run () =
        one ()

module Y =
    let two () = 2
    let run () =
        two ()
""")
    _assert_contained(analyze_fsharp(tmp_path), [6, 11])


def test_glsl_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: an overload pair. Its calls went to the last same-named
    declaration until the lookup keyed on position (WI-mapor)."""
    from hypergumbo_lang_common.glsl import analyze_glsl_files

    (tmp_path / "a.frag").write_text("""\
float one(float x) { return x; }
float two(float x) { return x; }
float run(float x) {
    return one(x);
}
float run(vec2 v) {
    return two(v.x);
}
""")
    _assert_contained(analyze_glsl_files(tmp_path), [4, 7])


def test_hlsl_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: an overload pair. Its calls went to the last same-named
    declaration until the lookup keyed on position (WI-mapor)."""
    from hypergumbo_lang_common.hlsl import analyze_hlsl

    (tmp_path / "a.hlsl").write_text("""\
float one(float x) { return x; }
float two(float x) { return x; }
float run(float x) {
    return one(x);
}
float run(float2 v) {
    return two(v.x);
}
""")
    _assert_contained(analyze_hlsl(tmp_path), [4, 7])


def test_julia_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: two methods of one function (multiple dispatch). Its calls went to
    the last same-named declaration until the lookup keyed on position (WI-mapor)."""
    from hypergumbo_lang_common.julia import analyze_julia

    (tmp_path / "a.jl").write_text("""\
one(x) = x
two(x) = x
function run(x::Int)
    one(x)
end
function run(x::String)
    two(x)
end
""")
    _assert_contained(analyze_julia(tmp_path), [4, 7])


def test_matlab_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: none: subfunction names are unique per file, so this pins
    containment. It measured correct when first checked (WI-mapor); this pins it."""
    from hypergumbo_lang_common.matlab import analyze_matlab

    (tmp_path / "main.m").write_text("""\
function main()
    one();
    two();
end
function one()
    helper();
end
function two()
    helper();
end
function helper()
end
""")
    _assert_contained(analyze_matlab(tmp_path), [2, 3, 6, 9])


def test_nix_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: ``run = x: ...`` in two attribute sets. Its calls went to the last
    same-named declaration until the lookup keyed on position (WI-mapor)."""
    from hypergumbo_lang_common.nix import analyze_nix_files

    (tmp_path / "a.nix").write_text("""\
let
  one = x: x;
  two = x: x;
  a = {
    run = x:
      one x;
  };
  b = {
    run = x:
      two x;
  };
in a
""")
    _assert_contained(analyze_nix_files(tmp_path), [6, 10])


def test_r_lang_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: a local helper of one name in two functions. Its calls went to the
    last same-named declaration until the lookup keyed on position (WI-mapor)."""
    from hypergumbo_lang_common.r_lang import analyze_r_files

    (tmp_path / "a.r").write_text("""\
one <- function() {}
two <- function() {}
outer1 <- function() {
  inner <- function() {
    one()
  }
  inner()
}
outer2 <- function() {
  inner <- function() {
    two()
  }
  inner()
}
""")
    _assert_contained(analyze_r_files(tmp_path), [5, 11])


def test_scheme_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: a top-level ``define`` repeated. It measured correct when first
    checked (WI-mapor); this pins it."""
    from hypergumbo_lang_common.scheme import analyze_scheme

    (tmp_path / "a.scm").write_text("""\
(define (one) 1)
(define (two) 2)
(define (run)
  (one))
(define (run)
  (two))
""")
    _assert_contained(analyze_scheme(tmp_path), [4, 6])


def test_starlark_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: none: a top-level name cannot be rebound, so this pins
    containment. It measured correct when first checked (WI-mapor); this pins it."""
    from hypergumbo_lang_common.starlark import analyze_starlark

    (tmp_path / "a.bzl").write_text("""\
def one():
    pass

def two():
    pass

def run():
    one()
    two()
""")
    _assert_contained(analyze_starlark(tmp_path), [8, 9])


def test_wgsl_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: none: a function name is unique per module, so this pins
    containment. It measured correct when first checked (WI-mapor); this pins it."""
    from hypergumbo_lang_common.wgsl import analyze_wgsl_files

    (tmp_path / "a.wgsl").write_text("""\
fn one() -> f32 { return 1.0; }
fn two() -> f32 { return 2.0; }
fn run() -> f32 {
    let a = one();
    return two();
}
""")
    _assert_contained(analyze_wgsl_files(tmp_path), [4, 5])


def test_nix_a_call_in_the_file_function_is_credited_to_it(tmp_path: Path) -> None:
    """A nix file is often itself a function (``{ pkgs }: let ... in ...``); its
    symbol is named after the file. A call inside it but outside any
    function-valued binding had no src, except when a binding inside shared
    the file's name, so the name lookup landed on the file function by
    accident (postgrest's style.nix)."""
    from hypergumbo_lang_common.nix import analyze_nix_files

    (tmp_path / "tool.nix").write_text("""\
{ runScript }:
let
  helper =
    runScript
      { name = "a"; };
in helper
""")
    result = analyze_nix_files(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    srcs = [by_id[e.src] for e in result.edges if e.edge_type == "calls" and e.line == 4]
    assert srcs, [(e.src, e.line) for e in result.edges]  # reach
    for src in srcs:
        assert (src.name, src.kind, src.span.start_line, src.span.end_line) == ("tool", "function", 1, 6)
