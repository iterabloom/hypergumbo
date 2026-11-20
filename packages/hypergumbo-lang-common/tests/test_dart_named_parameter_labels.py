# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Dart named parameter's name is part of its callable's hashed signature.

A caller writes a named parameter's name (``f(count: 1)`` calls ``void
f({required int count})``), so renaming it changes the API: it is identity,
as a Swift argument label is. Until stable-id v17 the hashed text held types
only, so ``void f({int a})`` and ``void f({int b})`` shared an id. Each named
parameter is now hashed as ``name:Type`` through the helper Swift's labels
use (``labelled_parameter``), so the two languages spell a label alike.

The same holds inside a function type: ``void Function({required int z})``
is a type whose callers write ``z:``, and a function type with ``{int y}``
is another type. A positional parameter's name (in a list, in an ``[optional]``
group, in a function type) is still left out: callers never write it.

Constructors are not covered here: their stable_id hashes no parameter list
at all (the owner-qualified name is the whole discriminant, WI-tusad).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tree_sitter_language_pack import get_parser

from hypergumbo_core.analyze.base import iter_tree, labelled_parameter
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


def _ids(tmp_path: Path, first: str, second: str, name: str) -> tuple[str, str]:
    a = _symbol(tmp_path / "x", first, name).stable_id
    b = _symbol(tmp_path / "y", second, name).stable_id
    assert a is not None and b is not None
    return a, b


#: Two declarations of one callable that differ only in a named parameter's
#: name (and the symbol's name).
NAMED_RENAMES = {
    "required": ("void f({required int count}) {}", "void f({required int total}) {}", "f"),
    "optional-with-default": ("void f({int count = 0}) {}", "void f({int total = 0}) {}", "f"),
    "nullable": ("void f({String? label}) {}", "void f({String? tag}) {}", "f"),
    "after-positional": ("void f(int a, {bool flag = false}) {}",
                         "void f(int a, {bool flagged = false}) {}", "f"),
    "second-of-two": ("void f({int a = 0, int b = 0}) {}", "void f({int a = 0, int c = 0}) {}", "f"),
    "untyped": ("void f({x}) {}", "void f({y}) {}", "f"),
    "annotated": ("void f({@A() int a = 1}) {}", "void f({@A() int b = 1}) {}", "f"),
    "final": ("void f({final int a = 1}) {}", "void f({final int b = 1}) {}", "f"),
    "covariant": ("class C { void f({covariant num a = 1}) {} }",
                  "class C { void f({covariant num b = 1}) {} }", "C.f"),
    "prefixed-type": ("void f({required p.Foo foo}) {}", "void f({required p.Foo bar}) {}", "f"),
    "function-typed": ("void f({required void Function(int v) onValue}) {}",
                       "void f({required void Function(int v) onItem}) {}", "f"),
    "old-style-function-typed": ("void f({int cb(int v)?}) {}", "void f({int fn(int v)?}) {}", "f"),
    "method": ("class C { void f({required int count}) {} }",
               "class C { void f({required int total}) {} }", "C.f"),
    "static-method": ("class C { static void f({int count = 0}) {} }",
                      "class C { static void f({int total = 0}) {} }", "C.f"),
    "extension-method": ("extension E on int { int f({required int other}) => other; }",
                         "extension E on int { int f({required int rhs}) => rhs; }", "E.f"),
    "local-function": ("void g() { void f({int a = 0}) {} }", "void g() { void f({int b = 0}) {} }", "f"),
    # Inside a function type: the type's callers write the name too.
    "in-function-type": ("void f(void Function({required int z}) cb) {}",
                         "void f(void Function({required int y}) cb) {}", "f"),
    "in-function-type-optional": ("void f(void Function(int a, {String? s}) cb) {}",
                                  "void f(void Function(int a, {String? t}) cb) {}", "f"),
    "in-nested-function-type": ("void f(Map<String, void Function({int u})>? m) {}",
                                "void f(Map<String, void Function({int v})>? m) {}", "f"),
    "in-return-type": ("int Function({int k}) f() => throw 0;",
                       "int Function({int j}) f() => throw 0;", "f"),
    "in-old-style-parameter": ("void f(int g({int a})) {}", "void f(int g({int b})) {}", "f"),
    "in-field-type": ("class C { void Function({int k})? cb; }",
                      "class C { void Function({int j})? cb; }", "C.cb"),
}


