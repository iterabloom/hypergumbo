# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every Groovy parameter and return type reaches the hashed signature.

``_extract_groovy_signature`` used to write a type only when its node kind
was one of ``type_identifier``, ``primitive_type`` (a kind tree-sitter-groovy
0.1.2 does not have), ``array_type`` and ``generic_type``. Every other type
was dropped: a parameter typed by a primitive (``integral_type``,
``floating_point_type``, ``boolean_type``) or a qualified name
(``scoped_type_identifier``) was written as its NAME alone, so the name was
hashed in place of the type; such a return type was dropped; a varargs
parameter (``spread_parameter``) was dropped whole; and dimensions written
after a parameter's name (``String a[]``) or after the parameter list
(``String f()[]``) were lost. ``void f(java.util.List x)`` and
``void f(java.util.Map x)`` shared one hashed text, ``(x)``; renaming ``x``
moved the id; ``void f(String... xs)`` hashed as ``void f()``.

Each pair puts the two declarations in two repositories at the same relative
path, so the file anchor is equal and only the hashed text can tell them
apart (within one file the occurrence ordinal would hide the tie).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.groovy import (
    analyze_groovy,
    normalize_groovy_signature,
)
from hypergumbo_core.identity_schemes import STABLE_ID_PREFIX


def _symbols(root: Path, body: str, *, top_level: bool = False) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    text = body if top_level else f"class C {{\n    {body}\n}}\n"
    (root / "A.groovy").write_text(text)
    result = analyze_groovy(root)
    return {s.name: s for s in result.symbols if s.kind in ("method", "function")}


def _pair(
    tmp_path: Path, name: str, a: str, b: str, *, top_level: bool = False,
) -> tuple[object, object]:
    sa = _symbols(tmp_path / "a", a, top_level=top_level)[name]
    sb = _symbols(tmp_path / "b", b, top_level=top_level)[name]
    return sa, sb


#: One sample per type node kind the renderer did not write before, as
#: (node kind, a type of that kind, another type of that kind). A literal
#: list: a kind dropped from it still has its case in the git history, and
#: every entry is a distinct parametrized case.
_FORMERLY_DROPPED_KINDS = [
    ("integral_type", "int", "long"),
    ("integral_type/char", "char", "byte"),
    ("integral_type/short", "short", "int"),
    ("floating_point_type", "double", "float"),
    ("boolean_type", "boolean", "int"),
    ("scoped_type_identifier", "java.util.List", "java.util.Map"),
    ("scoped_type_identifier/nested", "Map.Entry", "Map.Key"),
]


@pytest.mark.parametrize(("kind", "t1", "t2"), _FORMERLY_DROPPED_KINDS)
def test_a_parameter_type_of_every_kind_is_hashed(
    tmp_path: Path, kind: str, t1: str, t2: str,
) -> None:
    a, b = _pair(tmp_path, "C.f", f"void f({t1} x) {{}}", f"void f({t2} x) {{}}")
    assert a.signature == f"({t1} x)", kind
    assert b.signature == f"({t2} x)", kind
    assert a.stable_id != b.stable_id, kind


@pytest.mark.parametrize(("kind", "t1", "t2"), _FORMERLY_DROPPED_KINDS)
def test_a_return_type_of_every_kind_is_hashed(
    tmp_path: Path, kind: str, t1: str, t2: str,
) -> None:
    a, b = _pair(
        tmp_path, "C.f", f"{t1} f() {{ return null }}", f"{t2} f() {{ return null }}",
    )
    assert a.signature == f"(): {t1}", kind
    assert a.stable_id != b.stable_id, kind


@pytest.mark.parametrize(("kind", "t1", "t2"), _FORMERLY_DROPPED_KINDS)
def test_a_top_level_function_hashes_every_kind(
    tmp_path: Path, kind: str, t1: str, t2: str,
) -> None:
    # A script-level method (``function_definition``) uses the same renderer.
    a, b = _pair(
        tmp_path, "f", f"{t1} f({t1} x) {{ x }}\n", f"{t2} f({t2} x) {{ x }}\n",
        top_level=True,
    )
    assert a.signature == f"({t1} x): {t1}", kind
    assert a.stable_id != b.stable_id, kind


