# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hirol: every route by which objc.py binds a ``message_send`` edge states its mark.

``Edge.meta["resolved_by"]`` names what scoped the choice of the callee
(:mod:`hypergumbo_core.resolved_by`). Each test finds the edge first (it must
exist) and pins the value:

- a selector found in the caller's file table (one entry per selector,
  whatever the receiver): ``same_file``;
- a selector looked up among every class of the repository: ``name``, also
  when the hit is the enclosing class's method on a receiver other than
  ``self``; ``[self m]`` bound to the enclosing class's ``m``: ``own_type``.
"""

from __future__ import annotations

import pytest

from hypergumbo_lang_mainstream.objc import analyze_objc

_FILES = {
    'src/Foo.h': (
        '@interface Foo : NSObject\n'
        '@end\n'
        '@interface Bar : NSObject\n'
        '@end\n'
    ),
    'src/Foo.m': (
        '#import "Foo.h"\n'
        '\n'
        '@implementation Foo\n'
        '- (void)helper {\n'
        '}\n'
        '- (void)callLocal {\n'
        '    [self helper];\n'
        '}\n'
        '- (void)callOwnFar {\n'
        '    [self polish];\n'
        '}\n'
        '- (void)callOtherReceiver:(Foo *)f {\n'
        '    [f polish];\n'
        '}\n'
        '@end\n'
        '\n'
        'void cFunction(Bar *b) {\n'
        '    [b zap];\n'
        '}\n'
    ),
    'src/Foo+Cat.m': (
        '#import "Foo.h"\n'
        '\n'
        '@implementation Foo (Cat)\n'
        '- (void)polish {\n'
        '}\n'
        '@end\n'
        '\n'
        '@implementation Bar\n'
        '- (void)zap {\n'
        '}\n'
        '@end\n'
    ),
}


@pytest.fixture(scope="module")
def analysis(tmp_path_factory: pytest.TempPathFactory):
    repo = tmp_path_factory.mktemp("objc_mark") / "proj"
    for rel, text in _FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return analyze_objc(repo)


@pytest.mark.parametrize(("caller", "callee", "mark"), [
    ("Foo.callLocal", "Foo.helper", "same_file"),
    ("Foo.callOwnFar", "Foo.polish", "own_type"),
    ("Foo.callOtherReceiver:", "Foo.polish", "name"),
])
def test_route_states_its_mark(analysis, caller, callee, mark) -> None:
    by_id = {s.id: s for s in analysis.symbols}
    found = [
        e for e in analysis.edges
        if e.edge_type == "calls" and e.is_resolved
        and e.evidence_type == "message_send"
        and e.src in by_id and by_id[e.src].name == caller
        and e.dst in by_id and by_id[e.dst].name == callee
    ]
    assert len(found) == 1, (caller, callee)
    assert found[0].meta["resolved_by"] == mark
