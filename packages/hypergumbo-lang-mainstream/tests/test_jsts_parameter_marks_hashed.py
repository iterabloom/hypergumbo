# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
"""A TypeScript parameter's optional mark ``?`` and rest mark ``...`` are in
its callable's typed stable id (owner ruling 2026-11-24, stable-id v20).

Through v19 the shared names-first normalizer read a typed parameter's text
after its ``:`` and dropped everything before it, the marks with the name:
``f(x?: number)`` hashed like ``f(x: number)`` and ``f(...xs: T[])`` like
``f(xs: T[])``, though a caller calls them differently. An interface's
method member already hashed ``?T`` and ``...T`` (a function type's
spelling, ``_ts_function_parameter_type``); an outer parameter now hashes
the same spelling: the mark is written before the parameter's type, and the
type is normalized as an unmarked one is (its qualifier cut the same way).

Every outer-callable form that hashes its parameters goes through the one
reader (``_jsts_callable_hashed``), so each is a case here, and so is a
symbol declared inside such a callable, whose scope label hashes it. The
controls: a parameter without a mark, an untyped one (its name and marks
were already hashed whole), JavaScript's rest parameter and an interface
member keep the ids they had (pinned), a default value stays out, and the
marks do not depend on layout or on the parameter's name.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.identity_schemes import STABLE_ID_PREFIX
from hypergumbo_lang_mainstream.js_ts import (
    _get_parser_for_file,
    _jsts_callable_hashed,
    analyze_javascript,
)


def _ids(root: Path, name: str, text: str) -> dict[str, str | None]:
    """Each non-file symbol's stable id, by name."""
    root.mkdir(parents=True)
    (root / name).write_text(text)
    return {sym.name: sym.stable_id for sym in analyze_javascript(root).symbols if sym.kind != "file"}


def _pair(tmp_path: Path, first: str, second: str, symbol: str, name: str) -> tuple[str, str]:
    one = _ids(tmp_path / "x", name, first).get(symbol)
    two = _ids(tmp_path / "y", name, second).get(symbol)
    assert one is not None and two is not None, symbol  # reach: both emitted, typed ids
    return one, two


def _svelte(body: str) -> str:
    return f'<script lang="ts">\n{body}</script>\n<div>hi</div>\n'


def _vue(body: str) -> str:
    return f'<template><div/></template>\n<script setup lang="ts">\n{body}</script>\n'


#: (marked, unmarked, symbol, file name): one callable written with and
#: without a mark on one parameter; every outer-callable form that emits a
#: symbol with a typed stable id.
APART: dict[str, tuple[str, str, str, str]] = {
    "function-optional": ("export function f(x?: number) {}\n",
                          "export function f(x: number) {}\n", "f", "a.ts"),
    "function-rest": ("export function f(...xs: number[]) {}\n",
                      "export function f(xs: number[]) {}\n", "f", "a.ts"),
    "generator": ("export function* g(x?: number) {}\n",
                  "export function* g(x: number) {}\n", "g", "a.ts"),
    "method": ("export class C { m(a: string, x?: number): void {} }\n",
               "export class C { m(a: string, x: number): void {} }\n", "C.m", "a.ts"),
    "static-method": ("export class C { static m(...xs: string[]) {} }\n",
                      "export class C { static m(xs: string[]) {} }\n", "C.m", "a.ts"),
    "constructor-property": (
        "export class C { constructor(@Optional() private readonly x?: Foo) {} }\n",
        "export class C { constructor(@Optional() private readonly x: Foo) {} }\n",
        "C.constructor", "a.ts"),
    "constructor-rest": ("export class C { constructor(...xs: Foo[]) {} }\n",
                         "export class C { constructor(xs: Foo[]) {} }\n", "C.constructor", "a.ts"),
    "arrow-const": ("export const g = (x?: number): number => 1;\n",
                    "export const g = (x: number): number => 1;\n", "g", "a.ts"),
    "function-expression-const": ("export const g = function (...xs: number[]) {};\n",
                                  "export const g = function (xs: number[]) {};\n", "g", "a.ts"),
    "generator-expression-const": ("export const g = function* (x?: number) {};\n",
                                   "export const g = function* (x: number) {};\n", "g", "a.ts"),
    "object-literal-method": ("export const o = { m(x?: number) {} };\n",
                              "export const o = { m(x: number) {} };\n", "m", "a.ts"),
    "callback": ("[1].map((x?: number) => x);\n", "[1].map((x: number) => x);\n", "_cb_map", "a.ts"),
    "overload-implementation": (
        "export function h(x: number): void;\nexport function h(x?: any) {}\n",
        "export function h(x: number): void;\nexport function h(x: any) {}\n", "h", "a.ts"),
    "typed-pattern-optional": ("export function f({ a, b }?: O) {}\n",
                               "export function f({ a, b }: O) {}\n", "f", "a.ts"),
    "typed-pattern-rest": ("export function f(...[a, b]: T[]) {}\n",
                           "export function f([a, b]: T[]) {}\n", "f", "a.ts"),
    "tsx": ("export function F(p?: Props) { return <div/>; }\n",
            "export function F(p: Props) { return <div/>; }\n", "F", "a.tsx"),
    "svelte": (_svelte("export function f(x?: number) {}\n"),
               _svelte("export function f(x: number) {}\n"), "f", "Comp.svelte"),
    "vue": (_vue("function f(...xs: number[]) {}\n"),
            _vue("function f(xs: number[]) {}\n"), "f", "Comp.vue"),
    # A symbol declared inside the callable hashes its label, which hashes
    # the callable's parameters.
    "local-in-callable": ("export function outer(x?: number) { function inner() {} }\n",
                          "export function outer(x: number) { function inner() {} }\n",
                          "inner", "a.ts"),
}


