# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
"""A JavaScript or TypeScript parameter's default VALUE is not in its
function's typed stable id; that the parameter HAS a default is.

Owner ruling 2026-11-23 (stable-id v19): ``f(x: number = 5)`` and
``f(x: number = 6)`` are one function. A caller calls both alike, and
editing a default is an edit of behaviour, like an edit of the body. The
parameter's type stays in the id, and so does the fact that it has a
default: the hashed text writes every default value as ``...``
(``x: number = ...``), at any depth of the parameter (a default inside a
destructuring pattern too), the spelling JavaScript's assignment pattern was
already hashed with and the one Python hashes (``=…``). Through v18 a
TypeScript default was hashed as written, so editing it moved the id. The
displayed signature keeps the value.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _symbols(root: Path, name: str, text: str) -> dict[str, tuple[str | None, str | None]]:
    """Each callable's (stable id, displayed signature), by name."""
    root.mkdir(parents=True)
    (root / name).write_text(text)
    return {
        sym.name: (sym.stable_id, sym.signature)
        for sym in analyze_javascript(root).symbols
        if sym.kind in ("function", "method")
    }


def _pair(tmp_path: Path, first: str, second: str, symbol: str, name: str = "a.ts") -> tuple[
        tuple[str | None, str | None], tuple[str | None, str | None]]:
    one = _symbols(tmp_path / "x", name, first)[symbol]
    two = _symbols(tmp_path / "y", name, second)[symbol]
    assert one[0] is not None and two[0] is not None, symbol  # reach: both are typed ids
    return one, two


#: (first, second, symbol, file name): two declarations whose parameters
#: differ only in a default value.
SAME: dict[str, tuple[str, str, str, str]] = {
    "typed": ("export function f(x: number = 5) {}\n",
              "export function f(x: number = 6) {}\n", "f", "a.ts"),
    "untyped-ts": ("export function f(x = 5) {}\n", "export function f(x = 6) {}\n", "f", "a.ts"),
    "untyped-js": ("export function f(x = 5) {}\n", "export function f(x = 6) {}\n", "f", "a.js"),
    "string": ("export function f(s: string = 'a, b') {}\n",
               "export function f(s: string = \"c\") {}\n", "f", "a.ts"),
    "regex": ("export function f(r: RegExp = /a b/) {}\n",
              "export function f(r: RegExp = /c/g) {}\n", "f", "a.ts"),
    "arrow-value": ("export function f(cb = (y: number) => y) {}\n",
                    "export function f(cb = (z: number) => z + 1) {}\n", "f", "a.ts"),
    "object-value": ("export function f(o: O = { a: 1, b: [2] }) {}\n",
                     "export function f(o: O = {}) {}\n", "f", "a.ts"),
    "method": ("export class C { m(x: string = 'a', y = 1): void {} }\n",
               "export class C { m(x: string = 'b', y = 2): void {} }\n", "C.m", "a.ts"),
    "arrow-const": ("export const g = (x: number = 1): number => x;\n",
                    "export const g = (x: number = 2): number => x;\n", "g", "a.ts"),
    "constructor-property": (
        "export class D { constructor(private readonly x: number = 1) {} }\n",
        "export class D { constructor(private readonly x: number = 2) {} }\n",
        "D.constructor", "a.ts"),
    "nested-in-pattern": ("export function f({ a = 1 }: O = {}) {}\n",
                          "export function f({ a = 2 }: O = { a: 3 }) {}\n", "f", "a.ts"),
    "nested-untyped-pattern": ("export function f({ a = 1, b } = {}) {}\n",
                               "export function f({ a = 2, b } = {}) {}\n", "f", "a.ts"),
    "nested-array-element": ("export function f([a = 1, b]) {}\n",
                             "export function f([a = 2, b]) {}\n", "f", "a.ts"),
    "reflowed": ("export function f(x: number = 5, y = 'q') {}\n",
                 "export function f(\n  x: number =\n    7,\n  y = 'r',\n) {}\n", "f", "a.ts"),
}


@pytest.mark.parametrize("case", sorted(SAME))
def test_a_default_value_is_not_in_the_stable_id(tmp_path: Path, case: str) -> None:
    first, second, symbol, name = SAME[case]
    one, two = _pair(tmp_path, first, second, symbol, name)
    assert one[0] == two[0], case


def test_the_displayed_signature_keeps_the_value(tmp_path: Path) -> None:
    one, two = _pair(tmp_path, *SAME["typed"][:3])
    assert one[1] == "(x: number = 5)"
    assert two[1] == "(x: number = 6)"


#: (first, second, symbol): controls that must stay two ids.
APART: dict[str, tuple[str, str, str]] = {
    "has-a-default": ("export function f(x: number = 5) {}\n", "export function f(x: number) {}\n", "f"),
    "type": ("export function f(x: number = 5) {}\n", "export function f(x: string = '5') {}\n", "f"),
    "untyped-has-a-default": ("export function f(x = 5) {}\n", "export function f(x) {}\n", "f"),
    "second-parameter": ("export function f(x: number = 5, y?: string) {}\n",
                         "export function f(x: number = 5) {}\n", "f"),
}


@pytest.mark.parametrize("case", sorted(APART))
def test_what_a_caller_sees_stays_in_the_stable_id(tmp_path: Path, case: str) -> None:
    one, two = _pair(tmp_path, *APART[case])
    assert one[0] != two[0], case
