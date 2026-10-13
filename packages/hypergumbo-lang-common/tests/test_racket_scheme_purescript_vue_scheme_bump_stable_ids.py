# SPDX-License-Identifier: AGPL-3.0-or-later
"""Racket, Scheme, PureScript and Vue ids fixed in the WI-tusad stable_id
scheme bump (ADR-0035 §6).

Each tied two same-name symbols the per-file split pass then numbered by
emission order, so deleting the earlier one renumbered the later:

* Racket: ``run`` in ``(module+ a ...)`` and in ``(module+ b ...)`` (no scope
  chain, no arity: ``compute_stable_id`` finds no parameter list in ``define``);
* Scheme: ``(define (run x))`` redefined as ``(define (run x y))`` (no arity);
* PureScript: ``area`` in two instances (no instance scope), and a function's
  signature and equations, each emitted as its own same-name symbol;
* Vue: ``<p v-if="a">`` / ``<p v-if="b">`` under one element (no scope at all).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_file_stable_id
from hypergumbo_core.overload_arity import PARAMETERS_KEY
from hypergumbo_lang_common.purescript import analyze_purescript
from hypergumbo_lang_common.racket import RacketAnalyzer, analyze_racket
from hypergumbo_lang_common.scheme import SchemeAnalyzer, analyze_scheme
from hypergumbo_lang_common.vue import analyze_vue


def _symbols(analyze, root: Path, rel: str, text: str) -> list:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return [s for s in analyze(root).symbols if s.kind != "file"]


def _position(sym) -> tuple:
    return (sym.kind, sym.name, sym.span.start_line, sym.span.start_col)


def _assert_deletion_moves_nothing(analyze, tmp_path, rel, text, deleted) -> int:
    """Delete ``deleted`` (blanked, so lines keep their numbers) and check every
    surviving symbol, matched by its own position, keeps its non-null id."""
    edited = text.replace(deleted, "\n" * deleted.count("\n"))
    before: dict[tuple, list] = {}
    for s in _symbols(analyze, tmp_path / "with", rel, text):
        before.setdefault(_position(s), []).append(s.stable_id)
    after = _symbols(analyze, tmp_path / "without", rel, edited)
    for s in after:
        assert s.stable_id is not None, s.name
        assert s.stable_id in before[_position(s)], (s.name, s.span.start_line)
    return len(after)


def _top_level_node(analyzer, path: Path, needle: bytes):
    tree = analyzer._create_parser().parse(path.read_bytes())
    return next(n for n in tree.root_node.children if needle in (n.text or b""))


# -- racket --------------------------------------------------------------------

_RKT = (
    "#lang racket\n"
    "(module+ a\n  (define (run x) x))\n"
    "(module+ b\n  (define (run x y) x)\n  (define limit 1))\n"
    "(define (helper) 2)\n"
    "(define (opt x [y 1] #:scale [s 1] . more) x)\n"
    "(define total 0)\n"
    "(struct point (x y))\n"
)


def test_racket_same_name_in_two_submodules_get_distinct_ids(tmp_path: Path) -> None:
    runs = [s for s in _symbols(analyze_racket, tmp_path, "src/a.rkt", _RKT) if s.name == "run"]
    assert len(runs) == 2 and len({s.stable_id for s in runs}) == 2


def test_racket_deleting_an_earlier_submodule_moves_nothing(tmp_path: Path) -> None:
    survivors = _assert_deletion_moves_nothing(
        analyze_racket, tmp_path, "src/a.rkt", _RKT, "(module+ a\n  (define (run x) x))\n",
    )
    assert survivors == 6  # run, limit, helper, opt, total, point


def test_racket_lambda_list_parameters(tmp_path: Path) -> None:
    opt = next(s for s in _symbols(analyze_racket, tmp_path, "src/a.rkt", _RKT)
               if s.name == "opt")
    assert [(p["name"], p["default"], p.get("variadic", False))
            for p in opt.meta[PARAMETERS_KEY]] == [
        ("x", False, False), ("y", True, False), ("more", False, True)]


def test_racket_top_level_zero_parameter_ids_are_unchanged(tmp_path: Path) -> None:
    """A top-level definition has no scope, and a zero-parameter function no
    arity: their ids are the compute_stable_id values they had."""
    root = tmp_path / "r"
    syms = {s.name: s for s in _symbols(analyze_racket, root, "src/a.rkt", _RKT)}
    analyzer = RacketAnalyzer()
    anchor = make_file_stable_id("racket", "src/a.rkt")
    for name, kind, needle in (("helper", "function", b"(helper)"),
                               ("total", "variable", b"total"),
                               ("point", "class", b"point")):
        node = _top_level_node(analyzer, root / "src/a.rkt", needle)
        assert syms[name].stable_id == analyzer.compute_stable_id(
            node, kind=kind, name=name, file_stable_id=anchor,
        ), name


# -- scheme --------------------------------------------------------------------

_SCM = (
    "(define (run x) x)\n"
    "(define (run x y) (+ x y))\n"
    "(define (helper) 2)\n"
    "(define (sum . xs) xs)\n"
    "(define pi 3)\n"
    "(define-library (geo a)\n  (begin\n    (define (area r) r)))\n"
    "(define-library (geo b)\n  (begin\n    (define (area r) r)))\n"
)


def test_scheme_redefinitions_of_another_arity_get_distinct_ids(tmp_path: Path) -> None:
    runs = [s for s in _symbols(analyze_scheme, tmp_path, "a.scm", _SCM) if s.name == "run"]
    assert len(runs) == 2 and len({s.stable_id for s in runs}) == 2


def test_scheme_same_name_in_two_libraries_get_distinct_ids(tmp_path: Path) -> None:
    areas = [s for s in _symbols(analyze_scheme, tmp_path, "a.scm", _SCM) if s.name == "area"]
    assert len(areas) == 2 and len({s.stable_id for s in areas}) == 2


def test_scheme_deleting_an_earlier_definition_moves_nothing(tmp_path: Path) -> None:
    assert _assert_deletion_moves_nothing(
        analyze_scheme, tmp_path / "f", "a.scm", _SCM, "(define (run x) x)\n",
    ) == 6
    assert _assert_deletion_moves_nothing(
        analyze_scheme, tmp_path / "l", "a.scm", _SCM,
        "(define-library (geo a)\n  (begin\n    (define (area r) r)))\n",
    ) == 6


def test_scheme_rest_parameter_is_variadic_not_a_dot(tmp_path: Path) -> None:
    total = next(s for s in _symbols(analyze_scheme, tmp_path, "a.scm", _SCM)
                 if s.name == "sum")
    assert [(p["name"], p.get("variadic", False)) for p in total.meta[PARAMETERS_KEY]] == [
        ("xs", True)]


def test_scheme_top_level_zero_parameter_ids_are_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "r"
    syms = {s.name: s for s in _symbols(analyze_scheme, root, "a.scm", _SCM)}
    analyzer = SchemeAnalyzer()
    anchor = make_file_stable_id("scheme", "a.scm")
    for name, kind, needle in (("helper", "function", b"(helper)"), ("pi", "variable", b"pi")):
        node = _top_level_node(analyzer, root / "a.scm", needle)
        assert syms[name].stable_id == analyzer.compute_stable_id(
            node, kind=kind, name=name, file_stable_id=anchor,
        ), name


# -- purescript ----------------------------------------------------------------

_PURS = (
    "module Demo where\n\n"
    "class Shape a where\n  area :: a -> Int\n\n"
    "instance shapeInt :: Shape Int where\n  area x = x\n\n"
    "instance Shape Boolean where\n  area _ = 0\n\n"
    "fact :: Int -> Int\nfact 0 = 1\nfact n = n * fact (n - 1)\n\n"
    "helper :: Int\nhelper = 2\n"
)


def test_purescript_instance_methods_get_distinct_ids(tmp_path: Path) -> None:
    areas = [s for s in _symbols(analyze_purescript, tmp_path, "src/Demo.purs", _PURS)
             if s.name == "Demo.area"]
    # the class member's signature and the two instances' methods
    assert len(areas) == 3 and len({s.stable_id for s in areas}) == 3


def test_purescript_signature_and_equations_are_one_symbol(tmp_path: Path) -> None:
    syms = _symbols(analyze_purescript, tmp_path, "src/Demo.purs", _PURS)
    facts = [s for s in syms if s.name == "Demo.fact"]
    assert len(facts) == 1
    fact = facts[0]
    assert (fact.span.start_line, fact.span.end_line) == (13, 14)
    assert fact.signature == "Int -> Int"
    assert fact.cyclomatic_complexity == 2
    helpers = [s for s in syms if s.name == "Demo.helper"]
    assert [h.span.start_line for h in helpers] == [17]


def test_purescript_calls_in_a_later_equation_come_from_the_function(tmp_path: Path) -> None:
    target = tmp_path / "src" / "Demo.purs"
    target.parent.mkdir(parents=True)
    target.write_text(_PURS)
    result = analyze_purescript(tmp_path)
    fact = next(s for s in result.symbols if s.name == "Demo.fact")
    calls = [e for e in result.edges if e.edge_type == "calls" and e.line == 14]
    assert calls and {e.src for e in calls} == {fact.id}


def test_purescript_deleting_an_earlier_sibling_moves_nothing(tmp_path: Path) -> None:
    assert _assert_deletion_moves_nothing(
        analyze_purescript, tmp_path / "i", "src/Demo.purs", _PURS,
        "instance shapeInt :: Shape Int where\n  area x = x\n",
    ) == 6  # module, class, its area, the other area, fact, helper
    assert _assert_deletion_moves_nothing(
        analyze_purescript, tmp_path / "s", "src/Demo.purs", _PURS, "fact :: Int -> Int\n",
    ) == 8


def test_purescript_local_function_calls_keep_their_node_source(tmp_path: Path) -> None:
    """A ``where`` function has no symbol: its calls keep the id minted from
    its own node, as before."""
    target = tmp_path / "src" / "W.purs"
    target.parent.mkdir(parents=True)
    target.write_text("module W where\n\nf x = g x\n  where\n    g y = h y\n")
    result = analyze_purescript(tmp_path)
    assert [s.name for s in result.symbols if s.kind == "function"] == ["W.f"]
    srcs = {e.src for e in result.edges if e.edge_type == "calls"}
    assert any(":W.g:" in src for src in srcs)


# -- vue -----------------------------------------------------------------------

_VUE = (
    "<template>\n  <div>\n"
    "    <p v-if=\"first\">a</p>\n"
    "    <p v-if=\"second\">b</p>\n"
    "    <p v-if=third>c</p>\n"
    "    <ul><li v-for=\"i in items\" @click=\"pick(i)\" :key=\"i\">x</li></ul>\n"
    "    <slot name=\"footer\" />\n"
    "  </div>\n</template>\n"
    "<style>\np { color: red; }\n</style>\n"
    "<style scoped>\np { color: blue; }\n</style>\n"
)


def test_vue_same_directive_on_sibling_elements_gets_distinct_ids(tmp_path: Path) -> None:
    ifs = [s for s in _symbols(analyze_vue, tmp_path, "src/A.vue", _VUE) if s.name == "v-if"]
    assert len(ifs) == 3 and len({s.stable_id for s in ifs}) == 3


def test_vue_two_style_blocks_get_distinct_ids(tmp_path: Path) -> None:
    styles = [s for s in _symbols(analyze_vue, tmp_path, "src/A.vue", _VUE)
              if s.kind == "style_block"]
    assert len(styles) == 2 and len({s.stable_id for s in styles}) == 2


def test_vue_deleting_an_earlier_directive_moves_nothing(tmp_path: Path) -> None:
    survivors = _assert_deletion_moves_nothing(
        analyze_vue, tmp_path, "src/A.vue", _VUE, "    <p v-if=\"first\">a</p>\n",
    )
    assert survivors == 8  # 2 v-if, v-for, v-on, v-bind, slot, 2 styles
