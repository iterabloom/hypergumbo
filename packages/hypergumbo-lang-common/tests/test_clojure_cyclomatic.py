# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-loguk: Clojure callable Symbols carry non-null cyclomatic_complexity.

Verifies the homoiconic head-symbol walker (clojure dispatches to it via
``_HOMOICONIC_SPECS``) against the REAL tree-sitter-clojure grammar: the
per-callable CC values are counted conservatively (base 1 + one per control-form
head occurrence; variadic ``(and ...)`` and multi-clause ``(cond ...)``/
``(case ...)`` count once). Non-callable def forms (variable / protocol / record
/ type / multimethod) and the ``ns`` module symbol share the emit site and must
stay None.
"""
from pathlib import Path

from hypergumbo_lang_common.clojure import analyze_clojure

_CALLABLE_KINDS = {"function", "macro", "method"}

# Branchy fixture (control forms exercised: if/and/cond/when-not/case in
# classify; doseq/dotimes/when/or/if-let/for/some->/condp in process).
_FIXTURE = '''(ns my.app
  (:require [clojure.string :as str]))

(defn classify [x y]
  (if (and (pos? x) (pos? y))
    (cond
      (= x y) :equal
      (> x y) :greater
      :else :less)
    (when-not (zero? y)
      (case x
        1 :one
        2 :two
        :other))))

(defn process [items flag]
  (doseq [i items]
    (dotimes [n 3]
      (when (or flag (even? i))
        (println i n))))
  (if-let [v (first items)]
    (for [j (range v)]
      (some-> j inc (* 2)))
    (condp = flag
      :a 1
      :b 2
      0)))

(defn simple [a] (+ a 1))

(defmacro my-macro [body]
  (if body `(do ~body) nil))

(def some-config 42)

(defmulti area :shape)
(defmethod area :circle [s]
  (when (:radius s)
    (* 3.14 (:radius s))))
'''


def _callables_by_name(symbols: list) -> dict:
    return {s.name: s for s in symbols if s.kind in _CALLABLE_KINDS}


def test_clojure_callable_cyclomatic_complexity(tmp_path: Path) -> None:
    (tmp_path / "app.clj").write_text(_FIXTURE)
    result = analyze_clojure(tmp_path)
    assert not result.skipped
    callables = _callables_by_name(result.symbols)
    assert callables["classify"].cyclomatic_complexity == 6
    assert callables["process"].cyclomatic_complexity == 9
    assert callables["simple"].cyclomatic_complexity == 1
    assert callables["my-macro"].cyclomatic_complexity == 2
    # defmethod -> kind "method" (callable); defmulti -> "multimethod" (not).
    assert callables["area"].cyclomatic_complexity == 2


def test_clojure_callables_have_non_null_loc(tmp_path: Path) -> None:
    (tmp_path / "app.clj").write_text(_FIXTURE)
    result = analyze_clojure(tmp_path)
    for sym in result.symbols:
        if sym.kind in _CALLABLE_KINDS:
            assert sym.cyclomatic_complexity is not None
            assert sym.lines_of_code is not None


def test_clojure_non_callables_stay_null(tmp_path: Path) -> None:
    (tmp_path / "app.clj").write_text(_FIXTURE)
    result = analyze_clojure(tmp_path)
    # Exercises the non-callable branch of the shared emit site (def variable,
    # defmulti multimethod) plus the ns module symbol.
    non_callables = [s for s in result.symbols if s.kind not in _CALLABLE_KINDS]
    assert non_callables  # module + variable + multimethod present
    for sym in non_callables:
        assert sym.cyclomatic_complexity is None
        assert sym.lines_of_code is None
