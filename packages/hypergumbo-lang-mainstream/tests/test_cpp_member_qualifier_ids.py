# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/cpp.py
"""A C++ member function's cv-qualifiers and ref-qualifier enter its stable
id (WI-pohom-turot part 1).

Until scheme v20 ``T& d()`` / ``const T& d() const``, ``r() &`` /
``r() &&`` and ``q() volatile`` / ``q()`` tied before the occurrence split
(libzmq's ``blob_t::data``, zstd's pzstd ``Buffer::range``), and so did the
declarations local to them. The words fill the decorators slot, sorted, so a
member written without them keeps the value it had. The analyzer runs on each
fixture; ties are counted on its own output, before the split.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_stable_id
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import declared_arity, declared_arity_stable_id
from hypergumbo_lang_mainstream.cpp import analyze_cpp

TWINS = """\
struct T {
  int v;
  int& d() { struct L { int x; }; return v; }
  const int& d() const { struct L { int x; }; return v; }
  void r() & { struct L { int x; }; }
  void r() && { struct L { int x; }; }
  void q() volatile {}
  void q() {}
  virtual int f() const = 0;
  virtual int f() = 0;
  operator bool() const { return true; }
  operator bool() { return false; }
};
"""


def _symbols(root: Path, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.cpp").write_text(text)
    return [s for s in analyze_cpp(root).symbols if s.kind != "file"]


def test_qualified_twins_and_their_locals_do_not_tie(tmp_path: Path) -> None:
    symbols = _symbols(tmp_path, TWINS)
    methods = Counter(s.name for s in symbols if s.kind == "method")
    assert methods["T::d"] == methods["T::r"] == methods["T::q"] == methods["T::f"] == 2  # reach
    assert sum(1 for s in symbols if s.kind == "struct" and s.name == "L") == 4
    counts = Counter(s.stable_id for s in symbols)
    assert None not in counts
    assert sorted({(s.kind, s.name) for s in symbols if counts[s.stable_id] > 1}) == []


def test_a_member_without_qualifiers_keeps_its_value(tmp_path: Path) -> None:
    """The control: ``int& d()`` hashes what it hashed before v20."""
    (plain,) = [s for s in _symbols(tmp_path, TWINS)
                if s.name == "T::d" and s.span.start_line == 3]
    assert plain.stable_id == declared_arity_stable_id(
        kind="method", lang="cpp", path="a.cpp", name="T::d", qualified_name="",
        arity=declared_arity({"parameters": []}),
        containing_stable_id=make_file_stable_id("cpp", "a.cpp"),
    )


def test_a_pure_virtual_keeps_its_decorators_and_adds_the_qualifier(tmp_path: Path) -> None:
    pure = sorted((s for s in _symbols(tmp_path, TWINS) if s.name == "T::f"),
                  key=lambda s: s.span.start_line)
    assert [s.modifiers for s in pure] == [["public", "virtual", "abstract"]] * 2
    anchor = make_file_stable_id("cpp", "a.cpp")
    assert [s.stable_id for s in pure] == [
        declared_arity_stable_id(
            kind="method", lang="cpp", path="a.cpp", name="T::f", qualified_name="",
            arity=declared_arity({"parameters": []}), decorators=decorators,
            containing_stable_id=anchor,
        )
        for decorators in ("abstract,const,virtual", "abstract,virtual")
    ]


def test_an_in_class_and_an_out_of_class_definition_hash_alike(tmp_path: Path) -> None:
    """A declaration in the class and its out-of-class definition hash the
    same facts: the qualifiers are read off the function's own declarator."""
    inline = _symbols(tmp_path / "in", "struct T { int v; const int& d() const & { return v; } };\n")
    outside = _symbols(tmp_path / "out", (
        "struct T { int v; const int& d() const &; };\n"
        "const int& T::d() const & { return v; }\n"
    ))
    (a,) = [s for s in inline if s.name == "T::d"]
    (b,) = [s for s in outside if s.name == "T::d"]
    assert a.stable_id == b.stable_id
    (c,) = [s for s in _symbols(tmp_path / "plain", "struct T { int v; const int& d() & { return v; } };\n")
            if s.name == "T::d"]
    assert c.stable_id != a.stable_id
