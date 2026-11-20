# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Dart callable's signature and a Dart field's type write every type, and
hash them.

The signature text of a function or method is normalized and hashed into the
typed ``stable_id``; so is the declared type of a field. Three renderers lost
types:

- the return type was the first ``type_identifier`` child of the
  ``function_signature``, so type arguments (``List<int>`` -> ``List``), a
  ``?`` (``int?`` -> ``int``), a library prefix's type (``p.Foo`` -> ``p``)
  and a whole function type, record type or bare ``Function`` were dropped;
- a field's type was its value list's preceding named sibling when that was
  a ``type_identifier``, so ``List<String>``, ``int?``, ``void Function()``,
  ``(int, int)`` and a type followed by a comment hashed the empty text;
- the parameters were written as their source text, but the types-first
  normalization they go through (shared with Java, C# and Groovy, unchanged
  here) keeps what follows the last dot of the type's head, so a dot inside a
  function or record type (``int Function(p.A) g`` -> ``A)``) or behind a
  modifier or a group bracket (``{required p.Foo? q}`` -> ``Foo?``) took the
  rest of the type with it; and an old-style function-typed parameter whose
  last word is not its name alone (``int g()``, ``int g(int)``,
  ``int g(int a)?``) hashed as ``int``, ``int`` and ``int g(int``.

The hashed text is now read from the syntax tree one parameter at a time
(``_dart_hashed_signature``; every parameter name left out and every group
closed, see ``test_dart_hashed_signature_types_only``); the Dart rules for
these holes are applied to each parameter's text.

Each test below fills one hole: two declarations that differ only inside it
get two stable ids, and the rendered text contains the type. The last tests
pin the hashed text and the id of declarations that had no hole: they hash
as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tree_sitter_language_pack import get_parser

from hypergumbo_core.analyze.base import iter_tree, normalize_signature_types_first
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_common import dart
from hypergumbo_lang_common.dart import (
    _dart_hashed_signature,
    _extract_dart_return_type_name,
    _extract_dart_signature,
    analyze_dart,
)
from hypergumbo_core.identity_schemes import STABLE_ID_PREFIX


def _signature_node(decl: str) -> tuple:
    source = decl.encode()
    tree = get_parser("dart").parse(source)
    [sig] = [n for n in iter_tree(tree.root_node) if n.type == "function_signature"]
    return sig, source


def _hashed(decl: str) -> str:
    """The hashed text of the one function ``decl`` declares."""
    return _dart_hashed_signature(*_signature_node(decl))


def _displayed(decl: str) -> str:
    """The signature the analyzer displays for that function."""
    displayed = _extract_dart_signature(*_signature_node(decl))
    assert displayed is not None
    return displayed


def _symbol(root: Path, source: str, name: str) -> Symbol:
    """The one symbol called ``name`` declared by ``source`` (as ``a.dart``
    under ``root``; two roots give ids that compare, the file path being the
    same)."""
    root.mkdir()
    (root / "a.dart").write_text("import 'package:x/x.dart' as p;\n" + source)
    [found] = [s for s in analyze_dart(root).symbols if s.name == name]
    return found


def _pair(tmp_path: Path, first: str, second: str, name: str) -> tuple[Symbol, Symbol]:
    """Dart has no overloading, so the two declarations of a pair are
    analysed in two trees with the same file path."""
    return _symbol(tmp_path / "x", first, name), _symbol(tmp_path / "y", second, name)


# (symbol name, first declaration, second declaration, text the first's
# signature must contain): a return type of a node kind the renderer had no
# branch for, or that it cut.
RETURNS = [
    ("f", "List<int> f() => [];", "List<String> f() => [];", "() List<int>"),
    ("f", "int? f() => null;", "int f() => 1;", "() int?"),
    ("f", "p.Foo f() => null;", "p.Bar f() => null;", "() p.Foo"),
    ("f", "p.Foo<int>? f() => null;", "p.Foo f() => null;", "() p.Foo<int>?"),
    ("f", "int Function(int) f() => (x) => x;", "void f() {}", "() int Function(int)"),
    ("f", "int Function<T>(T)? f() => null;", "void f() {}", "() int Function<T>(T)?"),
    ("f", "Function f() => () {};", "void f() {}", "() Function"),
    ("f", "(int, String) f() => (1, '');", "void f() {}", "() (int, String)"),
    ("f", "({int a, String b}) f() => (a: 1, b: '');", "({int a, int b}) f() => (a: 1, b: 2);",
     "() ({int a, String b})"),
    ("f", "(int,)? f() => null;", "(int,) f() => (1,);", "() (int,)?"),
    ("f", "Future<void> f() async {}", "Future<int> f() async => 1;", "() Future<void>"),
    ("f", "FutureOr<int> f() => 1;", "FutureOr<String> f() => '';", "() FutureOr<int>"),
    ("C.f", "class C { Future<int> f() async => 1; }",
     "class C { Future<String> f() async => ''; }", "() Future<int>"),
    ("C.f", "class C { int? f() => 1; }", "class C { int f() => 1; }", "() int?"),
    ("C.f", "abstract class C { List<int> f(); }", "abstract class C { List<C> f(); }",
     "() List<int>"),
    ("M.f", "mixin M<T> { T? f() => null; }", "mixin M<T> { T f() => throw 1; }", "() T?"),
    ("E.f", "extension E on int { (int, int) f() => (1, 2); }",
     "extension E on int { (int, String) f() => (1, ''); }", "() (int, int)"),
]


@pytest.mark.parametrize(("name", "first", "second", "text"), RETURNS)
def test_a_return_type_in_a_former_hole_is_written_and_hashed(
    tmp_path: Path, name: str, first: str, second: str, text: str,
) -> None:
    a, b = _pair(tmp_path, first, second, name)
    assert a.signature == text
    assert a.stable_id != b.stable_id


# A parameter whose type the shared normalization cut: (first declaration,
# second declaration, the first's hashed text).
PARAMETERS = [
    # A dot inside a function type or a record type.
    ("void f(int Function(p.A) g) {}", "void f(int Function(p.B) g) {}",
     "(int Function(p.A))"),
    ("void f(p.A Function(p.B) g) {}", "void f(p.A Function(p.C) g) {}",
     "(p.A Function(p.B))"),
    ("void f((p.A, int) r) {}", "void f((p.B, int) r) {}", "((p.A, int))"),
    ("void f(({p.A a}) r) {}", "void f(({p.B a}) r) {}", "(({p.A a}))"),
    # A dot behind a modifier: the modifier is kept, the prefix stripped.
    ("void f(covariant p.Foo x) {}", "void f(p.Foo x) {}", "(covariant Foo)"),
    ("void f(final p.Foo x) {}", "void f(p.Foo x) {}", "(final Foo)"),
    # A dot behind a named or optional group's bracket.
    ("void f({p.Foo? x}) {}", "void f(p.Foo? x) {}", "({x:Foo?})"),
    ("void f({required p.Foo x}) {}", "void f({p.Foo x}) {}", "({x:required Foo})"),
    ("void f(int a, {required covariant p.Foo x}) {}", "void f(int a, {p.Foo x}) {}",
     "(int,{x:required covariant Foo})"),
    # A default value is not hashed (WI-doboh; it hashed ``{Foo x =``).
    ("void f({p.Foo x = const p.Foo()}) {}", "void f({covariant p.Foo x = const p.Foo()}) {}",
     "({x:Foo})"),
    # An old-style function-typed parameter whose last word is not its name:
    # written as the function type it declares, its names left out.
    ("void f(int g()) {}", "void f(int g) {}", "(int Function())"),
    ("void f(int g(int a)) {}", "void f(int g(String a)) {}", "(int Function(int))"),
    ("void f(int g(int a)?) {}", "void f(int g(int a)) {}", "(int Function(int)?)"),
    ("void f([void cb()]) {}", "void f([void cb]) {}", "([void Function()])"),
    ("void f({required int g()}) {}", "void f({required int g}) {}",
     "({g:required int Function()})"),
    ("void f(T g<T>()) {}", "void f(T g) {}", "(T Function<T>())"),
]


@pytest.mark.parametrize(("first", "second", "hashed"), PARAMETERS)
def test_a_parameter_in_a_former_hole_is_hashed(
    tmp_path: Path, first: str, second: str, hashed: str,
) -> None:
    a, b = _pair(tmp_path, first, second, "f")
    assert _hashed(first) == hashed
    assert a.stable_id != b.stable_id


# A field type of a node kind the renderer had no branch for: (first
# declaration, second declaration, the first's displayed type).
FIELDS = [
    ("List<int> x = [];", "List<String> x = [];", "List<int>"),
    ("int? x;", "int x = 0;", "int?"),
    ("final Map<String, p.Foo>? x = null;", "final Map<String, p.Bar>? x = null;",
     "Map<String, p.Foo>?"),
    ("void Function() x = () {};", "void Function(int) x = (_) {};", "void Function()"),
    ("int Function(int)? x;", "int Function(String)? x;", "int Function(int)?"),
    ("(int, int) x = (1, 2);", "(int, String) x = (1, '');", "(int, int)"),
    ("late final ({int a}) x;", "late final ({String a}) x;", "({int a})"),
    ("int /* c */ x = 0;", "String /* c */ x = '';", "int"),
    ("int /// d\n  x = 0;", "String /// d\n  x = '';", "int"),
    ("p.Foo<int> x = p.Foo();", "p.Foo<String> x = p.Foo();", "p.Foo<int>"),
]


@pytest.mark.parametrize(("first", "second", "written"), FIELDS)
def test_a_field_type_in_a_former_hole_is_written_and_hashed(
    tmp_path: Path, first: str, second: str, written: str,
) -> None:
    a, b = _pair(tmp_path, f"class C {{\n  {first}\n}}\n", f"class C {{\n  {second}\n}}\n", "C.x")
    assert a.signature == written
    assert a.stable_id != b.stable_id


def test_a_top_level_variable_shows_its_whole_type(tmp_path: Path) -> None:
    """A variable's id hashes its name only; its displayed type is whole."""
    v = _symbol(tmp_path / "x", "const List<p.Foo>? v = null;\n", "v")
    assert v.kind == "variable"
    assert v.signature == "List<p.Foo>?"


# The node kinds that spell a field's type, each with a field using it: the
# list itself and a literal copy, so removing an entry leaves its case here.
_TYPE_PART_CASES = {
    "type_identifier": ("int x = 0;", "int"),
    ".": ("p.Foo x = p.Foo();", "p.Foo"),
    "type_arguments": ("List<int> x = [];", "List<int>"),
    "nullable_type": ("int? x;", "int?"),
    "function_type": ("void Function() x = () {};", "void Function()"),
    "record_type": ("(int, int) x = (1, 2);", "(int, int)"),
}


@pytest.mark.parametrize(
    "kind", sorted(set(_TYPE_PART_CASES) | set(dart._DART_TYPE_PART_NODE_TYPES)),
)
def test_every_type_part_kind_is_written(tmp_path: Path, kind: str) -> None:
    source, written = _TYPE_PART_CASES[kind]
    field = _symbol(tmp_path / "x", f"class C {{ {source} }}\n", "C.x")
    assert field.signature == written


_COMMENT_CASES = {
    "comment": ("int /* c */ f() => 1;", "class C { int /* c */ x = 0; }"),
    "documentation_comment": ("int /// d\nf() => 1;", "class C { int /// d\n x = 0; }"),
}


@pytest.mark.parametrize(
    "kind", sorted(set(_COMMENT_CASES) | set(dart._DART_COMMENT_NODE_TYPES)),
)
def test_a_comment_inside_a_type_is_left_out(tmp_path: Path, kind: str) -> None:
    function, field = _COMMENT_CASES[kind]
    assert _symbol(tmp_path / "x", function + "\n", "f").signature == "() int"
    assert _symbol(tmp_path / "y", field + "\n", "C.x").signature == "int"


@pytest.mark.parametrize(
    "modifier",
    sorted({"required", "covariant", "final", "const", "late"} | dart._DART_PARAMETER_MODIFIERS),
)
def test_every_parameter_modifier_survives_the_prefix_strip(modifier: str) -> None:
    """A modifier is taken off before the library prefix is stripped and put
    back after; the shared strip alone would keep only ``Foo``. (``required``
    outside a group does not parse cleanly, and is still kept.)"""
    assert _hashed(f"void f({modifier} p.Foo x) {{}}") == f"({modifier} Foo)"
    assert _hashed(f"void f({{{modifier} p.Foo x}}) {{}}") == f"({{x:{modifier} Foo}})"


def test_a_type_named_like_a_modifier_is_still_a_type() -> None:
    """A modifier comes off only while a type remains after it."""
    assert _hashed("void f(int g(), final x) {}") == "(int Function(),final)"


# Hashed texts of the forms that gained a rendering.
FILLED = {
    "List<int> f() => [];": "()List<int>",
    "p.Foo<q.Bar>? f() => null;": "()Foo<q.Bar>?",
    "int Function(p.A) f() => null;": "()int Function(p.A)",
    "(p.A, int)? f() => null;": "()(p.A, int)?",
    "void f(int Function(p.A) g, {p.Foo? q}) {}": "(int Function(p.A),{q:Foo?})",
    "void f(final p.Foo x, {required p.Foo y, covariant p.Bar? z}) {}":
        "(final Foo,{y:required Foo,z:covariant Bar?})",
    # ``g(int)``'s ``int`` is a parameter NAME: the parameter is untyped.
    "p.Foo f(int g(int), [void cb()]) => x;": "(int Function(dynamic),[void Function()])Foo",
    "int f(int a, int g(int)) => a;": "(int,int Function(dynamic))int",
    # A default value beside a hole is left out like any other (WI-doboh).
    "void f({List<int> l = const [], int g()}) {}": "({l:List<int>,g:int Function()})",
}


@pytest.mark.parametrize(("decl", "hashed"), FILLED.items())
def test_the_hashed_text_of_a_filled_form(decl: str, hashed: str) -> None:
    assert _hashed(decl) == hashed


# Declarations with no group, no function-typed parameter and no function
# type naming a parameter: the hashed text is exactly what the shared rule
# makes of the displayed signature (parameter names dropped, a library
# prefix stripped from a type's head, ``void`` omitted, generic arguments as
# written), so their ids did not move.
UNCHANGED = [
    "int f(int a, int b) => a;",
    "void f(String name) {}",
    "f() {}",
    "void f() {}",
    "dynamic f(p.Foo f, Map<String, p.Bar> m) => f;",
    "Widget f(void Function(int)? cb, int Function<T>(T) gf, (int, {String n}) r) => w;",
    "p.Foo? f(var x, final y, dynamic z) => null;",
    "List<p.Foo> f(int a) => [];",
]


@pytest.mark.parametrize("decl", UNCHANGED)
def test_hole_free_signatures_hash_as_before(decl: str) -> None:
    assert _hashed(decl) == normalize_signature_types_first(_displayed(decl))


_CLEAN = """\
import 'package:x/x.dart' as p;

int add(int a, int b) => a + b;
dynamic dyn(p.Foo f, Map<String, p.Bar> m) => f;
void opt(covariant int a, final int b, [int c = 1, String? d]) {}
Foo named({required int a, int b = 2, List<int>? c}) => Foo();
Widget fnTyped(int g(int a), void Function(int)? cb, int Function<T>(T) gf, (int, {String n}) r) => Widget();
void fragment({Size s = const Size(1, 2), Foo f = const Foo(1, a.b + c)}) {}
int /* c */ commented() => 1;

class Widget {
  int count = 0;
  final p.Foo foo = p.Foo();
  final untyped = 1;
  late final int lateInt;
  Widget build(Widget context, [bool flag = false]) => this;
  T pick<T>(T x) => x;
}
"""

# Stable ids of the hole-free declarations above, taken from the analyzer
# before the holes were filled (dev 3da1f85b37, file ``clean.dart`` at the
# root of the analysed tree), except where a comment says an id moved.
# WI-kumoh (stable-id v16): layout is not hashed; a text is hashed in one
# canonical spacing, the one most texts already had. named, fnTyped and
# fragment were spelled otherwise: the hashed text left a named group's
# ``{`` open, so its commas read as a type's (canonical ``, ``) and the ``{``
# took a space after it (``({ required int, int, List<int>?)Foo``). Each was:
# named 1bb432604e33ccde, fnTyped 5b29217af303859d, fragment 5f06492ec22f4a50.
PINNED = {
    ("function", "add"): f"{STABLE_ID_PREFIX}b25ded629f362a02",
    ("function", "dyn"): f"{STABLE_ID_PREFIX}39de927c48988b68",
    # Moved by WI-doboh: a default value is not hashed, so ``[int c = 1]``
    # no longer hashes ``[int c =`` (opt was 3df1e40f64b5cae0) and ``{int b =
    # 2}`` no longer hashes ``int b`` (named was 724b4ce75e477e92).
    # Moved again in stable-id v16 because the text is types only with every
    # group closed (read from the tree): opt hashed ``[int c, String?`` (the
    # name ``c`` kept, the ``]`` lost; was 6b61da94e22d753c), now
    # ``(covariant int,final int,[int,String?])``; named left its ``{`` open
    # (was cc03281d65f04681), now ``({required int,int,List<int>?})Foo``.
    ("function", "opt"): f"{STABLE_ID_PREFIX}7959670ad49551cd",
    # Moved in stable-id v17: a named parameter's name is hashed (callers
    # write it), ``({a:required int,b:int,c:List<int>?})Foo`` (was
    # bda2449bb1d7dc6a).
    ("function", "named"): f"{STABLE_ID_PREFIX}d14b7f82c9c8e6ef",
    # ``int g(int a)`` hashed whole, its names included (was 2efe8e614ed4e81c);
    # now the type it declares, ``int Function(int)``.
    ("function", "fnTyped"): f"{STABLE_ID_PREFIX}eef1a4bbc5fa8286",
    # Moved three times in stable-id v16: by the shared FQN strip fix (the
    # renderer's default-value fragment closed the parameter list early, and
    # v15's strip cut the rest at its last dot, dropping ``Foo f``: was
    # 788a1f68d49853d8, then 520d66bf5aa2d402), by WI-doboh (each default
    # is written ``...`` whole and not hashed: was 5c870688c98590d7, the text
    # ``({Size,Foo)``), and by the closed group: ``({Size,Foo})``. Moved in
    # stable-id v17 by the hashed named-parameter names: ``({s:Size,f:Foo})``
    # (was 828190b178b8920f).
    ("function", "fragment"): f"{STABLE_ID_PREFIX}5a5bebd70a4635af",
    ("function", "commented"): f"{STABLE_ID_PREFIX}a980655d150d1db1",
    ("field", "Widget.count"): f"{STABLE_ID_PREFIX}b9ad19582563bf1f",
    ("field", "Widget.foo"): f"{STABLE_ID_PREFIX}52b2647c5aac5b99",
    ("field", "Widget.untyped"): f"{STABLE_ID_PREFIX}67b1c3afbda56fb7",
    ("field", "Widget.lateInt"): f"{STABLE_ID_PREFIX}fcfa601122856289",
    # WI-doboh: ``[bool flag = false]`` no longer hashes ``[bool flag =``
    # (was eec19e02b0716d60); the closed group: ``(Widget,[bool])Widget``
    # (was 9bfc0a5c64dd995c, ``(Widget,[bool)Widget``).
    ("method", "Widget.build"): f"{STABLE_ID_PREFIX}c2eb434db07b79eb",
    ("method", "Widget.pick"): f"{STABLE_ID_PREFIX}f9b79352893d5d48",
}


def test_hole_free_declarations_keep_their_stable_ids(tmp_path: Path) -> None:
    (tmp_path / "clean.dart").write_text(_CLEAN)
    got = {
        (s.kind, s.name): s.stable_id
        for s in analyze_dart(tmp_path).symbols
        if (s.kind, s.name) in PINNED
    }
    assert got == PINNED


RETURN_TYPE_NAMES = {
    "() User?": "User",
    "() List<String>": "List",
    "() Future<User>?": "Future",
    "() Function": None,
    "() User Function()": None,
    "() (User, User)": None,
    "() p.User": None,
}


@pytest.mark.parametrize(("signature", "name"), RETURN_TYPE_NAMES.items())
def test_the_receiver_typing_reads_the_head_of_a_whole_return_type(
    signature: str, name: str | None,
) -> None:
    """The return type a chained call types its receiver by is the one the
    first-``type_identifier`` renderer gave: the head of a generic, without
    its ``?``; a function type, a record type and a prefixed type give none."""
    assert _extract_dart_return_type_name(signature) == name
