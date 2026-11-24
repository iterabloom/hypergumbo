# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/c_family.py
"""The preprocessor branches open at a C-family declaration
(``c_family_open_guards``) and the scope chain that carries them
(``c_family_scope_chain(guards=True)``), on real C and C++ trees (WI-sifar).

A branch is named by its conditional's opening directive and, after the first,
its own (``#if A #elif B``, ``#ifdef X #else``), as Swift's ``#if`` branches
are; each level sits in the chain where it opens. A whole-file include guard
is no branch. The branches are read off the directives in source order, so
tree-sitter's recovery from ``#ifdef __cplusplus extern "C" { #endif`` (which
leaves the rest of the file inside the conditional) does not put a header's
declarations under ``__cplusplus``.
"""
from __future__ import annotations

import tree_sitter_language_pack as tlp

from hypergumbo_core.analyze.base import iter_tree
from hypergumbo_core.analyze.c_family import c_family_open_guards, c_family_scope_chain

_DECLS = ("function_definition", "declaration", "struct_specifier", "field_declaration")


def _chains(lang: str, code: str, **kwargs: bool) -> dict[int, list[str]]:
    """Line -> guarded scope chain of the first declaration node on it."""
    source = code.encode()
    tree = tlp.get_parser(lang).parse(source)
    chains: dict[int, list[str]] = {}
    for node in iter_tree(tree.root_node):
        line = node.start_point[0] + 1
        if node.type in _DECLS and line not in chains:
            chains[line] = c_family_scope_chain(node, source, guards=True, **kwargs)
    return chains


def test_branches_are_named_by_their_chain_head_and_their_own() -> None:
    chains = _chains("c", """\
#if defined(A)
int a(void) { return 1; }
#elif B
int b(void) { return 2; }
#elifdef C
int c(void) { return 3; }
#else
int d(void) { return 4; }
#endif
#ifndef E
int e(void) { return 5; }
#endif
int plain(void) { return 6; }
#if 0
int dead(void) { return 7; }
#endif
""")
    assert chains[2] == ["#if defined(A)"]
    assert chains[4] == ["#if defined(A) #elif B"]
    assert chains[6] == ["#if defined(A) #elifdef C"]
    assert chains[8] == ["#if defined(A) #else"]
    assert chains[11] == ["#ifndef E"]
    assert chains[13] == []
    assert chains[15] == ["#if 0"]


def test_nested_branches_compose_outermost_first_at_their_level() -> None:
    chains = _chains("cpp", """\
#ifdef A
namespace n {
#ifdef B
struct K {
#ifndef C
  int run(int x) { return x; }
#endif
};
#endif
}
#endif
""")
    assert chains[6] == ["#ifdef A", "n", "#ifdef B", "K", "#ifndef C"]
    assert chains[4] == ["#ifdef A", "n", "#ifdef B"]


def test_a_branch_inside_a_function_joins_the_function_chain() -> None:
    chains = _chains("c", """\
void outer(void) {
#ifdef A
  struct inner { int x; };
#endif
}
""", types=False, functions=True)
    assert chains[3] == ["outer", "#ifdef A"]


def test_condition_layout_and_comments_are_not_in_the_label() -> None:
    one = _chains("c", "#if defined(A) && defined(B)\nint f(void) { return 1; }\n#endif\n")
    two = _chains("c", "#  if defined( A ) /* x */ && \\\n    defined(B)\nint f(void) { return 1; }\n#endif\n")
    other = _chains("c", "#if defined(B) && defined(A)\nint f(void) { return 1; }\n#endif\n")
    assert one[2] == two[3] == ["#if defined(A)&&defined(B)"]
    # Operands are not reordered: the condition is hashed as written.
    assert other[2] == ["#if defined(B)&&defined(A)"]


def test_include_guards_are_not_branches() -> None:
    for code in (
        "// header\n#ifndef FOO_H\n#define FOO_H\nint f(void) { return 1; }\n#endif /* FOO_H */\n",
        "#if !defined(FOO_H)\n#define FOO_H 1\nint f(void) { return 1; }\n#endif FOO_H\n",
        "#if !defined FOO_H\n#define FOO_H\nint f(void) { return 1; }\n#endif\n// end\n",
        # ``#pragma once`` before the guard: both idioms in one header
        "#pragma once\n#ifndef FOO_H\n#define FOO_H\nint f(void) { return 1; }\n#endif\n",
        "// c\n#  pragma   once /* both */\n\n#ifndef FOO_H\n#define FOO_H\nint f(void) { return 1; }\n#endif\n",
        # no line break at the end of the file
        "#ifndef FOO_H\r\n#define FOO_H\r\nint f(void) { return 1; }\r\n#endif",
    ):
        chains = _chains("c", code)
        assert list(chains.values()) == [[]], code