def test_the_parameter_name_is_not_hashed(tmp_path: Path) -> None:
    # Before, ``(x)`` was hashed for ``int x``: renaming the parameter moved
    # the id. Now the type is hashed and the name is dropped.
    a, b = _pair(tmp_path, "C.f", "void f(int x) {}", "void f(int y) {}")
    assert a.stable_id == b.stable_id
    a, b = _pair(tmp_path / "q", "C.f", "void f(java.util.List x) {}",
                 "void f(java.util.List y) {}")
    assert a.stable_id == b.stable_id


#: Element types a varargs parameter can be written with (the spread
#: parameter's type child is any of these kinds).
_SPREAD_ELEMENTS = [
    ("type_identifier", "String"),
    ("integral_type", "int"),
    ("scoped_type_identifier", "java.lang.String"),
    ("generic_type", "List<String>"),
    ("array_type", "String[]"),
]


@pytest.mark.parametrize(("kind", "element"), _SPREAD_ELEMENTS)
def test_a_varargs_parameter_is_written(
    tmp_path: Path, kind: str, element: str,
) -> None:
    a, b = _pair(tmp_path, "C.f", f"void f({element}... xs) {{}}", "void f() {}")
    assert a.signature == f"({element}... xs)", kind
    assert a.stable_id != b.stable_id, kind


def test_varargs_and_a_single_parameter_hash_apart(tmp_path: Path) -> None:
    # ``f(Object o)`` and ``f(Object... os)`` are a legal overload pair; the
    # shared rule alone erases the ``...`` marker and would tie them.
    a, b = _pair(tmp_path, "C.f", "void f(Object... os) {}", "void f(Object o) {}")
    assert a.stable_id != b.stable_id


def test_varargs_hash_as_the_array_they_declare(tmp_path: Path) -> None:
    # ``f(T... x)`` and ``f(T[] x)`` are one method (Groovy, like Java, types
    # a variable-arity parameter as the array), so they hash as one.
    assert normalize_groovy_signature("(String fmt, Object... args): String") == (
        "(String,Object[])String"
    )
    assert normalize_groovy_signature("(int[]... xs)") == "(int[][])"
    a, b = _pair(tmp_path, "C.f", "void f(String... xs) {}", "void f(String[] xs) {}")
    assert a.stable_id == b.stable_id


def test_a_final_varargs_parameter_keeps_its_type_not_its_modifier(
    tmp_path: Path,
) -> None:
    a, b = _pair(tmp_path, "C.f", "void f(final String... xs) {}",
                 "void f(String... xs) {}")
    assert a.signature == "(String... xs)"
    assert a.stable_id == b.stable_id


def test_dimensions_after_a_parameter_name_join_its_type(tmp_path: Path) -> None:
    a, b = _pair(tmp_path, "C.f", "void f(String a[]) {}", "void f(String a) {}")
    assert a.signature == "(String[] a)"
    assert a.stable_id != b.stable_id
    c, d = _pair(tmp_path / "q", "C.f", "void f(String a[]) {}",
                 "void f(String[] a) {}")
    assert c.stable_id == d.stable_id


def test_dimensions_after_the_parameter_list_join_the_return_type(
    tmp_path: Path,
) -> None:
    a, b = _pair(tmp_path, "C.f", "String f()[] { null }", "String f() { null }")
    assert a.signature == "(): String[]"
    assert a.stable_id != b.stable_id


def test_a_static_method_of_an_enum_and_an_interface_method_are_rendered(
    tmp_path: Path,
) -> None:
    root = tmp_path / "r"
    root.mkdir()
    (root / "A.groovy").write_text(
        "interface I {\n    long m(java.util.List l)\n}\n"
        "enum E {\n    RED;\n    static boolean ok(double d) { return true }\n}\n"
    )
    syms = {s.name: s for s in analyze_groovy(root).symbols}
    assert syms["I.m"].signature == "(java.util.List l): long"
    assert syms["E.ok"].signature == "(double d): boolean"


