# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hilum, cpp: a call to an overloaded name binds by the call's ARITY.

The callee was taken from a name-keyed view that keeps one definition per
name, with no look at how many arguments the call passes. Among overloads
every call went to one of them, and an overload calling its sibling was drawn
as a call to ITSELF: sherpa-onnx's ``InitKeywords()`` calling
``InitKeywords(is)`` came out ``InitKeywords@294 -> InitKeywords@294``.

Each test asserts REACH (an edge exists at the call line) before asserting
which definition it reaches, and every fixture puts the WRONG overload where
the name-keyed view would pick it (registered last), so the pre-fix code fails.
"""
from __future__ import annotations

import math
from pathlib import Path

from hypergumbo_lang_mainstream.cpp import analyze_cpp


def _calls_at(result, line: int) -> list[tuple[str, int, object, float]]:
    """(dst name, dst start line, resolution_quality, confidence) per calls edge
    at ``line`` whose dst is a symbol of the run."""
    by_id = {s.id: s for s in result.symbols}
    return [
        (by_id[e.dst].name, by_id[e.dst].span.start_line,
         (e.meta or {}).get("resolution_quality"), e.confidence)
        for e in result.edges
        if e.edge_type == "calls" and e.line == line and e.dst in by_id
    ]


def _params(result, name: str, line: int):
    sym = next(
        s for s in result.symbols
        if s.name == name and s.span.start_line == line
    )
    return (sym.meta or {}).get("parameters")


def test_an_overload_calling_its_sibling_is_not_a_self_call(tmp_path: Path) -> None:
    """The filed instance, reduced: sherpa-onnx keyword-spotter-transducer-impl.h."""
    # ``.hpp``: a lone ``.h`` belongs to the C analyzer (header_owner).
    (tmp_path / "k.hpp").write_text("""\
#include <istream>
#include <sstream>
class Impl {
 private:
  void InitKeywords(std::istream &is) {
    Encode(is);
  }
  void InitKeywordsFromBuf() {
    std::istringstream is(buf_);
    InitKeywords(is);
  }
  void InitKeywords() {
    std::istringstream is(file_);
    InitKeywords(is);
  }
};
""")
    result = analyze_cpp(tmp_path)
    for line in (10, 14):
        calls = _calls_at(result, line)
        assert calls, line  # reach
        assert calls == [("Impl::InitKeywords", 5, None, calls[0][3])], (line, calls)


def test_a_default_declared_in_the_class_admits_the_shorter_call(tmp_path: Path) -> None:
    """C++ puts a default argument on the DECLARATION; the out-of-line definition
    does not repeat it. ``g(1)`` is ``g(int, int = 0)``, though its definition
    reads ``g(int a, int b)``: the declaration's defaults count."""
    (tmp_path / "t.h").write_text("""\
class T {
 public:
  void g(int a, int b = 0);
  void g();
  void h();
};
""")
    (tmp_path / "t.cpp").write_text("""\
#include "t.h"
void T::g(int a, int b) { }
void T::g() { }
void T::h() {
  g(1);
}
""")
    result = analyze_cpp(tmp_path)
    calls = _calls_at(result, 5)
    assert calls, calls  # reach
    assert [(n, s) for n, s, _, _ in calls] == [("T::g", 2)], calls
    assert _params(result, "T::g", 2) == [
        {"name": "a", "type": "int", "default": False},
        {"name": "b", "type": "int", "default": True},
    ]


def test_same_arity_overloads_are_an_ambiguous_record(tmp_path: Path) -> None:
    """``put(int)`` and ``put(double)`` both take one argument: arity cannot
    decide, and the edge says so instead of reading as a certain bind."""
    (tmp_path / "a.cpp").write_text("""\
void put(int x) { }
void put(double x) { }
void put() { }
void run() {
  put(1);
}
""")
    calls = _calls_at(analyze_cpp(tmp_path), 5)
    assert len(calls) == 1, calls  # reach, and one record
    name, start, quality, confidence = calls[0]
    assert name == "put" and start in (1, 2), calls
    assert quality == "ambiguous", calls
    # The certain local bind's confidence (ast_call, 0.85) scaled by 1/sqrt(2).
    assert abs(confidence - 0.85 / math.sqrt(2)) < 1e-6, calls


