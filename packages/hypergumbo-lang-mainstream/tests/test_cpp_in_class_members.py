# SPDX-License-Identifier: AGPL-3.0-or-later
"""A constructor or member template DEFINED in its class body is a method.

tree-sitter-cpp gives a member function defined in a class body a
``field_identifier`` name, except two shapes whose name is a plain
``identifier``: a constructor (``C() {}``, ``C(const C&) = default;``, a
templated ``template <class T> C(T) {}``) and a member template
(``template <class T> void f(T) {}``, inside a ``template_declaration``). The
analyzer read every ``identifier`` leaf as a free function, so these came out
as kind ``function`` named ``C`` / ``f``, while the out-of-class spelling of
the same member (``C::C() {}``) and every other in-class member is a method
``C::name``.

Each test pins the property, not a dump: kind ``method``, name qualified by
the class, and the shapes that are NOT members (a ``friend`` defined in the
body, a free function template) stay ``function``.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.cpp import analyze_cpp

FORMS = """\
struct C {
  C() {}
  explicit C(int x) : v(x) {}
  C(int a, int b) : v(a), w(b) { reset(); }
  C(const C&) = default;
  C& operator=(const C&) = delete;
  ~C() {}
  void reset() {}
  template <class T> void f(T t) {}
  template <class T> C(T* p, T* q) {}
  template <class T> static T make() { return T(); }
  operator bool() const { return v != 0; }
  int operator()(int x) const { return x; }
  friend bool operator==(const C&, const C&) { return true; }
  friend void swap(C&, C&) {}
  template <class U> friend void hook(U) {}
  int v;
  int w;
};
struct D { D(); ~D(); void run(); };
D::D() {}
D::~D() {}
void D::run() {}
template <class T> struct S {
  S() {}
  void g() {}
};
struct Outer {
  struct Inner {
    Inner() {}
  };
};
namespace ns {
class K {
 public:
  K() {}
};
}
class P {
 public:
  P() = default;
};
template <class T> void free_template(T) {}
void free_fn() {
  struct Local {
    Local() {}
  };
  Local l;
}
"""


def _at(result, line: int):
    """The one function/method symbol defined at ``line``."""
    found = [
        s for s in result.symbols
        if s.kind in ("function", "method") and s.span.start_line == line
    ]
    assert len(found) == 1, (line, found)
    return found[0]


def _analyze(tmp_path: Path):
    (tmp_path / "forms.cpp").write_text(FORMS)
    return analyze_cpp(tmp_path)


def test_in_class_constructors_are_methods_of_their_class(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    # plain, explicit with an initialiser list, two-argument, = default, templated
    for line in (2, 3, 4, 5, 10):
        sym = _at(result, line)
        assert (sym.kind, sym.name) == ("method", "C::C"), line


def test_in_class_member_templates_are_methods_of_their_class(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    assert (_at(result, 9).kind, _at(result, 9).name) == ("method", "C::f")
    assert (_at(result, 11).kind, _at(result, 11).name) == ("method", "C::make")


def test_constructors_in_every_enclosing_class_shape(tmp_path: Path) -> None:
    """A class template, a nested class, a class in a namespace, a ``class``
    with ``= default``, a local class: each constructor is its class's method."""
    result = _analyze(tmp_path)
    expected = {
        25: "S::S", 30: "Inner::Inner", 36: "K::K", 41: "P::P", 46: "Local::Local",
    }
    for line, name in expected.items():
        sym = _at(result, line)
        assert (sym.kind, sym.name) == ("method", name), line


def test_in_and_out_of_class_constructors_have_one_shape(tmp_path: Path) -> None:
    """``C() {}`` in the body and ``D::D() {}`` outside it are named alike."""
    result = _analyze(tmp_path)
    inside, outside = _at(result, 2), _at(result, 21)
    assert (inside.kind, outside.kind) == ("method", "method")
    assert inside.name.split("::") == ["C", "C"]
    assert outside.name.split("::") == ["D", "D"]


def test_friend_definitions_and_free_templates_stay_functions(tmp_path: Path) -> None:
    """A friend defined in the body is a free function, not a member."""
    result = _analyze(tmp_path)
    for line, name in ((14, "operator=="), (15, "swap"), (16, "hook"), (43, "free_template")):
        sym = _at(result, line)
        assert (sym.kind, sym.name) == ("function", name), line


def test_members_that_were_already_methods_keep_their_shape(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    expected = {
        6: "C::operator=", 7: "C::~C", 8: "C::reset", 12: "C::operator bool",
        13: "C::operator()", 21: "D::D", 22: "D::~D", 23: "D::run", 26: "S::g",
    }
    for line, name in expected.items():
        sym = _at(result, line)
        assert (sym.kind, sym.name) == ("method", name), line


def test_a_call_on_a_typed_receiver_binds_the_member_template(tmp_path: Path) -> None:
    """``c.f(1)`` on ``C c`` binds ``C::f`` once the template is C's member;
    as a free ``f`` the declared-receiver lookup ``C::f`` found nothing."""
    (tmp_path / "t.cpp").write_text("""\
struct C {
  template <class T> void f(T t) {}
};
void use() {
  C c;
  c.f(1);
}
""")
    result = analyze_cpp(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    targets = [
        by_id[e.dst].name for e in result.edges
        if e.edge_type == "calls" and e.line == 6 and e.dst in by_id
    ]
    assert targets == ["C::f"]


def test_new_lands_on_the_in_class_constructor_method(tmp_path: Path) -> None:
    """``new W(1)`` keeps its instantiates edge to the constructor definition,
    now a method, as ``new`` of an out-of-class constructor already does."""
    (tmp_path / "n.cpp").write_text("""\
struct W {
  W(int x) {}
};
void use() {
  W* w = new W(1);
}
""")
    result = analyze_cpp(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    targets = {
        (by_id[e.dst].kind, by_id[e.dst].name, by_id[e.dst].span.start_line)
        for e in result.edges
        if e.edge_type == "instantiates" and e.line == 5 and e.dst in by_id
    }
    assert ("method", "W::W", 2) in targets


def test_members_inside_a_preprocessor_block_of_the_body_are_methods(tmp_path: Path) -> None:
    """``#if`` / ``#ifdef`` / ``#else`` in a class body wrap a member without
    making it a free function (sherpa-onnx's ``#if __ANDROID_API__ >= 9``
    constructors; log.h's ``#if`` ``operator&``). A friend in such a block
    stays a free function."""
    (tmp_path / "p.cpp").write_text("""\
struct Q {
#if A
  Q(int x) {}
#elif B
  Q(int x, int y) {}
#else
  template <class M> Q(M* m) {}
#endif
#ifdef C
  template <class T> void t(T) {}
  void operator&(int) const {}
  ~Q() {}
  friend void fr(Q&) {}
#endif
};
""")
    result = analyze_cpp(tmp_path)
    expected = {
        3: ("method", "Q::Q"), 5: ("method", "Q::Q"), 7: ("method", "Q::Q"),
        10: ("method", "Q::t"), 11: ("method", "Q::operator&"),
        12: ("method", "Q::~Q"), 13: ("function", "fr"),
    }
    for line, shape in expected.items():
        sym = _at(result, line)
        assert (sym.kind, sym.name) == shape, line
