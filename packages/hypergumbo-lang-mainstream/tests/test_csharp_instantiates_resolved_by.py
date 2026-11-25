# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each C# ``new`` route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose an ``instantiates`` edge's target. One fixture line per
route of the three creation sites:

* a ``Type.Type`` constructor in the file's own table, and a class with no
  explicit constructor found there, are ``same_file``;
* a constructor picked by argument count among every same-keyed
  constructor of the run is ``name``;
* a class with no constructor found by the resolver (the ``using`` hint
  applies only among candidates) is ``name``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/csharp.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.csharp import analyze_csharp

_FILES = {
    "Svc.cs": (
        "namespace B {\n  public class Svc {\n    public Svc() {}\n"
        "    public Svc(int a) {}\n  }\n  public class Plain {}\n"
        "  public class Single { public Single() {} }\n}\n"
    ),
    "Main.cs": """namespace A {
  using B;
  public class Local {}
  public class Own { public Own() {} }
  public class Main {
    public void Go() {
      var a = new Svc(1);
      var b = new Local();
      var c = new Plain();
      var d = new Own();
      var e = new Single();
    }
  }
}
""",
}


def _edges(root: Path) -> list[Edge]:
    for rel, text in _FILES.items():
        (root / rel).write_text(text)
    return analyze_csharp(root).edges


def _mark(edges: list[Edge], line: int, dst_name: str) -> str:
    hits = [
        e for e in edges
        if e.edge_type == "instantiates" and e.is_resolved and e.line == line
        and e.dst.split(":")[-2] == dst_name
    ]
    assert len(hits) == 1, [(e.line, e.dst) for e in edges]
    return hits[0].meta["resolved_by"]


def test_every_route_of_one_file(tmp_path: Path) -> None:
    edges = _edges(tmp_path)
    assert _mark(edges, 7, "Svc.Svc") == "name"
    assert _mark(edges, 8, "Local") == "same_file"
    assert _mark(edges, 9, "Plain") == "name"
    assert _mark(edges, 10, "Own.Own") == "same_file"
    assert _mark(edges, 11, "Single.Single") == "name"