def test_a_variadic_overload_admits_any_longer_call(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text("""\
void log(const char *fmt, ...) { }
void log() { }
void run() {
  log("x %d %d", 1, 2);
  log();
}
""")
    result = analyze_cpp(tmp_path)
    assert [(n, s) for n, s, _, _ in _calls_at(result, 4)] == [("log", 1)]
    assert [(n, s) for n, s, _, _ in _calls_at(result, 5)] == [("log", 2)]
    assert _params(result, "log", 1)[-1] == {
        "name": "...", "type": None, "default": False, "variadic": True,
    }


def test_a_parameter_pack_call_has_no_known_arity(tmp_path: Path) -> None:
    """``f(args...)`` passes an unknown number of arguments, so arity decides
    nothing: both overloads stay candidates and the record says ambiguous."""
    (tmp_path / "a.cpp").write_text("""\
void f(int a) { }
void f(int a, int b) { }
template <typename... Args> void fwd(Args... args) {
  f(args...);
}
""")
    calls = _calls_at(analyze_cpp(tmp_path), 4)
    assert len(calls) == 1 and calls[0][2] == "ambiguous", calls


def test_parameters_meta_is_written_for_every_definition(tmp_path: Path) -> None:
    """``[]`` is a callable taking nothing; absent would be UNKNOWN."""
    (tmp_path / "a.cpp").write_text("""\
void none() { }
void voided(void) { }
int both(int a, const char *b) { return a; }
template <typename... Args> void pack(int n, Args&&... rest) { }
""")
    result = analyze_cpp(tmp_path)
    assert _params(result, "none", 1) == []
    assert _params(result, "voided", 2) == []
    assert _params(result, "both", 3) == [
        {"name": "a", "type": "int", "default": False},
        {"name": "b", "type": "const char", "default": False},
    ]
    assert _params(result, "pack", 4)[-1]["variadic"] is True


def test_an_overload_in_another_file_is_reached_when_the_pick_cannot_bind(
    tmp_path: Path,
) -> None:
    """Free overloads split across files (sherpa-onnx ``ReadFile(filename)`` in
    file-utils.cc, ``ReadFile(mgr, filename)`` beside it): when the definition
    the name gives cannot take the call's arguments and its own file has no
    overload that can, a same-named definition elsewhere that can is the one."""
    (tmp_path / "read_a.cpp").write_text("""\
int ReadFile(void *mgr, const char *name) { return 0; }
""")
    (tmp_path / "read_b.cpp").write_text("""\
int ReadFile(const char *name) { return 1; }
""")
    (tmp_path / "use.cpp").write_text("""\
void load(void *mgr) {
  ReadFile(mgr, "x");
  ReadFile("y");
}
""")
    result = analyze_cpp(tmp_path)
    by_line = {
        line: [(n, s) for n, s, _, _ in _calls_at(result, line)]
        for line in (2, 3)
    }
    assert all(by_line.values()), by_line  # reach
    paths = {
        e.line: next(s.path for s in result.symbols if s.id == e.dst)
        for e in result.edges if e.edge_type == "calls" and e.line in (2, 3)
    }
    assert paths[2].endswith("read_a.cpp"), paths
    assert paths[3].endswith("read_b.cpp"), paths


def test_a_call_no_overload_can_take_keeps_the_name_pick(tmp_path: Path) -> None:
    """Arity is a model: a definition whose defaults live in a header outside
    the repository, or an argument list a macro builds, can make every
    overload look unable to bind. Then arity has decided nothing, and the edge
    is the one the name gave, unchanged."""
    (tmp_path / "a.cpp").write_text("""\
void f(int a, int b) { }
void f(int a, int b, int c) { }
void run() {
  f(1);
}
""")
    calls = _calls_at(analyze_cpp(tmp_path), 4)
    assert len(calls) == 1 and calls[0][2] is None, calls


def test_a_call_on_another_object_is_not_rechosen(tmp_path: Path) -> None:
    """The pimpl idiom: ``X::Generate(a, b)`` forwards to
    ``impl_->Generate(a, b)``. With no receiver type the name lookup picked X's
    overloads, and the one matching the arity is the CALLER: re-choosing by
    arity would draw a false self-call. Such a call keeps the name's pick."""
    (tmp_path / "x.cpp").write_text("""\
class X {
 public:
  int Generate(int a, int b) const;
  int Generate(int a) const;
 private:
  Impl *impl_;
};
int X::Generate(int a, int b) const {
  return impl_->Generate(a, b);
}
int X::Generate(int a) const {
  return this->Generate(a, 0);
}
""")
    result = analyze_cpp(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    edges = {
        e.line: (by_id[e.src].span.start_line, by_id[e.dst].span.start_line)
        for e in result.edges
        if e.edge_type == "calls" and e.dst in by_id and e.line in (9, 12)
    }
    assert set(edges) == {9, 12}, edges  # reach
    assert edges[9] == (8, 11), edges  # the name's pick, unchanged
    assert edges[12] == (11, 8), edges  # ``this->``: the receiver is known


def test_a_struct_named_by_its_constructor_call_stays_the_struct(
    tmp_path: Path,
) -> None:
    """sherpa-onnx's ``EndpointRule(true, 1.2, 0)`` from another file: the name
    gives the struct, and overload choice is not a licence to swap it for a
    same-named constructor."""
    (tmp_path / "rule.hpp").write_text("""\
struct Rule {
  Rule() = default;
  Rule(bool a, float b, float c) { }
};
""")
    (tmp_path / "e.cpp").write_text("""\
#include "rule.hpp"
void make() {
  Rule(true, 1.0, 2.0);
}
""")
    result = analyze_cpp(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    kinds = [
        by_id[e.dst].kind for e in result.edges
        if e.edge_type == "calls" and e.line == 3 and e.dst in by_id
    ]
    assert kinds == ["struct"], kinds


def test_if_alternatives_of_one_function_are_not_ambiguous(tmp_path: Path) -> None:
    """``#if`` / ``#else`` definitions of one function share a signature, which
    overloads cannot; a call reaching them is not an ambiguous choice."""
    (tmp_path / "a.cpp").write_text("""\
#if USE_X
int Destroy(void *p) { return 1; }
#else
int Destroy(void *p) { return 0; }
#endif
int Destroy() { return 2; }
void run(void *p) {
  Destroy(p);
}
""")
    calls = _calls_at(analyze_cpp(tmp_path), 8)
    assert len(calls) == 1 and calls[0][2] is None and calls[0][1] in (2, 4), calls
    assert calls[0][3] == 0.85, calls


def test_a_comment_between_arguments_is_not_an_argument(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text("""\
void f(int a) { }
void f(int a, int b) { }
void run() {
  f(1, /* the second */ 2);
}
""")
    calls = _calls_at(analyze_cpp(tmp_path), 4)
    assert [(n, s, q) for n, s, q, _ in calls] == [("f", 2, None)], calls


def test_an_unknown_signature_is_not_an_if_alternative(tmp_path: Path) -> None:
    """A pure virtual records no parameters (unknown arity), so it is admitted
    beside a same-named overload and the two are NOT taken for ``#if``
    alternatives of one function: the choice stays ambiguous."""
    (tmp_path / "a.hpp").write_text("""\
class Base {
 public:
  virtual void run(int a) = 0;
  void run(double x) { }
  void go() {
    run(1);
  }
};
""")
    (tmp_path / "main.cpp").write_text("int main() { return 0; }\n")
    calls = _calls_at(analyze_cpp(tmp_path), 6)
    assert len(calls) == 1 and calls[0][2] == "ambiguous", calls


def test_an_ambiguous_choice_through_the_resolver_says_so(tmp_path: Path) -> None:
    """Overloads defined in ANOTHER file are reached through the global
    resolver, whose edge carries the same ambiguous record."""
    (tmp_path / "put.cpp").write_text("""\
void put(int x) { }
void put(double x) { }
""")
    (tmp_path / "use.cpp").write_text("""\
void run() {
  put(1);
}
""")
    calls = _calls_at(analyze_cpp(tmp_path), 2)
    assert len(calls) == 1, calls  # reach
    name, start, quality, confidence = calls[0]
    assert (name, start, quality) == ("put", 1, "ambiguous"), calls
    assert abs(confidence - 0.80 / math.sqrt(2)) < 1e-6, calls
