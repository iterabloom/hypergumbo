# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared C-family declarator walk (``analyze.c_family``), on real C and C++ trees.

It moved to core so ``cuda.py`` (hypergumbo-lang-common) can share it with
``c.py`` / ``cpp.py`` (WI-fohuh). Each shape is parsed by the real grammar: the
walk's contract is about tree-sitter's node layout, which a hand-built node would
only restate.
"""
from __future__ import annotations

import tree_sitter_language_pack as tlp

from hypergumbo_core.analyze.c_family import c_family_declarator


def _definitions(lang: str, code: str) -> list:
    tree = tlp.get_parser(lang).parse(code.encode())
    found = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            found.append(node)
        stack.extend(reversed(node.children))
    return found


def _text(node) -> str:
    return node.text.decode()


def test_pointer_levels_parentheses_and_function_pointer_returns() -> None:
    defs = _definitions("c", """\
int plain(int n) { return n; }
char **two(int n) { return 0; }
int (paren)(int n) { return n; }
int (*getfn(void))(int) { return 0; }
""")
    shapes = [c_family_declarator(d) for d in defs]
    assert [_text(s.name) for s in shapes] == ["plain", "two", "paren", "getfn"]
    # ``getfn``'s one level is the RETURNED pointer's ``*``; a caller renders no
    # return type when ``returns_function`` is set, so the count is never shown.
    assert [s.pointer_depth for s in shapes] == [0, 2, 0, 1]
    assert [s.returns_function for s in shapes] == [False, False, False, True]
    # The function's OWN declarator holds its parameters, not the returned
    # pointer's: ``getfn(void)``, not ``(int)``.
    assert _text(shapes[3].function_declarator) == "getfn(void)"


def test_a_reference_return_is_walked_through_its_unnamed_inner_declarator() -> None:
    """C++'s ``reference_declarator`` has no ``declarator`` field; the walk takes
    its declarator-or-name child."""
    (definition,) = _definitions("cpp", "int *&f() { static int *p; return p; }\n")
    shape = c_family_declarator(definition)
    assert _text(shape.name) == "f"
    assert shape.pointer_depth == 1


def test_a_macro_call_shaped_declarator_names_nothing() -> None:
    """``PFX(name)(args)``: a function_declarator directly inside another."""
    (definition,) = _definitions("c", "int PFX(cpu_test)(void) { return 0; }\n")
    assert c_family_declarator(definition) == (None, None, False, 0)


def test_a_definition_with_no_function_declarator_reports_its_leaf_only() -> None:
    """``TEST_BEGIN(test_x) {..}`` reads as a definition whose declarator is a
    parenthesised identifier: there is a leaf, but no function declarator, so the
    caller must not treat it as a function."""
    (definition,) = _definitions("c", "TEST_BEGIN(test_x) {\n  f(1);\n}\n")
    shape = c_family_declarator(definition)
    assert shape.function_declarator is None
    assert _text(shape.name) == "test_x"
