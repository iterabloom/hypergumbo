# SPDX-License-Identifier: AGPL-3.0-or-later
"""Layout is not a typed stable_id input.

Through stable-id scheme v15 the typed tier hashed its signature and scope
texts with their whitespace: a parameter list a formatter split over lines
(``dart format``, ``rustfmt``, Xcode's re-indent), ``& mut T`` beside
``&mut T``, or ``Map<String,\n Integer>`` beside ``Map<String, Integer>``
each hashed apart, so reformatting a file moved ids (WI-kumoh; owner ruling
2026-11-19: layout must not be an id input).

:func:`make_typed_stable_id` now hashes each of its text slots through
:func:`layout_free`, which writes a text in ONE canonical spacing whatever
spacing it came with: the space between two tokens is decided by the tokens
alone (none inside a bracket or before a comma; one after a comma inside a
type, none after one the normalizer joined a list with; one around ``->``,
``=>``, ``|``, after ``:``...), chosen so that the spelling the analysed
texts already used most is kept byte for byte (counted over thirteen
repositories), so only the other spellings move. Where whitespace is meaning
it is kept as present or absent (between two words: ``unsigned int``;
between ``+ +`` or ``- -``), and a string literal is kept whole. The untyped
tier (:func:`assemble_stable_id`) is NOT normalized, except for C++: it
hashes names, and in some languages a name's whitespace is meaning (a CSS
selector's descendant combinator: ``.a .b`` is not ``.a.b``).
"""
from __future__ import annotations

import pytest

from hypergumbo_core.analyze.base import (
    LAYOUT_FREE_NAME_LANGUAGES,
    assemble_stable_id,
    layout_free,
    make_typed_stable_id,
)
from hypergumbo_core.overload_arity import DeclaredArity, declared_arity_stable_id

# (case, text, its layout-free form)
_RULE = [
    ("dart-multiline-group", "({\n  required String text,\n  int sid,\n})",
     "({ required String text, int sid, })"),
    ("rust-ref-mut", "(x: & mut T)", "(x: &mut T)"),
    ("cpp-closing-angles", "vector<vector<int> >", "vector<vector<int>>"),
    ("two-words-keep-one-space", "unsigned \n\t int", "unsigned int"),
    ("rust-impl-header", "impl < $0 > Foo for Bar < $0 >", "impl<$0> Foo for Bar<$0>"),
    ("csharp-ref-readonly", "ref  readonly int", "ref readonly int"),
    ("swift-multiline-return", "() -> Effect<\n      First.Action\n    >",
     "() -> Effect<First.Action>"),
    ("positional-type-parameter", "<$0 extends Comparable< $0 >>",
     "<$0 extends Comparable<$0>>"),
    ("dollar-is-a-word-character", "T extends $0", "T extends $0"),
    ("go-send-channel", "chan <- int", "chan<-int"),
    ("go-receive-channel", "<- chan int", "<-chan int"),
    ("scala-lower-bound", "[$0>:A]", "[$0 >: A]"),
    ("rust-where-bound", "Cow<str> :ToSql", "Cow<str>: ToSql"),
    ("rust-absolute-path-in-generics", "Vec< ::std::String >", "Vec<::std::String>"),
    ("member-operator", "Equatable . ==", "Equatable.=="),
    ("rust-double-reference", "(& &T)", "(&&T)"),
    ("scala-structural-type", "F[$0]{type L}", "F[$0] { type L }"),
    ("intersection-after-generic", "Array<T>&Sendable", "Array<T> & Sendable"),
    ("no-whitespace-list", "(String,int)void", "(String,int)void"),
    ("nested-comma-gets-a-space", "Map<String,Integer>", "Map<String, Integer>"),
    ("empty", "", ""),
    ("leading-and-trailing", "  (int)\n", "(int)"),
    ("unicode-words", "Größe  été", "Größe été"),
    ("arrow-not-fused", "- >", "- >"),
    # A ``<`` that opens no type arguments (a comparison in a default value)
    # is closed with the bracket around it, so the next list is a list again.
    ("comparison-left-open", "(x = a < b)(c, d)", "(x = a<b)(c,d)"),
]

