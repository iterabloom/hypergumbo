# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
"""Parameter names a caller never writes are not hashed in four forms the
function-type rule (stable-id v17) missed (stable-id v19).

1. A TypeScript object type's method, call and construct signatures
   (``o: { m(x: number): void }``, ``{ (x: number): void }``,
   ``{ new (x: number): Q }``) are function types written without ``=>``:
   their parameter names go as a function type's do. The rule reads a
   group written directly inside an object type's braces as such a list; a
   parenthesized type there names no parameter and is left as written.
2. A Go interface literal's method specs (``i interface{ M(x int) }``):
   a group after a method name directly inside ``interface{ ... }``.
3. A TypeScript interface's method member hashed its raw signature, outer
   parameter names included (``m(cb: ...)`` vs ``m(fn: ...)`` moved
   ``I.m``). Its parameters are now written as a function type's are
   (``x: T`` -> ``T``, ``x?: T`` -> ``?T``, ``...xs: T`` -> ``...T``), and
   a member with no parameter hashes what it did.
4. A type predicate names a parameter (``(x: unknown) => x is T``,
   ``f(x: unknown): x is T``, ``asserts x``): the name is written as the
   parameter's position, ``#0 is T``, so a rename moves nothing and which
   parameter is narrowed stays in the id. ``this is T`` is kept.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.identity_schemes import STABLE_ID_PREFIX
from hypergumbo_lang_mainstream.go import analyze_go
from hypergumbo_lang_mainstream.js_ts import analyze_javascript

_ANALYZERS: dict[str, tuple[str, Callable[[Path], AnalysisResult]]] = {
    "go": ("a.go", analyze_go),
    "ts": ("a.ts", analyze_javascript),
}
_GO = "package p\n\n"


def _id(root: Path, lang: str, text: str, name: str) -> str:
    fname, analyze = _ANALYZERS[lang]
    root.mkdir(parents=True)
    (root / fname).write_text(text)
    found = [s for s in analyze(root).symbols if s.name == name]
    assert len(found) == 1, [s.name for s in analyze(root).symbols]
    assert found[0].stable_id is not None, name  # reach: a typed id
    return found[0].stable_id


def _pair(tmp_path: Path, lang: str, first: str, second: str, name: str) -> tuple[str, str]:
    return _id(tmp_path / "x", lang, first, name), _id(tmp_path / "y", lang, second, name)


#: (language, first, second, symbol): the two differ only in a name no caller writes.
RENAMES: dict[str, tuple[str, str, str, str]] = {
    # 1. object-type members
    "method-signature": ("ts", "export function g(o: { m(x: number): void }) {}\n",
                         "export function g(o: { m(y: number): void }) {}\n", "g"),
    "call-signature": ("ts", "export function h(o: { (x: number): void }) {}\n",
                       "export function h(o: { (y: number): void }) {}\n", "h"),
    "construct-signature": ("ts", "export function k(o: { new (x: number): Q }) {}\n",
                            "export function k(o: { new (y: number): Q }) {}\n", "k"),
    "generic-optional-method": ("ts", "export function g(o: { m?<T>(x: T, ...r: T[]): T }) {}\n",
                                "export function g(o: { m?<T>(y: T, ...s: T[]): T }) {}\n", "g"),
    "second-member": ("ts", "export function g(o: { a: number; m(x: number): void }) {}\n",
                      "export function g(o: { a: number; m(y: number): void }) {}\n", "g"),
    "in-a-function-type": ("ts", "export function g(cb: (o: { m(x: number): void }) => void) {}\n",
                           "export function g(cb: (p: { m(y: number): void }) => void) {}\n", "g"),
    "in-a-generic-argument": ("ts", "export function g(o: Array<{ m(x: number): void }>) {}\n",
                              "export function g(o: Array<{ m(y: number): void }>) {}\n", "g"),
    "class-field": ("ts", "export class C { f: { m(x: number): void }; }\n",
                    "export class C { f: { m(y: number): void }; }\n", "C.f"),
    "interface-property": ("ts", "export interface I { p: { m(x: number): void }; }\n",
                           "export interface I { p: { m(y: number): void }; }\n", "I.p"),
    # 2. Go interface literal method specs
    "go-interface-literal": ("go", _GO + "func F(i interface{ M(x int) }) {}\n",
                             _GO + "func F(i interface{ M(y int) }) {}\n", "F"),
    "go-interface-literal-results": ("go", _GO + "func F(i interface{ M(x int) (n int, err error) }) {}\n",
                                     _GO + "func F(i interface{ M(y int) (m int, e error) }) {}\n", "F"),
    "go-interface-literal-two-methods": ("go", _GO + "func F(i interface {\n\tM(x int)\n\tN(a, b string)\n}) {}\n",
                                         _GO + "func F(i interface {\n\tM(y int)\n\tN(c, d string)\n}) {}\n", "F"),
    "go-struct-field": ("go", _GO + "type S struct{ f interface{ M(x int) } }\n",
                        _GO + "type S struct{ f interface{ M(y int) } }\n", "S.f"),
    "go-constraint": ("go", _GO + "func F[T interface{ M(x int) }](t T) {}\n",
                      _GO + "func F[T interface{ M(y int) }](t T) {}\n", "F"),
    # 3. interface method members
    "interface-method-callback": ("ts", "export interface I { m(cb: (x: number) => void): void; }\n",
                                  "export interface I { m(fn: (x: number) => void): void; }\n", "I.m"),
    "interface-method-plain": ("ts", "export interface I { m(a: string, b?: number, ...c: T[]): void; }\n",
                               "export interface I { m(x: string, y?: number, ...z: T[]): void; }\n", "I.m"),
    "interface-method-pattern": ("ts", "export interface I { m({ a, b }: O): void; }\n",
                                 "export interface I { m({ c }: O): void; }\n", "I.m"),
    # 4. type predicates
    "predicate-function-type": ("ts", "export function p(f: (x: unknown) => x is string) {}\n",
                                "export function p(f: (y: unknown) => y is string) {}\n", "p"),
    "predicate-declaration": ("ts", "export function q(x: unknown): x is string { return true; }\n",
                              "export function q(y: unknown): y is string { return true; }\n", "q"),
    "asserts-is": ("ts", "export function a(x: unknown): asserts x is string {}\n",
                   "export function a(y: unknown): asserts y is string {}\n", "a"),
    "asserts": ("ts", "export function a(x: unknown): asserts x {}\n",
                "export function a(y: unknown): asserts y {}\n", "a"),
    "predicate-method": ("ts", "export class C { is(v: unknown): v is C { return true; } }\n",
                         "export class C { is(w: unknown): w is C { return true; } }\n", "C.is"),
    "predicate-interface-method": ("ts", "export interface I { is(v: unknown): v is I; }\n",
                                   "export interface I { is(w: unknown): w is I; }\n", "I.is"),
    "predicate-object-type-method": ("ts", "export function g(o: { is(v: unknown): v is A }) {}\n",
                                     "export function g(o: { is(w: unknown): w is A }) {}\n", "g"),
    "predicate-after-a-comment": ("ts", "export function q(/* a */ x: unknown): x is A { return true; }\n",
                                  "export function q(/* a */ y: unknown): y is A { return true; }\n", "q"),
    "predicate-arrow-const": ("ts", "export const isA = (v: unknown): v is A => true;\n",
                              "export const isA = (w: unknown): w is A => true;\n", "isA"),
}


@pytest.mark.parametrize("case", sorted(RENAMES))
def test_renaming_a_name_no_caller_writes_keeps_the_id(tmp_path: Path, case: str) -> None:
    lang, first, second, name = RENAMES[case]
    a, b = _pair(tmp_path, lang, first, second, name)
    assert a == b, case


#: Pairs that must still hash apart.
DISTINCT: dict[str, tuple[str, str, str, str]] = {
    "method-signature-type": ("ts", "export function g(o: { m(x: number): void }) {}\n",
                              "export function g(o: { m(x: string): void }) {}\n", "g"),
    "method-signature-name": ("ts", "export function g(o: { m(x: number): void }) {}\n",
                              "export function g(o: { n(x: number): void }) {}\n", "g"),
    "object-type-property-name": ("ts", "export function g(o: { m: (x: number) => void }) {}\n",
                                  "export function g(o: { n: (x: number) => void }) {}\n", "g"),
    "parenthesized-member-type": ("ts", "export function g(o: { a: (A | B) }) {}\n",
                                  "export function g(o: { a: (A | C) }) {}\n", "g"),
    "go-interface-literal-type": ("go", _GO + "func F(i interface{ M(x int) }) {}\n",
                                  _GO + "func F(i interface{ M(x string) }) {}\n", "F"),
    "go-interface-literal-method": ("go", _GO + "func F(i interface{ M(x int) }) {}\n",
                                    _GO + "func F(i interface{ N(x int) }) {}\n", "F"),
    "interface-method-optional": ("ts", "export interface I { m(x?: number): void; }\n",
                                  "export interface I { m(x: number): void; }\n", "I.m"),
    "interface-method-rest": ("ts", "export interface I { m(...x: number[]): void; }\n",
                              "export interface I { m(x: number[]): void; }\n", "I.m"),
    "interface-method-type": ("ts", "export interface I { m(x: number): void; }\n",
                              "export interface I { m(x: string): void; }\n", "I.m"),
    "predicate-position": ("ts", "export function q(a: unknown, b: unknown): a is A { return true; }\n",
                           "export function q(a: unknown, b: unknown): b is A { return true; }\n", "q"),
    "predicate-type": ("ts", "export function q(x: unknown): x is A { return true; }\n",
                       "export function q(x: unknown): x is B { return true; }\n", "q"),
    "predicate-or-boolean": ("ts", "export function q(x: unknown): x is A { return true; }\n",
                             "export function q(x: unknown): boolean { return true; }\n", "q"),
    # A subject that names no parameter (not valid TypeScript) is kept as written.
    "predicate-names-no-parameter": ("ts", "export function q(x: unknown): y is A { return true; }\n",
                                     "export function q(x: unknown): z is A { return true; }\n", "q"),
    "this-predicate": ("ts", "export class C { is(): this is A { return true; } }\n",
                       "export class C { is(): this is B { return true; } }\n", "C.is"),
}


@pytest.mark.parametrize("case", sorted(DISTINCT))
def test_a_difference_a_caller_sees_still_moves_the_id(tmp_path: Path, case: str) -> None:
    lang, first, second, name = DISTINCT[case]
    a, b = _pair(tmp_path, lang, first, second, name)
    assert a != b, case


#: Declarations with none of the four forms hash what they did: (source,
#: symbol, the id it had before).
UNCHANGED: list[tuple[str, str, str]] = [
    ("export interface I { m(): string; n(): Promise<void>; p: number; }\n", "I.m", "e3ed8cc7edc8f0c3"),
    ("export interface I { m(): string; n(): Promise<void>; p: number; }\n", "I.n", "dc8181510b3b8857"),
    ("export function g(o: { a: number; b(): void }) {}\n", "g", "4ec7c900696fd1b4"),
]


@pytest.mark.parametrize(("source", "name", "digest"), UNCHANGED)
def test_a_declaration_without_the_forms_keeps_its_id(
    tmp_path: Path, source: str, name: str, digest: str,
) -> None:
    assert _id(tmp_path / "x", "ts", source, name) == f"{STABLE_ID_PREFIX}{digest}"
