# SPDX-License-Identifier: AGPL-3.0-or-later
"""Emission, scope and producer stable_ids for Agda, Lean, TLA+ and Wolfram (WI-tusad).

Three of these analyzers DROPPED the second same-name declaration of a file
(INV-kagib): a per-file set of seen names kept ``run`` in Agda ``module A`` /
Lean ``namespace A`` and lost the one in ``B``, kept one record's ``field fst``
and lost the other's, and kept ``run[x_Integer]`` while losing the overload
``run[x_String]``. TLA+ emitted both LET-local ``f``s but left them null. All
four rode the ADR-0035 backstop floor. Each now emits every declaration with
its scope chain and mints the producer formula through
``overload_arity.declared_arity_stable_id``.

What is pinned, per language, on the analyzer's own output:

* every same-name declaration is emitted, each with its own non-null id
  (checked symbol by symbol, never through a dict keyed on name);
* the exact value (the shared helper over the inputs the module docstring
  names), so a producer that drifted onto other inputs fails here;
* the delete-an-earlier-sibling property: deleting an earlier same-name
  declaration leaves every survivor's id unchanged (lines are kept with blank
  lines so each survivor matches itself by line);
* the edge sources, which are now found by scope or position rather than by a
  repo-wide bare-name lookup.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, declared_arity, declared_arity_stable_id, parameter_entry,
)
from hypergumbo_lang_extended1.agda import analyze_agda
from hypergumbo_lang_extended1.lean import analyze_lean
from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus
from hypergumbo_lang_extended1.wolfram import (
    _definition_lhs, _pattern_parameter, _split_top_level, analyze_wolfram,
)

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
    meta: Optional[dict[str, Any]] = None,
) -> str:
    return declared_arity_stable_id(
        kind=kind, lang=lang, path=path, name=name,
        qualified_name=qualified_name, arity=declared_arity(meta),
    )


def _edge_names(result: AnalysisResult, edge_type: str) -> set[tuple[str, str]]:
    by_id = {s.id: s.name for s in result.symbols}
    return {
        (by_id.get(e.src, e.src), by_id.get(e.dst, e.dst))
        for e in result.edges if e.edge_type == edge_type
    }


# -- Agda -----------------------------------------------------------------

_AGDA = (
    "module Demo where\n"
    "\n"
    "data Nat : Set where\n"
    "  zero : Nat\n"
    "  suc : Nat -> Nat\n"
    "\n"
    "module A where\n"
    "  run : Nat -> Nat\n"
    "  run x = go x\n"
    "    where\n"
    "      go : Nat -> Nat\n"
    "      go y = y\n"
    "\n"
    "module B where\n"
    "  run : Nat -> Nat\n"
    "  run x = suc x\n"
    "\n"
    "module _ where\n"
    "  anon : Nat\n"
    "  anon = zero\n"
    "\n"
    "record Pair : Set where\n"
    "  field\n"
    "    fst : Nat\n"
    "\n"
    "record Box : Set where\n"
    "  field\n"
    "    fst : Nat\n"
    "\n"
    "data D : Set\n"
    "data D where\n"
    "  mk : D\n"
    "\n"
    "helper : Nat\n"
    "helper = A.run zero\n"
    "unsigned = zero\n"
)


def test_agda_emits_every_same_name_declaration_with_its_chain(
    tmp_path: Path,
) -> None:
    """INV-kagib: both ``run``s and both ``fst`` fields are emitted."""
    syms = _by_line(
        _symbols(analyze_agda, tmp_path, "Demo.agda", _AGDA),
        {"function", "field", "data", "record"},
    )
    assert (syms[8].name, syms[15].name) == ("A.run", "B.run")
    assert (syms[24].name, syms[28].name) == ("Pair.fst", "Box.fst")
    for a, b in ((8, 15), (24, 28)):
        assert None not in (syms[a].stable_id, syms[b].stable_id)
        assert syms[a].stable_id != syms[b].stable_id
    assert syms[8].stable_id == _expected("function", "agda", "Demo.agda", "run", "A.run")
    assert syms[28].stable_id == _expected("field", "agda", "Demo.agda", "fst", "Box.fst")
    # A constructor is named bare (in scope in its module) with its data type
    # in the chain; a where-bound function holds its owner in the chain.
    assert syms[4].name == "zero"
    assert syms[4].stable_id == _expected("function", "agda", "Demo.agda", "zero", "Nat.zero")
    assert syms[11].name == "go"
    assert syms[11].stable_id == _expected("function", "agda", "Demo.agda", "go", "A.run/go")
    # ``module _`` adds nothing; a forward ``data D : Set`` is not emitted, so
    # ``data D where`` is the one data type.
    assert syms[19].name == "anon"
    assert [s.name for s in syms.values() if s.kind == "data"] == ["Nat", "D"]
    assert syms[22].stable_id == _expected("record", "agda", "Demo.agda", "Pair", "Pair")


def test_agda_reference_sources_are_found_by_chain(tmp_path: Path) -> None:
    refs = _edge_names(_result(analyze_agda, tmp_path, "Demo.agda", _AGDA), "references")
    assert ("A.run", "go") in refs
    assert ("B.run", "suc") in refs
    assert ("helper", "A.run") in refs
    # ``unsigned`` has a clause but no signature, so no symbol to anchor on.
    assert not any(src == "unsigned" for src, _ in refs)


def test_agda_deleting_an_earlier_sibling_moves_no_id(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_agda, tmp_path, "Demo.agda", _AGDA,
        "module A where\n  run : Nat -> Nat\n  run x = go x\n    where\n"
        "      go : Nat -> Nat\n      go y = y\n",
        {"function", "field", "data", "record"}, survivors=12,
    )


# -- Lean -----------------------------------------------------------------

_LEAN = (
    "namespace A\n"
    "def run (x : Nat) : Nat := x\n"
    "structure P where\n"
    "  x : Nat\n"
    "end A\n"
    "namespace B\n"
    "section S\n"
    "def run (x y : Nat) {z : Nat} [Add Nat] (w : Nat := 1) : Nat := x + y\n"
    "end S\n"
    "end Missing\n"
    "end\n"
    "theorem foo : True := trivial\n"
    "instance : Add Nat := sorry\n"
    "instance : Add Int := sorry\n"
    "instance named : Mul Nat := sorry\n"
    "inductive T where\n"
    "  | a\n"
    "end B\n"
    "def helper : Nat := A.run 2\n"
    "def bare x y := x\n"
    "def _root_.top := 1\n"
)


def test_lean_emits_both_namespaced_definitions(tmp_path: Path) -> None:
    """INV-kagib: ``run`` in namespace A and in namespace B are two symbols."""
    syms = _by_line(
        _symbols(analyze_lean, tmp_path, "Demo.lean", _LEAN),
        {"function", "theorem", "instance", "inductive", "struct"},
    )
    assert (syms[2].name, syms[8].name) == ("A.run", "B.run")
    assert syms[2].stable_id != syms[8].stable_id
    assert syms[8].meta == {PARAMETERS_KEY: [
        parameter_entry("x"), parameter_entry("y"),
        parameter_entry("w", default=True),
    ]}
    assert syms[8].stable_id == _expected(
        "function", "lean", "Demo.lean", "run", "B.run", syms[8].meta,
    )
    # ``end Missing`` and a plain ``end`` close nothing that adds to names.
    assert syms[12].name == "B.foo"
    assert syms[16].name == "B.T"
    assert syms[16].stable_id == _expected("inductive", "lean", "Demo.lean", "T", "B.T")
    # Unnamed instances keep the typeclass name; their chain carries the type.
    assert syms[13].name == syms[14].name == "Add"
    assert syms[13].stable_id == _expected(
        "instance", "lean", "Demo.lean", "Add", "B.instance Add Nat",
    )
    assert syms[13].stable_id != syms[14].stable_id
    assert syms[15].name == "B.named"
    # A structure is named with its namespace too (INV-fabih); its id is the
    # table formula over that name, computed downstream.
    assert syms[3].name == "A.P" and syms[3].stable_id is None
    assert syms[20].meta == {PARAMETERS_KEY: [parameter_entry("x"), parameter_entry("y")]}
    assert syms[21].name == "top"


def test_lean_reference_source_is_the_declaration_itself(tmp_path: Path) -> None:
    refs = _edge_names(_result(analyze_lean, tmp_path, "Demo.lean", _LEAN), "references")
    assert ("helper", "A.run") in refs


def test_lean_deleting_an_earlier_sibling_moves_no_id(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_lean, tmp_path, "Demo.lean", _LEAN,
        "def run (x : Nat) : Nat := x\n",
        {"function", "theorem", "instance", "inductive"}, survivors=9,
    )


# -- TLA+ -----------------------------------------------------------------

_TLA = (
    "---- MODULE Demo ----\n"
    "CONSTANT N\n"
    "A == LET\n"
    "       f == 1\n"
    "     IN f\n"
    "B == LET\n"
    "       f == 2\n"
    "       g == LET\n"
    "              h == f\n"
    "            IN h\n"
    "     IN g\n"
    "Op(x, G(_, _)) == G(x, x) + A\n"
    "THEOREM T == TRUE\n"
    "ASSUME As == N > 0\n"
    "====\n"
)


def test_tlaplus_let_locals_get_scoped_ids(tmp_path: Path) -> None:
    syms = _symbols(analyze_tlaplus, tmp_path, "Demo.tla", _TLA)
    fs = [s for s in syms if s.kind == "operator" and s.name == "f"]
    assert [f.span.start_line for f in fs] == [4, 7]
    assert fs[0].stable_id == _expected(
        "operator", "tlaplus", "Demo.tla", "f", "A/f", {PARAMETERS_KEY: []},
    )
    assert fs[1].stable_id == _expected(
        "operator", "tlaplus", "Demo.tla", "f", "B/f", {PARAMETERS_KEY: []},
    )
    h = next(s for s in syms if s.name == "h")
    assert h.stable_id == _expected(
        "operator", "tlaplus", "Demo.tla", "h", "B/g/h", {PARAMETERS_KEY: []},
    )
    op = next(s for s in syms if s.name == "Op")
    assert op.meta == {PARAMETERS_KEY: [parameter_entry("x"), parameter_entry("G")]}
    for kind, name in (("constant", "N"), ("theorem", "T"), ("assumption", "As")):
        sym = next(s for s in syms if s.kind == kind)
        assert sym.stable_id == _expected(kind, "tlaplus", "Demo.tla", name, name)


def test_tlaplus_references_resolve_let_locals_by_scope(tmp_path: Path) -> None:
    result = _result(analyze_tlaplus, tmp_path, "Demo.tla", _TLA)
    by_id = {s.id: s for s in result.symbols}
    refs = {
        (by_id[e.src].name, by_id[e.src].span.start_line,
         by_id[e.dst].name, by_id[e.dst].span.start_line)
        for e in result.edges if e.edge_type == "references"
    }
    assert ("A", 3, "f", 4) in refs       # A's own f, not B's
    assert ("A", 3, "f", 7) not in refs
    assert ("B", 6, "f", 7) in refs
    assert ("g", 8, "f", 7) in refs       # B/g reaches B/f through the prefix
    assert ("Op", 12, "A", 3) in refs     # no LET-local A: the repo lookup


def test_tlaplus_deleting_an_earlier_sibling_moves_no_id(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_tlaplus, tmp_path, "Demo.tla", _TLA,
        "A == LET\n       f == 1\n     IN f\n",
        {"operator", "constant", "theorem", "assumption"}, survivors=8,
    )


# -- Wolfram --------------------------------------------------------------

_WOLFRAM = (
    "run[x_Integer] := x\n"
    "run[x_String] := StringLength[x]\n"
    "helper[] := 2\n"
    "f[x_, y_:1, z__, w___, d_., opts:OptionsPattern[]] := x\n"
    "g[x_?NumericQ, {a_, b_}, s:{__Integer}] := x\n"
    "fib[0] = 0\n"
    "fib[n_] := fib[n - 1]\n"
    "v = 3\n"
    "v = 4\n"
    "a[[1]] = 5\n"
    "k := 7\n"
    "c[x_][y_] := x + y\n"
    "q[x_] /; x > 0 := x\n"
)


def test_wolfram_emits_every_definition_with_typed_ids(tmp_path: Path) -> None:
    """INV-kagib: ``run[x_Integer]`` and ``run[x_String]`` are two symbols."""
    syms = _by_line(
        _symbols(analyze_wolfram, tmp_path, "demo.wl", _WOLFRAM),
        {"function", "variable"},
    )
    assert (syms[1].name, syms[2].name) == ("run", "run")
    assert syms[1].meta == {PARAMETERS_KEY: [parameter_entry("x", "Integer")]}
    assert syms[1].stable_id == _expected(
        "function", "wolfram", "demo.wl", "run", "run", syms[1].meta,
    )
    assert syms[1].stable_id != syms[2].stable_id
    assert syms[1].signature == "[x_Integer]"
    # ``helper[]`` is not a call node in the grammar; it is still a definition.
    assert syms[3].name == "helper" and syms[3].meta == {PARAMETERS_KEY: []}
    assert syms[6].name == syms[7].name == "fib"
    assert syms[6].stable_id != syms[7].stable_id
    # One variable for two assignments; a Part assignment and a conditioned
    # left-hand side define nothing.
    assert syms[8].kind == "variable" and 9 not in syms
    assert 10 not in syms and 13 not in syms
    assert syms[11].name == "k" and syms[11].meta == {PARAMETERS_KEY: []}
    assert syms[12].name == "c" and syms[12].meta == {PARAMETERS_KEY: [parameter_entry("x")]}


def test_wolfram_pattern_parameters() -> None:
    assert _pattern_parameter("y_:1") == parameter_entry("y", default=True)
    assert _pattern_parameter("d_.") == parameter_entry("d", default=True)
    assert _pattern_parameter("z__") == parameter_entry("z", variadic=True)
    assert _pattern_parameter("___") == parameter_entry("___", variadic=True)
    assert _pattern_parameter("x_?NumericQ") == parameter_entry("x", "?NumericQ")
    assert _pattern_parameter("opts:OptionsPattern[]") == parameter_entry(
        "opts", "OptionsPattern[]", variadic=True,
    )
    assert _pattern_parameter("s:{__Integer}") == parameter_entry("s", "{__Integer}")
    assert _pattern_parameter("{a_,  b_}") == parameter_entry("{a_, b_}", "{a_, b_}")


def test_wolfram_left_hand_side_reading() -> None:
    assert _definition_lhs('f["a, b", x_]') == ("f", ['"a, b"', "x_"])
    assert _definition_lhs("f[g[x_], {y_, z_}]") == ("f", ["g[x_]", "{y_, z_}"])
    assert _definition_lhs("f[]") == ("f", [])
    assert _definition_lhs("c[x_][y_]") == ("c", ["x_"])
    for not_a_definition in ("x", "a[[1]]", "f[x_", "c[x_][y_", "q[x_] /; x > 0"):
        assert _definition_lhs(not_a_definition) is None
    assert _split_top_level('"open') is None


def test_wolfram_deleting_an_earlier_overload_moves_no_id(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_wolfram, tmp_path, "demo.wl", _WOLFRAM, "run[x_Integer] := x\n",
        {"function"}, survivors=8,
    )
