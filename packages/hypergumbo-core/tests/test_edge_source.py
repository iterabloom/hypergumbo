# SPDX-License-Identifier: AGPL-3.0-or-later
"""``analyze.edge_source``: every edge src is an emitted symbol or the file, and a
call in a definition with no symbol is drawn from the nearest enclosing one, SAID.

The analyzer-level uses (pascal, cuda, c, cpp) are tested in their own packages
on the production path. These pin the three helpers' contracts on a real C tree.
"""
from __future__ import annotations

import tree_sitter_language_pack as tlp

from hypergumbo_core.analyze.base import file_anchor_symbol, make_file_id, symbols_at
from hypergumbo_core.analyze.edge_source import (
    SRC_STANDS_IN_FOR,
    UNNAMED_DEFINITION,
    anchor_in_definitions,
    mark_stand_in,
    unemitted_edge_sources,
)
from hypergumbo_core.axis_meta_keys import all_meta_key_names, per_call_site_keys
from hypergumbo_core.ir import Edge, Span, Symbol

_DEFS = frozenset({"function_definition"})


def _sym(name: str, line: int, col: int = 0, end_line: int | None = None) -> Symbol:
    return Symbol(
        id=f"c:a.c:{line}-{end_line or line}:{name}:function", name=name,
        kind="function", language="c", path="a.c",
        span=Span(start_line=line, end_line=end_line or line, start_col=col, end_col=1),
        origin="c-v1",
    )


def _edge(src: str, dst: str = "c:external:0-0:g:unresolved") -> Edge:
    return Edge.create(src=src, dst=dst, edge_type="calls", line=1, origin="c-v1")


def test_unemitted_edge_sources_keeps_symbol_and_file_srcs_and_reports_the_rest() -> None:
    sym = _sym("f", 1)
    ok = _edge(sym.id)
    file_src = _edge(make_file_id("c", "a.c"))
    dangling = _edge("c:a.c:4-4:Inner:function")
    assert unemitted_edge_sources([sym], [ok, file_src, dangling]) == [dangling]


def _calls(code: str) -> tuple[list, dict[int, object]]:
    tree = tlp.get_parser("c").parse(code.encode())
    calls, defs = [], {}
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "call_expression":
            calls.append(node)
        elif node.type == "function_definition":
            defs[node.start_point[0] + 1] = node
        stack.extend(reversed(node.children))
    return calls, defs


def test_anchor_in_definitions_by_shape() -> None:
    """Named definition: itself. A macro-named one inside a named one: the outer,
    standing in. A macro-named one at top level: the fallback, standing in. No
    definition: the top-level answer, not standing in."""
    calls, defs = _calls("""\
int f(void) {
  g(1);
  int PFX(inner)(void) { g(2); return 0; }
  return 0;
}
TEST_BEGIN(t) {
  g(3);
}
int x = g(4);
""")
    f = _sym("f", 1, end_line=5)
    index = symbols_at([f])
    anchor = file_anchor_symbol("c", "a.c", "c-v1", "run")
    got = {
        c.start_point[0] + 1: anchor_in_definitions(
            c, index, _DEFS, top_level=None, fallback=anchor,
        )
        for c in calls
    }
    assert got == {2: (f, False), 3: (f, True), 7: (anchor, True), 9: (None, False)}
    assert set(defs) == {1, 3, 6}  # reach: all three definitions parsed as such


def test_mark_stand_in_stamps_only_the_edges_from_the_stand_in() -> None:
    edges = [_edge("c:a.c:1-1:f:function"), _edge("anchor"), _edge("other"), _edge("anchor")]
    mark_stand_in(edges, 1, "anchor")
    assert [(e.meta or {}).get(SRC_STANDS_IN_FOR) for e in edges] == [
        None, UNNAMED_DEFINITION, None, UNNAMED_DEFINITION,
    ]


def test_the_marker_is_a_registered_per_call_site_key() -> None:
    """Two sites collapsed into one edge (a stand-in call and a real file-scope
    call to one callee) may disagree, so the key collapses per site."""
    assert SRC_STANDS_IN_FOR in all_meta_key_names()
    assert SRC_STANDS_IN_FOR in per_call_site_keys()
