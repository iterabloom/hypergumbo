# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared signature helpers keep distinct types distinct in the hashed text.

Under stable-id scheme v15 three helpers of ``analyze/base.py`` reduced distinct
types to one text, and the typed stable_id hashes that text:

- ``strip_fqn_prefix`` cut a type at its last dot even when the dot sat inside
  a compound type: ``(Int, A.B)`` -> ``B)``, ``(A) -> B.C`` -> ``C`` (the
  function type's parameters swallowed), ``@Ann a.B`` -> ``B``,
  ``ref Foo.Bar`` -> ``Bar``. It also cut a JVM nested type to its last
  segment (``Map.Entry`` = ``Other.Entry``) when asked to keep type paths.
- ``split_params_top_level`` took ``->``'s ``>`` for a closing bracket, read
  every ``<`` as an opening one, ignored ``{...}`` and split inside string
  literals.
- ``_find_matching_paren`` (now ``find_matching_paren``) closed a parameter
  list on a ``)`` inside a string literal.

The corrected helpers are checked here. Each language normalizer hashes the
corrected text: an id moves when its signature's v15 text differed from it,
which includes a v15 split that only lost a space or kept a parameter's name.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.analyze.base import (
    find_matching_paren,
    normalize_signature_names_first,
    normalize_signature_types_first,
    split_params_top_level,
    strip_fqn_prefix,
)


# ---------------------------------------------------------------------------
# strip_fqn_prefix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "(Int, A.B)",              # tuple with a qualified element
    "(A.B) -> C",              # function type, qualified parameter
    "(A) -> B.C",              # function type, qualified result: old text swallowed "(A) ->"
    "suspend (a.B) -> C",      # Kotlin suspend function type
    "@Ann a.B",                # annotated type
    "@A(\"x.y\") T",           # dot inside an annotation's string
    "ref Foo.Bar",             # C# ref return
    "(int, A.B)?",             # C# nullable tuple
    "(int, A.B)[]",            # C# array of tuples
    "String.() -> Unit",       # Kotlin function type with receiver
    "(x.A) -> List<B>",        # compound head before a generic argument list
    "None | a.B",              # union whose qualified member is not the head
])
def test_compound_type_is_written_whole(text: str) -> None:
    assert strip_fqn_prefix(text) == text


@pytest.mark.parametrize(("text", "stripped"), [
    ("java.lang.String", "String"),
    ("a.b.C<D.E>", "C<D.E>"),
    ("pkg.Foo?", "Foo?"),
    ("x.y.Z[]", "Z[]"),
    ("global::System.String", "String"),
    ("a.B | None", "B | None"),     # the cut takes a qualifier only
    ("java.lang.@A String", "@A String"),   # Java's annotated qualified type
    ("Foo.Bar", "Bar"),              # no type-path rule asked: the last segment
    ("String", "String"),
    ("", ""),
])
def test_plain_qualified_name_strips_as_before(text: str, stripped: str) -> None:
    assert strip_fqn_prefix(text) == stripped


@pytest.mark.parametrize(("text", "kept"), [
    ("Map.Entry", "Map.Entry"),
    ("java.util.Map.Entry<K, V>", "Map.Entry<K, V>"),
    ("Outer.Inner[]", "Outer.Inner[]"),
    ("java.lang.String", "String"),
    ("a.b.c", "c"),
    ("x.type", "x.type"),            # Scala singleton type
    ("scala.collection.Seq[A]", "Seq[A]"),
])
def test_keep_type_path_keeps_enclosing_types(text: str, kept: str) -> None:
    assert strip_fqn_prefix(text, keep_type_path=True) == kept


def test_nested_types_of_different_outers_stay_distinct() -> None:
    assert normalize_signature_types_first("(Foo.Bar x)", keep_type_path=True) != (
        normalize_signature_types_first("(Baz.Bar x)", keep_type_path=True))


