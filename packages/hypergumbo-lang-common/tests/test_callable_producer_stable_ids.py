# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer stable_ids for Erlang, Clojure, Common Lisp and R callables (WI-tusad).

These four analyzers used to leave every function, macro and method at
``stable_id=None`` for the backstop floor (``make_declaration_stable_id`` over
kind, language, path and name), which nulls every same-name group in a file:
a Clojure multimethod's ``defmethod``s, a Common Lisp generic's methods, an R
name reassigned to a function of another arity. Each now mints the ADR-0035
producer formula at construction through
``overload_arity.declared_arity_stable_id``.

What is pinned, per language, on the analyzer's own output:

* the exact value (the shared helper over the inputs the module docstring
  names), so a producer that drifted onto other inputs fails here;
* that a same-name group holds distinct, non-null ids;
* the delete-an-earlier-sibling property: deleting an earlier same-name
  sibling leaves every survivor's id unchanged (lines are kept with blank
  lines so each survivor matches itself by line).

The helpers that read parameters out of each grammar (Clojure arity bodies,
Common Lisp lambda lists, R parameter lists, Erlang ``-define`` arguments) are
pinned on the ``meta["parameters"]`` they write, since ``narrow_by_arity``
reads that list too.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, declared_arity, declared_arity_stable_id, parameter_entry,
)
from hypergumbo_lang_common.clojure import analyze_clojure
from hypergumbo_lang_common.commonlisp import analyze_commonlisp
from hypergumbo_lang_common.erlang import analyze_erlang
from hypergumbo_lang_common.r_lang import analyze_r_files

Analyze = Callable[[Path], AnalysisResult]


