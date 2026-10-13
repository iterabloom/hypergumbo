# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer stable_ids for notebook functions (WI-tusad).

``jupyter.py`` computed no ``stable_id`` for its functions, so they rode the
ADR-0035 backstop floor key ``(kind, language, path, name)``: a notebook that
redefines ``run(x)`` as ``run(x, y)`` in a later cell, or defines ``predict``
in two classes, shared the key and every member of the group was null. It also
emitted every method twice, once as ``Class.m`` and once more under its bare
name from the same ``def``.

The notebook's code cells are Python, so each ``def`` now takes the Python
producer's formula (``py.python_callable_stable_id``: the normalized signature,
the decorators, the name-convention visibility) anchored on the notebook file,
with the enclosing classes and functions in the qualified name. Each ``def`` is
emitted once.

What is pinned, on the analyzer's own output: every symbol holds its own
non-null id (checked symbol by symbol, by line and column, never through a dict
keyed on name); the exact value; the delete-an-earlier-sibling property (the
deleted cell's lines are kept as blank lines so each survivor matches itself by
line); and that a bare-name call still reaches the method.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.analyze.base import (
    floor_stable_id_key, make_file_stable_id, make_typed_stable_id,
    visibility_from_modifiers,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.jupyter import analyze_jupyter
from hypergumbo_lang_mainstream.py import _compute_stable_id

NB = "nb/demo.ipynb"


def _notebook(root: Path, cells: list[str]) -> None:
    path = root / NB
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"language_info": {"name": "python"}},
        "cells": [
            {"cell_type": "code", "metadata": {}, "outputs": [],
             "execution_count": None, "source": src.splitlines(keepends=True)}
            for src in cells
        ],
    }))


def _functions(root: Path, cells: list[str]) -> list[Symbol]:
    _notebook(root, cells)
    return [s for s in analyze_jupyter(root).symbols if s.kind == "function"]


def _at(symbols: list[Symbol]) -> dict[tuple[int, int], Symbol]:
    by_pos = {(s.span.start_line, s.span.start_col): s for s in symbols}
    assert len(by_pos) == len(symbols), "two functions at one position"
    return by_pos


def _typed(norm_sig: str, name: str, qualified: str, decorators: str = "") -> str:
    return make_typed_stable_id(
        "function", norm_sig, visibility_from_modifiers([]),
        make_file_stable_id("jupyter", NB), decorators,
        name=name, qualified_name=qualified,
    )


def test_redefined_function_across_cells_gets_distinct_ids(tmp_path: Path) -> None:
    funcs = _functions(tmp_path, [
        "def run(x):\n    return x\n",
        "def run(x, y):\n    return x\n\ndef helper():\n    return 2\n",
    ])
    assert sorted((s.name, s.span.start_line) for s in funcs) == [
        ("helper", 7), ("run", 1), ("run", 4)]
    by_pos = _at(funcs)
    assert by_pos[(1, 0)].stable_id == _typed("(x)", "run", "run")
    assert by_pos[(4, 0)].stable_id == _typed("(x,y)", "run", "run")
    assert by_pos[(7, 0)].stable_id == _typed("()", "helper", "helper")
    for sym in funcs:
        assert sym.stable_id != floor_stable_id_key(
            sym.kind, sym.language, NB, sym.name)


def test_methods_are_emitted_once_with_the_class_in_the_chain(tmp_path: Path) -> None:
    funcs = _functions(tmp_path, [
        "class A:\n    def predict(self, x):\n        return x\n",
        "class B:\n    @staticmethod\n    def predict(x):\n        return x\n",
        "def outer():\n    def predict():\n        return 1\n    return predict()\n",
    ])
    by_pos = _at(funcs)
    assert sorted((s.name, pos) for pos, s in by_pos.items()) == [
        ("A.predict", (2, 4)), ("B.predict", (7, 4)),
        ("outer", (10, 0)), ("predict", (11, 4))]
    assert by_pos[(2, 4)].stable_id == _typed("(x)", "predict", "A.predict")
    assert by_pos[(7, 4)].stable_id == _typed(
        "(x)", "predict", "B.predict", decorators="staticmethod")
    assert by_pos[(11, 4)].stable_id == _typed("()", "predict", "outer.predict")
    assert len({s.stable_id for s in funcs}) == len(funcs)


def test_bare_name_call_still_reaches_the_method(tmp_path: Path) -> None:
    _notebook(tmp_path, [
        "class Foo:\n    def bar(self):\n        pass\n",
        "def main():\n    f = Foo()\n    f.bar()\n",
    ])
    result = analyze_jupyter(tmp_path)
    by_name = {s.name: s for s in result.symbols}
    assert sorted(by_name) == ["Foo", "Foo.bar", "main"]
    assert sorted((e.src, e.dst) for e in result.edges) == sorted([
        (by_name["main"].id, by_name["Foo"].id),
        (by_name["main"].id, by_name["Foo.bar"].id),
    ])


def test_long_signature_takes_the_untyped_fallback(tmp_path: Path) -> None:
    """A signature past the identity cap does not normalize; the Python
    producer then hashes the parameter count and flags instead."""
    src = (
        "def wide(param_a, param_b, param_c, param_d, param_e, param_f, "
        "param_g, param_h):\n    return 1\n"
    )
    (sym,) = _functions(tmp_path, [src])
    import ast
    node = ast.parse(src).body[0]
    assert isinstance(node, ast.FunctionDef)
    assert sym.stable_id == _compute_stable_id(
        node, containing_stable_id=make_file_stable_id("jupyter", NB),
        name="wide", qualified_name="wide",
    )


def test_deleting_an_earlier_cell_moves_no_id(tmp_path: Path) -> None:
    first = "def run(x):\n    return x\n"
    rest = [
        "def run(x, y):\n    return x\n\ndef helper():\n    return 2\n",
        "class K:\n    def go(self):\n        return 1\n",
    ]
    before = _at(_functions(tmp_path / "r1", [first, *rest]))
    # A cell of blank lines is skipped as empty, which would renumber every
    # later line; a comment keeps the cell and its line count.
    blank = "#\n" + "\n" * (first.count("\n") - 1)
    after = _at(_functions(tmp_path / "r2", [blank, *rest]))
    assert len(after) == 3 < len(before)
    for pos, sym in after.items():
        assert sym.stable_id is not None, (pos, sym.name)
        assert sym.stable_id == before[pos].stable_id, (pos, sym.name)


def test_classes_take_the_python_class_formula(tmp_path: Path) -> None:
    import ast
    src = "def f():\n    class K:\n        pass\n    return K\n"
    _notebook(tmp_path, ["class K:\n    pass\n", src])
    classes = {s.span.start_line: s for s in analyze_jupyter(tmp_path).symbols
               if s.kind == "class"}
    top, nested = ast.parse("class K:\n    pass\n").body[0], ast.parse(src).body[0].body[0]
    assert isinstance(top, ast.ClassDef) and isinstance(nested, ast.ClassDef)
    anchor = make_file_stable_id("jupyter", NB)
    assert classes[1].stable_id == _compute_stable_id(
        top, containing_stable_id=anchor, name="K", qualified_name="K")
    assert classes[5].stable_id == _compute_stable_id(
        nested, containing_stable_id=anchor, name="K", qualified_name="f.K")
