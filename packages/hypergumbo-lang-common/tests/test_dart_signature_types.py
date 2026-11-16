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

Each test below fills one hole: two declarations that differ only inside it
get two stable ids, and the rendered text contains the type. The last tests
pin the hashed text and the id of declarations that had no hole: they hash
as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import normalize_signature_types_first
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_common import dart
from hypergumbo_lang_common.dart import (
    _extract_dart_return_type_name,
    analyze_dart,
    normalize_dart_signature,
)


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
    ("void f({p.Foo? x}) {}", "void f(p.Foo? x) {}", "({Foo?)"),
    ("void f({required p.Foo x}) {}", "void f({p.Foo x}) {}", "({required Foo)"),
    ("void f(int a, {required covariant p.Foo x}) {}", "void f(int a, {p.Foo x}) {}",
     "(int,{required covariant Foo)"),
    ("void f({p.Foo x = const p.Foo()}) {}", "void f({covariant p.Foo x = const p.Foo()}) {}",
     "({Foo x =)"),
    # An old-style function-typed parameter whose last word is not its name.
    ("void f(int g()) {}", "void f(int g) {}", "(int g())"),
    ("void f(int g(int)) {}", "void f(int g(String)) {}", "(int g(int))"),
    ("void f(int g(int a)?) {}", "void f(int g(int a)) {}", "(int g(int a)?)"),
    ("void f([void cb()]) {}", "void f([void cb]) {}", "([void cb()])"),
    ("void f({required int g()}) {}", "void f({required int g}) {}", "({required int g()})"),
    ("void f(T g<T>()) {}", "void f(T g) {}", "(T g<T>())"),
]


@pytest.mark.parametrize(("first", "second", "hashed"), PARAMETERS)
def test_a_parameter_in_a_former_hole_is_hashed(
    tmp_path: Path, first: str, second: str, hashed: str,
) -> None:
    a, b = _pair(tmp_path, first, second, "f")
    assert normalize_dart_signature(a.signature) == hashed
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
    back after; the shared strip alone would keep only ``Foo``."""
    assert normalize_dart_signature(f"({modifier} p.Foo x)") == f"({modifier} Foo)"
    assert normalize_dart_signature(f"({{{modifier} p.Foo x}})") == f"({{{modifier} Foo)"


def test_a_type_named_like_a_modifier_is_still_a_type() -> None:
    """A modifier comes off only while a type remains after it."""
    assert normalize_dart_signature("(int g(), final x)") == "(int g(),final)"


# Hashed texts of the forms that gained a rendering.
FILLED = {
    "() List<int>": "()List<int>",
    "() p.Foo<q.Bar>?": "()Foo<q.Bar>?",
    "() int Function(p.A)": "()int Function(p.A)",
    "() (p.A, int)?": "()(p.A, int)?",
    "(int Function(p.A) g, {p.Foo? q})": "(int Function(p.A),{Foo?)",
    "(final p.Foo x, {required p.Foo y, covariant p.Bar? z})":
        "(final Foo,{required Foo,covariant Bar?)",
    "(int g(int), [void cb()]) p.Foo": "(int g(int),[void cb()])Foo",
    "(int a, int g(int)) int": "(int,int g(int))int",
    # A default value's fragment (``2]``) beside a hole keeps the shared text.
    "({List<int> l = ..., 2], int g()})": "({List<int> l,2],int g()})",
}


@pytest.mark.parametrize(("signature", "hashed"), FILLED.items())
def test_the_hashed_text_of_a_filled_form(signature: str, hashed: str) -> None:
    assert normalize_dart_signature(signature) == hashed


def test_declared_type_parameters_are_by_position_in_a_filled_form() -> None:
    assert normalize_dart_signature("(T Function(p.A) g, List<T> l) T?", ["T"]) == (
        "($0 Function(p.A),List<$0>)$0?")
    assert normalize_dart_signature("(T Function(p.A) g) T", ["T"]) == "($0 Function(p.A))$0"


# Signatures in which the shared rule keeps every type: the hashed text is
# exactly the shared rule's (parameter names dropped, a library prefix
# stripped from a type's head, ``void`` omitted, generic arguments as written,
# the leftovers of a default value with a comma kept as they were).
UNCHANGED = [
    "(int a, int b) int",
    "(String name)",
    "()",
    "() void",
    "(p.Foo f, Map<String, p.Bar> m) dynamic",
    "(covariant int a, final int b, [int c = ..., String? d])",
    "({required int a, int b = ..., List<int>? c}) Foo",
    "(int g(int a), void Function(int)? cb, int Function<T>(T) gf, (int, {String n}) r) Widget",
    "({Size s = ..., 2), Foo f = ..., a.b + c)})",
    "({Size s = ..., 2), int g()})",
    "({List<int> l = ..., 2]})",
    "({List<X> l = ..., const b(2)]})",
    "([Foo f = ..., a.b + c)])",
    "(var x, final y, dynamic z) p.Foo?",
    "({int a: 1}) p.Foo<int>",
    "(int g<T>(T a)) List<p.Foo>",
]


@pytest.mark.parametrize("signature", UNCHANGED)
def test_hole_free_signatures_hash_as_before(signature: str) -> None:
    assert normalize_dart_signature(signature) == normalize_signature_types_first(signature)


def test_a_signature_that_does_not_open_a_parameter_list_does_not_normalize() -> None:
    assert normalize_dart_signature(None) is None
    assert normalize_dart_signature("") is None
    assert normalize_dart_signature("int") is None
    assert normalize_dart_signature("(int x") is None


def test_a_default_value_fragment_is_never_a_hole() -> None:
    """A fragment whose brackets do not balance keeps the shared text, even
    beside a parameter that is a hole."""
    assert dart._dart_parameter_hole("a.b + c)}", None) is None
    # ``= const [1, const b(2)]``: its last word holds a ``(`` like an
    # old-style function-typed parameter's, and its ``]`` closes nothing.
    assert dart._dart_parameter_hole("const b(2)]}", None) is None
    assert dart._dart_parameter_hole("x(]", None) is None
    assert dart._dart_balanced("(a]") is False
    assert dart._dart_balanced("(a") is False


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
# root of the analysed tree).
PINNED = {
    ("function", "add"): "hgstableid15:b25ded629f362a02",
    ("function", "dyn"): "hgstableid15:39de927c48988b68",
    ("function", "opt"): "hgstableid15:3df1e40f64b5cae0",
    ("function", "named"): "hgstableid15:724b4ce75e477e92",
    ("function", "fnTyped"): "hgstableid15:5b29217af303859d",
    ("function", "fragment"): "hgstableid15:788a1f68d49853d8",
    ("function", "commented"): "hgstableid15:a980655d150d1db1",
    ("field", "Widget.count"): "hgstableid15:b9ad19582563bf1f",
    ("field", "Widget.foo"): "hgstableid15:52b2647c5aac5b99",
    ("field", "Widget.untyped"): "hgstableid15:67b1c3afbda56fb7",
    ("field", "Widget.lateInt"): "hgstableid15:fcfa601122856289",
    ("method", "Widget.build"): "hgstableid15:eec19e02b0716d60",
    ("method", "Widget.pick"): "hgstableid15:f9b79352893d5d48",
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