def _symbols(analyze: Analyze, root: Path, name: str, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    return list(analyze(root).symbols)


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
    assert len(after) == survivors == len(before) - 1  # reach
    for line, sym in after.items():
        assert sym.stable_id is not None, (line, sym.name)
        assert sym.stable_id == before[line].stable_id, (line, sym.name)


def _expected(
    kind: str, lang: str, path: str, name: str, qualified_name: str,
    meta: Optional[dict[str, Any]], decorators: str = "",
) -> str:
    return declared_arity_stable_id(
        kind=kind, lang=lang, path=path, name=name,
        qualified_name=qualified_name, arity=declared_arity(meta),
        decorators=decorators,
    )


# -- Erlang ---------------------------------------------------------------

_ERLANG = (
    "-module(demo).\n"
    "-export([greet/1, greet/2]).\n"
    "greet(A) -> A.\n"
    "greet(A, B) -> {A, B}.\n"
    "-define(MAX, 10).\n"
    "-define(ADD(A, B), A + B).\n"
    "-define(ZERO(), 0).\n"
)


def test_erlang_function_and_macro_ids_are_the_producer_formula(
    tmp_path: Path,
) -> None:
    syms = _by_line(
        _symbols(analyze_erlang, tmp_path, "demo.erl", _ERLANG),
        {"function", "macro"},
    )
    assert syms[3].name == "greet/1" and syms[3].meta["arity"] == 1
    assert syms[3].stable_id == _expected(
        "function", "erlang", "demo.erl", "greet/1", "greet/1", {"arity": 1},
    )
    assert syms[4].stable_id == _expected(
        "function", "erlang", "demo.erl", "greet/2", "greet/2", {"arity": 2},
    )
    # ?MAX has no argument list; ?ADD(A, B) and ?ZERO() record theirs.
    assert syms[5].meta is None
    assert syms[6].meta == {
        PARAMETERS_KEY: [parameter_entry("A"), parameter_entry("B")],
    }
    assert syms[7].meta == {PARAMETERS_KEY: []}
    assert syms[6].stable_id == _expected(
        "macro", "erlang", "demo.erl", "ADD", "ADD", syms[6].meta,
    )
    assert syms[5].stable_id == _expected(
        "macro", "erlang", "demo.erl", "MAX", "MAX", None,
    )
    assert len({s.stable_id for s in syms.values()}) == len(syms)


def test_erlang_callback_and_same_named_function_stay_apart(
    tmp_path: Path,
) -> None:
    """A behaviour module declaring ``-callback area/0`` and defining
    ``area/0``: the callback's ``abstract`` decorator keeps them apart."""
    text = (
        "-module(shape).\n"
        "-callback area() -> integer().\n"
        "area() -> 0.\n"
    )
    syms = _by_line(
        _symbols(analyze_erlang, tmp_path, "shape.erl", text), {"function"},
    )
    assert syms[2].name == syms[3].name == "area/0"
    assert None not in (syms[2].stable_id, syms[3].stable_id)
    assert syms[2].stable_id != syms[3].stable_id


def test_erlang_deleting_an_earlier_arity_moves_no_id(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_erlang, tmp_path, "demo.erl", _ERLANG, "greet(A) -> A.\n",
        {"function", "macro"}, survivors=4,
    )


# -- Clojure --------------------------------------------------------------

_CLOJURE = (
    "(ns demo.core)\n"
    "(defmulti area :shape)\n"
    "(defn run [x] x)\n"
    "(defn run\n"
    "  \"doc\"\n"
    "  ([x] x)\n"
    "  ([x y] (+ x y)))\n"
    "(defmethod area :circle [s] 1)\n"
    "(defmethod area [:a,  :b] [s] 2)\n"
    "(defmacro mk [x & body] x)\n"
    "(defn- helper [^long n {:keys [a]} & more] n)\n"
)


def test_clojure_ids_carry_namespace_dispatch_and_arity(tmp_path: Path) -> None:
    syms = _by_line(
        _symbols(analyze_clojure, tmp_path, "core.clj", _CLOJURE),
        {"function", "macro", "method", "multimethod"},
    )
    path = "core.clj"
    one = {PARAMETERS_KEY: [parameter_entry("x")]}
    assert syms[3].meta[PARAMETERS_KEY] == one[PARAMETERS_KEY]
    assert syms[3].stable_id == _expected(
        "function", "clojure", path, "run", "demo.core/run", one,
    )
    # The multi-arity run: one node, its arity bodies merged (1 or 2 args).
    multi = [parameter_entry("x"), parameter_entry("y", default=True)]
    assert syms[4].meta[PARAMETERS_KEY] == multi
    assert syms[4].stable_id == _expected(
        "function", "clojure", path, "run", "demo.core/run",
        {PARAMETERS_KEY: multi},
    )
    # defmethods: the dispatch value, whitespace and commas collapsed.
    assert syms[8].stable_id == _expected(
        "method", "clojure", path, "area", "demo.core/area :circle",
        {PARAMETERS_KEY: [parameter_entry("s")]},
    )
    assert syms[9].stable_id == _expected(
        "method", "clojure", path, "area", "demo.core/area [:a :b]",
        {PARAMETERS_KEY: [parameter_entry("s")]},
    )
    # defmulti declares no parameters: the zero-parameter formula.
    assert syms[2].meta is None
    assert syms[2].stable_id == _expected(
        "multimethod", "clojure", path, "area", "demo.core/area", None,
    )
    assert syms[10].meta[PARAMETERS_KEY] == [
        parameter_entry("x"), parameter_entry("body", variadic=True),
    ]
    # A type hint is not a parameter; a destructuring map is one.
    assert syms[11].meta == {
        "visibility": "private",
        PARAMETERS_KEY: [
            parameter_entry("n"), parameter_entry("{:keys [a]}"),
            parameter_entry("more", variadic=True),
        ],
    }
    assert len({s.stable_id for s in syms.values()}) == len(syms)


def test_clojure_merges_variadic_and_non_contiguous_arity_bodies(
    tmp_path: Path,
) -> None:
    text = (
        "(defn f ([a] a) ([a b c] a) ([a b c d & more] a))\n"
        "(defmethod g :k ([s] 1) ([s t] 2))\n"
        "(defn h)\n"
        "(defn k {:m 1} 42)\n"
        "(defn m (oops) 1)\n"
        "(defn one ([x] x))\n"
    )
    syms = _by_line(
        _symbols(analyze_clojure, tmp_path, "m.clj", text),
        {"function", "method"},
    )
    assert syms[1].meta[PARAMETERS_KEY] == [
        parameter_entry("a"),
        parameter_entry("b", default=True),
        parameter_entry("c", default=True),
        parameter_entry("d", default=True),
        parameter_entry("more", variadic=True),
    ]
    assert syms[2].meta[PARAMETERS_KEY] == [
        parameter_entry("s"), parameter_entry("t", default=True),
    ]
    # No parameter vector at all: arity unknown, no parameters key, and the
    # name without a namespace (no ns form in this file).
    assert syms[3].meta is None
    assert syms[3].stable_id == _expected(
        "function", "clojure", "m.clj", "h", "h", None,
    )
    # Neither a vector nor arity bodies after the name: unknown, not empty.
    assert syms[4].meta is None
    assert PARAMETERS_KEY not in (syms[5].meta or {})
    # One arity body in the list form is that body's parameters.
    assert syms[6].meta == {PARAMETERS_KEY: [parameter_entry("x")]}


def test_clojure_deleting_an_earlier_sibling_moves_no_id(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_clojure, tmp_path, "core.clj", _CLOJURE,
        "(defn run [x] x)\n", {"function", "macro", "method", "multimethod"},
        survivors=6,
    )
    _assert_deletion_moves_nothing(
        analyze_clojure, tmp_path / "m", "core.clj", _CLOJURE,
        "(defmethod area :circle [s] 1)\n",
        {"function", "macro", "method", "multimethod"}, survivors=6,
    )


# -- Common Lisp ----------------------------------------------------------

_LISP = (
    "(in-package #:shapes)\n"
    "(defgeneric area (s))\n"
    "(defmethod area ((s circle)) 1)\n"
    "(defmethod area ((s square)) 2)\n"
    "(defmethod area :around ((s square)) 3)\n"
    "(defun helper (a &optional (b 1) &rest r &key k &aux tmp) a)\n"
    "(defmacro mk ((x y) &whole w &body body) x)\n"
    "(defmethod scale ((n (eql 3)) factor) n)\n"
)


def test_commonlisp_methods_hash_their_specializers(tmp_path: Path) -> None:
    syms = _by_line(
        _symbols(analyze_commonlisp, tmp_path, "s.lisp", _LISP),
        {"function", "macro", "method", "generic"},
    )
    path = "s.lisp"
    circle = {PARAMETERS_KEY: [parameter_entry("s", "circle")]}
    square = {PARAMETERS_KEY: [parameter_entry("s", "square")]}
    assert syms[3].meta == circle
    assert syms[3].stable_id == _expected(
        "method", "commonlisp", path, "area", "shapes::area", circle,
    )
    assert syms[4].stable_id == _expected(
        "method", "commonlisp", path, "area", "shapes::area", square,
    )
    # The :around method shares the primary's specializers; the qualifier
    # (decorators slot) is what tells them apart.
    assert syms[5].stable_id == _expected(
        "method", "commonlisp", path, "area", "shapes::area", square, ":around",
    )
    assert syms[2].kind == "generic"
    assert syms[2].meta == {PARAMETERS_KEY: [parameter_entry("s")]}
    assert syms[6].meta == {PARAMETERS_KEY: [
        parameter_entry("a"),
        parameter_entry("b", default=True),
        parameter_entry("r", variadic=True),
        parameter_entry("k", default=True),
    ]}
    # A macro's destructuring pattern is one untyped parameter; &whole's
    # variable is not an argument.
    assert syms[7].meta == {PARAMETERS_KEY: [
        parameter_entry("(x y)"), parameter_entry("body", variadic=True),
    ]}
    assert syms[8].meta == {PARAMETERS_KEY: [
        parameter_entry("n", "(eql 3)"), parameter_entry("factor"),
    ]}
    assert len({s.stable_id for s in syms.values()}) == len(syms)


def test_commonlisp_uppercase_forms_and_no_package(tmp_path: Path) -> None:
    """``DEFMETHOD`` parses as a plain list, not a ``defun`` node; it takes
    the same formula. With no ``in-package`` the chain is the bare name."""
    text = (
        "(DEFMETHOD AREA :BEFORE ((S CIRCLE)) 1)\n"
        "(DEFUN NOARGS)\n"
        "(defvar *x* 1)\n"
    )
    syms = _by_line(
        _symbols(analyze_commonlisp, tmp_path, "u.lisp", text),
        {"function", "method", "variable"},
    )
    circle = {PARAMETERS_KEY: [parameter_entry("s", "circle")]}
    assert syms[1].meta == circle
    assert syms[1].stable_id == _expected(
        "method", "commonlisp", "u.lisp", "area", "area", circle, ":before",
    )
    assert syms[2].meta is None
    assert syms[2].stable_id == _expected(
        "function", "commonlisp", "u.lisp", "noargs", "noargs", None,
    )
    assert syms[3].stable_id is None  # a variable is left to its table formula


def test_commonlisp_deleting_an_earlier_method_moves_no_id(
    tmp_path: Path,
) -> None:
    _assert_deletion_moves_nothing(
        analyze_commonlisp, tmp_path, "s.lisp", _LISP,
        "(defmethod area ((s circle)) 1)\n",
        {"function", "macro", "method", "generic"}, survivors=6,
    )


# -- R ----------------------------------------------------------------------

_R = (
    "run <- function(x) x\n"
    "run <- function(x, y = 2, ...) x + y\n"
    "outer_a <- function() {\n"
    "  inner <- function(a) a\n"
    "}\n"
    "outer_b = function() {\n"
    "  lapply(1:3, function(i) {\n"
    "    inner <<- function(a) a\n"
    "  })\n"
    "}\n"
)


def test_r_ids_carry_parameters_and_the_enclosing_functions(
    tmp_path: Path,
) -> None:
    syms = _by_line(
        _symbols(analyze_r_files, tmp_path, "demo.R", _R), {"function"},
    )
    path = "demo.R"
    one = {PARAMETERS_KEY: [parameter_entry("x")]}
    three = {PARAMETERS_KEY: [
        parameter_entry("x"), parameter_entry("y", default=True),
        parameter_entry("...", variadic=True),
    ]}
    assert syms[1].meta == one and syms[2].meta == three
    assert syms[1].stable_id == _expected("function", "r", path, "run", "run", one)
    assert syms[2].stable_id == _expected(
        "function", "r", path, "run", "run", three,
    )
    a = {PARAMETERS_KEY: [parameter_entry("a")]}
    assert syms[4].stable_id == _expected(
        "function", "r", path, "inner", "outer_a/inner", a,
    )
    # The anonymous function(i) between outer_b and inner is passed over.
    assert syms[8].stable_id == _expected(
        "function", "r", path, "inner", "outer_b/inner", a,
    )
    assert len({s.stable_id for s in syms.values()}) == len(syms)


def test_r_deleting_an_earlier_reassignment_moves_no_id(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_r_files, tmp_path, "demo.R", _R, "run <- function(x) x\n",
        {"function"}, survivors=5,
    )