def test_a_guard_that_is_not_the_whole_file_is_a_branch() -> None:
    cases = {
        # code after the #endif
        "#ifndef FOO_H\n#define FOO_H\nint f(void) { return 1; }\n#endif\nint g(void) { return 2; }\n": 3,
        # code before the #ifndef
        "int g(void) { return 2; }\n#ifndef FOO_H\n#define FOO_H\nint f(void) { return 1; }\n#endif\n": 4,
        # the #define names another macro
        "#ifndef FOO_H\n#define BAR_H\nint f(void) { return 1; }\n#endif\n": 3,
        # something between the #ifndef and its #define
        "#ifndef FOO_H\nint g(void);\n#define FOO_H\nint f(void) { return 1; }\n#endif\n": 4,
        # an #else at its level
        "#ifndef FOO_H\n#define FOO_H\nint f(void) { return 1; }\n#else\nint h(void);\n#endif\n": 3,
        # a later directive after its #endif
        "#ifndef FOO_H\n#define FOO_H\nint f(void) { return 1; }\n#endif\n#ifdef Z\n#endif\n": 3,
        # another directive before it, even another pragma or a second once
        "#pragma pack(1)\n#ifndef FOO_H\n#define FOO_H\nint f(void) { return 1; }\n#endif\n": 4,
        "#pragma once\n#include <x.h>\n#ifndef FOO_H\n#define FOO_H\nint f(void) { return 1; }\n#endif\n": 5,
        "#pragma once extra\n#ifndef FOO_H\n#define FOO_H\nint f(void) { return 1; }\n#endif\n": 4,
        # no #endif (the file ends inside it)
        "#ifndef FOO_H\n#define FOO_H\nint f(void) { return 1; }\n": 3,
        # no #define at all
        "#ifndef FOO_H\nint f(void) { return 1; }\n#endif\n": 2,
        # a plain #ifdef
        "#ifdef FOO_H\n#define FOO_H\nint f(void) { return 1; }\n#endif\n": 3,
    }
    for code, line in cases.items():
        chains = _chains("c", code)
        assert chains[line] and chains[line][0].startswith("#if"), code


def test_extern_c_recovery_does_not_guard_the_header() -> None:
    """tree-sitter keeps everything after ``#ifdef __cplusplus extern "C" {
    #endif`` inside the conditional; in source order it is closed."""
    chains = _chains("c", """\
#ifndef H_H
#define H_H
#ifdef __cplusplus
extern "C" {
#endif
struct S { int x; };
static int g(void) { return 1; }
#ifdef __cplusplus
}
#endif
#endif
""")
    assert chains[6] == []
    assert chains[7] == []


def test_stray_directives_are_read_and_unbalanced_ones_ignored() -> None:
    source = b"#endif\n#else\nint a(void) { return 1; }\n"
    tree = tlp.get_parser("c").parse(source)
    fn = next(n for n in iter_tree(tree.root_node) if n.type == "function_definition")
    assert c_family_open_guards(fn, source) == []
    source = b"struct S {\n#if A\n};\n#elif B\nint f(void) { return 1; }\n#endif\n"
    tree = tlp.get_parser("c").parse(source)
    fn = next(n for n in iter_tree(tree.root_node) if n.type == "function_definition")
    assert [label for _start, label in c_family_open_guards(fn, source)] == ["#if A #elif B"]


def test_unguarded_chain_is_unchanged() -> None:
    code = "namespace a { struct B { int run(int x) { return x; } }; }\n"
    source = code.encode()
    tree = tlp.get_parser("cpp").parse(source)
    fn = next(n for n in iter_tree(tree.root_node) if n.type == "function_definition")
    assert c_family_scope_chain(fn, source, guards=True) == c_family_scope_chain(fn, source)
    assert c_family_scope_chain(fn, source) == ["a", "B"]
