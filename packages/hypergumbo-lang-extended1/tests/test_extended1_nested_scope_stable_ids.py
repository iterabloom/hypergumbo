# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nested same-name scopes: ids fixed in the WI-tusad stable_id scheme bump
(INV-fabih's sweep; ADR-0035 §1, §6), plus this package's F1 cousins and the
Ada result-type overloads.

A symbol hashed only its nearest owner or no scope at all (D members, Zig and
Cap'n Proto nested types, GDScript inner classes, Solidity struct members, Tcl
nested namespaces, local functions in Ada, Nim, Odin, Luau, Wolfram and
Jsonnet objects), or a Lean ``structure`` / Agda nested ``module`` kept a bare
name: twins in two same-name outer scopes tied, the per-file split pass
numbered them by emission order, and deleting the first outer scope moved the
survivor's id. Each case runs the analyzer directly, with repo-relative paths
and the backstop's table formula applied as the pipeline does; survivors are
matched by their own position.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import populate_kind_stable_ids
from hypergumbo_lang_extended1.ada import analyze_ada
from hypergumbo_lang_extended1.agda import analyze_agda
from hypergumbo_lang_extended1.capnp import analyze_capnp
from hypergumbo_lang_extended1.d_lang import analyze_d
from hypergumbo_lang_extended1.gdscript import analyze_gdscript
from hypergumbo_lang_extended1.jsonnet import analyze_jsonnet
from hypergumbo_lang_extended1.lean import analyze_lean
from hypergumbo_lang_extended1.luau import analyze_luau
from hypergumbo_lang_extended1.nim import analyze_nim
from hypergumbo_lang_extended1.odin import analyze_odin
from hypergumbo_lang_extended1.solidity import analyze_solidity
from hypergumbo_lang_extended1.tcl import analyze_tcl
from hypergumbo_lang_extended1.twig import analyze_twig
from hypergumbo_lang_extended1.v_lang import analyze_v
from hypergumbo_lang_extended1.wolfram import analyze_wolfram
from hypergumbo_lang_extended1.zig import analyze_zig


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
    "ada": (analyze_ada, "src/nested.adb",
            "   function F return Integer is begin return 1; end F;\n"
            "   procedure A is\n      procedure Inner is begin null; end Inner;\n   begin Inner; end A;\n"
            "   package P is\n      type T is new Integer;\n   end P;\n",
            "   function F return Float is begin return 1.0; end F;\n"
            "   procedure B is\n      procedure Inner is begin null; end Inner;\n   begin Inner; end B;\n"
            "   package Q is\n      type T is new Integer;\n   end Q;\n"
            "   function G (X : Integer) return access Integer is begin return null; end G;\n"
            "end Nested;\n",
            "package body Nested is\n"),
    "agda": (analyze_agda, "src/Nested.agda",
             "module A where\n  module C where\n    v : Nat\n    v = zero\n"
             "  record R : Set where\n    field\n      x : Nat\n\n",
             "module B where\n  module C where\n    v : Nat\n    v = zero\n"
             "  record R : Set where\n    field\n      x : Nat\n",
             "module Nested where\n\ndata Nat : Set where\n  zero : Nat\n  suc : Nat -> Nat\n\n"),
    "capnp": (analyze_capnp, "src/nested.capnp",
              "struct A {\n  struct Inner {\n    v @0 :Int32;\n  }\n  enum E {\n    x @0;\n  }\n}\n",
              "struct B {\n  struct Inner {\n    v @0 :Int32;\n  }\n  enum E {\n    x @0;\n  }\n}\n",
              "@0xdbb9ad1f14bf0b38;\n"),
    "d": (analyze_d, "src/outer.d",
          "class A {\n    class Inner {\n        int go() { return 1; }\n        int v;\n    }\n"
          "    enum E { x }\n}\nint fa() {\n    int local() { return 1; }\n    return local();\n}\n",
          "class B {\n    class Inner {\n        int go() { return 2; }\n        int v;\n    }\n"
          "    enum E { x }\n}\nint fb() {\n    int local() { return 2; }\n    return local();\n}\n",
          "module outer;\n"),
    "gdscript": (analyze_gdscript, "src/nested.gd",
                 "class A:\n\tclass Inner:\n\t\tvar v = 1\n\t\tfunc go():\n\t\t\treturn 1\n\n",
                 "class B:\n\tclass Inner:\n\t\tvar v = 2\n\t\tfunc go():\n\t\t\treturn 2\n",
                 "extends Node\n"),
    "jsonnet": (analyze_jsonnet, "src/nested.jsonnet",
                "  a: { inner(x):: x, local k = 1, kk: k },\n",
                "  b: { inner(x):: x, local k = 1, kk: k },\n}\n", "{\n"),
    "lean": (analyze_lean, "src/Nested.lean",
             "namespace A\nstructure S where\n  x : Nat\nend A\n",
             "namespace B\nstructure S where\n  x : Nat\nend B\n"),
    "luau": (analyze_luau, "src/nested.luau",
             "function a()\n  local function inner(): number return 1 end\n  type T = number\n"
             "  local Mod = {}\n  return inner()\nend\n",
             "function b()\n  local function inner(): number return 2 end\n  type T = number\n"
             "  local Mod = {}\n  return inner()\nend\n"),
    "nim": (analyze_nim, "src/nested.nim",
            "proc a() =\n  proc inner() = discard\n  inner()\n",
            "proc b() =\n  proc inner() = discard\n  inner()\n"),
    "odin": (analyze_odin, "src/nested.odin",
             "a :: proc() {\n\tinner :: proc() {}\n\tinner()\n}\n",
             "b :: proc() {\n\tinner :: proc() {}\n\tinner()\n}\n", "package nested\n\n"),
    "solidity": (analyze_solidity, "src/Nested.sol",
                 "contract A {\n    struct Inner { uint v; }\n}\n",
                 "contract B {\n    struct Inner { uint v; }\n}\nstruct Top { uint v; }\n",
                 "pragma solidity ^0.8.0;\n"),
    "tcl": (analyze_tcl, "src/nested.tcl",
            "namespace eval a {\n  namespace eval inner {\n    proc go {} { return 1 }\n  }\n}\n",
            "namespace eval b {\n  namespace eval inner {\n    proc go {} { return 2 }\n  }\n}\n"),
    "wolfram": (analyze_wolfram, "src/nested.wl",
                "a[x_] := Module[{inner}, inner[y_] := y; inner[x]]\n",
                "b[x_] := Module[{inner}, inner[y_] := y; inner[x]]\n"),
    "zig": (analyze_zig, "src/nested.zig",
            "const A = struct {\n    const Inner = struct {\n        v: i32,\n        fn go() i32 { return 1; }\n"
            "    };\n    const E = enum { x };\n};\n",
            "const B = struct {\n    const Inner = struct {\n        v: i32,\n        fn go() i32 { return 2; }\n"
            "    };\n    const E = enum { x };\n};\n"),
}


