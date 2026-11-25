# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each Groovy call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture line per route:

* a bare ``helper()`` found under the enclosing class is ``own_type``;
* ``s.run()`` on a typed local is ``receiver_type``;
* ``Main.helper()`` found under the owner the call site names is
  ``qualified``;
* a bare call found in the file's bare-name table is ``same_file``; one the
  resolver found by name across the repository is ``name``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/groovy.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.groovy import analyze_groovy

_FILES = {
    "src/Svc.groovy": "class Svc {\n    def run() {}\n}\n",
    "src/far.groovy": "def farTop() {}\n",
    "src/Main.groovy": """class Main {
    def helper() {}
    def go() {
        helper()
        Svc s = new Svc()
        s.run()
        Main.helper()
        top()
        farTop()
    }
}

def top() {}
""",
}


def _edges(root: Path) -> list[Edge]:
    for rel, text in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return analyze_groovy(root).edges


def _mark(edges: list[Edge], line: int, dst_name: str) -> str:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.is_resolved and e.line == line
        and e.dst.split(":")[-2] == dst_name
    ]
    assert len(hits) == 1, [(e.line, e.dst) for e in edges]
    return hits[0].meta["resolved_by"]


def test_every_route_of_one_file(tmp_path: Path) -> None:
    edges = _edges(tmp_path)
    assert _mark(edges, 4, "Main.helper") == "own_type"
    assert _mark(edges, 6, "Svc.run") == "receiver_type"
    assert _mark(edges, 7, "Main.helper") == "qualified"
    assert _mark(edges, 8, "top") == "same_file"
    assert _mark(edges, 9, "farTop") == "name"
