# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-loguk: Racket callable Symbols carry non-null cyclomatic_complexity.

Verifies the homoiconic head-symbol walker against the REAL tree-sitter-racket
grammar. Racket forms are ``list`` nodes with a ``symbol`` head; CC counts
if/when/unless/cond/case/and/or/do/match/for and the ``for/...`` comprehension
family conservatively. The ``variable`` (define name value) and ``class``
(struct) defs share the recursion and stay None.
"""
from pathlib import Path

from hypergumbo_lang_common.racket import analyze_racket

_CALLABLE_KINDS = {"function"}

_FIXTURE = '''#lang racket
(define (classify n)
  (cond
    [(< n 0) "neg"]
    [(= n 0) "zero"]
    [else "pos"]))

(define (process x y)
  (if (and (> x 0) (or (< y 0) (= y 0)))
      (when (> x 10)
        (case x
          [(1 2 3) "small"]
          [else "big"]))
      (unless (= y 0)
        (for ([i (in-range x)])
          (displayln i)))))

(define (loopy lst)
  (do ([i 0 (+ i 1)])
      ((>= i 10))
    (match lst
      [(list a b) (+ a b)]
      [_ 0])))

(define answer 42)
(struct point (x y))
'''


def _callables_by_name(symbols: list) -> dict:
    return {s.name: s for s in symbols if s.kind in _CALLABLE_KINDS}


def test_racket_callable_cyclomatic_complexity(tmp_path: Path) -> None:
    (tmp_path / "code.rkt").write_text(_FIXTURE)
    result = analyze_racket(tmp_path)
    assert not result.skipped
    callables = _callables_by_name(result.symbols)
    assert callables["classify"].cyclomatic_complexity == 2
    assert callables["process"].cyclomatic_complexity == 8
    assert callables["loopy"].cyclomatic_complexity == 3


def test_racket_callables_have_non_null_loc(tmp_path: Path) -> None:
    (tmp_path / "code.rkt").write_text(_FIXTURE)
    result = analyze_racket(tmp_path)
    for sym in result.symbols:
        if sym.kind in _CALLABLE_KINDS:
            assert sym.cyclomatic_complexity is not None
            assert sym.lines_of_code is not None


def test_racket_non_callables_stay_null(tmp_path: Path) -> None:
    (tmp_path / "code.rkt").write_text(_FIXTURE)
    result = analyze_racket(tmp_path)
    non_callables = [s for s in result.symbols if s.kind not in _CALLABLE_KINDS]
    assert non_callables
    for sym in non_callables:
        assert sym.cyclomatic_complexity is None
        assert sym.lines_of_code is None
