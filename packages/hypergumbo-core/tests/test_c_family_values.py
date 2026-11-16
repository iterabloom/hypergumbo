# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a C-family expression is a VALUE, and which declarator an array table is
(META-mino; ``c_family_is_value_operand`` / ``c_family_array_declarator``)."""
from __future__ import annotations

import pytest
import tree_sitter_language_pack as tlp

from hypergumbo_core.analyze.c_family import (
    c_family_array_declarator,
    c_family_is_value_operand,
)


def _named(lang: str, code: str, text: str) -> list:
    tree = tlp.get_parser(lang).parse(code.encode())
    out = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in ("identifier", "qualified_identifier") and node.text.decode() == text:
            out.append(node)
        stack.extend(node.children)
    return out


@pytest.mark.parametrize("lang", ["c", "cpp"])
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("int (*g)(void) = f;", True),
        ("void m(void) { h = f; }", True),
        ("int (*m(void))(void) { return f; }", True),
        ("struct s x = { .run = f };", True),
        ("struct s x = { \"n\", f };", True),
        ("int (*t[])(void) = { f };", True),
        ("void m(void) { f(); }", False),
        ("void m(void) { g(f); }", False),
        ("void m(void) { f = g; }", False),
        ("void m(void) { x += f; }", False),
        ("void m(void) { x -= f; }", False),
        ("void m(void) { int y = f + 1; }", False),
        ("void f(void) {}", False),
    ],
)
def test_value_positions(lang: str, code: str, expected: bool) -> None:
    nodes = _named(lang, code, "f")
    assert nodes, code
    assert any(c_family_is_value_operand(n) for n in nodes) is expected, code


@pytest.mark.parametrize("lang", ["c", "cpp"])
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("int (*t[])(void) = { f };", "t"),
        ("static struct cmd t[] = { { \"a\", f } };", "t"),
        ("int (*g)(void) = f;", None),
        ("struct s x = { .run = f };", None),
    ],
)
def test_the_array_a_declarator_declares(lang: str, code: str, expected: str | None) -> None:
    tree = tlp.get_parser(lang).parse(code.encode())
    decl = tree.root_node.named_children[0]
    init = next(c for c in decl.named_children if c.type == "init_declarator")
    found = c_family_array_declarator(init.child_by_field_name("declarator"))
    if expected is None:
        assert found is None
    else:
        assert found is not None
        assert found.child_by_field_name("declarator").text.decode() == expected


@pytest.mark.parametrize("lang", ["c", "cpp"])
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("int m(int f) { return f; }", True),
        ("int m(void) { int f = 1; return f; }", True),
        ("int m(void) { int (*f)(void) = 0; int (*g)(void) = f; return g(); }", True),
        ("int m(void) { int *f, g; int *h = f; return g + *h; }", True),
        ("int m(void) { int xs[3]; int (*h)(void); h = f; return xs[0]; }", False),
        ("int (*g)(void) = f;", False),
        # a file-scope VARIABLE of that name shadows the function (zstd)
        ("static const int f = 4; int m(void) { int x = f; return x; }", True),
        # a prototype is the function, not a variable -- a pointer return too
        ("int f(void); int m(void) { int (*h)(void) = f; return h(); }", False),
        ("void *f(int n); int m(void) { void *(*h)(int) = f; return 0; }", False),
        # a macro-wrapped prototype reads as a variable, but the file defines f
        ("void *f _Z_OF((int)); void *f(int n) { return 0; } int m(void) { void *(*h)(int) = f; return 0; }", False),
        # a file-scope function POINTER variable of that name is a variable
        ("void *(*f)(int) = 0; int m(void) { void *(*h)(int) = f; return 0; }", True),
    ],
)
def test_a_name_declared_in_the_enclosing_function(lang: str, code: str, expected: bool) -> None:
    from hypergumbo_core.analyze.c_family import c_family_declares_local

    nodes = [n for n in _named(lang, code, "f") if c_family_is_value_operand(n)]
    assert nodes, code
    assert c_family_declares_local(nodes[0], "f", code.encode()) is expected, code


def test_a_cpp_template_parameter_is_local() -> None:
    from hypergumbo_core.analyze.c_family import c_family_declares_local

    code = "template<int size> void f(short *dst) { int n = size; dst[0] = n; }"
    nodes = [n for n in _named("cpp", code, "size") if c_family_is_value_operand(n)]
    assert nodes
    assert c_family_declares_local(nodes[0], "size", code.encode()) is True


def test_a_cpp_range_for_variable_is_local() -> None:
    """apt: ``for (auto const& Arch : Archs) { Itm.Arch = Arch; }``."""
    from hypergumbo_core.analyze.c_family import c_family_declares_local

    code = "void f(int *xs) { for (auto const& Arch : xs) { int y = Arch; } }"
    nodes = [n for n in _named("cpp", code, "Arch") if c_family_is_value_operand(n)]
    assert nodes
    assert c_family_declares_local(nodes[0], "Arch", code.encode()) is True


@pytest.mark.parametrize("lang", ["c", "cpp"])
def test_a_local_enumerator_is_local(lang: str) -> None:
    from hypergumbo_core.analyze.c_family import c_family_declares_local

    code = "void f(void) { enum { empty, full } state = empty; }"
    nodes = [n for n in _named(lang, code, "empty") if c_family_is_value_operand(n)]
    assert nodes
    assert c_family_declares_local(nodes[0], "empty", code.encode()) is True
