# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-loguk: Fennel callable Symbols carry non-null cyclomatic_complexity.

Verifies the homoiconic head-symbol walker against the REAL tree-sitter-fennel
grammar. Fennel is PARTIALLY homoiconic: if/when/when-not/if-not/cond/case/and/
or/while are ``list`` forms with a ``symbol`` head, while for/each/match are
dedicated node types (counted under the ``is_named`` guard to avoid the
named-construct-vs-anonymous-keyword double-count). The ``variable`` (local)
def stays None.
"""
from pathlib import Path

from hypergumbo_lang_extended1.fennel import analyze_fennel

_CALLABLE_KINDS = {"function"}

_FIXTURE = '''(fn classify [x]
  (if (> x 10) "big" (< x 0) "neg" "small"))

(fn handle [a b]
  (when (and a b) (print "both"))
  (case a 1 "one" 2 "two" _ "many"))

(fn loopy [n]
  (var total 0)
  (for [i 1 n] (set total (+ total i)))
  (each [k v (pairs {})] (print k v))
  (while (> n 0) (set n (- n 1)))
  total)

(fn matcher [t]
  (match t [1 2] "ab" _ "other"))

(fn manyor [a b c] (or a b c))

(fn plain [a] (+ a 1))

(local config 42)
'''


def _callables_by_name(symbols: list) -> dict:
    return {s.name: s for s in symbols if s.kind in _CALLABLE_KINDS}


def test_fennel_callable_cyclomatic_complexity(tmp_path: Path) -> None:
    (tmp_path / "code.fnl").write_text(_FIXTURE)
    result = analyze_fennel(tmp_path)
    assert not result.skipped
    callables = _callables_by_name(result.symbols)
    assert callables["classify"].cyclomatic_complexity == 2
    assert callables["handle"].cyclomatic_complexity == 4
    assert callables["loopy"].cyclomatic_complexity == 4
    assert callables["matcher"].cyclomatic_complexity == 2
    assert callables["manyor"].cyclomatic_complexity == 2
    assert callables["plain"].cyclomatic_complexity == 1


def test_fennel_callables_have_non_null_loc(tmp_path: Path) -> None:
    (tmp_path / "code.fnl").write_text(_FIXTURE)
    result = analyze_fennel(tmp_path)
    for sym in result.symbols:
        if sym.kind in _CALLABLE_KINDS:
            assert sym.cyclomatic_complexity is not None
            assert sym.line_span is not None


def test_fennel_non_callables_stay_null(tmp_path: Path) -> None:
    (tmp_path / "code.fnl").write_text(_FIXTURE)
    result = analyze_fennel(tmp_path)
    non_callables = [s for s in result.symbols if s.kind not in _CALLABLE_KINDS]
    assert any(s.kind == "variable" for s in non_callables)
    for sym in non_callables:
        assert sym.cyclomatic_complexity is None
        assert sym.line_span is None
