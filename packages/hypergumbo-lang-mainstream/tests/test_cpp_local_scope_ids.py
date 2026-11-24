# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/cpp.py
"""A C++ declaration local to a function is scoped by that function, labelled
by its name and the parameter types its own id hashes (WI-finam, the defect
WI-podor fixed for Swift).

Until scheme v19 a C++ stable id's scope chain held namespaces and classes
only, so a local ``struct L`` (and its fields and methods) in ``f()`` and in
``g()``, in two overloads ``h(int)`` / ``h(double)``, in two constructors or
two ``operator==``, or in two lambdas bound to ``a`` and ``b``, tied; the
occurrence split numbered them by order. Each case runs the analyzer: no two
symbols of the file share a stable id, and blanking the first alternative
moves no survivor's id. Left tied, as the callables themselves still tie:
the locals of ``c() const`` and ``c()`` (a ``const`` qualifier is not in a
method's id), and those of a member of an explicit specialisation
``template <> struct Tp<int>``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import iter_tree
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.cpp import _cpp_scope_slot, analyze_cpp


def _symbols(root: Path, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.cpp").write_text(text)
    return [s for s in analyze_cpp(root).symbols if s.kind != "file"]


#: (head, earlier, rest): blanking ``earlier`` leaves a valid file.
CASES: dict[str, tuple[str, str, str]] = {
    "free-functions": ("", "void f() { struct L { int a; void m() {} }; }\n",
                       "void g() { struct L { int a; void m() {} }; }\n"),
    "overloads": ("", "void h(int) { enum E { X }; class C { public: void m() {} }; }\n",
                  "void h(double) { enum E { X }; class C { public: void m() {} }; }\n"),
    "constructors": ("struct A {\n", "  A(int a) { struct L { int x; }; }\n",
                     "  A(double b) { struct L { int x; }; }\n};\n"),
    "operator-overloads": ("struct S {\n", "  bool operator==(int) { struct L {}; return true; }\n",
                           "  bool operator==(double) { struct L {}; return true; }\n};\n"),
    "bound-lambdas": ("", "auto a = []() { struct L { int x; }; };\n",
                      "auto b = []() { struct L { int x; }; };\n"),
    "dispatch-tables-in-overloads": (
        "int g(void) { return 1; }\n", "void f(int) { int (*tbl[])(void) = { g }; }\n",
        "void f(double) { int (*tbl[])(void) = { g }; }\n",
    ),
    "methods-of-two-classes": ("", "struct P { void run() { struct L {}; } };\n",
                               "struct Q { void run() { struct L {}; } };\n"),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_locals_of_two_callables_get_distinct_ids(tmp_path: Path, case: str) -> None:
    head, earlier, rest = CASES[case]
    both = _symbols(tmp_path / "with", head + earlier + rest)
    ids = [s.stable_id for s in both]
    assert None not in ids
    assert len(set(ids)) == len(ids), sorted(
        (s.kind, s.name, s.span.start_line) for s in both if ids.count(s.stable_id) > 1)
    before = {(s.kind, s.name, s.span.start_line): s.stable_id for s in both}
    after = _symbols(tmp_path / "without", head + "\n" * earlier.count("\n") + rest)
    assert len(after) < len(both)
    for s in after:
        assert before[(s.kind, s.name, s.span.start_line)] == s.stable_id, (s.kind, s.name)


def _slots(code: str, kinds: tuple[str, ...], name: str) -> list[str]:
    import tree_sitter
    import tree_sitter_cpp

    src = code.encode()
    tree = tree_sitter.Parser(tree_sitter.Language(tree_sitter_cpp.language())).parse(src)
    nodes = sorted((n for n in iter_tree(tree.root_node) if n.type in kinds),
                   key=lambda n: n.start_byte)
    return [_cpp_scope_slot(n, src, name) for n in nodes]


def test_slot_spells_callable_labels_and_branches() -> None:
    """A callable hashing no parameter type keeps its bare name; a branch and
    a callable label compose, each at its level; a top-level or class-level
    declaration hashes what it did."""
    assert _slots(
        "namespace n {\n#ifdef X\nvoid f(int a, const char *p) { struct L {}; }\n#else\n"
        "void f(int a, const char *p) { struct L {}; }\n#endif\nvoid g() { struct L {}; }\n}\n",
        ("struct_specifier",), "L",
    ) == ["n/#ifdef X/f(int,const char*)/L", "n/#ifdef X #else/f(int,const char*)/L", "n/g/L"]
    assert _slots("struct K { void m(int) { struct L { void q() {} }; } };\n",
                  ("function_definition",), "L::q")[-1] == "K/m(int)/L::q"
    top_and_member = _slots("struct L {};\nstruct K { struct L {}; };\n", ("struct_specifier",), "L")
    assert (top_and_member[0], top_and_member[-1]) == ("", "K/L")



def test_a_macro_misparsed_class_is_no_function() -> None:
    """``class C MACRO : public B { .. }`` parses as a function definition
    with no parameter list: what sits in its "body" is no local and gets no
    callable label; nor does a parameter's elaborated ``class Q``, which is
    not in the body. A real function's local does."""
    src = ("class C FINAL : public B\n{\n  public:\n  struct P { int a; };\n};\n"
           "void f(class Q *q) { struct R {}; }\n")
    assert _slots(src, ("struct_specifier",), "P") == ["", "f(class Q*)/P"]
    assert _slots(src, ("class_specifier",), "Q") == ["", ""]
