# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hirol: every route by which cpp.py binds a call-family edge states its mark.

``Edge.meta["resolved_by"]`` names what scoped the choice of the target
(:mod:`hypergumbo_core.resolved_by`). One fixture tree holds one caller per
route; each test finds that caller's edge (it must exist) and pins the value:

- a bare call found in the file's table: ``same_file``; under the enclosing
  class (``init()`` / ``this->init()``, in this file or through the resolver):
  ``own_type``; ``Tool::helper()`` matched by the owner it names:
  ``qualified``;
- a receiver whose declaration names its class: ``receiver_type``;
- a name looked up repository-wide (a callee, a callback argument, a field
  chain's method found by its name alone, a class in the single-valued global
  table): ``name``;
- ``instantiates``: what chose the class (``std::make_unique<Box::Nested>``
  names its owner: ``qualified``), and the constructor chosen within that
  class carries the same value.
"""

from __future__ import annotations

import pytest

from hypergumbo_lang_mainstream.cpp import analyze_cpp

_FILES = {
    'src/a.cpp': (
        '#include <memory>\n'
        '#include "far.h"\n'
        '\n'
        'namespace ns {\n'
        'void helper_ns() {}\n'
        '}\n'
        '\n'
        'class Widget {\n'
        'public:\n'
        '    Widget() {}\n'
        '    Widget(int x) {}\n'
        '    void draw() {}\n'
        '};\n'
        '\n'
        'class Inner {\n'
        'public:\n'
        '    void run() {}\n'
        '};\n'
        '\n'
        'class Outer {\n'
        'public:\n'
        '    Inner inner;\n'
        '    void go() { this->inner.run(); }\n'
        '};\n'
        '\n'
        'class Parser {\n'
        'public:\n'
        '    void init() {}\n'
        '    void bare_own() { init(); }\n'
        '    void this_own() { this->init(); }\n'
        '    void bare_own_far() { setup_far(); }\n'
        '};\n'
        '\n'
        'void free_local() {}\n'
        'void call_free_local() { free_local(); }\n'
        'void call_ns_local() { ns::helper_ns(); }\n'
        'void call_far() { far_function(); }\n'
        'void call_ns_far() { farns::far_ns_fn(); }\n'
        'void call_declared() { Widget w; w.draw(); }\n'
        'void call_field_chain(Outer o) { o.inner.run(); }\n'
        'void take(void (*cb)()) {}\n'
        'void call_cb() { take(far_function); }\n'
        'void new_local() { Widget* w = new Widget(1); }\n'
        'void new_far() { Far* f = new Far(); }\n'
        'void make_qualified() { auto t = std::make_unique<farns::Thing>(); }\n'
        'void stack_local() { Widget w(1); }\n'
        'void stack_far() { Far f; }\n'
        'void literal_local() { auto w = Widget{1}; }\n'
        'class Box {\n'
        'public:\n'
        '    class Nested;\n'
        '};\n'
        'class Box::Nested {\n'
        'public:\n'
        '    Nested() {}\n'
        '};\n'
        'void make_nested() { auto n = std::make_unique<Box::Nested>(); }\n'
        'void make_plain() { auto n = std::make_unique<Inner>(); }\n'
        'class Tool {\n'
        'public:\n'
        '    static void helper() {}\n'
        '};\n'
        'void call_static_local() { Tool::helper(); }\n'
        'void call_static_far() { Gadget::reset(); }\n'
    ),
    'src/far.cpp': (
        '#include "far.h"\n'
        'void far_function() {}\n'
        'namespace farns {\n'
        'void far_ns_fn() {}\n'
        'class Thing { public: Thing() {} };\n'
        '}\n'
        'class Far { public: Far() {} };\n'
        'void Parser::setup_far() {}\n'
        'class Gadget { public: static void reset() {} };\n'
        'class Other { public: static void reset() {} void setup_far() {} };\n'
    ),
    'src/far.h': (
        'void far_function();\n'
    ),
}


@pytest.fixture(scope="module")
def analysis(tmp_path_factory: pytest.TempPathFactory):
    repo = tmp_path_factory.mktemp("cpp_mark") / "proj"
    for rel, text in _FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return analyze_cpp(repo)


def _edge(analysis, caller: str, callee: str, edge_type: str, evidence: str):
    """The one resolved edge of ``edge_type`` from ``caller`` to ``callee``."""
    by_id = {s.id: s for s in analysis.symbols}
    found = [
        e for e in analysis.edges
        if e.edge_type == edge_type and e.is_resolved
        and e.evidence_type == evidence
        and e.src in by_id and by_id[e.src].name == caller
        and e.dst in by_id and by_id[e.dst].name == callee
    ]
    assert len(found) == 1, (caller, callee, [
        (e.edge_type, e.dst, e.evidence_type) for e in analysis.edges
        if e.src in by_id and by_id[e.src].name == caller
    ])
    return found[0]


@pytest.mark.parametrize(("caller", "callee", "evidence", "mark"), [
    ("call_free_local", "free_local", "ast_call", "same_file"),
    ("call_ns_local", "helper_ns", "ast_call", "same_file"),
    ("Parser::bare_own", "Parser::init", "ast_call", "own_type"),
    ("Parser::this_own", "Parser::init", "ast_call", "own_type"),
    ("Parser::bare_own_far", "Parser::setup_far", "ast_call", "own_type"),
    ("call_static_local", "Tool::helper", "ast_call", "qualified"),
    ("call_static_far", "Gadget::reset", "ast_call", "qualified"),
    ("call_far", "far_function", "ast_call", "name"),
    ("call_ns_far", "far_ns_fn", "ast_call", "name"),
    ("call_declared", "Widget::draw", "ast_call", "receiver_type"),
    ("Outer::go", "Inner::run", "ast_call", "name"),
    ("call_cb", "far_function", "function_pointer_arg", "name"),
])
def test_call_route_states_its_mark(analysis, caller, callee, evidence, mark) -> None:
    edge = _edge(analysis, caller, callee, "calls", evidence)
    assert edge.meta["resolved_by"] == mark


@pytest.mark.parametrize(("caller", "callee", "evidence", "mark"), [
    ("new_local", "Widget::Widget", "ast_new", "same_file"),
    ("new_far", "Far", "ast_new", "name"),
    ("new_far", "Far::Far", "ast_new", "name"),
    ("make_plain", "Inner", "ast_new", "same_file"),
    ("make_nested", "Box::Nested", "ast_new", "qualified"),
    ("make_nested", "Box::Nested::Nested", "ast_new", "qualified"),
    ("stack_far", "Far", "stack_construction", "name"),
    ("stack_far", "Far::Far", "stack_construction", "name"),
    ("literal_local", "Widget", "stack_construction", "name"),
    ("literal_local", "Widget::Widget", "stack_construction", "name"),
])
def test_instantiates_route_states_its_mark(analysis, caller, callee, evidence, mark) -> None:
    edge = _edge(analysis, caller, callee, "instantiates", evidence)
    assert edge.meta["resolved_by"] == mark


def test_a_class_the_file_table_holds_is_same_file(tmp_path) -> None:
    """``Plain p;`` of a class with no constructor: the file's own table keeps
    the class itself (no constructor overwrote its short name)."""
    (tmp_path / "p.cpp").write_text(
        "class Plain { public: int x; };\nvoid f() { Plain p; }\n",
    )
    edge = _edge(analyze_cpp(tmp_path), "f", "Plain", "instantiates", "stack_construction")
    assert edge.meta["resolved_by"] == "same_file"
