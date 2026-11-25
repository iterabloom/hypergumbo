# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each Java call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture line per route:

* a bare ``helper()`` and ``this.helper()`` answered from the enclosing
  class's own members are ``own_type``;
* ``Util.make()`` looked up under the class the call site names is
  ``qualified``;
* ``new Helper()`` and ``Helper::new`` take the class resolver's own value: a
  name looked up across the repository (the file-import check refuses a
  mismatch, it does not scope the search), ``name``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/java.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
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
    return analyze_java(root).edges


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
    assert _mark(edges, 11, "Util.make", "ast_call_static") == "qualified"
    assert _mark(edges, 12, "Helper", "ast_new", "instantiates") == "name"
    assert _mark(edges, 13, "Helper", "constructor_reference", "instantiates") == "name"
