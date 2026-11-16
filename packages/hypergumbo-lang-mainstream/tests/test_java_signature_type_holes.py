# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every Java type a declaration spells reaches its signature and stable_id.

A method's or constructor's signature text is normalized and hashed into its
typed ``stable_id`` (spec section 6, typed tier), and a field's declared type
is hashed the same way. ``_extract_java_signature`` once rendered a parameter
or return type only when its node kind was on a whitelist, and dropped any
other: a parameter typed ``Map.Entry`` (``scoped_type_identifier``) vanished
with its name, a primitive or qualified varargs parameter (``int... xs``)
vanished, a parameter named ``_`` (``underscore_pattern``) vanished, a
qualified return type read as ``void``, and the dimensions written after a
method's parameter list (``int f()[]``) or after a field's name
(``int a[];``) were lost. Two declarations that differ only there shared one
hashed text, so they shared one stable_id.

Separately, the shared types-first normalizer erases the varargs marker
(``Object... a`` hashes as ``Object``), so ``log(Object o)`` and
``log(Object... os)``, a legal overload pair, tied. Java's normalizer writes a
variable-arity parameter as the array it is (JLS 8.4.1), ``Object[]``.

Each test pair puts the two declarations in two repositories at the same
relative path, so the file anchor is equal and only the hashed text can tell
them apart (within one file the occurrence ordinal would hide the tie).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.java import analyze_java, normalize_java_signature


def _symbols(root: Path, body: str, *, header: str = "class C {") -> dict[str, object]:
    src = root / "p" / "C.java"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(f"package p;\n{header}\n{body}\n}}\n")
    return {s.name: s for s in analyze_java(root).symbols}


def _pair(tmp_path: Path, name: str, body_a: str, body_b: str, **kw: str):
    a = _symbols(tmp_path / "a", body_a, **kw)[name]
    b = _symbols(tmp_path / "b", body_b, **kw)[name]
    return a, b


# (case id, symbol name, declaration A, declaration B, text A's signature holds)
_HOLES = [
    ("scoped_param", "C.f", "void f(Map.Entry e) {}", "void f(Foo.Bar e) {}",
     "Map.Entry e"),
    ("scoped_param_vs_none", "C.f", "void f(java.util.List x) {}", "void f() {}",
     "java.util.List x"),
    ("scoped_return", "C.f", "Map.Entry f() { return null; }",
     "Foo.Bar f() { return null; }", ") Map.Entry"),
    ("scoped_return_vs_void", "C.f", "java.util.List f() { return null; }",
     "void f() {}", ") java.util.List"),
    ("annotated_scoped_param", "C.f", "void f(java.lang.@A String s) {}",
     "void f() {}", "java.lang.@A String s"),
    ("vararg_integral", "C.f", "void f(int... xs) {}", "void f(long... xs) {}",
     "int... xs"),
    ("vararg_floating", "C.f", "void f(double... xs) {}", "void f() {}",
     "double... xs"),
    ("vararg_boolean", "C.f", "void f(boolean... xs) {}", "void f() {}",
     "boolean... xs"),
    ("vararg_scoped", "C.f", "void f(java.lang.String... xs) {}", "void f() {}",
     "java.lang.String... xs"),
    ("underscore_name", "C.f", "void f(int _) {}", "void f() {}", "int _"),
    ("vararg_underscore_name", "C.f", "void f(String... _) {}", "void f() {}",
     "String... _"),
    ("annotated_vararg", "C.f", "void f(int a, String @Nullable ... xs) {}",
     "void f(int a) {}", "String @Nullable ... xs"),
    ("dims_after_params", "C.f", "int f()[] { return null; }",
     "int f() { return 0; }", ") int[]"),
    ("constructor_scoped_param", "C.C", "C(Map.Entry e) {}", "C() {}",
     "Map.Entry e"),
    ("default_method_scoped", "C.f", "default Map.Entry f(Map.Entry e) { return e; }",
     "default Foo.Bar f(Map.Entry e) { return null; }", ") Map.Entry"),
]


@pytest.mark.parametrize(
    "name,body_a,body_b,text", [c[1:] for c in _HOLES], ids=[c[0] for c in _HOLES],
)
def test_a_declaration_differing_only_in_a_former_hole_has_its_own_id(
    tmp_path: Path, name: str, body_a: str, body_b: str, text: str,
) -> None:
    header = "interface C {" if body_a.startswith("default") else "class C {"
    a, b = _pair(tmp_path, name, body_a, body_b, header=header)
    assert text in a.signature
    assert a.stable_id and b.stable_id
    assert a.stable_id != b.stable_id


