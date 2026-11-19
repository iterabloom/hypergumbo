# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/csharp.py
"""A C# callable's signature writes every parameter and type, and hashes them.

The signature text of a method, constructor, indexer and operator is
normalized and hashed into the typed ``stable_id``. Its parameter renderer
had a branch for seven type node kinds (``predefined_type``, ``identifier``,
``generic_name``, ``array_type``, ``nullable_type``, ``qualified_name``,
``pointer_type``) and the method return type for the same seven plus
``ref_type``. Any other kind was dropped: a parameter typed by a
``tuple_type``, ``alias_qualified_name``, ``function_pointer_type`` or
``scoped_type`` vanished from the text, and so did the return type. The
parameter modifiers (``ref`` / ``out`` / ``in`` / ``this`` / ``scoped`` /
``readonly``) were skipped, a ``params`` array (the grammar puts it in the
parameter list, not in a ``parameter`` node) and ``__arglist`` were dropped
whole. ``F(ref int x)`` and ``F(int x)``, two overloads C# keeps apart, then
hashed one text and shared one id.

Each test below fills one hole: two declarations that differ only inside it
get two stable ids, and the rendered signature contains the text. The last
tests pin the hashed text and the id of declarations whose types use only
the kinds that had a branch: they hash as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.csharp import (
    _CSHARP_PARAMETER_MODIFIERS,
    analyze_csharp,
    normalize_csharp_signature,
)
from hypergumbo_core.identity_schemes import STABLE_ID_PREFIX


def _members(tmp_path: Path, body: str, name: str) -> list[Symbol]:
    (tmp_path / "A.cs").write_text(
        "using System;\nnamespace N {\n public unsafe class C {\n"
        f"{body}\n }}\n}}\n"
    )
    found = [s for s in analyze_csharp(tmp_path).symbols if s.name == f"C.{name}"]
    return sorted(found, key=lambda s: s.span.start_line)


# (member name, first declaration, second declaration, text the first's
# signature must contain). The two declarations of each row are overloads
# (or same-name members) of one class that differ only inside the hole.
PAIRS = [
    ("F", "void F(ref int x) { }", "void F(int x) { }", "(ref int x)"),
    ("F", "void F(out int x) { x = 1; }", "void F(int x) { }", "(out int x)"),
    ("F", "void F(in int x) { }", "void F(int x) { }", "(in int x)"),
    ("F", "void F(ref readonly int x) { }", "void F(int x) { }", "(ref readonly int x)"),
    ("F", "void F(scoped ref int x) { }", "void F(int x) { }", "(scoped ref int x)"),
    ("F", "void F(scoped Span<int> s) { }", "void F() { }", "(scoped Span<int> s)"),
    ("F", "static void F(this string s) { }", "static void F(string s) { }", "(this string s)"),
    ("F", "void F(params int[] xs) { }", "void F() { }", "(params int[] xs)"),
    ("F", "void F(int a, [X] params string[] xs) { }", "void F(int a) { }",
     "(int a, params string[] xs)"),
    ("F", "void F(__arglist) { }", "void F() { }", "(__arglist)"),
    ("F", "void F((int, string) t) { }", "void F((long, long) t) { }", "((int, string) t)"),
    ("F", "void F((int x, string y) t) { }", "void F() { }", "((int x, string y) t)"),
    ("F", "void F(A::B u) { }", "void F() { }", "(A::B u)"),
    ("F", "void F(delegate*<int, void> p) { }", "void F(delegate*<long, void> p) { }",
     "(delegate*<int, void> p)"),
    # The modifier survives the FQN strip of a qualified parameter type, and a
    # tuple's qualified elements are kept as a generic's arguments are.
    ("F", "void F(ref System.Int32 x) { }", "void F(System.Int32 x) { }", "(ref System.Int32 x)"),
    ("F", "void F(params System.Object[] xs) { }", "void F(System.Object[] xs) { }",
     "(params System.Object[] xs)"),
    ("F", "void F((int, System.String) t) { }", "void F((long, System.String) t) { }",
     "((int, System.String) t)"),
    # A constructor, an indexer and an operator take the same parameter list.
    ("C", "C(ref int x) { }", "C(int x) { }", "(ref int x)"),
    ("C", "C(params int[] xs) { }", "C() { }", "(params int[] xs)"),
    ("this[]", "int this[(int, int) t] => 0;", "int this[(long, long) t] => 0;",
     "((int, int) t) int"),
    ("this[]", "int this[params int[] xs] => 0;", "int this[int i] => 0;",
     "(params int[] xs) int"),
    ("operator+", "public static C operator +(C a, (int, int) b) => a;",
     "public static C operator +(C a, (long, long) b) => a;", "(C a, (int, int) b) C"),
    ("operator+", "public static C operator +(in C a, C b) => a;",
     "public static C operator +(C a, C b) => a;", "(in C a, C b) C"),
]


@pytest.mark.parametrize(("name", "first", "second", "text"), PAIRS)
def test_a_parameter_in_a_former_hole_is_written_and_hashed(
    tmp_path: Path, name: str, first: str, second: str, text: str,
) -> None:
    a, b = _members(tmp_path, f"  {first}\n  {second}", name)
    assert a.signature is not None and a.signature.startswith(text)
    assert a.stable_id != b.stable_id


# A return type of a kind that had no branch: (source, rendered return type).
RETURNS = [
    ("(int, string) F() => default;", "(int, string)"),
    ("(int a, System.String b) F() => default;", "(int a, System.String b)"),
    ("A::B F() => default;", "A::B"),
    ("delegate*<int> F() => null;", "delegate*<int>"),
]


@pytest.mark.parametrize(("source", "returned"), RETURNS)
def test_a_return_type_in_a_former_hole_is_written_and_hashed(
    tmp_path: Path, source: str, returned: str,
) -> None:
    """Overloads cannot differ by return type alone, so the same member is
    analysed twice: with the type, and returning ``void``."""
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    [typed] = _members(tmp_path / "x", f"  {source}", "F")
    [void] = _members(tmp_path / "y", "  void F() { }", "F")
    assert typed.signature == f"() {returned}"
    assert typed.stable_id != void.stable_id


def test_a_type_named_like_a_modifier_is_still_a_type(tmp_path: Path) -> None:
    """``scoped`` is a contextual keyword, so it can name a type: ``scoped x``
    is a parameter of type ``scoped``, and hashes that type."""
    [f] = _members(tmp_path, "  void F(scoped x) { }", "F")
    assert f.signature == "(scoped x)"
    assert normalize_csharp_signature(f.signature) == "(scoped)"


# Hashed texts of the forms that gained a rendering.
FILLED = {
    "(ref int x, out int y, in int z)": "(ref int,out int,in int)",
    "(this string s, scoped ref int q, ref readonly int r)":
        "(this string,scoped ref int,ref readonly int)",
    "(ref System.Int32 x, params System.Object[] xs)": "(ref Int32,params Object[])",
    "(scoped System.Span<int> s)": "(scoped Span<int>)",
    "(__arglist)": "(__arglist)",
    "((int, System.String) t) (System.Int32 a, string b)":
        "((int, System.String))(System.Int32 a, string b)",
    "(ref (int, int) t)": "(ref (int, int))",
    "(A::B u, delegate*<int, void> p) A::B": "(A::B,delegate*<int, void>)A::B",
}


@pytest.mark.parametrize(("signature", "hashed"), FILLED.items())
def test_the_hashed_text_of_a_filled_form(signature: str, hashed: str) -> None:
    assert normalize_csharp_signature(signature) == hashed


# Signatures whose types use only the kinds that had a branch: the hashed text
# is the shared types-first normalization's, unchanged (parameter names
# dropped, FQN heads stripped, ``void`` omitted, generic arguments kept as
# written, declared type parameters by position).
UNCHANGED = {
    "(int x, string? s, int? n, int[,] m, int[][] j, global::System.String g)":
        "(int,string?,int?,int[,],int[][],String)",
    "(int* p)": "(int*)",
    "(dynamic d) List<Dictionary<string, int>>": "(dynamic)List<Dictionary<string, int>>",
    "(System.Collections.Generic.List<System.String> xs) System.Int32":
        "(List<System.String>)Int32",
    "(int x) ref int": "(int)ref int",
    "()": "()",
    "(Clean f, int Q) Clean": "(Clean,int)Clean",
}


@pytest.mark.parametrize(("signature", "hashed"), UNCHANGED.items())
def test_hole_free_signatures_hash_as_before(signature: str, hashed: str) -> None:
    assert normalize_csharp_signature(signature) == hashed


# Forms the shared FQN strip cut at their last dot under stable-id scheme v15
# (``(int)Int32``, ``((int, int)?,Int32, int)[])``): a ``ref`` return keeps its
# modifier, and a nullable or array tuple is written whole as a tuple is.
NO_LONGER_CUT = {
    "(int x) ref System.Int32": "(int)ref Int32",
    "() ref readonly System.Int32": "()ref readonly Int32",
    "((int, int)? t, (System.Int32, int)[] u)": "((int, int)?,(System.Int32, int)[])",
}


@pytest.mark.parametrize(("signature", "hashed"), NO_LONGER_CUT.items())
def test_ref_returns_and_decorated_tuples_are_not_cut(signature: str, hashed: str) -> None:
    assert normalize_csharp_signature(signature) == hashed


def test_generic_parameters_by_position_are_kept() -> None:
    assert normalize_csharp_signature("(List<T> a, ref T b) (T, U)", ["T", "U"]) == (
        "(List<$0>,ref $0)($0, $1)")


def test_a_signature_that_does_not_open_a_parameter_list_does_not_normalize() -> None:
    assert normalize_csharp_signature("int") is None
    assert normalize_csharp_signature("(int x") is None
    assert normalize_csharp_signature("") is None


_CLEAN = """\
using System;
using System.Collections.Generic;

