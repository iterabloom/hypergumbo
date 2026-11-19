# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Dart callable's hashed signature holds types only, every group closed.

The hashed text was the displayed signature put through the types-first
rule, which drops each comma-separated piece's last word. A Dart parameter
list is not a flat comma list, so the rule kept names and lost brackets:

- an optional positional group ``[int c = 1, String? d]`` is one piece to the
  split (``[`` is a bracket to it, ``{`` is not), so only ``d`` was dropped,
  ``c`` was hashed, and the ``]`` went with ``d`` (``(int,[int c, String?)``);
  a named group's ``}`` went with its last name the same way;
- a function type's parameter names were hashed (``void Function(int
  numProcessedChunks)``), in a parameter, a return type and a field type;
- an old-style function-typed parameter ``int g(int a)`` was hashed whole,
  its own name and its parameters' names included;
- an untyped parameter ``x`` was hashed as its name (the rule keeps a lone word);
- a bracket inside an annotation's string (``@A("x)") int a``) unbalanced
  the split: the text kept every name, or the callable got no typed id.

So renaming any of those parameters moved the id. The text is now rendered
from the syntax tree, one parameter at a time: its annotations, modifiers
and type with every parameter name left out, a group as its bracket, its
parameters and its closing bracket, an old-style function-typed parameter as
the function type it declares (``int Function(int)``). Dart has no
overloading, so each pair is analysed in two trees with the same file path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tree_sitter_language_pack import get_parser

from hypergumbo_core.analyze.base import iter_tree
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_common.dart import _dart_hashed_signature, analyze_dart


def _hashed(decl: str) -> str:
    """The hashed text of the one function ``decl`` declares."""
    source = decl.encode()
    tree = get_parser("dart").parse(source)
    [sig] = [n for n in iter_tree(tree.root_node) if n.type == "function_signature"]
    return _dart_hashed_signature(sig, source)


def _symbol(root: Path, source: str, name: str) -> Symbol:
    root.mkdir()
    (root / "a.dart").write_text(source)
    [found] = [s for s in analyze_dart(root).symbols if s.name == name]
    return found


def _same_id(tmp_path: Path, first: str, second: str, name: str = "f") -> None:
    a = _symbol(tmp_path / "x", first, name)
    b = _symbol(tmp_path / "y", second, name)
    assert a.stable_id is not None
    assert a.stable_id == b.stable_id, (a.signature, b.signature)


#: Two declarations of one callable that differ only in parameter names (or
#: in how an old-style function-typed parameter spells its type).
RENAMES = {
    "optional-group": ("void f(int a, [int c = 1, String? d]) {}",
                       "void f(int a, [int cc = 1, String? dd]) {}"),
    "optional-group-one": ("void f([int c]) {}", "void f([int cc]) {}"),
    "named-group": ("void f({required int a, int b = 2}) {}",
                    "void f({required int aa, int bb = 2}) {}"),
    "untyped": ("void f(x, int y) {}", "void f(z, int y) {}"),
    "untyped-is-dynamic": ("void f(x) {}", "void f(dynamic y) {}"),
    "untyped-in-old-style": ("void f(int g(a)) {}", "void f(int h(b)) {}"),
    "function-type": ("void f(void Function(int n, {required String s}) cb) {}",
                      "void f(void Function(int m, {required String t}) cb) {}"),
    "function-type-optional": ("void f(void Function([int n]) cb) {}",
                               "void f(void Function([int m]) cb) {}"),
    "function-type-nested": ("void f(Map<String, void Function(int u)>? m) {}",
                             "void f(Map<String, void Function(int v)>? m) {}"),
    "old-style": ("void f(int g(int a, [String b])?) {}",
                  "void f(int h(int c, [String d])?) {}"),
    "old-style-is-function-type": ("void f(int g(int a)) {}",
                                   "void f(int Function(int) g) {}"),
    "old-style-generic": ("void f(T g<T>(T t)) {}", "void f(T Function<T>(T) g) {}"),
    "return-type": ("void Function(int x) f() => (_) {};",
                    "void Function(int y) f() => (_) {};"),
    "method": ("class C { int f(int a, [int c = 1]) => a; }",
               "class C { int f(int b, [int d = 1]) => b; }"),
    # A bracket inside an annotation's string: the text split kept every name.
    "annotation-string": ('void f(@A("x)") int a, int b) {}',
                          'void f(@A("x)") int c, int d) {}'),
}