@pytest.mark.parametrize("case", sorted(NAMED_RENAMES))
def test_renaming_a_named_parameter_moves_the_id(tmp_path: Path, case: str) -> None:
    first, second, name = NAMED_RENAMES[case]
    a, b = _ids(tmp_path, first, second, name)
    assert a != b


#: Renames callers never see: a positional parameter's name, wherever it is.
POSITIONAL_RENAMES = {
    "positional": ("void f(int count) {}", "void f(int total) {}"),
    "optional-positional": ("void f([int count = 0]) {}", "void f([int total = 0]) {}"),
    "positional-beside-named": ("void f(int pos, {int a = 0}) {}", "void f(int position, {int a = 0}) {}"),
    "positional-in-function-type": ("void f(void Function(int z) cb) {}",
                                    "void f(void Function(int y) cb) {}"),
    "positional-in-named-function-type": ("void f({required void Function(int v) cb}) {}",
                                          "void f({required void Function(int w) cb}) {}"),
    "positional-in-old-style-named": ("void f({int cb(int v)?}) {}", "void f({int cb(int w)?}) {}"),
    "function-typed-positional": ("void f(void Function({int a}) cb) {}",
                                  "void f(void Function({int a}) callback) {}"),
}


@pytest.mark.parametrize("case", sorted(POSITIONAL_RENAMES))
def test_renaming_a_positional_parameter_keeps_the_id(tmp_path: Path, case: str) -> None:
    first, second = POSITIONAL_RENAMES[case]
    a, b = _ids(tmp_path, first, second, "f")
    assert a == b


#: The hashed text: each named parameter ``name:`` before what was hashed
#: for it (its annotations, modifiers and type).
HASHED = {
    "void f({required int count}) {}": "({count:required int})",
    "void f(int pos, {String? label, required bool flag}) {}": "(int,{label:String?,flag:required bool})",
    "void f([int count = 0]) {}": "([int])",
    "void f({x}) {}": "({x:dynamic})",
    "void f({required p.Foo foo}) {}": "({foo:required Foo})",
    "void f({@A() final int a = 1}) {}": "({a:@A() final int})",
    "void f({required void Function(int v) onValue}) {}": "({onValue:required void Function(int)})",
    "void f({int cb(int v)?}) {}": "({cb:int Function(int)?})",
    "void f(void Function(int a, {required int z, String w}) cb) {}":
        "(void Function(int, {z:required int, w:String}))",
    "void f(int g(int a, {int b})) {}": "(int Function(int, {b:int}))",
    "int Function({int k}) f() => throw 0;": "()int Function({k:int})",
    "void f({\n  // c\n  required String text, // trailing\n  int sid = 1,\n}) {}":
        "({text:required String,sid:int})",
    # The comment goes, its leading space stays: layout is ``layout_free``'s.
    "void f(void Function({/* c */ required int z}) cb) {}": "(void Function({ z:required int}))",
    # A record type's named field is kept as written, as before.
    "void f(({int a}) r, {({int a}) s = const (a: 1)}) {}": "(({int a}),{s:({int a})})",
}


@pytest.mark.parametrize(("decl", "hashed"), HASHED.items())
def test_the_hashed_text_labels_each_named_parameter(decl: str, hashed: str) -> None:
    assert _hashed(decl) == hashed


def test_dart_spells_a_label_as_the_shared_helper_does() -> None:
    """Dart and Swift labels go through one helper, so they read alike."""
    assert _hashed("void f({int count = 0}) {}") == "({" + labelled_parameter("count", "int") + "})"


def test_a_constructor_still_hashes_no_parameter_list(tmp_path: Path) -> None:
    """Out of this change's reach: a constructor's id is its owner-qualified
    name only, so a named parameter's rename keeps it (``this.x``, ``super.x``
    and typed named parameters alike)."""
    for first, second, name in (
        ("class C { int count; C({required this.count}); }",
         "class C { int count; C({required this.total}); }", "C.C"),
        ("class B { B({int k = 0}); } class C extends B { C({super.k}); }",
         "class B { B({int k = 0}); } class C extends B { C({super.j}); }", "C.C"),
        ("class C { C.named({int a = 0}); }", "class C { C.named({int b = 0}); }", "C.C.named"),
    ):
        root = tmp_path / f"{name}-{len(first)}"
        root.mkdir()
        a, b = _ids(root, first, second, name)
        assert a == b