@pytest.mark.parametrize("lang", sorted(CASES))
def test_deleting_an_earlier_same_name_scope_moves_no_id(tmp_path: Path, lang: str) -> None:
    analyze, rel, earlier, rest, *head = CASES[lang]
    assert _check(analyze, tmp_path, rel, earlier, rest, *head)


def test_ada_result_type_overloads_get_distinct_ids(tmp_path: Path) -> None:
    """``function F return Integer`` / ``function F return Float`` take no
    parameter: only the result type, part of Ada's overload profile, tells
    them apart. A procedure has none and keeps its value."""
    syms = _symbols(analyze_ada, tmp_path, "src/m.adb",
                    "package body M is\n"
                    "   function F return Integer is begin return 1; end F;\n"
                    "   function F return Float is begin return 1.0; end F;\n"
                    "   function F (X : Integer) return Integer is begin return X; end F;\n"
                    "   function F (X : Integer) return Float is begin return 1.0; end F;\n"
                    "end M;\n")
    fs = [s.stable_id for s in syms if s.name == "F"]
    assert len(fs) == 4 and len(set(fs)) == 4


def test_lean_and_agda_name_table_kinds_by_their_scope(tmp_path: Path) -> None:
    lean = _symbols(analyze_lean, tmp_path / "l", "M.lean",
                    "structure Top where\n  x : Nat\nnamespace A\nstructure S where\n  x : Nat\nend A\n")
    assert sorted(s.name for s in lean if s.kind == "struct") == ["A.S", "Top"]
    agda = _symbols(analyze_agda, tmp_path / "a", "M.agda",
                    "module M where\n\ndata Nat : Set where\n  zero : Nat\n  suc : Nat -> Nat\n\n"
                    "module A where\n  module C where\n    v : Nat\n    v = zero\n")
    assert sorted(s.name for s in agda if s.kind == "module") == ["A", "A.C", "M"]


# -- V: one-letter type names, receiver in the signature ---------------------


@pytest.mark.parametrize(("text", "expected"), [
    ("module m\n\nstruct A {}\n", [("class", "A", {"is_public": False, "field_count": 0})]),
    ("module m\n\npub struct A {\n\tx int\n\ty string\n}\n",
     [("class", "A", {"is_public": True, "field_count": 2})]),
    ("module m\n\nenum E {\n\ta\n\tb\n}\n", [("enum", "E", {"is_public": False, "variant_count": 2})]),
    ("module m\n\ninterface I {\n}\n", [("interface", "I", {"is_public": False})]),
])
def test_v_one_letter_type_names_are_recovered(tmp_path: Path, text, expected) -> None:
    """tree-sitter-v lexes a lone capital as a generic placeholder, so the
    declaration was an ERROR node and emitted nothing."""
    syms = _symbols(analyze_v, tmp_path, "src/m.v", text)
    assert [(s.kind, s.name, s.meta) for s in syms] == expected
    assert all(s.stable_id for s in syms)