def test_an_enum_constructor_with_a_scoped_parameter_has_its_own_id(tmp_path: Path) -> None:
    a, b = _pair(
        tmp_path, "C.C", "X; C(Map.Entry e) {}", "X; C() {}", header="enum C {",
    )
    assert a.signature == "(Map.Entry e)"
    assert a.stable_id != b.stable_id


def test_a_compact_constructor_reads_scoped_and_varargs_components(tmp_path: Path) -> None:
    a = _symbols(tmp_path / "a", "C {}", header="record C(Map.Entry e, int... xs) {")
    b = _symbols(tmp_path / "b", "C {}", header="record C(Foo.Bar e, long... xs) {")
    ctor_a = next(s for s in a.values() if s.kind == "constructor")
    ctor_b = next(s for s in b.values() if s.kind == "constructor")
    assert ctor_a.signature == "(Map.Entry e, int... xs)"
    assert ctor_a.stable_id != ctor_b.stable_id


def test_a_field_with_dimensions_after_its_name_hashes_the_array_type(tmp_path: Path) -> None:
    a, b = _pair(tmp_path, "C.a", "int a[][], z;", "int a, z;")
    assert a.signature == "int[][]"
    assert a.stable_id != b.stable_id
    # the second declarator of the same declaration has no dimensions of its own
    z = _symbols(tmp_path / "a", "int a[][], z;")["C.z"]
    assert z.signature == "int"


def test_every_parameter_shape_renders_in_order(tmp_path: Path) -> None:
    sym = _symbols(
        tmp_path,
        "Map.Entry<String, Integer>[] f(Map.Entry e, int x[], String[][] y,"
        " Outer.Inner<String>.Deep q, final int _, float... rest) { return null; }",
    )["C.f"]
    assert sym.signature == (
        "(Map.Entry e, int[] x, String[][] y, Outer.Inner<String>.Deep q, int _,"
        " float... rest) Map.Entry<String, Integer>[]"
    )


def test_varargs_and_single_parameter_overloads_hash_apart(tmp_path: Path) -> None:
    a, b = _pair(tmp_path, "C.log", "void log(Object... os) {}", "void log(Object o) {}")
    assert a.signature == "(Object... os)"
    assert a.stable_id != b.stable_id


def test_an_annotated_vararg_hashes_as_the_annotated_array_it_declares(
    tmp_path: Path,
) -> None:
    # tree-sitter-java leaves `T @A ... xs` as an ERROR node; its source text
    # is written, and hashes as `T @A [] xs` (an array_type) would.
    a, b = _pair(
        tmp_path, "C.f",
        "void f(String @A\n      ... xs) {}", "void f(String @A [] xs) {}",
    )
    assert a.signature == "(String @A ... xs)"
    assert a.stable_id == b.stable_id


def test_varargs_hashes_as_the_array_it_declares() -> None:
    # JLS 8.4.1: the type of `T... xs` is T[]; `f(T... x)` and `f(T[] x)`
    # are one signature (a class cannot declare both), so they hash as one.
    assert normalize_java_signature("(String fmt, Object... args) String") == (
        "(String,Object[])String"
    )
    assert normalize_java_signature("(Object... args)") == normalize_java_signature(
        "(Object[] args)"
    )
    assert normalize_java_signature("(int[]... xs)") == "(int[][])"


def test_a_receiver_parameter_is_not_a_formal_parameter(tmp_path: Path) -> None:
    # JLS 8.4: `C this` only carries annotations; it is not part of the
    # method's signature, so it is dropped on purpose (as before).
    a, b = _pair(tmp_path, "C.f", "void f(C this, int x) {}", "void f(int x) {}")
    assert a.signature == "(int x)"
    assert a.stable_id == b.stable_id


# Hole-free declarations: stable_ids taken from the base arm (dev 3da1f85b37)
# before the fix. These must not move.
_HOLE_FREE = """\
  int add(int a, int b) { return a + b; }
  List<String> g(Map<String, Integer> m, String args[]) { return null; }
  byte[] h(String[] in, boolean b, double d) { return null; }
  void v(final @Deprecated String s) {}
  C(String name) {}
  String name;
  Map<String, List<Integer>> table;
"""

_BASE_IDS = {
    "C.add": "sha256:5c8b45367506a4e6",
    "C.g": "sha256:e5fef13ec33fe01e",
    "C.h": "sha256:bccce9530b4d4b7d",
    "C.v": "sha256:baca420412b946d3",
    "C.C": "sha256:167880bda81d22a7",
    "C.name": "sha256:66946f25afcc920b",
    "C.table": "sha256:33ffedc784a9392a",
}


def test_hole_free_declarations_keep_their_base_stable_ids(tmp_path: Path) -> None:
    syms = _symbols(tmp_path, _HOLE_FREE)
    assert {name: syms[name].stable_id for name in _BASE_IDS} == _BASE_IDS
