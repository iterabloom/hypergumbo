# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on F#'s call-family edges (audit 0030, WI-hirol).

One fixture per route of the two creation sites:

``member_edge`` (the target ``_FsharpCallResolver`` decides):

* ``s.Scale`` on a parameter annotated ``Shape`` and ``(Shape 1.0).Scale``
  on a constructed expression: ``receiver_type``;
* ``this.Scale`` on the member's self identifier, and the read
  ``this.Area``: ``own_type``;
* ``Geo.Shape.Make`` through the type the call site names: ``qualified``
  (``Shape.Make``, a registry key as written, is the application site's
  ``qualified`` route below);
* a ``let``-bound function seen from inside its scope: ``lexical_scope``;
* a construction ``Shape(1.0)`` (the type found through the site's
  namespaces and ``open``s): ``same_module``, a closest fit (see the report
  of WI-hirol's lang-common package).

The application site: a dotted ``Ops.twice`` or ``Shape.Make`` found under the name the call
site writes is ``qualified``; a bare name nothing in scope binds falls to
the run-wide resolver and takes the lookup's value (``name``).
"""
from pathlib import Path

from hypergumbo_lang_common.fsharp import analyze_fsharp


def _edge(result, src: str, dst: str, edge_type: str = "calls"):
    by_id = {s.id: s for s in result.symbols}
    found = [
        e for e in result.edges
        if e.edge_type == edge_type and e.src in by_id and e.dst in by_id
        and by_id[e.src].name == src and by_id[e.dst].name == dst
    ]
    assert len(found) == 1, f"{edge_type} {src} -> {dst}: {len(found)} edge(s)"
    return found[0]


SHAPE = """module Geo

type Shape(r: float) =
    member this.Area = r * r
    member this.Scale(k: float) = r * k
    member this.Grow() = this.Scale 2.0
    member this.Twice() = this.Area * 2.0
    static member Make (r: float) : Shape = Shape(r)

module Ops =
    let twice x = x * 2
    let run (s: Shape) =
        let local y = y + 1
        let a = s.Scale 1.0
        let d = local 2
        twice d

let fromNew () = (Shape 1.0).Scale 2.0
let build () = Shape.Make 1.0
let viaNs () = Geo.Shape.Make 1.0
let make () = Shape(3.0)
let outside () = Ops.twice 3
"""


def test_fsharp_routes(tmp_path: Path) -> None:
    (tmp_path / "Geo.fs").write_text(SHAPE)
    (tmp_path / "Other.fs").write_text("module Other\n\nlet go () = helper 1\n")
    (tmp_path / "Lib.fs").write_text("module Lib\n\nlet helper x = x\n")
    result = analyze_fsharp(tmp_path)
    assert _edge(result, "Ops.run", "Shape.Scale").meta["resolved_by"] == "receiver_type"
    assert _edge(result, "fromNew", "Shape.Scale").meta["resolved_by"] == "receiver_type"
    assert _edge(result, "Shape.Grow", "Shape.Scale").meta["resolved_by"] == "own_type"
    assert _edge(result, "Shape.Twice", "Shape.Area").meta["resolved_by"] == "own_type"
    assert _edge(result, "build", "Shape.Make").meta["resolved_by"] == "qualified"
    assert _edge(result, "viaNs", "Shape.Make").meta["resolved_by"] == "qualified"
    assert _edge(result, "Ops.run", "local").meta["resolved_by"] == "lexical_scope"
    made = _edge(result, "make", "Shape", edge_type="instantiates")
    assert made.meta["resolved_by"] == "same_module"
    assert _edge(result, "outside", "Ops.twice").meta["resolved_by"] == "qualified"
    assert _edge(result, "go", "helper").meta["resolved_by"] == "name"
