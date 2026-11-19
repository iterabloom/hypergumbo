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

The corrected helpers are checked here, and so is the guarantee that keeps
every other id where it was: ``keeps_v15_text_when_lossless`` returns the v15
text whenever it holds every token of the corrected text, so only a signature
whose v15 text lost something hashes differently.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.analyze.base import (
    find_matching_paren,
    keeps_v15_text_when_lossless,
    normalize_signature_names_first,
    normalize_signature_types_first,
    split_params_top_level,
    strip_fqn_prefix,
    writes_v15_signature_text,
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
    ("a.B | None", "B | None"),     # the cut takes a qualifier only: kept as v15 cut it
    ("java.lang.@A String", "@A String"),   # Java's annotated qualified type
    ("Foo.Bar", "Bar"),              # no type-path rule asked: v15's cut
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
    ("x = f(a<b, c), d", ["x = f(a<b, c)", "d"]),   # a comparison's "<" closes with its ")"
    ('s = "a, b', ['s = "a, b']),                   # an unclosed string runs to the end
    ("a,,b, ", ["a", "", "b"]),                     # as v15: inner empty kept, last dropped
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

    @keeps_v15_text_when_lossless
    def v15_unmatched(signature: str | None) -> str | None:
        assert signature is not None
        return f"{find_matching_paren(signature, 0)}{find_matching_paren(signature, 9)}"

    assert v15_unmatched("(a") == "-1-1"


def test_paren_in_annotation_string_no_longer_truncates_the_parameters() -> None:
    with_paren = normalize_signature_types_first('(@A(")") String x) int')
    assert with_paren is not None and with_paren.endswith(")int")


# ---------------------------------------------------------------------------
# The v15 text, and when it is kept
# ---------------------------------------------------------------------------

@keeps_v15_text_when_lossless
def _names_first(signature: str | None) -> str | None:
    return normalize_signature_names_first(signature, return_sep="->")


def test_v15_text_kept_when_only_whitespace_differs() -> None:
    # v15 split the tuple of function types at its inner comma; the corrected
    # split does not. The texts differ by one space only: v15's is kept.
    signature = "(a: ((Int) -> Int, (Int) -> Int), b: Int)"
    assert _names_first(signature) == "(((Int) -> Int,(Int) -> Int),Int)"


def test_v15_text_kept_when_it_holds_more() -> None:
    # v15 read Scala's ``<:<`` as an unclosed bracket and kept the next
    # parameter's name; it lost no type, so its text stays.
    assert _names_first("(ev: B <:< AA, F: Functor[F]) -> X") == "(B <:< AA, F: Functor[F])X"


def test_corrected_text_when_v15_lost_a_type() -> None:
    assert _names_first("(f: (Int) -> a.B)") == "((Int) -> a.B)"
    assert _names_first("(f: (Str) -> a.B)") == "((Str) -> a.B)"


def test_v15_mode_reproduces_the_v15_helpers() -> None:
    seen: list[tuple[bool, str, list[str], int]] = []

    @keeps_v15_text_when_lossless
    def probe(signature: str | None) -> str | None:
        seen.append((
            writes_v15_signature_text(),
            strip_fqn_prefix("(Int, A.B)"),
            split_params_top_level("a: (Int) -> Int, b: {c, d}"),
            find_matching_paren('(")")', 0),
        ))
        return signature

    assert probe("x") == "x"
    assert seen == [
        (False, "(Int, A.B)", ["a: (Int) -> Int", "b: {c, d}"], 4),
        (True, "B)", ["a: (Int) -> Int", "b: {c", "d}"], 2),
    ]
    assert writes_v15_signature_text() is False


def test_nested_lossless_wrappers_run_once_in_v15_mode() -> None:
    calls: list[bool] = []

    @keeps_v15_text_when_lossless
    def inner(signature: str | None) -> str | None:
        calls.append(writes_v15_signature_text())
        return signature

    @keeps_v15_text_when_lossless
    def outer(signature: str | None) -> str | None:
        return inner(signature)

    assert outer("s") == "s"
    # outer: fixed run (inner: fixed + v15), then outer's v15 run (inner once).
    assert calls == [False, True, True]


def test_unparsable_corrected_text_falls_back_to_v15() -> None:
    @keeps_v15_text_when_lossless
    def only_v15(signature: str | None) -> str | None:
        return signature if writes_v15_signature_text() else None

    assert only_v15("(x)") == "(x)"
