# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-loguk: Common Lisp callable Symbols carry non-null cyclomatic_complexity.

Verifies the homoiconic head-symbol walker against the REAL tree-sitter-commonlisp
grammar. Two emit sites are exercised: the ``defun`` node (lowercase
defun/defmacro/...) and the ``list_lit`` fallback (uppercase ``DEFUN`` -> callable;
``defvar``/``defclass``/... -> non-callable, stays None). CC is conservative
(base 1 + one per control-form head; ``loop`` counted via the ``loop_macro``
alias; case-insensitive head matching).
"""
from pathlib import Path

from hypergumbo_lang_common.commonlisp import analyze_commonlisp

_CALLABLE_KINDS = {"function", "macro", "method", "generic"}

_FIXTURE = '''(defun classify (n x)
  (if (> x 0)
      (cond ((evenp x) :even)
            ((oddp x) :odd))
      (when (and (numberp x) (or (zerop x) (minusp x)))
        (case n
          (1 :one)
          (otherwise :many)))))

(defmacro guarded (a b)
  (unless (and a b)
    `(error "bad")))

(defun counter (n)
  (loop for i from 0 below n
        do (dotimes (j i)
             (dolist (k (list j))
               (while (> k 0) (decf k))))))

(defun trivial (x) (+ x 1))

;; Uppercase DEFUN routes through the list_lit fallback emit site (callable).
(DEFUN upper-fn (x)
  (if x 1 2))

;; Non-callable defs route through the same fallback site and stay None.
(defvar *config* 42)
(defparameter *limit* 100)
'''


def _callables_by_name(symbols: list) -> dict:
    return {s.name: s for s in symbols if s.kind in _CALLABLE_KINDS}


def test_commonlisp_callable_cyclomatic_complexity(tmp_path: Path) -> None:
    (tmp_path / "code.lisp").write_text(_FIXTURE)
    result = analyze_commonlisp(tmp_path)
    assert not result.skipped
    callables = _callables_by_name(result.symbols)
    assert callables["classify"].cyclomatic_complexity == 7
    assert callables["guarded"].cyclomatic_complexity == 3
    assert callables["counter"].cyclomatic_complexity == 5
    assert callables["trivial"].cyclomatic_complexity == 1
    # Uppercase DEFUN via the list_lit fallback site is still a callable.
    assert callables["upper-fn"].cyclomatic_complexity == 2


def test_commonlisp_callables_have_non_null_loc(tmp_path: Path) -> None:
    (tmp_path / "code.lisp").write_text(_FIXTURE)
    result = analyze_commonlisp(tmp_path)
    for sym in result.symbols:
        if sym.kind in _CALLABLE_KINDS:
            assert sym.cyclomatic_complexity is not None
            assert sym.line_span is not None


def test_commonlisp_non_callables_stay_null(tmp_path: Path) -> None:
    (tmp_path / "code.lisp").write_text(_FIXTURE)
    result = analyze_commonlisp(tmp_path)
    non_callables = [s for s in result.symbols if s.kind not in _CALLABLE_KINDS]
    assert any(s.kind == "variable" for s in non_callables)
    for sym in non_callables:
        assert sym.cyclomatic_complexity is None
        assert sym.line_span is None