@pytest.mark.parametrize("text", [
    "module m\n\nstruct A\nfn f() {}\n",       # a placeholder not followed by a body
    "module m\n\nfn f() { x := ) }\n",          # an ERROR with no type keyword
    "module m\n\nstruct {}\n",                  # a keyword with no placeholder at all
])
def test_v_errors_that_are_not_one_letter_types_emit_nothing_new(tmp_path: Path, text) -> None:
    syms = _symbols(analyze_v, tmp_path, "src/m.v", text)
    assert all(s.kind == "function" for s in syms)


def test_v_method_signature_shows_the_receiver_type_not_its_name(tmp_path: Path) -> None:
    syms = _symbols(analyze_v, tmp_path / "a", "src/m.v",
                    "module m\n\nstruct Box {}\n\nfn (b Box) run(x int) int {\n\treturn x\n}\n"
                    "fn plain(y int) int {\n\treturn y\n}\n")
    sigs = {s.name: s.signature for s in syms if s.kind == "function"}
    assert sigs == {"run": "fn (Box) (x) int", "plain": "fn(y) int"}
    renamed = _symbols(analyze_v, tmp_path / "b", "src/m.v",
                       "module m\n\nstruct Box {}\n\nfn (other Box) run(x int) int {\n\treturn x\n}\n"
                       "fn plain(y int) int {\n\treturn y\n}\n")
    assert [s.signature for s in renamed if s.name == "run"] == ["fn (Box) (x) int"]
    assert ({s.name: s.stable_id for s in syms if s.kind == "function"}
            == {s.name: s.stable_id for s in renamed if s.kind == "function"})


# -- Twig: else is no conditional of its own ----------------------------------


def test_twig_else_emits_no_symbol_and_elseif_keeps_its_keyword(tmp_path: Path) -> None:
    """``{% else %}`` was a ``conditional`` named ``"if "``, and ``{% elseif b
    %}`` was named ``if b`` like an ``if``."""
    syms = _symbols(analyze_twig, tmp_path, "templates/p.twig",
                    "{% if a %}x{% elseif b %}y{% else %}z{% endif %}\n")
    assert sorted(s.name for s in syms if s.kind == "conditional") == ["elseif b", "if a"]


def test_wolfram_scope_skips_binary_expressions_that_define_nothing(tmp_path: Path) -> None:
    """A rule ``Module[...] -> 1`` encloses ``inner`` but defines nothing: only
    the ``a[x_] := ...`` around it enters the chain."""
    syms = _symbols(analyze_wolfram, tmp_path / "rule", "src/m.wl",
                    "a[x_] := Module[{}, inner[y_] := y] -> 1\n")
    plain = _symbols(analyze_wolfram, tmp_path / "bare", "src/m.wl",
                     "a[x_] := Module[{}, inner[y_] := y]\n")
    inner = [s for s in syms if s.name == "inner"]
    assert len(inner) == 1
    assert inner[0].stable_id == next(s.stable_id for s in plain if s.name == "inner")


def test_tcl_names_carry_every_enclosing_namespace_eval(tmp_path: Path) -> None:
    """A proc's id name slot and ``meta.namespace`` carry every enclosing
    ``namespace eval`` (``a::inner::go``, Tcl's own name for it), not the
    innermost only (``inner::go``); a nested namespace's id slot is
    ``a::inner``. The stable_ids are the values they had: they already hashed
    the full path."""
    syms = {s.name: s for s in _symbols(
        analyze_tcl, tmp_path, "src/n.tcl",
        "namespace eval a {\n  namespace eval inner {\n    proc go {} { return 1 }\n"
        "  }\n}\nproc top {} { go }\n")}
    assert syms["go"].id == "tcl:src/n.tcl:3-3:a..inner..go:proc"  # ids spell : as .
    assert syms["go"].meta == {"namespace": "a::inner"}
    assert syms["inner"].id == "tcl:src/n.tcl:2-4:a..inner:namespace"
    assert syms["a"].id == "tcl:src/n.tcl:1-5:a:namespace"
    assert syms["top"].meta is None
    assert {n: s.stable_id for n, s in syms.items()} == {
        "a": "sha256:5f8ecf75437ae496", "inner": "sha256:67a662494d8be518",
        "go": "sha256:5a06c54a2c6ba3bd", "top": "sha256:94b7459b9a4080ad",
    }


def test_tcl_call_edges_leave_the_renamed_proc(tmp_path: Path) -> None:
    """The caller of a call edge is the proc's node id, rebuilt from the call
    site: it must carry the same full namespace path."""
    root = tmp_path
    (root / "n.tcl").write_text(
        "namespace eval a {\n  namespace eval inner {\n    proc go {} { helper }\n"
        "  }\n}\nproc helper {} { return 1 }\n")
    result = analyze_tcl(root)
    ids = {s.id for s in result.symbols}
    calls = [e for e in result.edges if e.edge_type == "calls"]
    assert calls and all(e.src in ids for e in calls)

