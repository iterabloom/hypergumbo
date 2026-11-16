# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-tohuj: a class declared with a qualified name owns its members.

``class OnlineStream::Impl { .. }`` -- the out-of-line definition of a nested
class the pimpl idiom uses -- names itself with a ``qualified_identifier``,
not a ``type_identifier``. The analyzer read only the latter, so the class
emitted no symbol and no fields, and every member defined in its body was
registered bare (``AcceptWaveform``), with no owner. A forward inside it,
``feat_extractor_.AcceptWaveform(n)``, then bound the bare method ITSELF, and
the pimpl forward ``impl_->AcceptWaveform(n)`` found no ``Impl`` member.

The class is now named as declared (``OnlineStream::Impl``, template
arguments dropped), and its members are ``OnlineStream::Impl::name`` -- the
name an out-of-line ``void OnlineStream::Impl::Out() {}`` already has. A
union is a class for this purpose: its constructor and methods are
``U::U`` / ``U::um``.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.cpp import analyze_cpp

SOURCE = """\
#include <memory>
class FeatureExtractor {
 public:
  void AcceptWaveform(int n) {}
};
class OnlineStream {
 public:
  OnlineStream();
  void AcceptWaveform(int n);
 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};
class OnlineStream::Impl {
 public:
  Impl() {}
  explicit Impl(int x) : n_(x) {}
  ~Impl() {}
  void AcceptWaveform(int n) { feat_extractor_.AcceptWaveform(n); }
  bool operator==(const Impl&) const { return true; }
  template <class T> void Push(T t) {}
  void Out();
  int n_;
  FeatureExtractor feat_extractor_;
  struct Inner { void go() {} };
};
OnlineStream::OnlineStream() : impl_(new Impl(1)) {}
void OnlineStream::AcceptWaveform(int n) { impl_->AcceptWaveform(n); }
void OnlineStream::Impl::Out() {}
struct A { struct S; class B; };
struct A::S { void f() { g(); } void g() {} };
class A::B { public: class C; void h() {} };
class A::B::C { public: void k() {} };
namespace ns {
struct P { struct Q; };
struct P::Q { Q() {} void q() {} };
}
template <class T> struct TT { struct N; };
template <class T> struct TT<T>::N { void tn() {} };
struct E { enum class Color : int; };
enum class E::Color : int { Red, Green };
union U {
  U() {}
  void um() {}
  int a;
  float b;
};
"""


def _analyze(tmp_path: Path):
    (tmp_path / "s.cc").write_text(SOURCE)
    return analyze_cpp(tmp_path)


def _at(result, line: int, kinds=("function", "method")):
    found = [s for s in result.symbols if s.kind in kinds and s.span.start_line == line]
    assert len(found) == 1, (line, found)
    return found[0]


def test_members_of_a_qualified_class_are_owned_by_it(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    expected = {
        16: "OnlineStream::Impl::Impl", 17: "OnlineStream::Impl::Impl",
        18: "OnlineStream::Impl::~Impl", 19: "OnlineStream::Impl::AcceptWaveform",
        20: "OnlineStream::Impl::operator==", 21: "OnlineStream::Impl::Push",
        32: "A::B::h", 33: "A::B::C::k", 39: "TT::N::tn", 43: "U::U", 44: "U::um",
    }
    for line, name in expected.items():
        sym = _at(result, line)
        assert (sym.kind, sym.name) == ("method", name), line
    for line, names in ((31, {"A::S::f", "A::S::g"}), (36, {"P::Q::Q", "P::Q::q"})):
        assert {
            s.name for s in result.symbols
            if s.kind == "method" and s.span.start_line == line
        } == names, line


def test_the_qualified_class_fields_and_enum_are_emitted(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    cls = _at(result, 14, ("class",))
    assert cls.name == "OnlineStream::Impl"
    fields = {s.name for s in result.symbols if s.kind == "field"}
    assert {"OnlineStream::Impl::n_", "OnlineStream::Impl::feat_extractor_"} <= fields
    assert _at(result, 36, ("struct",)).qualified_name == "ns::P::Q"
    assert _at(result, 41, ("enum",)).name == "E::Color"
    assert {"E::Color::Red", "E::Color::Green"} <= fields


def test_out_of_class_members_keep_their_name_and_id(tmp_path: Path) -> None:
    """The out-of-line member is the reference: same name before and after,
    and now the in-body members are spelled the same way."""
    result = _analyze(tmp_path)
    out = _at(result, 29)
    assert (out.kind, out.name) == ("method", "OnlineStream::Impl::Out")
    assert _at(result, 28).name == "OnlineStream::AcceptWaveform"


def test_a_member_forward_binds_the_field_type_not_itself(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    at19 = [
        (by_id[e.src].name, by_id[e.dst].name) for e in result.edges
        if e.edge_type == "calls" and e.line == 19 and e.dst in by_id
    ]
    assert at19 == [("OnlineStream::Impl::AcceptWaveform", "FeatureExtractor::AcceptWaveform")]


def test_the_pimpl_forward_reaches_the_impl_member(tmp_path: Path) -> None:
    """``impl_`` is declared ``unique_ptr<Impl>`` inside ``OnlineStream``, so
    ``Impl`` is looked up in ``OnlineStream``'s scope first, as C++ does."""
    result = _analyze(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    at28 = [
        by_id[e.dst].name for e in result.edges
        if e.edge_type == "calls" and e.line == 28 and e.dst in by_id
    ]
    assert at28 == ["OnlineStream::Impl::AcceptWaveform"]


def test_a_bare_call_in_a_qualified_struct_binds_its_own_member(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    at31 = [
        (by_id[e.src].name, by_id[e.dst].name) for e in result.edges
        if e.edge_type == "calls" and e.line == 31 and e.dst in by_id
    ]
    assert at31 == [("A::S::f", "A::S::g")]



def test_an_out_of_line_member_finds_its_class_fields_by_suffix(tmp_path: Path) -> None:
    """``ns::Tts::Run`` reads the fields of class ``Tts`` (no class is named
    ``ns::Tts``); a receiver no class field or local declares stays unbound."""
    (tmp_path / "t.cc").write_text("""\
struct Engine { void Go() {} };
namespace ns {
struct Tts { Engine engine_; void Run(); };
}
void ns::Tts::Run() { engine_.Go(); }
struct Z { void m() { other.Go(); } };
""")
    result = analyze_cpp(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    def dst(line: int) -> list[str]:
        return [
            by_id[e.dst].name for e in result.edges
            if e.edge_type == "calls" and e.line == line and e.dst in by_id
        ]
    assert dst(5) == ["Engine::Go"]
    assert dst(6) == []
