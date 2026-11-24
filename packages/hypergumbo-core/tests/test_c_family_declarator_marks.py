# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/c_family.py
"""A C-family parameter's declarator marks are part of what its callable's
stable id hashes (WI-pohom-turot part 2).

Until scheme v20 ``c_family_parameters`` recorded the text BEFORE a
parameter's declarator as its type, so the ``*`` / ``&`` / ``&&`` / ``[]`` /
function-pointer shape a declarator adds was in no hash input:
``f(int)`` / ``f(int*)``, ``g(int)`` / ``g(int&)``, ``n(int&)`` /
``n(int&&)``, ``k(int**)`` / ``k(int*&)``, ``m(int a[])`` / ``m(int a)`` and
``p(int (*cb)(int))`` / ``p(int cb)`` tied, and the occurrence split numbered
them. Each entry now carries its declarator as written, the declared name
(and the names of a function pointer's own parameters) removed, in canonical
spacing, beside the type the other readers take as a type NAME. Each case is
parsed by the real grammar (C++, CUDA, GLSL, HLSL) and checked against a
control that must keep the value it had.
"""
from __future__ import annotations

import tree_sitter_language_pack as tlp

from hypergumbo_core.analyze.c_family import (
    c_family_parameter_declarator, c_family_parameter_list, c_family_parameters,
)
from hypergumbo_core.overload_arity import declared_arity_of_entries


def _params(lang: str, code: str) -> list[list[dict]]:
    tree = tlp.get_parser(lang).parse(code.encode())
    out = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            plist = c_family_parameter_list(node)
            assert plist is not None
            out.append(c_family_parameters(plist, code.encode()))
        stack.extend(reversed(node.children))
    return out


def _signature(lang: str, code: str) -> list[str]:
    """The typed tier's ``(T1,T2)`` of each definition in ``code``."""
    out = []
    for entries in _params(lang, code):
        arity = declared_arity_of_entries(entries)
        assert arity is not None
        out.append(arity.type_signature)
    return out


def test_each_mark_is_recorded_beside_the_type_without_the_name() -> None:
    (entries,) = _params("cpp", (
        "void f(int* const q, int a[3], int (*cb)(int x), int (*pa)[4],"
        " int*& s, int&& r, const char* t, int plain) {}\n"
    ))
    assert [(e["name"], e["type"], e.get("declarator")) for e in entries] == [
        ("q", "int", "*const"),
        ("a", "int", "[3]"),
        ("cb", "int", "(*)(int)"),
        ("pa", "int", "(*)[4]"),
        ("s", "int", "*&"),
        ("r", "int", "&&"),
        ("t", "const char", "*"),
        # The control: a plain parameter's entry is the triple it always was.
        ("plain", "int", None),
    ]
    assert "declarator" not in entries[-1]


def test_every_tie_of_the_item_hashes_apart() -> None:
    """The pairs the item names, each a different declaration."""
    pairs = [
        ("void f(int a) {}", "void f(int* a) {}"),
        ("void g(int a) {}", "void g(int& a) {}"),
        ("void n(int& a) {}", "void n(int&& a) {}"),
        ("void k(int** a) {}", "void k(int*& a) {}"),
        ("void m(int a[]) {}", "void m(int a) {}"),
        ("void m(int a[]) {}", "void m(int a[2]) {}"),
        ("void p(int (*cb)(int)) {}", "void p(int cb) {}"),
        ("void p(int (*cb)(int)) {}", "void p(int (*cb)(long)) {}"),
    ]
    for one, other in pairs:
        assert _signature("cpp", one) != _signature("cpp", other), (one, other)


def test_nothing_is_evaluated_an_array_is_not_a_pointer() -> None:
    """``int a[]`` and ``int* a`` declare the same parameter type in C++, and
    ``const int a`` the same as ``int a``; they are hashed as written, apart."""
    assert _signature("cpp", "void m(int a[]) {}") != _signature("cpp", "void m(int* a) {}")
    assert _signature("cpp", "void m(const int a) {}") != _signature("cpp", "void m(int a) {}")


