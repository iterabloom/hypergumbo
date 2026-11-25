# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each Ruby call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture line per route:

* ``self.helper``, and a receiverless ``helper`` / ``helper()`` that finds a
  method of the caller's own class, are ``own_type``;
* a receiverless send found in the file's bare-name table is ``same_file``;
  one the resolver found by name across the repository is ``name``;
* ``Svc.make`` and ``Svc.new`` (bound to ``Svc#initialize``) on the constant
  the call site writes are ``qualified``;
* a local assigned from ``Svc.new`` and then sent ``run`` is
  ``receiver_type``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.ruby import analyze_ruby

_MAIN = """require_relative 'svc'

def top_helper
end

class Main
  def helper
  end

  def go
    self.helper
    helper
    helper()
    top_helper
    top_helper()
    far_helper
    far_helper()
    Svc.make
    s = Svc.new
    s.run
  end
end
"""

_FILES = {
    "lib/svc.rb": (
        "class Svc\n  def initialize\n  end\n\n  def run\n  end\n\n"
        "  def self.make\n  end\nend\n"
    ),
    "lib/far.rb": "def far_helper\nend\n",
    "lib/main.rb": _MAIN,
}


def _edges(root: Path) -> list[Edge]:
    for rel, text in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return analyze_ruby(root).edges


def _mark(
    edges: list[Edge], line: int, dst_name: str, edge_type: str = "calls",
) -> str:
    hits = [
        e for e in edges
        if e.edge_type == edge_type and e.is_resolved and e.line == line
        and e.dst.split(":")[-2] == dst_name
    ]
    assert len(hits) == 1, [(e.line, e.dst) for e in edges]
    return hits[0].meta["resolved_by"]


def test_every_route_of_one_file(tmp_path: Path) -> None:
    edges = _edges(tmp_path)
    assert _mark(edges, 11, "Main#helper") == "own_type"
    assert _mark(edges, 12, "Main#helper") == "own_type"
    assert _mark(edges, 13, "Main#helper") == "own_type"
    assert _mark(edges, 14, "top_helper") == "same_file"
    assert _mark(edges, 15, "top_helper") == "same_file"
    assert _mark(edges, 16, "far_helper") == "name"
    assert _mark(edges, 17, "far_helper") == "name"
    assert _mark(edges, 18, "Svc.make") == "qualified"
    assert _mark(edges, 19, "Svc#initialize", "instantiates") == "qualified"
    assert _mark(edges, 20, "Svc#run") == "receiver_type"
