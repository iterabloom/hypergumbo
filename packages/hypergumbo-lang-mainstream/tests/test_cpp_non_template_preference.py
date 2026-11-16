# SPDX-License-Identifier: AGPL-3.0-or-later
"""cpp: among overloads the argument count cannot separate, the non-template is the pick.

C++ [over.match.best]: when a non-template function and a function template
specialization are equally good for a call, the non-template is chosen. The
analyzer does not read argument types, so it cannot know they are equally
good (a ``Manager*`` argument makes only the template viable). It applies the
tie-breaker to the PICK only: exactly one non-template among several admitted
candidates is the edge's target, and the record stays ambiguous, with the
templates still among its candidates and the confidence an arity tie gets.
Two or more non-templates: unchanged.

Reduced from sherpa-onnx's keyword-spotter-transducer-impl.h, where
``InitKeywords(is)`` with a local ``std::istringstream is`` bound the
``InitKeywords(std::istream &)`` overload uniquely until the template
``InitKeywords(Manager *)`` became a member of the class; arity admits both.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.cpp import analyze_cpp


def _targets(result, line: int) -> list[tuple[int, object]]:
    by_id = {s.id: s for s in result.symbols}
    return sorted(
        (by_id[e.dst].span.start_line, (e.meta or {}).get("resolution_quality"))
        for e in result.edges
        if e.edge_type == "calls" and e.line == line and e.dst in by_id
    )


SHAPE = """\
#include <istream>
#include <sstream>
struct Kws {
  void InitKeywords(std::istream &is) {}
  void InitKeywords() { std::istringstream is(buf_); InitKeywords(is); }
  template <typename Manager>
  void InitKeywords(Manager *mgr) { std::istringstream is(buf_); InitKeywords(is); }
  std::string buf_;
};
"""

# The same members with the template first.
SHAPE_TEMPLATE_FIRST = """\
#include <istream>
#include <sstream>
struct Kws {
  template <typename Manager>
  void InitKeywords(Manager *mgr) { std::istringstream is(buf_); InitKeywords(is); }
  void InitKeywords(std::istream &is) {}
  void InitKeywords() { std::istringstream is(buf_); InitKeywords(is); }
  std::string buf_;
};
"""


def _confidences(result, line: int) -> list[float]:
    by_id = {s.id: s for s in result.symbols}
    return [
        e.confidence for e in result.edges
        if e.edge_type == "calls" and e.line == line and e.dst in by_id
    ]


def test_the_non_template_is_the_pick_and_the_record_stays_ambiguous(
    tmp_path: Path,
) -> None:
    (tmp_path / "k.hpp").write_text(SHAPE)
    result = analyze_cpp(tmp_path)
    assert _targets(result, 5) == [(4, "ambiguous")]
    assert _targets(result, 7) == [(4, "ambiguous")]
    # Two candidates: an arity tie's 1/sqrt(2), nothing more confident.
    assert all(c < 0.75 for c in _confidences(result, 5) + _confidences(result, 7))


def test_the_pick_does_not_depend_on_definition_order(tmp_path: Path) -> None:
    (tmp_path / "k.hpp").write_text(SHAPE_TEMPLATE_FIRST)
    result = analyze_cpp(tmp_path)
    assert _targets(result, 5) == [(6, "ambiguous")]
    assert _targets(result, 7) == [(6, "ambiguous")]


def test_the_record_keeps_the_template_among_its_candidates() -> None:
    """The choice itself: pick the non-template, keep both candidates, keep
    the confidence and the ambiguity -- whichever order they arrive in."""
    from hypergumbo_core.ir import Span, Symbol
    from hypergumbo_core.overload_arity import OverloadChoice

    from hypergumbo_lang_mainstream.cpp import _prefer_non_template

    def sym(sid: str, line: int) -> Symbol:
        return Symbol(
            id=sid, name="K::f", kind="method", language="cpp", path="k.hpp",
            span=Span(start_line=line, end_line=line, start_col=0, end_col=1),
            origin="t",
        )

    plain, tmpl = sym("plain", 4), sym("tmpl", 6)
    for admitted in ((plain, tmpl), (tmpl, plain)):
        choice = _prefer_non_template(
            OverloadChoice(admitted[0], 0.7, True, admitted), {"tmpl"},
        )
        assert choice.symbol is plain
        assert choice.ambiguous
        assert choice.confidence == 0.7
        assert sorted(s.id for s in choice.admitted) == ["plain", "tmpl"]


def test_two_non_templates_stay_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text("""\
struct P {
  void Put(int x) {}
  void Put(const char *s) {}
  template <class T> void Put(T *t) {}
  void Go() { Put(1); }
};
""")
    result = analyze_cpp(tmp_path)
    assert [q for _, q in _targets(result, 5)] == ["ambiguous"]


def test_a_member_of_a_class_template_is_not_a_function_template(tmp_path: Path) -> None:
    """In ``template <class T> struct A``, ``void f(int)`` is an ordinary
    member (only ``template <class U> void f(U)`` is a function template), so
    ``f(1)`` binds it."""
    (tmp_path / "a.cpp").write_text("""\
template <class T> struct A {
  void f(int x) {}
  template <class U> void f(U u) {}
  void g() { f(1); }
};
""")
    result = analyze_cpp(tmp_path)
    assert _targets(result, 4) == [(2, "ambiguous")]


def test_out_of_line_template_shapes() -> None:
    """``template <class T> void A<T>::h()`` defines a member of a class
    template: its one ``template <..>`` belongs to ``A<T>``. A member
    template defined out of line has one more."""
    import tree_sitter
    import tree_sitter_cpp

    from hypergumbo_lang_mainstream.cpp import _cpp_is_function_template

    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_cpp.language()))
    source = (
        b"template <class T> void A<T>::h() {}\n"
        b"template <class T> template <class U> void A<T>::k(U u) {}\n"
        b"template <> void f<int>(int x) {}\n"
        b"void plain() {}\n"
    )
    tree = parser.parse(source)
    defs = [n for n in _walk(tree.root_node) if n.type == "function_definition"]
    assert [_cpp_is_function_template(d) for d in defs] == [False, True, True, False]


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def test_free_functions_follow_the_same_rule(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text("""\
void put(double d) {}
template <class T> void put(T t) {}
void use() { put(1); }
""")
    result = analyze_cpp(tmp_path)
    assert _targets(result, 3) == [(1, "ambiguous")]


def test_an_ambiguous_constructor_choice_reaches_every_constructor(
    tmp_path: Path,
) -> None:
    """``Box b(1)`` with ``Box(int)`` and ``template <class T> Box(T*)``: the
    choice is ambiguous, so no edge goes to one constructor and the class
    edge alone stands -- from which construction.py reaches both."""
    (tmp_path / "a.cpp").write_text("""\
struct Box {
  explicit Box(int x) {}
  template <class T> explicit Box(T *p) {}
};
void build() { Box b(1); }
""")
    result = analyze_cpp(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    assert {
        (by_id[e.dst].kind, by_id[e.dst].span.start_line) for e in result.edges
        if e.edge_type == "instantiates" and e.line == 5 and e.dst in by_id
    } == {("struct", 1)}