@pytest.mark.parametrize("case", sorted(APART))
def test_a_parameter_mark_is_in_the_stable_id(tmp_path: Path, case: str) -> None:
    marked, unmarked, symbol, name = APART[case]
    one, two = _pair(tmp_path, marked, unmarked, symbol, name)
    assert one != two


def test_optional_and_rest_are_two_marks(tmp_path: Path) -> None:
    optional, rest = _pair(tmp_path, "export function f(x?: number[]) {}\n",
                           "export function f(...x: number[]) {}\n", "f", "a.ts")
    assert optional != rest


#: (first, second, symbol): the same marked callable in two layouts, with
#: another parameter name, or with another qualifier on the type (a cut the
#: unmarked type gets too: the mark does not change how the type is hashed).
SAME: dict[str, tuple[str, str, str]] = {
    "no-space": ("export function f(x?: number) {}\n", "export function f(x?:number) {}\n", "f"),
    "spaced-mark": ("export function f(x?: number) {}\n",
                    "export function f(x ?  :  number) {}\n", "f"),
    "comment": ("export function f(x?: number) {}\n",
                "export function f(x?: /* c */ number) {}\n", "f"),
    "leading-union-bar": ("export function f(x?: A | B) {}\n",
                          "export function f(\n  x?:\n    | A\n    | B,\n) {}\n", "f"),
    "rest-reflowed": ("export function f(...xs: number[]) {}\n",
                      "export function f(\n  ...xs:\n    number[]\n) {}\n", "f"),
    "renamed": ("export function f(x?: number, ...r: T[]) {}\n",
                "export function f(y?: number, ...s: T[]) {}\n", "f"),
    "qualifier": ("export function f(x?: ns.Foo) {}\n", "export function f(x?: other.Foo) {}\n", "f"),
    "qualifier-rest": ("export function f(...x: ns.Foo[]) {}\n",
                       "export function f(...x: other.Foo[]) {}\n", "f"),
}


@pytest.mark.parametrize("case", sorted(SAME))
def test_a_mark_does_not_depend_on_layout_or_name(tmp_path: Path, case: str) -> None:
    first, second, symbol = SAME[case]
    one, two = _pair(tmp_path, first, second, symbol, "a.ts")
    assert one == two


def test_the_qualifier_rule_is_the_unmarked_one(tmp_path: Path) -> None:
    """The control of the two qualifier cases above: the unmarked type's
    qualifier is cut too (so the marked rule is not new)."""
    one, two = _pair(tmp_path, "export function f(x: ns.Foo) {}\n",
                     "export function f(x: other.Foo) {}\n", "f", "a.ts")
    assert one == two