# (case, text, its layout-free form): string and character literals keep
# every whitespace character they hold.
_LITERALS = [
    ("double-quoted", 'x: string = "a  b , c"', 'x: string = "a  b , c"'),
    ("single-quoted", "x = 'a , b'", "x = 'a , b'"),
    ("backtick", "fun `a  b`()", "fun`a  b`()"),
    ("escaped-quote", r'"a \"  b"  ,  x', r'"a \"  b",x'),
    ("char-literal-space", "c: ' ' ", "c: ' '"),
    ("unterminated", 'x = "a  b', 'x = "a  b'),
    ("literal-union", "'x' | 'y z'", "'x' | 'y z'"),
    ("raw-go-tag", 'struct{ a int `json:"a b"` }', 'struct{ a int`json:"a b"` }'),
]

# (case, text, its layout-free form): a ``'`` that does not close before a
# word character opens no literal (a Rust lifetime, a Haskell prime), so the
# whitespace after it is layout.
_NOT_LITERALS = [
    ("rust-lifetimes", "(&'a  str, &'b  str) -> &'a str", "(&'a str,&'b str) -> &'a str"),
    ("rust-anonymous-lifetimes", "&'_ T, &'_  U", "&'_ T,&'_ U"),
    ("single-lifetime", "&'static  str", "&'static str"),
]

# (case, text, its layout-free form with ``separators``): what a formatter
# writes when it splits a list over lines is layout in a signature.
_SEPARATORS = [
    ("dart-trailing-comma", "({\n  required String,\n  int,\n})", "({ required String, int })"),
    ("rust-generic-arguments", "Result<\n    (),\n    String,\n>", "Result<(), String>"),
    ("go-function-type", "func( int, ) error", "func(int)error"),
    ("kotlin-one-parameter", "suspend (\n  Int,\n) -> Unit", "suspend(Int) -> Unit"),
    ("scala-type-arguments", "Map[\n  String,\n  B,\n]", "Map[String, B]"),
    ("ts-object-type", "{\n  a: string;\n  b: number;\n}", "{ a: string; b: number }"),
    ("ts-leading-bar", "(u:\n  | 'a'\n  | 'b')", "(u: 'a' | 'b')"),
    ("leading-bar-first", "| A | B", "A | B"),
    ("bar-after-a-type-is-a-union", "(A|B)", "(A | B)"),
    ("comma-inside-a-literal", "(x = \"a,)\")", "(x = \"a,)\")"),
    ("one-tuple", "(T,)", "(T)"),
    ("separator-without-whitespace", "(int,error,)", "(int,error)"),
    # A comma that follows an opening bracket or another comma separates two
    # empty elements: C#'s array rank (``int[,]`` is not ``int[]``), an unbound
    # generic (``Dictionary<,>``), a Haskell tuple constructor ``(,)``.
    ("csharp-array-rank", "(int[ , ] m, int[,,] n)", "(int[,]m,int[,,]n)"),
    ("unbound-generic", "Dictionary< , >", "Dictionary<,>"),
    ("tuple-constructor", "(,) a b", "(,)a b"),
    ("sparse-array-default", "(x = [1, , ])", "(x = [1,,])"),
]