# ---------------------------------------------------------------------------
# split_params_top_level
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "parts"), [
    ("a: ((Int) -> Int, (Int) -> Int), b: Int",
     ["a: ((Int) -> Int, (Int) -> Int)", "b: Int"]),
    ("f: (A, B) => C, d: D", ["f: (A, B) => C", "d: D"]),
    ("a: Map<(Int) -> Int, String>, b: Int", ["a: Map<(Int) -> Int, String>", "b: Int"]),
    ("c <-chan int, d int", ["c <-chan int", "d int"]),
    ("x: Int = a < b, y: Int", ["x: Int = a < b", "y: Int"]),
    ("opts: {a: string, b: number}, x: number", ["opts: {a: string, b: number}", "x: number"]),
    ('s: String = "a, b", t: T', ['s: String = "a, b"', "t: T"]),
    ("s: String = 'a, (b', t: T", ["s: String = 'a, (b'", "t: T"]),
    ('s = "a\\", b", t', ['s = "a\\", b"', "t"]),
    ("Map<String, List<Integer>>, boolean flag", ["Map<String, List<Integer>>", "boolean flag"]),
    ("{int a, int b}", ["{int a", "int b}"]),        # a Dart named group splits as before
    ("int x, {int a, int b}", ["int x", "{int a", "int b}"]),
    ("a: Int)", ["a: Int)"]),                       # an unmatched closer is text
    # A type argument list may follow a non-name character: C#'s function
    # pointers (``delegate*<...>``, ``delegate* unmanaged[Cdecl]<...>``).
    ("delegate*<int, void> p, int q", ["delegate*<int, void> p", "int q"]),
    ("delegate* unmanaged[Cdecl]<int, void> p, int q",
     ["delegate* unmanaged[Cdecl]<int, void> p", "int q"]),
    ("x = f(a<b, c), d", ["x = f(a<b, c)", "d"]),   # a comparison's "<" closes with its ")"
    ('s = "a, b', ['s = "a, b']),                   # an unclosed string runs to the end
    ("a,,b, ", ["a", "", "b"]),                     # inner empty kept, last dropped
])
def test_split_respects_every_bracket_and_string(text: str, parts: list[str]) -> None:
    assert split_params_top_level(text) == parts


def test_split_without_single_quote_keeps_rust_lifetimes_apart() -> None:
    text = "x: &'a str, y: &'a T"
    assert split_params_top_level(text, quotes='"') == ["x: &'a str", "y: &'a T"]


# ---------------------------------------------------------------------------
# find_matching_paren
# ---------------------------------------------------------------------------

def test_matching_paren_skips_string_literals() -> None:
    text = '(a = ")", b) R'
    assert find_matching_paren(text, 0) == text.index(") R")


def test_matching_paren_without_open_paren() -> None:
    assert find_matching_paren("x", 0) == -1
    assert find_matching_paren("", 0) == -1
    assert find_matching_paren("(a", 0) == -1


def test_paren_in_annotation_string_no_longer_truncates_the_parameters() -> None:
    with_paren = normalize_signature_types_first('(@A(")") String x) int')
    assert with_paren is not None and with_paren.endswith(")int")


# The names-first rule on the corrected split
# ---------------------------------------------------------------------------

def _names_first(signature: str | None) -> str | None:
    return normalize_signature_names_first(signature, return_sep="->")


def test_tuple_of_function_types_is_one_parameter() -> None:
    # v15 split the tuple at its inner comma (its text lost the space after it).
    signature = "(a: ((Int) -> Int, (Int) -> Int), b: Int)"
    assert _names_first(signature) == "(((Int) -> Int, (Int) -> Int),Int)"


def test_scala_subtype_evidence_no_longer_hashes_parameter_names() -> None:
    # v15 read Scala's ``<:<`` as an unclosed bracket, never split the clause,
    # and hashed ``F: Functor[F]`` with its parameter name.
    assert _names_first("(ev: B <:< AA, F: Functor[F]) -> X") == "(B <:< AA,Functor[F])X"


def test_function_type_result_is_kept() -> None:
    assert _names_first("(f: (Int) -> a.B)") == "((Int) -> a.B)"
    assert _names_first("(f: (Str) -> a.B)") == "((Str) -> a.B)"


def test_typescript_generic_function_type_is_one_parameter() -> None:
    assert split_params_top_level("f: <T, U>(a: T) => U, x: X") == [
        "f: <T, U>(a: T) => U", "x: X"]
    assert split_params_top_level("f: (<T, U>(a: T) => U), x: X") == [
        "f: (<T, U>(a: T) => U)", "x: X"]