def test_an_unnamed_parameter_hashes_like_the_named_one() -> None:
    """A prototype and its definition must not move apart: the name, and the
    names of a function pointer's own parameters, are not hashed."""
    pairs = [
        ("void f(int*) {}", "void f(int* a) {}"),
        ("void f(int&&) {}", "void f(int&& a) {}"),
        ("void f(int[3]) {}", "void f(int a[3]) {}"),
        ("void f(int (*)(int)) {}", "void f(int (*cb)(int x)) {}"),
        ("void f(int (*)(int (*)(char))) {}", "void f(int (*cb)(int (*g)(char c))) {}"),
        ("void f(int* const) {}", "void f(int* const p) {}"),
    ]
    for unnamed, named in pairs:
        assert _signature("cpp", unnamed) == _signature("cpp", named), (unnamed, named)


def test_layout_and_comments_are_not_hashed() -> None:
    spellings = [
        "void f(int *a) {}", "void f(int* a) {}", "void f(int * a) {}",
        "void f(int\n  *a) {}", "void f(int /* p */ * a) {}",
    ]
    assert len({tuple(_signature("cpp", s)) for s in spellings}) == 1
    fps = ["void f(int (*cb)(int)) {}", "void f(int ( * cb ) ( int )) {}"]
    assert len({tuple(_signature("cpp", s)) for s in fps}) == 1


def test_a_plain_signature_keeps_its_spelling() -> None:
    """The control: a list with no declarator mark spells what it spelled."""
    assert _signature("cpp", "void f(int a, const char b) {}") == ["(int,const char)"]
    assert _signature("cpp", "void f(int a, int* b) {}") == ["(int,int*)"]


def test_an_unnamed_void_pointer_is_a_parameter_not_an_empty_list() -> None:
    """``f(void)`` declares none; ``f(void*)`` declares one, unnamed."""
    assert _params("cpp", "void f(void) {}") == [[]]
    (entries,) = _params("cpp", "void f(void*) {}")
    assert [(e["name"], e["type"], e.get("declarator")) for e in entries] == [("", "void", "*")]


def test_a_pack_keeps_its_reference_mark() -> None:
    plain, forwarding = _params("cpp", (
        "template <class... A> void f(A... a) {}\n"
        "template <class... A> void f(A&&... a) {}\n"
    ))
    assert plain[0]["declarator"] == "..."
    assert forwarding[0]["declarator"] == "&&..."
    assert plain[0]["variadic"] and forwarding[0]["variadic"]


def test_the_shader_and_cuda_grammars_read_the_same_marks() -> None:
    """CUDA extends tree-sitter-cpp, HLSL too, GLSL extends tree-sitter-c:
    each records an array or pointer mark, and an HLSL semantic (a sibling of
    the declarator) is not one."""
    assert _signature("cuda", "__device__ void f(float* a) {}") != _signature(
        "cuda", "__device__ void f(float a) {}")
    assert _signature("glsl", "void f(in vec2 a[3]) {}") == ["(in vec2[3])"]
    assert _signature("glsl", "void f(in vec2 a) {}") == ["(in vec2)"]
    (hlsl,) = _params("hlsl", "float f(float a[2], float4 p : POSITION) { return 0; }\n")
    assert [(e["type"], e.get("declarator")) for e in hlsl] == [
        ("float", "[2]"), ("float4", None),
    ]


def test_parameter_declarator_of_a_plain_or_absent_declarator_is_empty() -> None:
    code = "void f(int a, int) {}\n"
    tree = tlp.get_parser("cpp").parse(code.encode())
    plist = c_family_parameter_list(tree.root_node.children[0])
    assert plist is not None
    params = [c for c in plist.children if c.type == "parameter_declaration"]
    assert [c_family_parameter_declarator(p, code.encode()) for p in params] == ["", ""]