# Hashed texts in the spelling the analysed repositories wrote most often:
# hashed exactly as written, so their ids did not move.
_COMMONEST_SPELLINGS = [
    ("java-list-and-nested", "(String,int)List<Map<String, Integer>>"),
    ("swift-function-type", "((Int) -> String,(Int, String))"),
    ("swift-attribute", "(@escaping (Int) -> Void)"),
    ("swift-attributes", "(@MainActor @Sendable () -> Void)"),
    ("swift-dictionary", "(Int,[String: Int])Int?"),
    ("rust-reference-and-generics", "(&mut Self,Option<Vec<u8>>)Result<(), String>"),
    ("rust-bounds", "(Box<dyn Error + Send + Sync>)()&Self"),
    ("scala-union-and-bound", "(A | B,F[_])Int"),
    ("scala-function-type", "((Int, String) => Boolean,(Int, Int))"),
    ("scala-lower-bound", "[$0 >: A]($0)"),
    ("java-wildcard", "(Class<? extends T>,List<? super T>)"),
    ("php-nullable", "(?string,?User)?array"),
    ("swift-intersection", "(any Equatable & Sendable)"),
    ("python-defaults", "(dtype=…,copy=…)"),
    ("typescript-default", "(number = 5,string = '')void"),
    ("typescript-object-type", "({ a: string; b: number })"),
    ("go-tuple-result", "(map[K]V)(int,error)"),
    ("go-empty-struct", "(chan struct{})"),
    ("type-parameter-clause", "<$0 extends Comparable<$0>>($0)"),
    ("decorators", "property,with_phil"),
]

# (case, one line, the same reflowed by a formatter): one hashed text.
_REFLOWS = [
    ("dart", "({required String text, int sid})", "({\n  required String text,\n  int sid,\n})"),
    ("rust", "(x: &mut T,y: Vec<(u8, u16)>)", "(\n    x: & mut T,\n    y: Vec<(\n        u8,\n        u16,\n    )>,\n)"),
    ("swift", "() -> Effect<First.Action>", "() -> Effect<\n      First.Action\n    >"),
    ("kotlin", "((Int) -> Unit)", "((\n  Int,\n) -> Unit)"),
    ("typescript", "(o: { a: string; b: number }, u: 'a' | 'b')",
     "(\n  o: {\n    a: string;\n    b: number;\n  },\n  u:\n    | 'a'\n    | 'b',\n)"),
    ("go", "(int,error)", "(\n\tint,\n\terror,\n)"),
    ("cpp", "std::vector<std::vector<int>>", "std::vector<std::vector<int> >"),
    ("java", "Map<String, Integer>", "Map<String,\n        Integer>"),
]

# Pairs that are different code and must stay different once layout-free.
_DISTINCT = [
    ("string-content", 'f(x = "a  b")', 'f(x = "a b")'),
    ("plus-plus", "a + ++b", "a++ + b"),
    ("minus-minus", "- -x", "--x"),
    ("two-words", "unsigned int", "unsignedint"),
    ("arrow-or-two-tokens", "a -> b", "a - > b"),
    ("array-rank", "int[,]", "int[]"),
    ("backtick-name", "`a b`", "`ab`"),
]


@pytest.mark.parametrize(("case", "text", "expected"), _RULE + _LITERALS + _NOT_LITERALS,
                         ids=[c[0] for c in _RULE + _LITERALS + _NOT_LITERALS])
def test_layout_free_rule(case: str, text: str, expected: str) -> None:
    assert layout_free(text) == expected
    # Idempotent: the hashed form of a hashed form is itself.
    assert layout_free(expected) == expected


@pytest.mark.parametrize(("case", "text", "expected"), _SEPARATORS,
                         ids=[c[0] for c in _SEPARATORS])
def test_layout_free_separators(case: str, text: str, expected: str) -> None:
    assert layout_free(text, separators=True) == expected
    assert layout_free(expected, separators=True) == expected


def test_a_name_keeps_its_separators() -> None:
    # ``(,)`` and ``(|>)`` are names (a Haskell constructor, an F# operator).
    assert layout_free("(,)") == "(,)"
    assert layout_free("( |> )") == "(|>)"
    assert layout_free("{a;}") == "{ a; }"


@pytest.mark.parametrize(("case", "text"), _COMMONEST_SPELLINGS,
                         ids=[c[0] for c in _COMMONEST_SPELLINGS])
def test_the_commonest_spelling_is_hashed_as_written(case: str, text: str) -> None:
    assert layout_free(text, separators=True) == text


@pytest.mark.parametrize(("case", "one_line", "reflowed"), _REFLOWS, ids=[c[0] for c in _REFLOWS])
def test_a_reflowed_text_hashes_as_its_one_line_form(case: str, one_line: str, reflowed: str) -> None:
    assert layout_free(reflowed, separators=True) == layout_free(one_line, separators=True)


