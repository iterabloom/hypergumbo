# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-loguk: Janet callable Symbols carry non-null cyclomatic_complexity.

Verifies the homoiconic head-symbol walker against the REAL tree-sitter-janet
grammar. Janet is PARTIALLY homoiconic: if/while are dedicated node types
(counted under the ``is_named`` guard against the named-vs-anonymous-keyword
collision), while when/unless/cond/case/and/or/loop/for/each are generic
``tuple`` forms with a ``symbol`` head. The ``variable`` (def) stays None.
"""
from pathlib import Path

from hypergumbo_lang_extended1.janet import analyze_janet

_CALLABLE_KINDS = {"function"}

_FIXTURE = '''(defn classify [x]
  (if (< x 0)
    :neg
    (cond
      (= x 0) :zero
      (< x 10) :small
      :big)))

(defn process [items]
  (var total 0)
  (each item items
    (when (> item 0)
      (set total (+ total item))))
  (loop [i :range [0 10]]
    (case i
      0 (print "zero")
      1 (print "one")
      (print "other")))
  (while (> total 100)
    (set total (- total 1)))
  (and (> total 0) (< total 50)))

(def CONSTANT 42)
'''


def _callables_by_name(symbols: list) -> dict:
    return {s.name: s for s in symbols if s.kind in _CALLABLE_KINDS}


def test_janet_callable_cyclomatic_complexity(tmp_path: Path) -> None:
    (tmp_path / "code.janet").write_text(_FIXTURE)
    result = analyze_janet(tmp_path)
    assert not result.skipped
    callables = _callables_by_name(result.symbols)
    assert callables["classify"].cyclomatic_complexity == 3
    assert callables["process"].cyclomatic_complexity == 7


def test_janet_callables_have_non_null_loc(tmp_path: Path) -> None:
    (tmp_path / "code.janet").write_text(_FIXTURE)
    result = analyze_janet(tmp_path)
    for sym in result.symbols:
        if sym.kind in _CALLABLE_KINDS:
            assert sym.cyclomatic_complexity is not None
            assert sym.line_span is not None


def test_janet_non_callables_stay_null(tmp_path: Path) -> None:
    (tmp_path / "code.janet").write_text(_FIXTURE)
    result = analyze_janet(tmp_path)
    non_callables = [s for s in result.symbols if s.kind not in _CALLABLE_KINDS]
    assert any(s.kind == "variable" for s in non_callables)
    for sym in non_callables:
        assert sym.cyclomatic_complexity is None
        assert sym.line_span is None