@pytest.mark.parametrize("case", sorted(RENAMES))
def test_renaming_a_parameter_keeps_the_id(tmp_path: Path, case: str) -> None:
    first, second = RENAMES[case]
    _same_id(tmp_path, first, second, "C.f" if case == "method" else "f")


def test_a_bracket_in_an_annotation_string_still_gets_a_typed_id(tmp_path: Path) -> None:
    """``@A("(")`` opened a bracket the split never saw closed: no typed id."""
    f = _symbol(tmp_path / "x", 'void f(@A("(") int a, int b) {}', "f")
    assert f.stable_id is not None
    g = _symbol(tmp_path / "y", 'void f(@A("(") int a, String b) {}', "f")
    assert f.stable_id != g.stable_id


def test_a_field_function_type_hashes_no_parameter_name(tmp_path: Path) -> None:
    _same_id(tmp_path, "class C { void Function(int x)? cb; }",
             "class C { void Function(int y)? cb; }", "C.cb")


#: Pairs that must still hash apart: a type inside a group or a function
#: type, a group's kind, a modifier.
DISTINCT = {
    "group-kind": ("void f([int a]) {}", "void f({int a}) {}"),
    "untyped-is-a-parameter": ("void f(x) {}", "void f() {}"),
    "untyped-in-old-style-is-a-parameter": ("void f(int g(a)) {}", "void f(int g()) {}"),
    "group-member-type": ("void f(int a, [int c, String? d]) {}",
                          "void f(int a, [int c, int? d]) {}"),
    "group-position": ("void f(int a, [int c]) {}", "void f([int a, int c]) {}"),
    "required": ("void f({required int a}) {}", "void f({int a}) {}"),
    "required-after-default": ("void f({int a = 1, required int b}) {}",
                               "void f({int a = 1, int b}) {}"),
    "function-type-member": ("void f(void Function(int n) cb) {}",
                             "void f(void Function(String n) cb) {}"),
    "function-type-group": ("void f(void Function([int n]) cb) {}",
                            "void f(void Function({int n}) cb) {}"),
    "old-style-arity": ("void f(int g(int a)) {}", "void f(int g(int a, int b)) {}"),
    "record-field-name": ("void f(({int a}) r) {}", "void f(({int b}) r) {}"),
}


@pytest.mark.parametrize("case", sorted(DISTINCT))
def test_a_type_difference_still_moves_the_id(tmp_path: Path, case: str) -> None:
    first, second = DISTINCT[case]
    a = _symbol(tmp_path / "x", first, "f")
    b = _symbol(tmp_path / "y", second, "f")
    assert None not in (a.stable_id, b.stable_id)
    assert a.stable_id != b.stable_id


#: The item's repro and its siblings, as hashed texts.
HASHED = {
    "void f(int a, [int c = 1, String? d]) {}": "(int,[int,String?])",
    "Foo f({required int a, int b = 2, List<int>? c}) => x;": "({required int,int,List<int>?})Foo",
    "void f(int Function(int numProcessedChunks, int total)? cb) {}": "(int Function(int, int)?)",
    "void f({\n  // c\n  required String text, // trailing\n  int sid = 1,\n}) {}":
        "({required String,int})",
    'void f(@A("x)") int a, int b) {}': '(@A("x)") int,int)',
    # A comment inside a parameter is no type.
    "void f(int /* c */ a, {required /* d */ int b}) {}": "(int,{required int})",
    "void f(int g(int a, [String b])?) {}": "(int Function(int, [String])?)",
    "void f(void cb<T>(T t), (int a, {String n}) r) {}": "(void Function<T>(T),(int a, {String n}))",
    "List<void Function(int x)> f(Map<String, void Function({required int z})>? m) => [];":
        "(Map<String, void Function({required int})>?)List<void Function(int)>",
}


@pytest.mark.parametrize(("decl", "hashed"), HASHED.items())
def test_the_hashed_text_is_types_only_with_every_group_closed(decl: str, hashed: str) -> None:
    """A record type's field names stay (a named field is part of the type)."""
    assert _hashed(decl) == hashed
