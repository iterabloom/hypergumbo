# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each Java call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture line per route:

* a bare ``helper()`` and ``this.helper()`` answered from the enclosing
  class's own members are ``own_type`` (the analyzer's bind);
* ``Util.make()``, ``new Helper()`` and ``Helper::new`` are reported by the
  analyzer and bound by the shared pass through the caller's import table
  (ADR-0064), which marks the rule that scoped them: ``Util`` names its owner
  (``qualified``), ``Helper`` is a type of the caller's own package
  (``same_module``).
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/java.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_core.linkers.inherited_calls import link_inherited_calls
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_lang_mainstream.java import analyze_java

_FILES = {
    "src/a/Main.java": """package a;

import b.Util;
import java.util.function.Supplier;

public class Main {
    void helper() {}
    void run() {
        helper();
        this.helper();
        Util.make();
        Helper h = new Helper();
        Supplier<Helper> s = Helper::new;
    }
}
""",
    "src/a/Helper.java": "package a;\n\npublic class Helper {\n}\n",
    "src/b/Util.java": (
        "package b;\n\npublic class Util {\n    public static void make() {}\n}\n"
    ),
}


def _edges(root: Path) -> list[Edge]:
    for rel, text in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    result = analyze_java(root)
    linked = link_inherited_calls(LinkerContext(
        repo_root=root, symbols=result.symbols, edges=result.edges,
        import_scopes=result.import_scopes,
    ))
    return [*result.edges, *linked.edges]


def _mark(
    edges: list[Edge], line: int, dst_name: str, evidence: str,
    edge_type: str = "calls",
) -> str:
    hits = [
        e for e in edges
        if e.edge_type == edge_type and e.is_resolved and e.line == line
        and e.dst.split(":")[-2] == dst_name and e.evidence_type == evidence
    ]
    assert len(hits) == 1, [(e.line, e.dst, e.evidence_type) for e in edges]
    return hits[0].meta["resolved_by"]


def test_every_route_of_one_file(tmp_path: Path) -> None:
    edges = _edges(tmp_path)
    assert _mark(edges, 9, "Main.helper", "ast_call_direct") == "own_type"
    assert _mark(edges, 10, "Main.helper", "ast_call_direct") == "own_type"
    assert _mark(edges, 11, "Util.make", "ast_call_type_inferred") == "qualified"
    assert _mark(edges, 12, "Helper", "ast_new", "instantiates") == "same_module"
    # ``Helper::new`` constructs the same type from the same caller: the shared
    # pass keeps one edge per caller and type, so its stub stays as reported.
    (reference,) = [e for e in edges if e.edge_type == "instantiates" and e.line == 13]
    assert not reference.is_resolved and reference.meta["callee_name"] == "Helper"
