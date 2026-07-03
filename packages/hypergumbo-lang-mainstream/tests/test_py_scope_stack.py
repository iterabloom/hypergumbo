# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavioral tests for the py.py scope-stack rewrite (identity:F1/F4a, PR-0).

The rewrite replaces the single-level ``inner_scope`` dict with a materialized
LEGB frame chain (``_pyscope.ScopeStack``) and adds ONE additive resolution
surface: a last-resort ``lookup_enclosing`` (step-4) on the two direct emitters
(``_process_call`` Case 1, ``_emit_function_ref``). It fires only where the
pre-rewrite resolver emitted no edge, and returns only nested FUNCTIONS defined
in an enclosing (grandparent-or-higher) function scope — so every new edge is
``is_resolved=True`` to a real in-repo node (the taint-safety property), and no
existing edge is re-targeted (step-4 is strictly last-resort, after
local_symbols and imports — the deliberate additive ordering; strict-LEGB
re-ordering is deferred to a bakeoff-gated follow-up).
"""

from __future__ import annotations

import ast
from pathlib import Path

from hypergumbo_lang_mainstream.py import _collect_scope_local_names, extract_nodes


def _analyze(tmp_path: Path, src: str):
    f = tmp_path / "m.py"
    f.write_text(src)
    res = extract_nodes(f)
    names = {s.id: (s.name, s.kind) for s in res.symbols}
    ids_by_name = {s.name: s.id for s in res.symbols}
    return res, names, ids_by_name


class TestGrandparentScopeResolution:
    """Step-4 resolves a bare call/reference to a helper defined in a
    GRANDPARENT (or higher) enclosing function — which the flat single-level
    inner_scope missed (was no edge)."""

    def test_grandparent_nested_helper_call_resolves(self, tmp_path: Path) -> None:
        src = (
            "def outer():\n"
            "    def target():\n"
            "        return 1\n"
            "    def middle():\n"
            "        def leaf():\n"
            "            return target()\n"  # target is defined in grandparent outer
            "        return leaf()\n"
            "    return middle()\n"
        )
        res, names, ids = _analyze(tmp_path, src)
        leaf_id = ids["outer.middle.leaf"]
        target_id = ids["outer.target"]
        matches = [
            e for e in res.edges
            if e.edge_type == "calls" and e.src == leaf_id and e.dst == target_id
        ]
        assert matches, (
            "leaf()'s bare call to the grandparent helper target() did not "
            "resolve via the enclosing scope (step-4)"
        )
        # Taint-safety property: every new edge is resolved to a REAL in-repo node.
        symbol_ids = {s.id for s in res.symbols}
        for e in matches:
            assert getattr(e, "is_resolved", True) is True
            assert e.dst in symbol_ids

    def test_grandparent_function_reference_emitted(self, tmp_path: Path) -> None:
        src = (
            "def outer():\n"
            "    def target():\n"
            "        return 1\n"
            "    def middle():\n"
            "        def leaf():\n"
            "            cb = target\n"  # bare REFERENCE (non-call) to grandparent helper
            "            return cb\n"
            "        return leaf()\n"
            "    return middle()\n"
        )
        res, names, ids = _analyze(tmp_path, src)
        leaf_id = ids["outer.middle.leaf"]
        target_id = ids["outer.target"]
        refs = [
            e for e in res.edges
            if e.edge_type == "references" and e.src == leaf_id and e.dst == target_id
        ]
        assert refs, (
            "cb = target (a reference to the grandparent helper) did not emit a "
            "references edge via the enclosing scope (step-4)"
        )

    def test_method_in_nested_class_resolves_enclosing_helper(self, tmp_path: Path) -> None:
        # A method inside a class inside a function sees the ENCLOSING FUNCTION's
        # helpers (the class body is transparent to LEGB for a nested method).
        src = (
            "def outer():\n"
            "    def helper():\n"
            "        return 1\n"
            "    class K:\n"
            "        def m(self):\n"
            "            return helper()\n"  # helper is in outer (m's enclosing function)
            "    return K\n"
        )
        res, names, ids = _analyze(tmp_path, src)
        m_id = ids["K.m"]
        helper_id = ids["outer.helper"]
        matches = [
            e for e in res.edges
            if e.edge_type == "calls" and e.src == m_id and e.dst == helper_id
        ]
        assert matches, (
            "K.m()'s call to the enclosing function's helper() did not resolve "
            "(method callers must get enclosing FUNCTION frames)"
        )


class TestStep4IsLastResort:
    """Step-4 never RE-TARGETS an edge that already resolves via a higher-priority
    rule — it is additive, firing only when local/import resolution missed."""

    def test_top_level_name_wins_over_grandparent(self, tmp_path: Path) -> None:
        # A top-level `helper` (in local_symbols) AND a grandparent-nested
        # `helper`; leaf's call must resolve to the TOP-LEVEL one (step-2), not
        # the grandparent (step-4) — proving no re-targeting.
        src = (
            "def outer():\n"
            "    def helper():\n"  # grandparent-scope helper (outer.helper)
            "        return 1\n"
            "    def middle():\n"
            "        def leaf():\n"
            "            return helper()\n"
            "        return leaf()\n"
            "    return middle()\n"
            "\n"
            "def helper():\n"  # top-level helper (in local_symbols)
            "    return 99\n"
        )
        res, names, ids = _analyze(tmp_path, src)
        leaf_id = ids["outer.middle.leaf"]
        top_helper_id = ids["helper"]
        grandparent_helper_id = ids["outer.helper"]
        leaf_calls = [
            e for e in res.edges
            if e.edge_type == "calls" and e.src == leaf_id
            and names.get(e.dst, ("", ""))[0].endswith("helper")
        ]
        assert leaf_calls, "leaf's helper() call did not resolve at all"
        assert all(e.dst == top_helper_id for e in leaf_calls), (
            "leaf's helper() re-targeted to the grandparent helper instead of "
            "the top-level helper (step-4 is not last-resort)"
        )
        assert all(e.dst != grandparent_helper_id for e in leaf_calls)


class TestLocalShadowGuard:
    """LEGB "L": a local binding (param/assignment/`global`) must shadow a
    same-named enclosing def — step-4 must NOT emit a resolved edge to the def
    (the adversarial-review defect: a confidently-WRONG is_resolved=True edge)."""

    def test_caller_param_shadows_enclosing_helper(self, tmp_path: Path) -> None:
        src = (
            "def outer():\n"
            "    def helper():\n"
            "        return 1\n"
            "    def leaf(helper):\n"  # parameter shadows outer.helper
            "        return helper()\n"
            "    return leaf\n"
        )
        res, names, ids = _analyze(tmp_path, src)
        wrong = [
            e for e in res.edges
            if e.edge_type == "calls" and e.src == ids["outer.leaf"]
            and e.dst == ids["outer.helper"]
        ]
        assert not wrong, (
            "the parameter `helper` must shadow the enclosing def — step-4 "
            "wrongly resolved the call to outer.helper"
        )

    def test_global_declaration_shadows_enclosing_helper(self, tmp_path: Path) -> None:
        src = (
            "def outer():\n"
            "    def helper():\n"
            "        return 1\n"
            "    def leaf():\n"
            "        global helper\n"  # forces module scope, bypasses the enclosing def
            "        return helper()\n"
            "    return leaf\n"
        )
        res, names, ids = _analyze(tmp_path, src)
        wrong = [
            e for e in res.edges
            if e.edge_type == "calls" and e.src == ids["outer.leaf"]
            and e.dst == ids["outer.helper"]
        ]
        assert not wrong, (
            "`global helper` must bypass the enclosing def — step-4 wrongly "
            "resolved the call to outer.helper"
        )

    def test_nonlocal_does_not_shadow_enclosing_helper(self, tmp_path: Path) -> None:
        # `nonlocal helper` REFERS to the enclosing def binding, so the call
        # still resolves to it — nonlocal must NOT be treated as a local shadow.
        src = (
            "def outer():\n"
            "    def helper():\n"
            "        return 1\n"
            "    def leaf():\n"
            "        nonlocal helper\n"
            "        return helper()\n"
            "    return leaf\n"
        )
        res, names, ids = _analyze(tmp_path, src)
        matches = [
            e for e in res.edges
            if e.edge_type == "calls" and e.src == ids["outer.leaf"]
            and e.dst == ids["outer.helper"]
        ]
        assert matches, (
            "`nonlocal helper` refers to the enclosing def; the call should "
            "still resolve to outer.helper"
        )


class TestCollectScopeLocalNames:
    """Unit coverage for the LEGB "L" shadow-set collector."""

    def test_all_binding_kinds_collected(self) -> None:
        node = ast.parse(
            "def f(pos, /, normal, *args, kwonly, **kw):\n"
            "    x = 1\n"
            "    import os\n"
            "    from sys import path as sp\n"
            "    global g\n"
            "    for i in [1]:\n"
            "        pass\n"
            "    def nested():\n"
            "        inner_only = 5\n"
            "        return inner_only\n"
            "    return nested\n"
        ).body[0]
        got = _collect_scope_local_names(node)
        assert got == frozenset(
            {"pos", "normal", "args", "kwonly", "kw", "x", "os", "sp", "g", "i"}
        )
        assert "inner_only" not in got  # nested-scope binding excluded
        assert "nested" not in got  # def name is a NestedDef binding, not a local

    def test_nonlocal_excluded_even_when_assigned(self) -> None:
        outer = ast.parse(
            "def outer():\n"
            "    y = 0\n"
            "    def f():\n"
            "        nonlocal y\n"
            "        y = 3\n"
            "        return y\n"
            "    return f\n"
        ).body[0]
        inner = outer.body[1]  # the `def f`
        assert "y" not in _collect_scope_local_names(inner)