#: (source, symbol, file name, stable id on dev fc8c8cb19, before the mark
#: was hashed): forms with no mark to hash keep their ids. An untyped
#: parameter is hashed whole (``x?``, ``...xs``), so its marks were in already.
UNCHANGED: dict[str, tuple[str, str, str, str]] = {
    "plain": ("export function f(x: number) {}\n", "f", "a.ts", f"{STABLE_ID_PREFIX}9a3af27e3d5ad247"),
    "default": ("export function f(x: number = 5) {}\n", "f", "a.ts",
                f"{STABLE_ID_PREFIX}7476eb82f4214bbf"),
    "untyped-optional": ("export function f(x?) {}\n", "f", "a.ts", f"{STABLE_ID_PREFIX}8ab835d1f8ae5e68"),
    "untyped-rest": ("export function f(...xs) {}\n", "f", "a.ts", f"{STABLE_ID_PREFIX}aa42fc6e9e98c9c5"),
    "js-rest": ("export function f(...xs) {}\n", "f", "a.js", f"{STABLE_ID_PREFIX}0988d20633f5626e"),
    "function-type-optional": ("export function f(cb: (y?: number) => void) {}\n", "f", "a.ts",
                               f"{STABLE_ID_PREFIX}cf1f4ee437c2bb0b"),
    "interface-member": ("export interface I { m(x?: number): void; }\n", "I.m", "a.ts",
                         f"{STABLE_ID_PREFIX}87e250b896ad7a79"),
}


@pytest.mark.parametrize("case", sorted(UNCHANGED))
def test_a_form_without_a_mark_to_hash_keeps_its_id(tmp_path: Path, case: str) -> None:
    source, symbol, name, pinned = UNCHANGED[case]
    assert _ids(tmp_path / "x", name, source).get(symbol) == pinned


def test_javascript_rest_and_plain_parameter_stay_apart(tmp_path: Path) -> None:
    """JavaScript's rest parameter has no type, so it is hashed whole
    (``...xs``) and never tied with ``xs``: nothing to change there."""
    rest, plain = _pair(tmp_path, "export function f(...xs) {}\n",
                        "export function f(xs) {}\n", "f", "a.js")
    assert rest != plain


def test_a_default_value_stays_out(tmp_path: Path) -> None:
    """WI-dozos rule 1 holds beside the marks: the value is not hashed."""
    five, six = _pair(tmp_path, "export function f(x: number = 5, ...r: T[]) {}\n",
                      "export function f(x: number = 6, ...r: T[]) {}\n", "f", "a.ts")
    assert five == six


def test_the_displayed_signature_is_as_written(tmp_path: Path) -> None:
    root = tmp_path / "x"
    root.mkdir()
    (root / "a.ts").write_text("export function f(x?: number, ...xs: T[]) {}\n")
    (sym,) = [s for s in analyze_javascript(root).symbols if s.name == "f"]
    assert sym.signature == "(x?: number, ...xs: T[])"


@pytest.mark.parametrize(("source", "hashed"), [
    ("function f(x?: ns.Foo, ...xs: T[]): void {}", "(?Foo,...T[])void"),
    ("function f(@D() private readonly x?: number, { a }?: O, ...[b]: T[]) {}", "(?number,?O,...T[])"),
    ("function f(x: ns.Foo, xs: T[], y?, ...z) {}", "(Foo,T[],y?,...z)"),
])
def test_the_hashed_spelling_is_a_function_types(source: str, hashed: str) -> None:
    """The mark is written before the type, as a function type's parameter
    and an interface's method member write it (``?T``, ``...T``), and the
    type behind it is normalized as an unmarked one (``ns.Foo`` -> ``Foo``).
    The third case is the control: no typed mark, nothing written."""
    data = source.encode()
    (node,) = _get_parser_for_file(Path("a.ts")).parse(data).root_node.named_children
    assert node.type == "function_declaration"  # reach
    assert _jsts_callable_hashed(node, data) == hashed
