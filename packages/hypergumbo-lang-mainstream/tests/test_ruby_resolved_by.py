# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each Ruby call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture line per route:

* ``self.helper``, and a receiverless ``helper`` / ``helper()`` that finds a
  method of the caller's own class, are ``own_type``;
* a receiverless send from a top-level method to a top-level ``def`` of the
  same file is ``same_file``;
* since ADR-0064 (batch 5b) nothing is looked up by its name across the
  repository, nor in a table of the file's other names: ``top_helper`` sent
  from a method of ``Main`` and ``far_helper`` (another file's only
  top-level ``def``) stay unresolved, reported with ``enclosing_class``;
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

def top_caller
  top_helper
  top_helper()
  far_helper
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
    for line, name in ((14, "top_helper"), (15, "top_helper"), (16, "far_helper"), (17, "far_helper")):
        (stub,) = [e for e in edges if e.edge_type == "calls" and e.line == line]
        assert (stub.is_resolved, stub.dst) == (False, f"ruby:Main:0-0:{name}:unresolved")
        assert stub.meta["enclosing_class"] == "Main"
    assert _mark(edges, 25, "top_helper") == "same_file"
    assert _mark(edges, 26, "top_helper") == "same_file"
    (far,) = [e for e in edges if e.edge_type == "calls" and e.line == 27]
    assert (far.is_resolved, far.dst) == (False, "ruby:external:0-0:far_helper:unresolved")
    assert _mark(edges, 18, "Svc.make") == "qualified"
    assert _mark(edges, 19, "Svc#initialize", "instantiates") == "qualified"
    assert _mark(edges, 20, "Svc#run") == "receiver_type"
