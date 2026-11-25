# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each Kotlin call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture line per route:

* ``super.m()``, ``this.m()`` and a bare call found under the enclosing class
  are ``own_type``;
* ``this.prop.m()`` on a typed property and a typed local's method are
  ``receiver_type``;
* ``Type.m()`` and an inline-qualified ``pkg.Type.m()`` are ``qualified``;
* a bare call the file imports is ``import``; one found in the file's own
  bare-name table is ``same_file``; one the resolver found by name alone is
  ``name``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

_MAIN = """package a

import b.Svc
import b.util.topFn

open class Base {
    open fun greet() {}
}

class Main(private val svc: Svc) : Base() {
    fun helper() {}
    override fun greet() {
        super.greet()
        this.helper()
        helper()
        this.svc.run()
        val s: Svc = Svc()
        s.run()
        Svc.make()
        b.Svc.make()
        topFn()
        local()
        far()
    }
}

fun local() {}
"""

_FILES = {
    "src/a/Main.kt": _MAIN,
    "src/b/Svc.kt": (
        "package b\n\nclass Svc {\n    fun run() {}\n"
        "    companion object { fun make() {} }\n}\n"
    ),
    "src/b/util/Util.kt": "package b.util\n\nfun topFn() {}\n",
    "src/c/Far.kt": "package c\n\nfun far() {}\n",
}


def _edges(root: Path) -> list[Edge]:
    for rel, text in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return analyze_kotlin(root).edges


def _mark(edges: list[Edge], line: int, dst_name: str, evidence: str) -> str:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.is_resolved and e.line == line
        and e.dst.split(":")[-2] == dst_name and e.evidence_type == evidence
    ]
    assert len(hits) == 1, [(e.line, e.dst, e.evidence_type) for e in edges]
    return hits[0].meta["resolved_by"]


def test_every_route_of_one_file(tmp_path: Path) -> None:
    edges = _edges(tmp_path)
    assert _mark(edges, 13, "Base.greet", "ast_call_this") == "own_type"
    assert _mark(edges, 14, "Main.helper", "ast_call_this") == "own_type"
    assert _mark(edges, 15, "Main.helper", "ast_call") == "own_type"
    assert _mark(edges, 16, "Svc.run", "ast_call_this_property") == "receiver_type"
    assert _mark(edges, 18, "Svc.run", "ast_call_type_inferred") == "receiver_type"
    assert _mark(edges, 19, "Svc.make", "ast_call_static") == "qualified"
    assert _mark(edges, 20, "Svc.make", "ast_call_static") == "qualified"
    assert _mark(edges, 21, "topFn", "ast_call") == "import"
    assert _mark(edges, 22, "local", "ast_call") == "same_file"
    assert _mark(edges, 23, "far", "ast_call") == "name"
