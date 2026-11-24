# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/c_family.py
"""A C++ member function's cv-qualifiers and ref-qualifier are part of its
identity (WI-pohom-turot part 1).

``T& data()`` / ``const T& data() const``, ``r() &`` / ``r() &&`` and
``q() volatile`` / ``q()`` are different functions with one parameter list;
until scheme v20 nothing a stable id hashes held the words after the list,
so each pair tied and the occurrence split numbered them, and the
declarations local to them tied too (their scope label was the name and
hashed parameter types). :func:`c_family_function_qualifiers` reads the
words from the function's OWN declarator -- the one holding its parameter
list -- so an in-class definition, an out-of-class one and a pure virtual
declaration read the same facts. Each shape is parsed by the real grammar.
"""
from __future__ import annotations

import tree_sitter_language_pack as tlp

from hypergumbo_core.analyze.base import iter_tree
from hypergumbo_core.analyze.c_family import (
    c_family_function_qualifiers, c_family_scope_chain,
)


def _nodes(lang: str, code: str, types: tuple[str, ...]) -> list:
    tree = tlp.get_parser(lang).parse(code.encode())
    return sorted((n for n in iter_tree(tree.root_node) if n.type in types),
                  key=lambda n: n.start_byte)


def _qualifiers(code: str, types: tuple[str, ...] = ("function_definition",)) -> list:
    return [c_family_function_qualifiers(n, code.encode()) for n in _nodes("cpp", code, types)]


def test_cv_and_ref_qualifiers_are_read_after_the_parameter_list() -> None:
    code = """struct T {
  int& d() { return v; }
  const int& d() const { return v; }
  void r() & {}
  void r() && {}
  void q() volatile {}
  void q() {}
  void w() const volatile && {}
};
"""
    assert _qualifiers(code) == [
        (), ("const",), ("&",), ("&&",), ("volatile",), (), ("&&", "const", "volatile"),
    ]


def test_the_words_are_sorted_not_evaluated() -> None:
    """``const volatile`` and ``volatile const`` are one function, spelled
    two ways; the words are sorted, never interpreted."""
    a, b = _qualifiers("struct T { void f() const volatile {} void f() volatile const {} };\n")
    assert a == b == ("const", "volatile")


def test_other_words_after_the_list_are_not_qualifiers() -> None:
    """``noexcept``, ``override``, ``final``, a trailing return type and
    ``= 0`` stay out."""
    code = """struct T : B {
  void a() noexcept {}
  void b() override {}
  void c() final {}
  auto h() const -> int { return 1; }
  auto k() -> int { return 1; }
};
"""
    assert _qualifiers(code) == [(), (), (), ("const",), ()]


def test_a_declaration_in_the_class_and_its_definitions_read_the_same_facts() -> None:
    code = """struct T {
  virtual const int& d() const & = 0;
  operator bool() const { return true; }
};
const int& T::d() const & { return v; }
T::operator bool() const { return true; }
"""
    assert _qualifiers(code, ("field_declaration", "function_definition")) == [
        ("&", "const"), ("const",), ("&", "const"), ("const",),
    ]


def test_a_declaration_that_is_no_function_has_none() -> None:
    code = "struct S { int a; };\nint x = 1;\nvoid PFX(f)(void) {}\n"
    assert _qualifiers(code, ("struct_specifier", "declaration", "function_definition")) == [
        (), (), (),
    ]


def test_the_locals_of_qualified_twins_get_distinct_labels() -> None:
    """The label of an enclosing function carries its qualifiers after its
    hashed parameter types; one with none keeps the label it had."""
    code = """struct T {
  int& d() { struct L {}; return v; }
  const int& d() const { struct L {}; return v; }
  void r(int) & { struct L {}; }
  void r(int) && { struct L {}; }
};
"""
    tags = [n for n in _nodes("cpp", code, ("struct_specifier",)) if n.parent.type != "translation_unit"]
    assert [c_family_scope_chain(n, code.encode(), functions=True, signatures=True)
            for n in tags] == [
        ["T", "d"], ["T", "d const"], ["T", "r(int) &"], ["T", "r(int) &&"],
    ]


def test_cuda_reads_the_same_qualifiers() -> None:
    code = "struct T { __device__ int g() const { return 1; } __device__ int g() { return 2; } };\n"
    got = [c_family_function_qualifiers(n, code.encode())
           for n in _nodes("cuda", code, ("function_definition",))]
    assert got == [("const",), ()]
