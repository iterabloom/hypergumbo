# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each Scala call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture line per route:

* a bare ``helper()`` found in the file's table, a method of the enclosing
  class, is ``own_type``; a top-level def of the same file is ``same_file``;
* ``s.run()`` on a typed local is ``receiver_type``;
* ``Factory.make()`` on a project object the call site names is
  ``qualified``;
* a bare call the resolver found by name across the repository is ``name``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/scala.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.scala import analyze_scala

_FILES = {
    "Helper.scala": "def greet(): Int = 1\n",
    "Svc.scala": (
        "class Svc {\n  def run(): Unit = {}\n}\n\n"
        "object Factory {\n  def make(): Int = 1\n}\n"
    ),
    "Main.scala": """def local(): Int = 2

class Main {
  def helper(): Unit = {}
  def go(): Unit = {
    helper()
    local()
    greet()
    val s: Svc = new Svc()
    s.run()
    Factory.make()
  }
}
""",
}


def _edges(root: Path) -> list[Edge]:
    for rel, text in _FILES.items():
        (root / rel).write_text(text)
    return analyze_scala(root).edges


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
    assert _mark(edges, 6, "Main.helper") == "own_type"
    assert _mark(edges, 7, "local") == "same_file"
    assert _mark(edges, 8, "greet") == "name"
    assert _mark(edges, 10, "Svc.run") == "receiver_type"
    assert _mark(edges, 11, "Factory.make") == "qualified"
