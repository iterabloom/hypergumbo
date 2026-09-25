# SPDX-License-Identifier: AGPL-3.0-or-later
"""The python DDG stores each function under the key the taint walk asks for
(WI-ripas).

``_ddg_taint_reaches`` gates on ``fn_has_ddg = source_fn in ddg_symbols``, where
``source_fn`` is the ANALYZER's symbol id. The python ``LanguageDdgSpec``
supplied neither ``name_for`` nor ``kind_for``, so ``_walk_functions`` fell back
to the bare ``name`` field and the constant kind ``"function"`` — and every
method was stored under a key nothing ever constructs. Measured before the fix
on this repository: **0 of 24,915 methods matched**, while 21,257 of them
(85.3%) WERE in the set under the unreachable bare-name key. The walk ran, the
reaching-definitions solve succeeded, and the answer was discarded at a dict
lookup. 68.5% of python callables could never have ``fn_has_ddg`` true, so every
taint finding rooted in one was ``unavailable`` -> ``structural`` by
construction.

Go already did this correctly (``go_def_use`` supplies both hooks, reusing
``go.py``'s own receiver extractor); rust omits them WITH A WRITTEN REASON, its
methods being ``function_item`` nested in ``impl_item`` so the defaults are
already right. Python and the two ts_def_use registrations omitted them with no
reason at all. This is the INV-hokig shape: a cure that shipped wired to one
language.

PY.PY'S NAMING RULE, derived from the analyzer rather than assumed — a probe
over all four shapes gives:

    top-level function      top
    nested function         top.inner        <- prefixed with the enclosing FUNCTION
    method                  Outer.meth
    method of nested class  Inner.deep       <- IMMEDIATE class only, not Outer.Inner

So the rule is uniform: prefix with the IMMEDIATE enclosing scope's name, one
level, class or function; and the kind slot is ``method`` exactly when that
immediate scope is a class. Nested FUNCTIONS were mis-keyed too, which the row
did not note.

THE PATH SLOT IS NORMALISED IN THESE TESTS, DELIBERATELY AND VISIBLY. The two
producers can be handed different root semantics, and then every id mismatches
in the path slot for a reason that has nothing to do with this defect — that
error is what made a first measurement of this bug read 0-of-1 for FUNCTIONS
too. Normalising it here keeps these assertions about the name and kind slots,
which is what the fix changes.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core import dataflow_scope
from hypergumbo_core.ddg_build import build_repo_ddg
from hypergumbo_lang_mainstream.py import analyze_python

_SRC = '''
import os


def top(raw):
    a = raw.strip()
    b = a.lower()
    os.system(b)

    def inner(x):
        c = x.strip()
        d = c.lower()
        os.system(d)
    return inner


class Outer:
    def meth(self, raw):
        a = raw.strip()
        b = a.lower()
        os.system(b)

    class Inner:
        def deep(self, raw):
            a = raw.strip()
            b = a.lower()
            os.system(b)
'''


def _basename_path_slot(symbol_id: str) -> str:
    parts = symbol_id.split(":")
    parts[1] = parts[1].rsplit("/", 1)[-1]
    return ":".join(parts)


def _both_arms(tmp_path: Path) -> tuple[set[str], set[str]]:
    """(ddg keys, analyzer callable ids), path slot normalised on both."""
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "n.py").write_text(_SRC)
    # INV-zunik: the def/use extractors register by import side-effect, and a
    # bare read returns an EMPTY set that would make every assertion below
    # vacuously true.
    dataflow_scope.ensure_def_use_extractors_registered()
    ddg = build_repo_ddg(root, languages=("python",))
    analyzer = analyze_python(root)
    return (
        {_basename_path_slot(k) for k in ddg.ddg_symbols},
        {
            _basename_path_slot(s.id)
            for s in analyzer.symbols
            if s.kind in ("method", "function")
        },
    )


class TestTheFixtureReachesTheMachinery:
    """LIVE.md §1.6: if the fixture cannot reach the thing, every assertion is
    vacuously true. ``ddg_symbols`` is filled only ``if result.ddg_edges``, so a
    fixture of trivial bodies yields an empty set and proves nothing."""

    def test_the_ddg_actually_solved_something(self, tmp_path: Path) -> None:
        ddg_keys, analyzer_ids = _both_arms(tmp_path)
        assert ddg_keys, "DDG stored nothing — the assertions would be vacuous"
        assert analyzer_ids, "analyzer produced no callables"


class TestEveryCallableIsStoredUnderTheKeyTheWalkAsks:
    def test_methods_match(self, tmp_path: Path) -> None:
        ddg_keys, analyzer_ids = _both_arms(tmp_path)
        methods = {i for i in analyzer_ids if i.endswith(":method")}
        assert methods, "fixture has no methods"
        assert sorted(methods - ddg_keys) == []

    def test_functions_match(self, tmp_path: Path) -> None:
        ddg_keys, analyzer_ids = _both_arms(tmp_path)
        functions = {i for i in analyzer_ids if i.endswith(":function")}
        assert functions, "fixture has no functions"
        assert sorted(functions - ddg_keys) == []

    def test_the_ddg_invents_no_key_the_analyzer_never_produces(
        self, tmp_path: Path
    ) -> None:
        """One-directional in the other direction: a stored key that names no
        analyzer symbol is a lookup nothing will ever perform."""
        ddg_keys, analyzer_ids = _both_arms(tmp_path)
        assert sorted(ddg_keys - analyzer_ids) == []


class TestTheSlotsThemselves:
    def test_a_method_takes_its_class_and_the_method_kind(
        self, tmp_path: Path
    ) -> None:
        ddg_keys, _ = _both_arms(tmp_path)
        assert [k for k in ddg_keys if k.endswith(":Outer.meth:method")]

    def test_a_nested_class_method_takes_the_immediate_class_only(
        self, tmp_path: Path
    ) -> None:
        ddg_keys, _ = _both_arms(tmp_path)
        assert [k for k in ddg_keys if k.endswith(":Inner.deep:method")]
        assert [k for k in ddg_keys if "Outer.Inner.deep" in k] == []

    def test_a_nested_function_takes_its_enclosing_function_and_stays_function(
        self, tmp_path: Path
    ) -> None:
        ddg_keys, _ = _both_arms(tmp_path)
        assert [k for k in ddg_keys if k.endswith(":top.inner:function")]

    def test_a_top_level_function_is_unprefixed(self, tmp_path: Path) -> None:
        ddg_keys, _ = _both_arms(tmp_path)
        assert [k for k in ddg_keys if k.endswith(":top:function")]