namespace Fx
{
    public class Clean
    {
        public Clean(int a, string b) { }
        public void H(int x, string? s, int? n, int[,] m, int[][] j, global::System.String g) { }
        public List<Dictionary<string, int>> G(dynamic d) => null;
        public Clean Def(Clean f = null, int y = Q) => f;
        public ref readonly int RoRet(int[] a) => ref a[0];
        public int this[string k] => 0;
        public static Clean operator -(Clean a, Clean b) => a;
        const int Q = 1;
    }
}
"""

# Stable ids of the hole-free members above, taken from the analyzer before
# the holes were filled (dev 3da1f85b37, file ``Clean.cs`` at the root).
PINNED = {
    ("constructor", "Clean.Clean"): f"{STABLE_ID_PREFIX}ad210095a4bc8b3e",
    ("method", "Clean.H"): f"{STABLE_ID_PREFIX}857b56ab37223405",
    ("method", "Clean.G"): f"{STABLE_ID_PREFIX}fd5db1288a1f18fd",
    ("method", "Clean.Def"): f"{STABLE_ID_PREFIX}c1657d9473d0a6bb",
    ("method", "Clean.RoRet"): f"{STABLE_ID_PREFIX}05fd7e7aaaaae32c",
    ("subscript", "Clean.this[]"): f"{STABLE_ID_PREFIX}220ec7dbd954342f",
    ("method", "Clean.operator-"): f"{STABLE_ID_PREFIX}1f7384d67b5de455",
}


def test_hole_free_members_keep_their_stable_ids(tmp_path: Path) -> None:
    (tmp_path / "Clean.cs").write_text(_CLEAN)
    got = {
        (s.kind, s.name): s.stable_id
        for s in analyze_csharp(tmp_path).symbols
        if (s.kind, s.name) in PINNED
    }
    assert got == PINNED


def test_a_default_value_is_not_the_parameter_name(tmp_path: Path) -> None:
    """The displayed name is the parameter's own, not an identifier in its
    default value (``int y = Q`` was shown as ``int Q``); the hashed text,
    which drops names, is unchanged."""
    [d] = _members(tmp_path, "  C D(C f = null, int y = Q) => f;\n  const int Q = 1;", "D")
    assert d.signature == "(C f, int y) C"


# Every parameter modifier keeps its place in the hashed text in front of a
# QUALIFIED type, where the shared FQN strip (which keeps what follows the last
# dot of a type's head) would otherwise cut it off. The cases are the union of
# the normalizer's own modifier set (so a modifier added there is covered) and
# the modifiers C# has today (so removing one from that set fails its case).
_MODIFIERS = sorted(
    set(_CSHARP_PARAMETER_MODIFIERS)
    | {"this", "scoped", "ref", "out", "in", "readonly", "params"},
)
# The smallest legal parameter for a modifier that cannot stand alone in front
# of ``System.Int32``: (modifier words, qualified type). The default is the
# modifier alone. ``this`` is legal because every case is a static method of
# a static class.
_MODIFIER_SHAPES = {
    "params": ("params", "System.Int32[]"),  # an array type
    "readonly": ("ref readonly", "System.Int32"),  # only after ``ref``
    "scoped": ("scoped ref", "System.Int32"),  # a ref, or a ref struct type
}


def _static_member_id(tmp_path: Path, folder: str, parameter: str) -> tuple[str, str | None]:
    root = tmp_path / folder
    root.mkdir()
    (root / "A.cs").write_text(
        "namespace N {\n public static class C {\n"
        f"  public static void F({parameter}) {{ }}\n }}\n}}\n"
    )
    [f] = [s for s in analyze_csharp(root).symbols if s.name == "C.F"]
    assert f.stable_id is not None
    return f.stable_id, f.signature


@pytest.mark.parametrize("modifier", _MODIFIERS)
def test_every_parameter_modifier_survives_a_qualified_type(
    tmp_path: Path, modifier: str,
) -> None:
    words, qualified = _MODIFIER_SHAPES.get(modifier, (modifier, "System.Int32"))
    simple = qualified.rsplit(".", 1)[-1]
    other = "out" if words == "ref" else "ref"
    modified, signature = _static_member_id(tmp_path, "modified", f"{words} {qualified} x")
    plain, _ = _static_member_id(tmp_path, "plain", f"{qualified} x")
    contrast, _ = _static_member_id(tmp_path, "contrast", f"{other} {qualified} x")
    assert signature == f"({words} {qualified} x)"
    assert normalize_csharp_signature(signature) == f"({words} {simple})"
    assert modified != plain
    assert modified != contrast
