# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hirol: every route by which swift.py binds a ``calls`` edge states its mark.

``Edge.meta["resolved_by"]`` names what scoped the choice of the callee
(:mod:`hypergumbo_core.resolved_by`). One fixture tree holds one caller per
route; each test finds that caller's edge (it must exist) and pins the value:

- ``Widget.init(...)`` chosen within the type the call names: ``qualified``;
  ``self.init`` / ``super.init``: ``own_type``;
- ``w.draw()`` on a typed receiver (this file's or another's): ``receiver_type``;
  ``Tool.helper()`` on a type the call names: ``qualified``;
- a bare ``Widget(...)`` the call's position sees: ``lexical_scope``; a bare
  free function in the file's table: ``same_file``;
- a bare name through the resolver: ``name``, or ``own_type`` when it is a
  method of the enclosing type (Swift's implicit ``self``).
"""

from __future__ import annotations

import pytest

from hypergumbo_lang_mainstream.swift import analyze_swift

_FILES = {
    'Sources/a.swift': (
        'class Base {\n'
        '    init() {}\n'
        '}\n'
        '\n'
        'class Widget: Base {\n'
        '    init(size: Int) { super.init() }\n'
        '    convenience init(label: String) { self.init(size: 1) }\n'
        '    func draw() {}\n'
        '    func redraw() { draw() }\n'
        '    func farOwn() { polish() }\n'
        '}\n'
        '\n'
        'struct Tool {\n'
        '    static func helper() {}\n'
        '}\n'
        '\n'
        'func helper() {}\n'
        '\n'
        'func callInit() { _ = Widget.init(size: 2) }\n'
        'func callTypedLocal() { let w: Widget = Widget(size: 1); w.draw() }\n'
        'func callTypedFar() { let g: Gadget = Gadget(); g.spin() }\n'
        'func callStatic() { Tool.helper() }\n'
        'func callBare() { helper() }\n'
        'func callConstruct() { _ = Widget(size: 3) }\n'
        'func callFar() { farFunction() }\n'
    ),
    'Sources/b.swift': (
        'class Gadget {\n'
        '    func spin() {}\n'
        '}\n'
        'extension Widget {\n'
        '    func polish() {}\n'
        '}\n'
        'func farFunction() {}\n'
    ),
}


@pytest.fixture(scope="module")
def analysis(tmp_path_factory: pytest.TempPathFactory):
    repo = tmp_path_factory.mktemp("swift_mark") / "proj"
    for rel, text in _FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return analyze_swift(repo)


@pytest.mark.parametrize(("caller", "callee", "mark"), [
    ("callInit", "Widget.init", "qualified"),
    ("Widget.init", "Base.init", "own_type"),
    ("Widget.init", "Widget.init", "own_type"),
    ("callTypedLocal", "Widget.draw", "receiver_type"),
    ("callTypedFar", "Gadget.spin", "receiver_type"),
    ("callStatic", "Tool.helper", "qualified"),
    ("callConstruct", "Widget.init", "lexical_scope"),
    ("callBare", "helper", "same_file"),
    ("callFar", "farFunction", "name"),
    ("callTypedFar", "Gadget", "name"),
    ("Widget.redraw", "Widget.draw", "own_type"),
    ("Widget.farOwn", "Widget.polish", "own_type"),
])
def test_route_states_its_mark(analysis, caller, callee, mark) -> None:
    by_id = {s.id: s for s in analysis.symbols}
    found = [
        e for e in analysis.edges
        if e.edge_type == "calls" and e.is_resolved
        and e.src in by_id and by_id[e.src].name == caller
        and e.dst in by_id and by_id[e.dst].name == callee
    ]
    assert len(found) == 1, (caller, callee, [
        e.dst for e in analysis.edges
        if e.edge_type == "calls" and e.src in by_id and by_id[e.src].name == caller
    ])
    assert found[0].meta["resolved_by"] == mark
