# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-loguk: Scheme callable Symbols carry non-null cyclomatic_complexity.

Verifies the homoiconic head-symbol walker against the REAL tree-sitter-scheme
grammar. Scheme forms are ``list`` nodes with a ``symbol`` head; CC counts
if/when/unless/cond/case/and/or/do/match/for conservatively. The ``variable``
def (``(define name value)``) shares the recursion and stays None.
"""
from pathlib import Path

from hypergumbo_lang_common.scheme import analyze_scheme

_CALLABLE_KINDS = {"function"}

_FIXTURE = '''(define (classify n)
  (cond ((< n 0) (quote neg))
        ((= n 0) (quote zero))
        (else (quote pos))))

(define (process lst flag)
  (if (and (pair? lst) flag)
      (when (> (length lst) 3)
        (case (car lst)
          ((1) (quote one))
          ((2) (quote two))
          (else (quote many))))
      (unless (null? lst)
        (or (car lst) 0))))

(define (loopy n)
  (do ((i 0 (+ i 1)))
      ((= i n))
    (display i)))

(define answer 42)
'''


def _callables_by_name(symbols: list) -> dict:
    return {s.name: s for s in symbols if s.kind in _CALLABLE_KINDS}


def test_scheme_callable_cyclomatic_complexity(tmp_path: Path) -> None:
    (tmp_path / "code.scm").write_text(_FIXTURE)
    result = analyze_scheme(tmp_path)
    assert not result.skipped
    callables = _callables_by_name(result.symbols)
    assert callables["classify"].cyclomatic_complexity == 2
    assert callables["process"].cyclomatic_complexity == 7
    assert callables["loopy"].cyclomatic_complexity == 2


def test_scheme_callables_have_non_null_loc(tmp_path: Path) -> None:
    (tmp_path / "code.scm").write_text(_FIXTURE)
    result = analyze_scheme(tmp_path)
    for sym in result.symbols:
        if sym.kind in _CALLABLE_KINDS:
            assert sym.cyclomatic_complexity is not None
            assert sym.line_span is not None


def test_scheme_non_callables_stay_null(tmp_path: Path) -> None:
    (tmp_path / "code.scm").write_text(_FIXTURE)
    result = analyze_scheme(tmp_path)
    non_callables = [s for s in result.symbols if s.kind not in _CALLABLE_KINDS]
    assert any(s.kind == "variable" for s in non_callables)
    for sym in non_callables:
        assert sym.cyclomatic_complexity is None
        assert sym.line_span is None