def test_a_receiver_parameter_is_not_written(tmp_path: Path) -> None:
    # ``C this`` only carries annotations; it is not a parameter of the
    # method's signature (as in Java, JLS 8.4), so it stays out as before.
    a, b = _pair(tmp_path, "C.f", "void f(C this, int x) {}", "void f(int x) {}")
    assert a.signature == "(int x)"
    assert a.stable_id == b.stable_id


def test_parameter_annotations_and_final_are_not_written(tmp_path: Path) -> None:
    # Neither changes the parameter's type or the overload set.
    a, b = _pair(
        tmp_path, "C.f",
        "void f(@DelegatesTo(String) final Closure c, final int n) {}",
        "void f(Closure c, int n) {}",
    )
    assert a.signature == "(Closure c, int n)"
    assert a.stable_id == b.stable_id


def test_untyped_parameters_the_grammar_cannot_parse_are_left_out(
    tmp_path: Path,
) -> None:
    # tree-sitter-groovy 0.1.2 parses no untyped parameter: ``def f(x)``
    # leaves an ERROR node, and the ``y`` of ``f(String x, y)`` a parameter
    # whose type is ``y`` and whose name is missing. Neither is written, as
    # before the fix.
    syms = _symbols(tmp_path, "def f(String x, y) {}\n    def g(x) {}")
    assert syms["C.f"].signature == "(String x): def"
    assert syms["C.g"].signature == "(): def"


def test_a_default_value_is_not_written(tmp_path: Path) -> None:
    a, b = _pair(tmp_path, "C.f", "void f(int a = 1) {}", "void f(int a) {}")
    assert a.signature == "(int a)"
    assert a.stable_id == b.stable_id


# Hashed texts and stable ids of declarations whose types all use a node
# kind the renderer wrote before the fix (type_identifier, generic_type,
# array_type, void_type, ``def``), taken from the base arm (dev 3da1f85b37).
_PINNED_SOURCE = """class Pinned {
    String name(String first, List<String> rest) { first }
    Map<String, List<Integer>> table(String[] keys, int[][] grid) { null }
    def dyn(def x) { x }
    void run(Closure<String> body) {}
    private static String[] split(final String s) { null }
}
def script(String a) { a }
"""

_PINNED = {
    "Pinned.name": ("(String,List<String>)String", f"{STABLE_ID_PREFIX}58141b54f6dc044f"),
    "Pinned.table": (
        "(String[],int[][])Map<String, List<Integer>>", f"{STABLE_ID_PREFIX}bba74ff853e0a9ba",
    ),
    "Pinned.dyn": ("(def)def", f"{STABLE_ID_PREFIX}23dc82d5787f8d5f"),
    "Pinned.run": ("(Closure<String>)", f"{STABLE_ID_PREFIX}6b6efff128fde6e1"),
    "Pinned.split": ("(String)String[]", f"{STABLE_ID_PREFIX}d4329860e58cd08b"),
    "script": ("(String)", f"{STABLE_ID_PREFIX}8795b3125f7af9c9"),
}


def test_hole_free_declarations_hash_exactly_as_before(tmp_path: Path) -> None:
    (tmp_path / "Pinned.groovy").write_text(_PINNED_SOURCE)
    syms = {s.name: s for s in analyze_groovy(tmp_path).symbols}
    for name, (hashed, stable_id) in _PINNED.items():
        assert normalize_groovy_signature(syms[name].signature) == hashed, name
        assert syms[name].stable_id == stable_id, name


def test_the_normalizer_leaves_a_signature_without_varargs_untouched() -> None:
    # A ``...`` not followed by a parameter name (inside a type annotation's
    # string, say) is not a varargs marker and goes to the shared rule as is.
    from hypergumbo_core.analyze.base import normalize_signature_types_first

    for sig in ('(List<@A("x...") String> l)', "(int a, String b): long", "()"):
        assert normalize_groovy_signature(sig) == normalize_signature_types_first(
            sig, None, skip_void_return=False, return_sep=":",
        )
