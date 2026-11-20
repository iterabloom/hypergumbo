# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared rewrite that leaves a function type's parameter names out of a
hashed type text, and Go's rule over it (stable-id v17).

``drop_function_type_parameter_names`` finds each function type's parameter
list by the language's predicate, hands its parameters to the language's
rule, and writes everything else as it was, so a text with no named
function-type parameter comes back byte for byte (its id does not move).
Go's lists are found by the ``func`` keyword; a result list follows the
parameter list; names are all-or-none, and ``a, b int`` names two ``int``.
"""
from __future__ import annotations

import re

import pytest

from hypergumbo_core.analyze.base import (
    drop_arrow_function_type_parameter_names,
    drop_function_type_parameter_names,
    drop_go_function_type_parameter_names,
    normalize_signature_go,
)

_ARROW = re.compile(r"\s*->")
_NAME = re.compile(r"[A-Za-z_]\w*\s*:\s*")


def _arrow(text: str, *, declaration: bool = False) -> str:
    """A toy arrow language: ``(x: T) -> R``, a name is ``x: ``."""
    def without(part: str) -> str:
        match = _NAME.match(part)
        return part[match.end():] if match else part

    return drop_function_type_parameter_names(
        text,
        lambda t, _o, close: _ARROW.match(t, close + 1) is not None,
        lambda parts: [without(p) for p in parts],
        declaration=declaration,
    )


ARROW = {
    "(x: Int) -> Unit": "(Int) -> Unit",
    "(a: Int, b: Map<K, V>) -> Unit": "(Int, Map<K, V>) -> Unit",
    "Map<String, (k: String) -> Int>": "Map<String, (String) -> Int>",
    "(f: (y: Int) -> Unit) -> (z: Int) -> Unit": "((Int) -> Unit) -> (Int) -> Unit",
    "((x: Int) -> Unit)?": "((Int) -> Unit)?",
    # Not a function type's list: kept as written, inside rewritten.
    "(x: Int, y: Int)": "(x: Int, y: Int)",
    "(p: (q: Int) -> Unit)": "(p: (Int) -> Unit)",
    # Nothing to drop: byte for byte, spacing included.
    "(Int,String)  ->  Unit": "(Int,String)  ->  Unit",
    "": "",
    "Int": "Int",
    # A string is text, its parentheses too.
    '@A("(x: Int) -> ") (x: Int) -> Unit': '@A("(x: Int) -> ") (Int) -> Unit',
    # An unclosed group: the rest as written.
    "(x: Int) -> (y: Int": "(Int) -> (y: Int",
    "(a: Int, , b: Int) -> U": "(Int, , Int) -> U",
}


@pytest.mark.parametrize(("text", "expected"), ARROW.items())
def test_a_function_types_parameter_names_are_left_out(text: str, expected: str) -> None:
    assert _arrow(text) == expected


def test_a_declarations_own_list_is_not_a_function_types() -> None:
    """Swift writes a declaration ``(name: T) -> R``: its own list keeps its
    names (the declaration's normalizer reads them), the types inside go."""
    sig = "(cb: (x: Int) -> Unit) -> (y: Int) -> Unit"
    assert _arrow(sig, declaration=True) == "(cb: (Int) -> Unit) -> (Int) -> Unit"
    # Read as a type, the same text is a function type taking a function.
    assert _arrow(sig) == "((Int) -> Unit) -> (Int) -> Unit"
    assert _arrow("Int", declaration=True) == "Int"


GO = {
    "func(x int) error": "func(int) error",
    "func(a, b int)": "func(int, int)",
    "func(a, b int, s string)": "func(int, int, string)",
    "func(xs ...int)": "func(...int)",
    "func(_ int)": "func(int)",
    "func(int, string) error": "func(int, string) error",
    "func() (n int, err error)": "func() (int, error)",
    "func(x int) (int, error)": "func(int) (int, error)",
    "func(x int)(n int)": "func(int)(int)",
    "func(f func(y int)) func(z int)": "func(func(int)) func(int)",
    "map[string]func(k string) int": "map[string]func(string) int",
    "chan func(x int)": "chan func(int)",
    "func(c chan<- int, d int)": "func(chan<- int, int)",
    "func(m map[string]int, s struct{ a, b int })": "func(map[string]int, struct{ a, b int })",
    "func(x pkg.T) pkg.U": "func(pkg.T) pkg.U",
    # A keyword is a type's first word, not a name; ``myfunc(`` is no ``func(``.
    "func(chan int)": "func(chan int)",
    "myfunc(x int)": "myfunc(x int)",
    # A lone identifier with no named part after it stays.
    "func(x int, y)": "func(int, y)",
    # A struct tag holding parentheses is a string.
    'struct{ F int "json:\\"func(x int)\\"" }': 'struct{ F int "json:\\"func(x int)\\"" }',
    "(cb func(x int) error) (n int, err error)": "(cb func(int) error) (n int, err error)",
}


@pytest.mark.parametrize(("text", "expected"), GO.items())
def test_go_function_types_name_no_parameter(text: str, expected: str) -> None:
    assert drop_go_function_type_parameter_names(text) == expected


def test_go_signature_hashes_a_function_types_types_only() -> None:
    """The declaration's own names and result names are the normalizer's,
    as before; the function types inside it name nothing."""
    first = normalize_signature_go("(cb func(x int) error, m map[string]func(k string) int) (n int, err error)")
    second = normalize_signature_go("(cb func(y int) error, m map[string]func(j string) int) (n int, err error)")
    assert first == second == "(func(int) error,map[string]func(string) int)(n int,err error)"
    assert normalize_signature_go("(x int") is None


def test_the_arrow_adapter_takes_a_name_pattern_or_a_rule() -> None:
    """Kotlin and Swift hand it the name's pattern, TypeScript a rule."""
    text = "Map<K, (x: Int) => V>"
    assert drop_arrow_function_type_parameter_names(text, re.compile(r"\s*=>"), _NAME) == "Map<K, (Int) => V>"
    assert drop_arrow_function_type_parameter_names(
        text, re.compile(r"\s*=>"), lambda part: part.upper()) == "Map<K, (X: INT) => V>"
    assert drop_arrow_function_type_parameter_names(
        "(cb: (x: Int) -> U) -> R", _ARROW, _NAME, declaration=True) == "(cb: (Int) -> U) -> R"
