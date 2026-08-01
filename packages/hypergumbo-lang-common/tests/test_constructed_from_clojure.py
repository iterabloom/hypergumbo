# SPDX-License-Identifier: AGPL-3.0-or-later
"""Clojure's ``meta["constructed_from"]`` edges (WI-nopod).

Clojure has no ``new`` keyword in idiomatic code — objects come from factory
functions — so the same rule the other analyzers apply (record the callee of
a call-valued binding) lands on the s-expression's head symbol.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import hypergumbo_lang_common.clojure as clj


def _symbols(source: str):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "m.clj").write_text(source)
        return {s.name: s for s in clj.analyze_clojure(root).symbols}


def test_factory_call_is_recorded() -> None:
    syms = _symbols("(def app (make-c))\n")
    assert (syms["app"].meta or {}).get("constructed_from") == "make-c"


def test_private_def_keeps_both_meta_keys() -> None:
    """Visibility and the binding coexist — the reason meta is assembled by a
    helper rather than a conditional literal, which could only carry one."""
    syms = _symbols("(defn- ^:private h [] 1)\n(def ^:private app (make-c))\n")
    meta = syms["app"].meta or {}
    assert meta.get("constructed_from") == "make-c"


def test_literal_and_valueless_defs_record_nothing() -> None:
    syms = _symbols("(def n 3)\n(def bare)\n")
    assert "constructed_from" not in (syms["n"].meta or {})
    if "bare" in syms:
        assert "constructed_from" not in (syms["bare"].meta or {})


def test_non_symbol_head_records_nothing() -> None:
    """`(def x ((f) 1))` — the head is a form, not a name."""
    syms = _symbols("(def x ((f) 1))\n")
    assert "constructed_from" not in (syms["x"].meta or {})