@pytest.mark.parametrize(("case", "a", "b"), _DISTINCT, ids=[c[0] for c in _DISTINCT])
def test_layout_free_keeps_distinct_code_distinct(case: str, a: str, b: str) -> None:
    assert layout_free(a) != layout_free(b)
    assert layout_free(a, separators=True) != layout_free(b, separators=True)


def _typed(sig: str, qualified_name: str = "C.f", name: str = "f",
           visibility: str = "public", decorators: str = "") -> str:
    return make_typed_stable_id(
        "method", sig, visibility, "", decorators, name=name,
        qualified_name=qualified_name, file_stable_id="anchor",
    )


def test_a_reflowed_signature_keeps_its_typed_id() -> None:
    one_line = _typed("(Map<String, Integer>,int)List<Map<String, Integer>>")
    reflowed = _typed("(Map<String,\n      Integer,\n>,int)List<Map<String,Integer>>")
    assert one_line == reflowed
    # The signature slot drops a formatter's separators; the name slots do not
    # (an F# operator ``(|>)`` is not ``(>)``, nor a name ``(a,)`` ``(a)``).
    for a, b in (("(|>)", "(>)"), ("(a,)", "(a)")):
        assert _typed("()", f"C.{a}", "f") != _typed("()", f"C.{b}", "f")
        assert _typed("()", "C.f", a) != _typed("()", "C.f", b)


def test_a_reflowed_scope_label_keeps_its_typed_id() -> None:
    # A Swift extension's where clause and a Rust impl header are in the
    # hashed qualified name.
    assert _typed("()", "Array where Element: P,\n  Element: Q.f") == _typed(
        "()", "Array where Element : P, Element : Q.f")
    assert _typed("()", "impl < $0 > Tr for & Foo < $0 >::f") == _typed(
        "()", "impl<$0> Tr for &Foo<$0>::f")


def test_every_text_slot_of_the_typed_formula_is_layout_free() -> None:
    base = _typed("()", "C.f", "f", "pub(crate)", "A,B")
    assert _typed("()", "C . f", "f", "pub(crate)", "A,B") == base
    assert _typed("()", "C.f", " f ", "pub(crate)", "A,B") == base
    assert _typed("()", "C.f", "f", "pub( crate )", "A,B") == base
    assert _typed("()", "C.f", "f", "pub(crate)", "A, B") == base


def test_whitespace_inside_a_literal_still_separates_typed_ids() -> None:
    assert _typed('(x: string = "a  b")') != _typed('(x: string = "a b")')


def test_the_untyped_tier_hashes_its_names_as_written() -> None:
    # A CSS selector's space is its descendant combinator.
    def untyped(qualified_name: str) -> str:
        return assemble_stable_id("class_selector", 0, "", "", "anchor", "s", qualified_name)
    assert untyped(".a .b") != untyped(".a.b")
    css = [declared_arity_stable_id(kind="class_selector", lang="css", path="a.css", name=n,
                                    qualified_name=n, arity=None) for n in (".a .b", ".a.b")]
    assert css[0] != css[1]


def test_a_cpp_name_is_hashed_layout_free_in_the_untyped_tier() -> None:
    assert "cpp" in LAYOUT_FREE_NAME_LANGUAGES

    def untyped(name: str, **kw: bool) -> str:
        return assemble_stable_id("method", 0, "", "", "anchor", name, f"W::{name}", **kw)
    assert untyped("operator void *", names_layout_free=True) == untyped(
        "operator void*", names_layout_free=True)
    assert untyped("operator void *") != untyped("operator void*")

    def cpp(name: str) -> str:
        return declared_arity_stable_id(
            kind="method", lang="cpp", path="w.cpp", name=name, qualified_name=f"W::{name}",
            arity=DeclaredArity(0, ""),
        )
    assert cpp("operator void *") == cpp("operator void*")
