# SPDX-License-Identifier: AGPL-3.0-or-later
"""Swift's hashed signature writes every type as written (owner ruling 2026-11-16).

The signature renderers read six type node kinds (``user_type``, ``array_type``,
``dictionary_type``, ``optional_type``, ``tuple_type``, ``function_type``) and
dropped the rest. A parameter typed ``some P``, ``any P``, ``A & B``,
``repeat each T`` or ``~Copyable`` vanished from the signature; ``inout``,
``borrowing`` / ``consuming``, ``@escaping`` / ``@autoclosure``, a type
attribute (``@Sendable``), a variadic's ``...`` and an implicitly unwrapped
``!`` were lost; a return type or a property type of those kinds was not
written at all. The signature text is the typed stable_id's input, so two
declarations differing only there shared an id.

Each test below writes two one-declaration files that differ only inside a
former hole and asserts: the declaration is emitted, the two ids differ, and
the signature carries the type. Hole-free declarations keep their ids (pinned
from the commit before the fix), and the Swift normalizer gives the shared
names-first normalizer's result for every hole-free signature.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import normalize_signature_names_first
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.swift import analyze_swift, normalize_swift_signature

_HEAD = "protocol P {}\nprotocol Q {}\n"


def _symbol(root: Path, text: str, name: str) -> Symbol:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.swift").write_text(_HEAD + text + "\n")
    hits = [s for s in analyze_swift(root).symbols if s.name == name]
    assert len(hits) == 1, [s.name for s in analyze_swift(root).symbols]
    return hits[0]


#: (case, declaration A, declaration B, symbol name, text A's signature contains)
_CASES = [
    ("inout", "func f(x: inout Int) {}", "func f(x: Int) {}", "f", "x: inout Int"),
    ("variadic", "func f(x: Int...) {}", "func f(x: Int) {}", "f", "x: Int..."),
    ("escaping", "func f(x: @escaping () -> Void) {}", "func f(x: () -> Void) {}", "f",
     "@escaping () -> Void"),
    ("autoclosure", "func f(x: @autoclosure () -> Bool) {}", "func f(x: () -> Bool) {}", "f",
     "@autoclosure () -> Bool"),
    ("type-attribute", "func f(x: @Sendable () -> Void) {}", "func f(x: () -> Void) {}", "f",
     "@Sendable () -> Void"),
    ("borrowing-consuming", "func f(x: borrowing Int) {}", "func f(x: consuming Int) {}", "f",
     "x: borrowing Int"),
    ("opaque-parameter", "func f(x: some P) {}", "func f(x: some Q) {}", "f", "x: some P"),
    ("existential-parameter", "func f(x: any P) {}", "func f(x: any Q) {}", "f", "x: any P"),
    ("composition-parameter", "func f(x: P & Q) {}", "func f(x: P) {}", "f", "x: P & Q"),
    ("pack-expansion", "func f<each T>(x: repeat each T) {}", "func f<T>(x: T) {}", "f",
     "x: repeat each T"),
    ("suppressed-constraint", "func f(x: ~Copyable) {}", "func f() {}", "f", "x: ~Copyable"),
    ("iuo-parameter", "func f(x: Int!) {}", "func f(x: Int) {}", "f", "x: Int!"),
    ("opaque-return", "func f() -> some P { x }", "func f() -> some Q { x }", "f", "-> some P"),
    ("existential-return", "func f() -> any P { x }", "func f() {}", "f", "-> any P"),
    ("composition-return", "func f() -> P & Q { x }", "func f() -> P { x }", "f", "-> P & Q"),
    ("iuo-return", "func f() -> Int! { x }", "func f() -> Int { x }", "f", "-> Int!"),
    ("operator", "struct V {}\nfunc + (l: V, r: inout V) -> V { l }",
     "struct V {}\nfunc + (l: V, r: V) -> V { l }", "+", "r: inout V"),
    ("initialiser", "struct S { init(x: some P) {} }", "struct S { init(x: some Q) {} }",
     "S.init", "x: some P"),
    ("subscript-parameter", "struct S { subscript(i: Int...) -> Int { 1 } }",
     "struct S { subscript(i: Int) -> Int { 1 } }", "S.subscript(i:)", "i: Int..."),
    ("subscript-return", "struct S { subscript(i: Int) -> some P { x } }",
     "struct S { subscript(i: Int) -> some Q { x } }", "S.subscript(i:)", "-> some P"),
    ("computed-property", "struct S { var body: some P { x } }",
     "struct S { var body: any P { x } }", "S.body", "some P"),
    ("stored-field", "struct S { var v: any P }", "struct S { var v: any Q }", "S.v", "any P"),
    ("stored-field-attribute", "struct S { var v: @Sendable () -> Void }",
     "struct S { var v: () -> Void }", "S.v", "@Sendable () -> Void"),
    ("stored-field-iuo", "class S { var v: Int! }", "class S { var v: Int }", "S.v", "Int!"),
    ("top-level-variable", "var v: any P = x", "var v: any Q = x", "v", "any P"),
    ("protocol-function", "protocol R { func m(x: inout Int) -> any P }",
     "protocol R { func m(x: Int) -> any P }", "R.m", "x: inout Int"),
    ("protocol-property", "protocol R { var p: some P { get } }",
     "protocol R { var p: any P { get } }", "R.p", "some P"),
]


@pytest.mark.parametrize(
    ("text_a", "text_b", "name", "rendered"),
    [case[1:] for case in _CASES],
    ids=[case[0] for case in _CASES],
)
def test_a_former_hole_is_written_and_hashed(
    tmp_path: Path, text_a: str, text_b: str, name: str, rendered: str,
) -> None:
    a = _symbol(tmp_path / "a", text_a, name)
    b = _symbol(tmp_path / "b", text_b, name)
    assert a.stable_id is not None and b.stable_id is not None
    assert a.stable_id != b.stable_id, (a.signature, b.signature)
    assert rendered in (a.signature or ""), a.signature


def test_two_overloads_differing_in_a_former_hole_do_not_tie(tmp_path: Path) -> None:
    """In one file: ``merge(_: Self...)`` tied with ``merge(with: Self)``
    (swift-composable-architecture's ``Effect``), and the second was told
    apart only by an occurrence ordinal."""
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.swift").write_text(
        "struct E {\n"
        "    static func merge(_ effects: Self...) -> Self { effects[0] }\n"
        "    static func merge(_ effects: some Sequence<Self>) -> Self { x }\n"
        "    func merge(with other: Self) -> Self { other }\n"
        "}\n"
    )
    ids = [s.stable_id for s in analyze_swift(root).symbols if s.name == "E.merge"]
    assert len(ids) == 3
    assert len(set(ids)) == 3


#: Hole-free declarations, and their ids on the commit before the fix
#: (547149bf89): none of their types used a node kind that had no branch.
_HOLE_FREE = (
    "protocol P {}\n"
    "func f(x: Int, y: [String: Int]) -> Int? { nil }\n"
    "func h(_ cb: (Int) -> String, t: (Int, String)) {}\n"
    "struct S {\n"
    "    init(a: (Int) -> String) {}\n"
    "    var v: [Int] = []\n"
    "    var c: Int { 1 }\n"
    "    subscript(i: Int) -> Int { 1 }\n"
    "    func g(_ x: Foo.Bar) -> Set<Int> { [] }\n"
    "    deinit {}\n"
    "}\n"
    "protocol R {\n"
    "    func m(x: Int) -> String\n"
    "    var p: Int { get }\n"
    "}\n"
    "let top: [String] = []\n"
)
_PINNED = {
    "f": "hgstableid15:5a2f000fa219a85c",
    "h": "hgstableid15:140fa1de7861b2a7",
    "S.init": "hgstableid15:3e28183fc36b3c3f",
    "S.v": "hgstableid15:b97120ecdb3b499a",
    "S.c": "hgstableid15:9d321b7f3c78b955",
    "S.subscript(i:)": "hgstableid15:c3190acb35203179",
    "S.g": "hgstableid15:e5190034f69ef295",
    "S.deinit": "hgstableid15:1079eea70112982c",
    "R.m": "hgstableid15:7359f05f4fa047d9",
    "R.p": "hgstableid15:dc5dc570dda64d74",
    "top": "hgstableid15:53fd55052be58942",
}


def test_hole_free_declarations_hash_as_before(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.swift").write_text(_HOLE_FREE)
    got = {s.name: s.stable_id for s in analyze_swift(root).symbols if s.name in _PINNED}
    assert got == _PINNED


@pytest.mark.parametrize("signature", [
    "(x: Int, y: Int) -> Int",
    "(x: [String: Int]) -> Int?",
    "(_: Foo.Bar) -> Set<Int>",
    "(cb: (Int) -> String, t: (Int, String))",
    "(x: Result<Foo.A, Error>) -> Swift.Int",
    "(x: (Swift.Int) -> Foo.Bar)",
    "(x: Dictionary<String, [Int]>, y: Int?)",
    "(i: Int) -> Int",
    "() -> Void",
    "()",
    "(x: Int.Type)",
    "(x)",
])
def test_the_swift_normalizer_agrees_with_the_shared_one_on_hole_free_text(signature: str) -> None:
    assert normalize_swift_signature(signature) == normalize_signature_names_first(
        signature, return_sep="->",
    )


@pytest.mark.parametrize(("signature", "normalized"), [
    ("(x: Int...)", "(Int...)"),
    ("(x: inout Foo.Bar)", "(inout Bar)"),
    ("(x: Foo.A & Bar.B)", "(A & B)"),
    ("(x: @escaping (Int) -> Void)", "(@escaping (Int) -> Void)"),
    ("(x: @convention(c) (Int) -> Void)", "(@convention(c) (Int) -> Void)"),
    ("(x: ~Copyable)", "(~Copyable)"),
    ("(x: Int!) -> Foo.Bar!", "(Int!)Bar!"),
    ("() -> some View", "()some View"),
    ("(x: repeat each T)", "(repeat each T)"),
])
def test_a_decorated_type_keeps_its_decoration(signature: str, normalized: str) -> None:
    """The shared rewrite splits at the last ``.``: ``Int...`` would hash as
    ``""`` and ``inout Foo.Bar`` as ``Bar``. Only the core type takes it."""
    assert normalize_swift_signature(signature) == normalized


def test_the_normalizer_numbers_declared_generic_parameters() -> None:
    assert normalize_swift_signature("(x: inout T, y: U...)", ["T", "U"]) == (
        normalize_signature_names_first("(x: T, y: U)", ["T", "U"], return_sep="->")
        .replace("$0", "inout $0").replace("$1", "$1...")
    )


def test_a_signature_the_renderer_cannot_have_written_normalizes_to_none() -> None:
    assert normalize_swift_signature(None) is None
    assert normalize_swift_signature("x: Int") is None
    assert normalize_swift_signature("(x: Int") is None


#: A literal copy of ``_SWIFT_TYPE_PREFIX_RE``'s word alternation, so a word
#: removed from the regex keeps its case here.
_PREFIX_WORDS_LITERAL = (
    "inout", "borrowing", "consuming", "__owned", "__shared", "sending",
    "isolated", "some", "any", "repeat", "each",
)


def _regex_words() -> set[str]:
    from hypergumbo_lang_mainstream.swift import _SWIFT_TYPE_PREFIX_RE

    found = re.search(r"\(\?:([a-z_|]+)\)", _SWIFT_TYPE_PREFIX_RE.pattern)
    assert found is not None
    return set(found.group(1).split("|"))


def _decorated(word: str) -> str:
    """The parameter type text for ``word`` before a QUALIFIED core."""
    if word == "repeat":
        return "repeat each Mod.T"
    return f"{word} Mod.T"


#: Every prefix entry, as (case id, decorated parameter type, the hashed type
#: it must normalize to). The attribute branch is three cases, one with a
#: parenthesised argument.
_PREFIX_CASES = sorted(
    [(w, _decorated(w), "repeat each T" if w == "repeat" else f"{w} T")
     for w in set(_PREFIX_WORDS_LITERAL) | _regex_words()]
    + [
        ("@escaping", "@escaping Mod.T", "@escaping T"),
        ("@Sendable", "@Sendable Mod.T", "@Sendable T"),
        ("@convention(c)", "@convention(c) Mod.T", "@convention(c) T"),
    ],
)


def _f_symbol(root: Path, param_type: str) -> Symbol:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.swift").write_text(f"func f<each U>(x: {param_type}) {{}}\n")
    (hit,) = [s for s in analyze_swift(root).symbols if s.name == "f"]
    return hit


def test_the_literal_word_list_is_the_regexs() -> None:
    assert _regex_words() == set(_PREFIX_WORDS_LITERAL)


@pytest.mark.parametrize(
    ("param_type", "hashed"),
    [case[1:] for case in _PREFIX_CASES],
    ids=[case[0] for case in _PREFIX_CASES],
)
def test_a_prefix_before_a_qualified_type_is_hashed(
    tmp_path: Path, param_type: str, hashed: str,
) -> None:
    """Every entry of ``_SWIFT_TYPE_PREFIX_RE`` before a QUALIFIED core: the
    hashed text keeps the prefix and the simple name (``some T``, not the
    shared strip's ``T``), and the id differs from the undecorated
    declaration's and from the same declaration under another prefix.

    All eleven words and the three attributes go through the analyzer:
    ``__owned`` and ``__shared`` parse with an ERROR node in the venv's
    grammar, but the renderer writes the parameter's text after its ``:``
    whole, so they reach the normalizer as written (checked: the signature
    contains them). ``each`` alone (``x: each Mod.T``) parses as a pack."""
    decorated = _f_symbol(tmp_path / "decorated", param_type)
    plain = _f_symbol(tmp_path / "plain", "Mod.T")
    other = _f_symbol(
        tmp_path / "other", "borrowing Mod.T" if param_type.startswith("inout") else "inout Mod.T",
    )
    assert f"x: {param_type}" in (decorated.signature or "")
    assert normalize_swift_signature(decorated.signature) == f"({hashed})"
    assert decorated.stable_id != plain.stable_id
    assert decorated.stable_id != other.stable_id


@pytest.mark.parametrize(("param_type", "hashed"), [
    ("Mod.T...", "T..."),
    ("A.B & C.D", "B & D"),
    ("Mod.T!", "T!"),
])
def test_a_suffix_or_composition_around_a_qualified_type_is_hashed(
    tmp_path: Path, param_type: str, hashed: str,
) -> None:
    """The variadic ``...`` and a top-level ``&`` are kept beside qualified
    cores (the shared strip would give ``""`` and ``D``). ``Mod.T!`` needs no
    branch -- the ``!`` follows the last ``.`` -- and is pinned so that stays
    true."""
    decorated = _f_symbol(tmp_path / "decorated", param_type)
    plain = _f_symbol(tmp_path / "plain", "Mod.T")
    assert normalize_swift_signature(decorated.signature) == f"({hashed})"
    assert decorated.stable_id != plain.stable_id
