# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-loguk: Jupyter notebook function/method callables carry non-null CC + LOC.

Notebook code cells are Python, so the jupyter analyzer reuses py.py's
language-agnostic Python-AST complexity walkers. Verified end-to-end through the
production path (`analyze_jupyter`) on a branchy fixture; the class symbol
(kind="class") is outside the function-like scope and stays None.
"""
import json
from pathlib import Path

from hypergumbo_lang_mainstream.jupyter import analyze_jupyter

_CLASSIFY = (
    "def classify(x):\n"
    "    if x > 0 and x < 100:\n"
    "        return 'small'\n"
    "    elif x < 0 or x == 999:\n"
    "        return 'neg'\n"
    "    for i in range(x):\n"
    "        if i % 2:\n"
    "            print(i)\n"
    "    while x > 0:\n"
    "        x -= 1\n"
    "    return 'done'\n"
)

_CALC = (
    "class Calc:\n"
    "    def run(self, n):\n"
    "        total = 0\n"
    "        for i in range(n):\n"
    "            total += i\n"
    "        if total > 10:\n"
    "            return total\n"
    "        return 0\n"
)


def _write_notebook(path: Path, *sources: str) -> None:
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": src,
            }
            for src in sources
        ],
        "metadata": {
            "kernelspec": {
                "name": "python3", "language": "python",
                "display_name": "Python 3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(nb))


def test_jupyter_callable_cyclomatic_complexity(tmp_path: Path) -> None:
    _write_notebook(tmp_path / "nb.ipynb", _CLASSIFY, _CALC)
    result = analyze_jupyter(tmp_path)
    assert not result.skipped
    by_name = {s.name: s for s in result.symbols if s.kind == "function"}
    # py.py's AST walker: if + and + elif + or + for + nested-if + while = 8.
    assert by_name["classify"].cyclomatic_complexity == 8
    assert by_name["classify"].line_span == 11
    # class method (emitted both as "run" and qualified "Calc.run"): for + if = 3.
    assert by_name["Calc.run"].cyclomatic_complexity == 3
    assert by_name["run"].cyclomatic_complexity == 3


def test_jupyter_class_symbol_stays_null(tmp_path: Path) -> None:
    _write_notebook(tmp_path / "nb.ipynb", _CALC)
    result = analyze_jupyter(tmp_path)
    classes = [s for s in result.symbols if s.kind == "class"]
    assert classes  # the Calc class is emitted
    for sym in classes:
        assert sym.cyclomatic_complexity is None
        assert sym.line_span is None
