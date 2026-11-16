# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py
"""A PHP callable's signature writes every parameter and type, and hashes them.

The signature text of a function and a method is normalized and hashed into
the typed ``stable_id``. Its renderer wrote a parameter only when it was a
``simple_parameter``, and a parameter or return type only when its node kind
was one of ``primitive_type``, ``named_type``, ``optional_type`` and
``union_type``. A ``variadic_parameter`` (``int ...$xs``) and a
``property_promotion_parameter`` (``private int $x``) were dropped whole; an
``intersection_type`` (``A&B``) and a ``disjunctive_normal_form_type``
(``(A&B)|null``) were dropped from a parameter (its name was kept) and from
the return type, and so was ``never`` (a ``bottom_type``). The by-reference
``&`` of a parameter and of a function (``function &f()``) was not written.
Even a written ``int ...`` would have hashed as the empty string: the PHP
normalizer runs each type through the shared FQN strip, which cuts at the
last dot.

Each test below fills one hole: two declarations that differ only inside it
get two stable ids, and the rendered signature contains the text. The last
tests pin the hashed text and the id of declarations whose types use only
the kinds that had a branch: they hash as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tree_sitter
import tree_sitter_php

from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.php import (
    _PHP_PARAMETER_NODE_TYPES,
    analyze_php,
    normalize_php_signature,
)


def _named(tmp_path: Path, source: str, name: str) -> list[Symbol]:
    (tmp_path / "a.php").write_text("<?php\n" + source + "\n")
    found = [s for s in analyze_php(tmp_path).symbols if s.name == name]
    return sorted(found, key=lambda s: s.span.start_line)


# (symbol name, first declaration, second declaration, text the first's
# signature must contain). The two declarations of a row share a name and a
# scope (PHP refuses such a pair at run time, but a conditional declaration
# or a polyfill writes one, and the analyzer reads both) and differ only
# inside the hole.
PAIRS = [
    # variadic_parameter: dropped whole.
    ("f", "function f(int ...$xs) {}", "function f() {}", "(int ...$xs)"),
    ("f", "function f(int ...$xs) {}", "function f(string ...$xs) {}", "(int ...$xs)"),
    ("f", "function f(int $a, string ...$xs) {}", "function f(int $a) {}",
     "(int $a, string ...$xs)"),
    ("f", "function f(...$xs) {}", "function f() {}", "(...$xs)"),
    ("f", "function f(\\Foo\\Bar ...$xs) {}", "function f(\\Foo\\Baz ...$xs) {}",
     "(\\Foo\\Bar ...$xs)"),
    # A by-reference parameter: its & was not written.
    ("f", "function f(&$x) {}", "function f($x) {}", "(&$x)"),
    ("f", "function f(array &$x) {}", "function f(array $x) {}", "(array &$x)"),
    ("f", "function f(&...$xs) {}", "function f(...$xs) {}", "(&...$xs)"),
    ("f", "function f(int &...$xs) {}", "function f(int ...$xs) {}", "(int &...$xs)"),
    # intersection_type / disjunctive_normal_form_type in a parameter: the
    # type was dropped, the name kept.
    ("f", "function f(A&B $x) {}", "function f($x) {}", "(A&B $x)"),
    ("f", "function f(A&B $x) {}", "function f(A&C $x) {}", "(A&B $x)"),
    ("f", "function f((A&B)|null $x) {}", "function f((A&C)|null $x) {}",
     "((A&B)|null $x)"),
    # Return types of those kinds, and never (a bottom_type).
    ("f", "function f(): never {}", "function f() {}", "(): never"),
    ("f", "function f(): A&B {}", "function f(): A&C {}", "(): A&B"),
    ("f", "function f(): (A&B)|null {}", "function f() {}", "(): (A&B)|null"),
    # A function returning by reference.
    ("f", "function &f() {}", "function f() {}", "&()"),
    ("f", "function &f(int $x): array {}", "function f(int $x): array {}", "&(int $x): array"),
    # property_promotion_parameter: dropped whole.
    ("C.__construct",
     "class C { function __construct(private int $a) {} function __construct(private string $a) {} }",
     None, "(int $a)"),
    ("C.__construct",
     "class C { function __construct(public $a, $b) {} function __construct($b) {} }",
     None, "($a, $b)"),
    ("C.__construct",
     "class C { function __construct(public array &$a) {} function __construct(public array $a) {} }",
     None, "(array &$a)"),
    # The same renderer reads every method: interface, trait, enum, abstract,
    # by-reference.
    ("I.m", "interface I { function m(A&B $x); function m(A&C $x); }", None, "(A&B $x)"),
    ("T.m", "trait T { function m(): never {} function m() {} }", None, "(): never"),
    ("E.m", "enum E { case A; function m(int ...$x) {} function m() {} }", None, "(int ...$x)"),
    ("C.m",
     "abstract class C { abstract function m(): A&B; abstract function m(): A&C; }",
     None, "(): A&B"),
    ("C.m", "class C { function &m() {} function m() {} }", None, "&()"),
]


@pytest.mark.parametrize(("name", "first", "second", "text"), PAIRS)
def test_declarations_differing_only_in_a_hole_get_two_ids(
    tmp_path: Path, name: str, first: str, second: str | None, text: str
) -> None:
    source = first if second is None else f"{first}\n{second}"
    found = _named(tmp_path, source, name)
    assert len(found) == 2
    assert found[0].signature is not None and text in found[0].signature
    assert found[0].stable_id and found[1].stable_id
    assert found[0].stable_id != found[1].stable_id


# One source per parameter node kind the renderer reads. The list taken from
# the analyzer is united with a literal copy, so that removing a kind from the
# analyzer's list leaves its case here (and the case fails).
_PARAMETER_SOURCES = {
    "simple_parameter": ("function f(int $x) {}", "f", "(int $x)"),
    "variadic_parameter": ("function f(int ...$x) {}", "f", "(int ...$x)"),
    "property_promotion_parameter": (
        "class C { function __construct(private int $x) {} }", "C.__construct", "(int $x)"
    ),
}
_LITERAL_PARAMETER_NODE_TYPES = {
    "simple_parameter", "variadic_parameter", "property_promotion_parameter",
}


def _parser() -> tree_sitter.Parser:
    return tree_sitter.Parser(tree_sitter.Language(tree_sitter_php.language_php()))


def _kinds_in(source: str) -> set[str]:
    tree = _parser().parse(("<?php\n" + source).encode())
    kinds: set[str] = set()
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        kinds.add(node.type)
        stack.extend(node.children)
    return kinds


@pytest.mark.parametrize("kind", sorted(_PHP_PARAMETER_NODE_TYPES | _LITERAL_PARAMETER_NODE_TYPES))
def test_every_parameter_kind_is_written(tmp_path: Path, kind: str) -> None:
    source, name, text = _PARAMETER_SOURCES[kind]
    assert kind in _kinds_in(source)  # the fixture reaches this kind
    (sym,) = _named(tmp_path, source, name)
    assert sym.signature == text


def test_the_parameter_kind_list_is_the_literal_copy() -> None:
    assert set(_PHP_PARAMETER_NODE_TYPES) == _LITERAL_PARAMETER_NODE_TYPES


# One source per type node kind of the grammar (the ``type`` supertype's six
# subtypes, and ``bottom_type``, which only a return type takes), in a
# parameter and as a return type.
_TYPE_NODE_TEXTS = {
    "primitive_type": "int",
    "named_type": "\\Foo\\Bar",
    "optional_type": "?int",
    "union_type": "int|string",
    "intersection_type": "A&B",
    "disjunctive_normal_form_type": "(A&B)|null",
}


@pytest.mark.parametrize("kind", sorted(_TYPE_NODE_TEXTS))
def test_every_type_kind_is_written_in_a_parameter_and_a_return(
    tmp_path: Path, kind: str
) -> None:
    written = _TYPE_NODE_TEXTS[kind]
    source = f"function f({written} $x): {written} {{}}"
    assert kind in _kinds_in(source)
    (sym,) = _named(tmp_path, source, "f")
    assert sym.signature == f"({written} $x): {written}"


def test_bottom_type_is_written_as_a_return(tmp_path: Path) -> None:
    source = "function f(): never {}"
    assert "bottom_type" in _kinds_in(source)
    (sym,) = _named(tmp_path, source, "f")
    assert sym.signature == "(): never"


@pytest.mark.parametrize(
    ("signature", "normalized"),
    [
        ("(int ...$xs)", "(int...)"),
        ("(...$xs)", "(...)"),
        ("(&...$xs)", "(&...)"),
        ("(int &...$xs)", "(int &...)"),
        ("(\\Foo\\Bar ...$xs): int", "(\\Foo\\Bar...)int"),
        ("(&$x, array &$y)", "(&,array &)"),
        ("(A&B $x, (A&B)|null $y): (A&B)|null", "(A&B,(A&B)|null)(A&B)|null"),
        ("&(int $x): int", "&(int)int"),
        ("&()", "&()"),
        ("(): never", "()never"),
    ],
)
def test_normalized_text_keeps_the_filled_forms(signature: str, normalized: str) -> None:
    assert normalize_php_signature(signature) == normalized


@pytest.mark.parametrize("signature", [None, "", "no parens", "(", "&", "&(int $x"])
def test_a_text_that_is_no_signature_normalizes_to_none(signature: str | None) -> None:
    assert normalize_php_signature(signature) is None


# Hole-free declarations: the hashed text and the stable_id are those the
# analyzer wrote before (pinned from the base arm, fd65842b25, on this exact
# file at the root of the analyzed tree).
_HOLE_FREE = """namespace App;
function plain(int $a, ?string $b, int|float $c, $d, \\Foo\\Bar $e): ?static {}
function untyped($x, $y) {}
function returnsVoid(array $xs): void {}
class K {
    public function m(callable $c, iterable $i, mixed $m, object $o, self $s): \\Foo\\Bar {}
    private static function n(?int $x = null): int|false {}
}"""

_PINNED = {
    "plain": ("(int,?string,int|float,$d,\\Foo\\Bar)?static", "sha256:9986f7cf938cb60d"),
    "untyped": ("($x,$y)", "sha256:37f79e237ad0871f"),
    "returnsVoid": ("(array)", "sha256:b7aa9afb515e4391"),
    "K.m": ("(callable,iterable,mixed,object,self)\\Foo\\Bar", "sha256:522d753fbb322574"),
    "K.n": ("(?int)int|false", "sha256:f66f14ed3b34a50f"),
}


@pytest.mark.parametrize("name", sorted(_PINNED))
def test_hole_free_declarations_hash_as_before(tmp_path: Path, name: str) -> None:
    (sym,) = _named(tmp_path, _HOLE_FREE, name)
    text, stable_id = _PINNED[name]
    assert normalize_php_signature(sym.signature) == text
    assert sym.stable_id == stable_id


@pytest.mark.parametrize(
    "signature",
    [
        "(int $x, int $y): int",
        "(string $msg): void",
        "($x, $y)",
        "(?string $name): ?int",
        "(int $x,,string $y): string",
        "(int $x): VOID",
        "(\\Foo\\Bar $x): \\Foo\\Baz",
        "(int|string $x, ?A $y): static",
    ],
)
def test_the_wrapper_writes_the_php_rule_text_without_a_new_form(signature: str) -> None:
    from hypergumbo_core.analyze.base import normalize_signature_php

    assert normalize_php_signature(signature) == normalize_signature_php(signature)
