# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared C-family declarator walk (``analyze.c_family``), on real C and C++ trees.

It moved to core so ``cuda.py`` (hypergumbo-lang-common) can share it with
``c.py`` / ``cpp.py`` (WI-fohuh). Each shape is parsed by the real grammar: the
walk's contract is about tree-sitter's node layout, which a hand-built node would
only restate. The parameter readers and the scope chain (WI-tusad: the
identity input C-family overloads were missing) are pinned on C++, CUDA, GLSL
and HLSL trees, the four grammars that share the shape.
"""
from __future__ import annotations

import tree_sitter_language_pack as tlp

from hypergumbo_core.analyze.c_family import (
    c_family_declarator, c_family_parameter_list, c_family_parameter_name,
    c_family_parameters, c_family_scope_chain,
)


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


def _params(lang: str, code: str) -> list[list[dict]]:
    out = []
    for d in _definitions(lang, code):
        plist = c_family_parameter_list(d)
        out.append(c_family_parameters(plist, code.encode()) if plist is not None else None)
    return out


def test_parameters_carry_types_defaults_and_variadics() -> None:
    got = _params("cpp", """int a(void) { return 0; }
int b(const char *s, int &n, int y = 2) { return 0; }
template <typename... Args> int c(Args&&... args) { return 0; }
int d(int, ...) { return 0; }
""")
    assert got[0] == []  # ``(void)`` is the C spelling of no parameter
    assert got[1] == [
        {"name": "s", "type": "const char", "default": False},
        {"name": "n", "type": "int", "default": False},
        {"name": "y", "type": "int", "default": True},
    ]
    assert got[2] == [{"name": "args", "type": "Args", "default": False, "variadic": True}]
    assert got[3] == [
        {"name": "", "type": "int", "default": False},
        {"name": "...", "type": None, "default": False, "variadic": True},
    ]


def test_a_conversion_operator_finds_its_abstract_declarators_list() -> None:
    code = "struct S { operator bool() const { return true; } };\n"
    (definition,) = _definitions("cpp", code)
    plist = c_family_parameter_list(definition)
    assert plist is not None and c_family_parameters(plist, code.encode()) == []


def test_no_parameter_list_without_a_function_declarator() -> None:
    """A macro-call-shaped declarator names nothing, so it lists nothing."""
    (definition,) = _definitions("c", "int PFX(name)(void) { return 0; }\n")
    assert c_family_parameter_list(definition) is None


def test_glsl_and_hlsl_qualifiers_belong_to_the_type() -> None:
    """GLSL overloads ``f(float)`` / ``f(vec2)`` and HLSL ``f(float2 x : SEM)``
    differ by these types: they are what the typed tier hashes."""
    glsl = _params("glsl", "vec2 f(in vec2 x, out float y[2]) { return x; }\n")
    assert glsl == [[
        {"name": "x", "type": "in vec2", "default": False},
        {"name": "y", "type": "out float", "default": False},
    ]]
    hlsl = _params(
        "hlsl", "float2 f(float2 x : TEXCOORD0, uniform float y = 1.0) { return x; }\n",
    )
    assert hlsl == [[
        {"name": "x", "type": "float2", "default": False},
        {"name": "y", "type": "uniform float", "default": True},
    ]]


def test_parameter_name_of_an_unnamed_parameter_is_empty() -> None:
    code = "int f(int) { return 0; }\n"
    (definition,) = _definitions("cpp", code)
    plist = c_family_parameter_list(definition)
    assert plist is not None
    (param,) = [c for c in plist.children if c.type == "parameter_declaration"]
    assert c_family_parameter_name(param, code.encode()) == ""


def test_scope_chain_names_namespaces_and_classes_outermost_first() -> None:
    code = """\
namespace outer::inner {
struct A { int size() { return 0; } };
namespace { class B { int size() { return 1; } }; }
}
int free_fn() { return 2; }
"""
    defs = _definitions("cuda", code)
    chains = [c_family_scope_chain(d, code.encode()) for d in defs]
    # an anonymous namespace adds nothing; a free function has no chain
    assert chains == [["outer::inner", "A"], ["outer::inner", "B"], []]


def _first(lang: str, code: str, node_type: str):
    tree = tlp.get_parser(lang).parse(code.encode())
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == node_type:
            return node
        stack.extend(reversed(node.children))
    raise AssertionError(node_type)  # pragma: no cover - the fixtures hold one


def test_scope_chain_adds_enclosing_functions_on_request() -> None:
    """INV-punig: a block-scope declaration's identity needs the functions
    around it; C leaves its struct nesting out (tags inside a struct are
    file-scope there), and a macro-named function contributes nothing."""
    code = "struct s {\n  int k;\n};\nvoid outer(void) {\n  struct inner { int w; } v;\n}\n"
    top = _first("c", code, "field_declaration_list").parent
    body = code[code.index("void"):]
    tag = _first("c", body, "struct_specifier")
    assert c_family_scope_chain(top, code.encode(), types=False, functions=True) == []
    assert c_family_scope_chain(tag, body.encode(), types=False, functions=True) == ["outer"]
    hlsl = "namespace a {\nfloat f(float x) {\n  float y = x;\n  return y;\n}\n}\n"
    local = _first("hlsl", hlsl, "declaration")
    assert c_family_scope_chain(local, hlsl.encode(), functions=True) == ["a", "f"]
    assert c_family_scope_chain(local, hlsl.encode()) == ["a"]
    macro = "int PFX(f)(void) {\n  struct t { int v; } x;\n}\n"
    assert c_family_scope_chain(
        _first("c", macro, "struct_specifier"), macro.encode(), types=False, functions=True,
    ) == []
