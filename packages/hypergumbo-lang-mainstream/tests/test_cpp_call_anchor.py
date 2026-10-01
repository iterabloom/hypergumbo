# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-saduj / INV-midag, cpp: an edge drawn from a function definition comes FROM
that definition, and every definition shape has a symbol to draw it from.

Two defects, one walk. The edge walk found the enclosing definition by its SHORT
NAME in ``symbol_by_name``, which keeps one symbol per name: of ``P::run`` and
``Q::run``, or ``over(int)`` and ``over(double)``, every call, ``new``,
dispatch-table reference and ``std::cout`` read was drawn from whichever
registered last. And a definition whose name the extractor could not read got no
symbol, so everything in it was left unemitted: ``char **f()``, ``int *&f()``,
an explicit specialisation ``template <> void f<int>(int)``, ``operator==``, an
in-class destructor or conversion operator, a function returning a function
pointer.

Each test asserts REACH (the edge exists, at the line) before CONTAINMENT (the
src's span holds the line), so an edge that vanished cannot pass as contained.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.cpp import analyze_cpp


def _contained(result, edge_type: str) -> dict[int, tuple[str, int, int]]:
    """line -> (src name, src start, src end) for every ``edge_type`` edge whose
    src is a symbol of the run; asserts each such edge's line is inside its src."""
    by_id = {s.id: s for s in result.symbols}
    out: dict[int, tuple[str, int, int]] = {}
    for e in result.edges:
        if e.edge_type != edge_type or not e.line or e.src not in by_id:
            continue
        src = by_id[e.src]
        assert src.span.start_line <= e.line <= src.span.end_line, (
            edge_type, e.line, src.name, src.span.start_line, src.span.end_line)
        out[e.line] = (src.name, src.span.start_line, src.span.end_line)
    return out


def test_same_named_definitions_are_told_apart(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text("""\
void sink(int);
struct P { void run(); };
struct Q { void run(); };
void P::run() { sink(1); }
void Q::run() { sink(2); }
void over(int) { sink(3); }
void over(double) { sink(4); }
struct S { void run() { sink(5); } };
struct T { void run() { sink(6); } };
""")
    calls = _contained(analyze_cpp(tmp_path), "calls")
    assert set(calls) >= {4, 5, 6, 7, 8, 9}, calls  # reach
    assert calls[4][0] == "P::run" and calls[5][0] == "Q::run", calls
    assert calls[8][0] == "S::run" and calls[9][0] == "T::run", calls


def test_every_edge_kind_of_an_overload_is_drawn_from_it(tmp_path: Path) -> None:
    """The call is not the only edge drawn from the enclosing definition: a ``new``
    (instantiates), a dispatch-table use (references) and a ``std::cout`` read
    (module_attr_ref, which fell to the FILE for the overload the name view lost)."""
    (tmp_path / "o.cpp").write_text("""\
#include <iostream>
struct W { W(); };
int h(int argc, char **argv) { return 0; }
struct cmd { const char *name; int (*fn)(int, char **); };
static cmd table[] = { { "h", h } };
void over(int) {
    std::cout << 1;
    W *w = new W();
    table[0].fn(0, 0);
}
void over(double) {
    std::cout << 2;
    W *w = new W();
    table[0].fn(0, 0);
}
""")
    result = analyze_cpp(tmp_path)
    assert set(_contained(result, "calls")) >= {9, 14}
    assert set(_contained(result, "instantiates")) >= {8, 13}
    assert set(_contained(result, "references")) >= {9, 14}
    attr = _contained(result, "module_attr_ref")
    assert set(attr) >= {7, 12}, attr
    assert attr[7][1:] == (6, 10) and attr[12][1:] == (11, 15), attr


def test_every_definition_shape_has_a_symbol_and_its_calls(tmp_path: Path) -> None:
    (tmp_path / "m.cpp").write_text("""\
void sink(int);
struct A { bool x; };
char **two_ptr(int n) { sink(1); return 0; }
template <typename T> void tpl(T x) { sink(2); }
template <> void tpl<int>(int x) { sink(3); }
int *&refptr() { sink(4); static int *p; return p; }
int &&rref() { sink(5); return 1; }
bool operator==(const A &a, const A &b) { sink(6); return true; }
struct S {
  ~S() { sink(7); }
  bool operator<(const S &o) const { sink(8); return true; }
  explicit operator bool() const { sink(9); return true; }
  int **pp() { sink(10); return 0; }
  friend bool operator!=(const S &a, const S &b) { sink(11); return false; }
};
int (*getfn(void))(int) { sink(12); return 0; }
int (paren)(int n) { sink(13); return 0; }
S::operator int() const { sink(14); return 0; }
int **S2_pp() { sink(15); return 0; }
""")
    result = analyze_cpp(tmp_path)
    kinds = {(s.name, s.kind) for s in result.symbols if s.kind in ("function", "method")}
    assert kinds >= {
        ("two_ptr", "function"),
        ("tpl", "function"),
        ("refptr", "function"),
        ("rref", "function"),
        ("operator==", "function"),
        ("S::~S", "method"),
        ("S::operator<", "method"),
        ("S::operator bool", "method"),
        ("S::pp", "method"),
        ("operator!=", "function"),
        ("getfn", "function"),
        ("paren", "function"),
        ("S::operator int", "method"),
        ("S2_pp", "function"),
    }, sorted(kinds)
    # The primary template AND its explicit specialisation each have a symbol.
    assert len([s for s in result.symbols if s.name == "tpl"]) == 2
    calls = _contained(result, "calls")
    # reach: every line holding a call (9 opens ``struct S``, 15 closes it)
    assert set(calls) >= set(range(3, 20)) - {9, 15}, sorted(calls)
    sigs = {s.name: s.signature for s in result.symbols if s.kind in ("function", "method")}
    assert sigs["S::operator bool"] == "()", sigs
    # A function returning a function pointer: its OWN parameters, and no return
    # type rather than a false ``int``.
    assert sigs["getfn"] == "(void)", sigs


def test_a_macro_misparse_mints_no_function(tmp_path: Path) -> None:
    """A definition with no function declarator is a function only when it is a
    conversion operator. ``class API X : public MoveOnly<X, Y> {..}`` (sherpa-onnx)
    parses as a definition whose declarator is the template ``MoveOnly<..>``, and
    ``PFX(name)(args)`` (x265) as a macro call; naming either would mint a phantom
    function and draw the body's calls from it."""
    (tmp_path / "m.cpp").write_text("""\
void g();
template <typename T, typename U> class MoveOnly {};
class SHERPA_API Stream : public MoveOnly<Stream, int> {
 public:
  void f() { g(); }
};
int PFX(cpu_test)(void) { g(); return 0; }
void ok() { g(); }
""")
    result = analyze_cpp(tmp_path)
    names = {s.name for s in result.symbols if s.kind in ("function", "method")}
    assert "ok" in names, names  # reach
    assert not names & {"MoveOnly", "PFX", "cpu_test", "Stream"}, names
    assert 8 in _contained(result, "calls")
