# SPDX-License-Identifier: AGPL-3.0-or-later
"""A function type's parameter names are not part of a typed stable_id.

Kotlin ``(x: Int) -> Unit``, Swift ``(_ x: Int) -> Void``, Go ``func(x int)
error`` and TypeScript ``(x: number) => void`` name their parameters for the
reader only: a value of that type is called positionally, and two function
types that differ only in those names are one type. Until stable-id v17 the
hashed signature wrote such a type as written, so renaming a callback's inner
parameter moved the id of every declaration that mentioned the type (owner
ruling 2026-11-20: drop them). Dart already dropped its positional ones.

The names go wherever the function type stands: a parameter, a return type,
a generic argument, a nested function type, a field or property type, an
interface or protocol member. What a caller of the DECLARATION writes is
untouched: a Swift outer argument label stays hashed (a Swift function type
cannot carry labels), and a Swift tuple type's element labels are its type.
TypeScript's ``...`` and ``?`` markers stay (``(...xs: T[])`` and ``(x?: T)``
are other types than ``(x: T)``), and a ``this:`` parameter is a type.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.identity_schemes import STABLE_ID_PREFIX
from hypergumbo_lang_mainstream.go import analyze_go
from hypergumbo_lang_mainstream.js_ts import _ts_function_parameter_type, analyze_javascript
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin
from hypergumbo_lang_mainstream.swift import analyze_swift

_ANALYZERS: dict[str, tuple[str, Callable[[Path], AnalysisResult]]] = {
    "kotlin": ("a.kt", analyze_kotlin),
    "swift": ("a.swift", analyze_swift),
    "go": ("a.go", analyze_go),
    "ts": ("a.ts", analyze_javascript),
}


def _id(root: Path, lang: str, text: str, name: str) -> str:
    fname, analyze = _ANALYZERS[lang]
    root.mkdir(parents=True)
    (root / fname).write_text(text)
    found = [s for s in analyze(root).symbols if s.name == name]
    assert len(found) == 1, [s.name for s in analyze(root).symbols]
    assert found[0].stable_id is not None, name
    return found[0].stable_id


def _pair(tmp_path: Path, lang: str, first: str, second: str, name: str) -> tuple[str, str]:
    return _id(tmp_path / "x", lang, first, name), _id(tmp_path / "y", lang, second, name)


_GO = "package p\n\n"

#: (language, first, second, symbol): the two declare one symbol whose
#: function types differ only in their parameters' names.
RENAMES: dict[str, tuple[str, str, str, str]] = {
    # Kotlin
    "kt-parameter": ("kotlin", "fun f(cb: (x: Int) -> Unit) {}\n", "fun f(cb: (y: Int) -> Unit) {}\n", "f"),
    "kt-two": ("kotlin", "fun f(cb: (a: Int, b: String) -> Unit) {}\n",
               "fun f(cb: (c: Int, d: String) -> Unit) {}\n", "f"),
    "kt-return": ("kotlin", "fun f(): (x: Int) -> String = { \"\" }\n",
                  "fun f(): (y: Int) -> String = { \"\" }\n", "f"),
    "kt-generic-argument": ("kotlin", "fun f(m: Map<String, (k: String) -> Int>) {}\n",
                            "fun f(m: Map<String, (j: String) -> Int>) {}\n", "f"),
    "kt-nested": ("kotlin", "fun f(cb: (g: (y: Int) -> Unit) -> Unit) {}\n",
                  "fun f(cb: (h: (z: Int) -> Unit) -> Unit) {}\n", "f"),
    "kt-suspend": ("kotlin", "fun f(cb: suspend (x: Int) -> Unit) {}\n",
                   "fun f(cb: suspend (y: Int) -> Unit) {}\n", "f"),
    "kt-receiver-function-type": ("kotlin", "fun f(cb: String.(x: Int) -> Unit) {}\n",
                                  "fun f(cb: String.(y: Int) -> Unit) {}\n", "f"),
    "kt-nullable": ("kotlin", "fun f(cb: ((x: Int) -> Unit)?) {}\n",
                    "fun f(cb: ((y: Int) -> Unit)?) {}\n", "f"),
    "kt-vararg": ("kotlin", "fun f(vararg cb: (x: Int) -> Unit) {}\n",
                  "fun f(vararg cb: (y: Int) -> Unit) {}\n", "f"),
    "kt-field": ("kotlin", "class C { val p: (x: Int) -> Unit = {} }\n",
                 "class C { val p: (y: Int) -> Unit = {} }\n", "C.p"),
    "kt-nullable-field": ("kotlin", "class C { var p: ((x: Int) -> Unit)? = null }\n",
                          "class C { var p: ((y: Int) -> Unit)? = null }\n", "C.p"),
    "kt-method": ("kotlin", "class C { fun m(cb: (x: Int) -> Unit) {} }\n",
                  "class C { fun m(cb: (y: Int) -> Unit) {} }\n", "C.m"),
    "kt-extension-receiver": ("kotlin", "fun ((x: Int) -> Unit).f() {}\n",
                              "fun ((y: Int) -> Unit).f() {}\n", "f"),
    "kt-extension-property-receiver": ("kotlin", "class C { val ((x: Int) -> Unit).p: Int get() = 1 }\n",
                                       "class C { val ((y: Int) -> Unit).p: Int get() = 1 }\n", "C.p"),
    # Swift
    "sw-parameter": ("swift", "func f(cb: (_ x: Int) -> Void) {}\n", "func f(cb: (_ y: Int) -> Void) {}\n", "f"),
    "sw-two": ("swift", "func f(cb: (_ a: Int, _ b: String) -> Void) {}\n",
               "func f(cb: (_ c: Int, _ d: String) -> Void) {}\n", "f"),
    "sw-return": ("swift", "func f() -> (_ x: Int) -> String { { _ in \"\" } }\n",
                  "func f() -> (_ y: Int) -> String { { _ in \"\" } }\n", "f"),
    "sw-dictionary-value": ("swift", "func f(m: [String: (_ k: String) -> Int]) {}\n",
                            "func f(m: [String: (_ j: String) -> Int]) {}\n", "f"),
    "sw-nested": ("swift", "func f(cb: (_ g: (_ y: Int) -> Void) -> Void) {}\n",
                  "func f(cb: (_ h: (_ z: Int) -> Void) -> Void) {}\n", "f"),
    "sw-escaping": ("swift", "func f(cb: @escaping (_ x: Int) -> Void) {}\n",
                    "func f(cb: @escaping (_ y: Int) -> Void) {}\n", "f"),
    "sw-throws": ("swift", "func f(cb: (_ x: Int) throws -> Void) {}\n",
                  "func f(cb: (_ y: Int) throws -> Void) {}\n", "f"),
    "sw-async-throws": ("swift", "func f(cb: (_ x: Int) async throws -> Void) {}\n",
                        "func f(cb: (_ y: Int) async throws -> Void) {}\n", "f"),
    "sw-labelled-outer": ("swift", "func f(on cb: (_ x: Int) -> Void) {}\n",
                          "func f(on cb: (_ y: Int) -> Void) {}\n", "f"),
    "sw-init": ("swift", "struct S { init(cb: (_ x: Int) -> Void) {} }\n",
                "struct S { init(cb: (_ y: Int) -> Void) {} }\n", "S.init"),
    "sw-stored-property": ("swift", "struct S { var p: (_ x: Int) -> Void }\n",
                           "struct S { var p: (_ y: Int) -> Void }\n", "S.p"),
    "sw-optional-property": ("swift", "struct S { let p: ((_ x: Int) -> Void)? }\n",
                             "struct S { let p: ((_ y: Int) -> Void)? }\n", "S.p"),
    "sw-computed-property": ("swift", "struct S { var p: (_ x: Int) -> Void { { _ in } } }\n",
                             "struct S { var p: (_ y: Int) -> Void { { _ in } } }\n", "S.p"),
    "sw-protocol-method": ("swift", "protocol P { func m(cb: (_ x: Int) -> Void) }\n",
                           "protocol P { func m(cb: (_ y: Int) -> Void) }\n", "P.m"),
    "sw-protocol-property": ("swift", "protocol P { var p: (_ x: Int) -> Void { get } }\n",
                             "protocol P { var p: (_ y: Int) -> Void { get } }\n", "P.p"),
    # Go
    "go-parameter": ("go", _GO + "func F(cb func(x int) error) {}\n", _GO + "func F(cb func(y int) error) {}\n", "F"),
    "go-grouped": ("go", _GO + "func F(cb func(a, b int)) {}\n", _GO + "func F(cb func(c, d int)) {}\n", "F"),
    "go-results": ("go", _GO + "func F(cb func() (n int, err error)) {}\n",
                   _GO + "func F(cb func() (m int, e error)) {}\n", "F"),
    "go-variadic": ("go", _GO + "func F(cb func(xs ...int)) {}\n", _GO + "func F(cb func(ys ...int)) {}\n", "F"),
    "go-return": ("go", _GO + "func F() func(x int) error { return nil }\n",
                  _GO + "func F() func(y int) error { return nil }\n", "F"),
    "go-map-value": ("go", _GO + "func F(m map[string]func(k string) int) {}\n",
                     _GO + "func F(m map[string]func(j string) int) {}\n", "F"),
    "go-nested": ("go", _GO + "func F(cb func(g func(y int))) {}\n", _GO + "func F(cb func(h func(z int))) {}\n", "F"),
    "go-slice-and-chan": ("go", _GO + "func F(s []func(x int), c chan func(x int)) {}\n",
                          _GO + "func F(s []func(y int), c chan func(z int)) {}\n", "F"),
    "go-method": ("go", _GO + "type S struct{}\n\nfunc (s S) M(cb func(x int)) {}\n",
                  _GO + "type S struct{}\n\nfunc (s S) M(cb func(y int)) {}\n", "S.M"),
    "go-interface-method": ("go", _GO + "type I interface {\n\tM(cb func(x int))\n}\n",
                            _GO + "type I interface {\n\tM(cb func(y int))\n}\n", "I.M"),
    "go-field": ("go", _GO + "type S struct {\n\tF func(x int) error\n}\n",
                 _GO + "type S struct {\n\tF func(y int) error\n}\n", "S.F"),
    "go-type-parameter-constraint": ("go", _GO + "func F[T func(x int)](t T) {}\n",
                                     _GO + "func F[T func(y int)](t T) {}\n", "F"),
    # TypeScript
    "ts-parameter": ("ts", "export function f(cb: (x: number) => void) {}\n",
                     "export function f(cb: (y: number) => void) {}\n", "f"),
    "ts-two": ("ts", "export function f(cb: (a: number, b: string) => void) {}\n",
               "export function f(cb: (c: number, d: string) => void) {}\n", "f"),
    "ts-return": ("ts", "export function f(): (x: number) => string { return () => \"\"; }\n",
                  "export function f(): (y: number) => string { return () => \"\"; }\n", "f"),
    "ts-generic-argument": ("ts", "export function f(m: Map<string, (k: string) => number>) {}\n",
                            "export function f(m: Map<string, (j: string) => number>) {}\n", "f"),
    "ts-nested": ("ts", "export function f(cb: (g: (y: number) => void) => void) {}\n",
                  "export function f(cb: (h: (z: number) => void) => void) {}\n", "f"),
    "ts-optional": ("ts", "export function f(cb: (x?: number) => void) {}\n",
                    "export function f(cb: (y?: number) => void) {}\n", "f"),
    "ts-rest": ("ts", "export function f(cb: (...xs: number[]) => void) {}\n",
                "export function f(cb: (...ys: number[]) => void) {}\n", "f"),
    "ts-constructor-type": ("ts", "export function f(c: new (x: number) => object) {}\n",
                            "export function f(c: new (y: number) => object) {}\n", "f"),
    "ts-generic-function-type": ("ts", "export function f(cb: <T>(x: T) => T) {}\n",
                                 "export function f(cb: <T>(y: T) => T) {}\n", "f"),
    "ts-destructured": ("ts", "export function f(cb: ({ a, b }: O) => void) {}\n",
                        "export function f(cb: ({ c }: O) => void) {}\n", "f"),
    "ts-this-kept-name-dropped": ("ts", "export function f(cb: (this: W, x: number) => void) {}\n",
                                  "export function f(cb: (this: W, y: number) => void) {}\n", "f"),
    "ts-arrow-const": ("ts", "export const f = (cb: (x: number) => void): void => {};\n",
                       "export const f = (cb: (y: number) => void): void => {};\n", "f"),
    "ts-method": ("ts", "export class C { m(cb: (x: number) => void) {} }\n",
                  "export class C { m(cb: (y: number) => void) {} }\n", "C.m"),
    "ts-field": ("ts", "export class C { p: (x: number) => void = () => {}; }\n",
                 "export class C { p: (y: number) => void = () => {}; }\n", "C.p"),
    "ts-interface-method": ("ts", "export interface I { m(cb: (x: number) => void): void; }\n",
                            "export interface I { m(cb: (y: number) => void): void; }\n", "I.m"),
    "ts-interface-property": ("ts", "export interface I { p: (x: number) => void; }\n",
                              "export interface I { p: (y: number) => void; }\n", "I.p"),
    "ts-before-a-default": ("ts", "export function f(cb: (x: number) => void = g, s: string = \"a=b\") {}\n",
                            "export function f(cb: (y: number) => void = g, s: string = \"a=b\") {}\n", "f"),
}


@pytest.mark.parametrize("case", sorted(RENAMES))
def test_renaming_a_function_type_parameter_keeps_the_id(tmp_path: Path, case: str) -> None:
    lang, first, second, name = RENAMES[case]
    a, b = _pair(tmp_path, lang, first, second, name)
    assert a == b


#: Pairs that must still hash apart: a type inside the function type, its
#: arity, a TypeScript marker, a Swift outer label or tuple label, and what
#: is not a function type's parameter list.
DISTINCT: dict[str, tuple[str, str, str, str]] = {
    "kt-type": ("kotlin", "fun f(cb: (x: Int) -> Unit) {}\n", "fun f(cb: (x: Long) -> Unit) {}\n", "f"),
    "kt-arity": ("kotlin", "fun f(cb: (x: Int) -> Unit) {}\n", "fun f(cb: (x: Int, y: Int) -> Unit) {}\n", "f"),
    "sw-type": ("swift", "func f(cb: (_ x: Int) -> Void) {}\n", "func f(cb: (_ x: String) -> Void) {}\n", "f"),
    "sw-outer-label": ("swift", "func f(on cb: (_ x: Int) -> Void) {}\n",
                       "func f(at cb: (_ x: Int) -> Void) {}\n", "f"),
    "sw-tuple-labels": ("swift", "func f(t: (x: Int, y: Int)) {}\n", "func f(t: (a: Int, b: Int)) {}\n", "f"),
    "sw-inout": ("swift", "func f(cb: (_ x: inout Int) -> Void) {}\n", "func f(cb: (_ x: Int) -> Void) {}\n", "f"),
    "go-type": ("go", _GO + "func F(cb func(x int)) {}\n", _GO + "func F(cb func(x string)) {}\n", "F"),
    "go-grouped-arity": ("go", _GO + "func F(cb func(a, b int)) {}\n", _GO + "func F(cb func(a int)) {}\n", "F"),
    "go-variadic": ("go", _GO + "func F(cb func(xs ...int)) {}\n", _GO + "func F(cb func(xs []int)) {}\n", "F"),
    "go-results": ("go", _GO + "func F(cb func() (n int, err error)) {}\n",
                   _GO + "func F(cb func() (n int, err string)) {}\n", "F"),
    "ts-type": ("ts", "export function f(cb: (x: number) => void) {}\n",
                "export function f(cb: (x: string) => void) {}\n", "f"),
    "ts-optional": ("ts", "export function f(cb: (x?: number) => void) {}\n",
                    "export function f(cb: (x: number) => void) {}\n", "f"),
    "ts-rest": ("ts", "export function f(cb: (...xs: number[]) => void) {}\n",
                "export function f(cb: (xs: number[]) => void) {}\n", "f"),
    "ts-this": ("ts", "export function f(cb: (this: W, x: number) => void) {}\n",
                "export function f(cb: (w: W, x: number) => void) {}\n", "f"),
    "ts-object-type-property": ("ts", "export function f(o: { x: number }) {}\n",
                                "export function f(o: { y: number }) {}\n", "f"),
    # A default value is a value, hashed as written: an arrow function there
    # keeps its parameter names, as any expression keeps its identifiers.
    "ts-arrow-default-value": ("ts", "export function f(cb: (x: number) => void = (y: number) => {}) {}\n",
                               "export function f(cb: (x: number) => void = (z: number) => {}) {}\n", "f"),
    "ts-string-default-value": ("ts", "export function f(s: string = \"(a: b) => c\") {}\n",
                                "export function f(s: string = \"(d: b) => c\") {}\n", "f"),
}


@pytest.mark.parametrize("case", sorted(DISTINCT))
def test_a_type_difference_still_moves_the_id(tmp_path: Path, case: str) -> None:
    lang, first, second, name = DISTINCT[case]
    a, b = _pair(tmp_path, lang, first, second, name)
    assert a != b


#: A declaration with no named function-type parameter hashes what it did,
#: so its id does not move: (language, source, symbol, the id it had before).
UNCHANGED: list[tuple[str, str, str, str]] = [
    ("kotlin", "fun f(cb: (Int) -> Unit, n: Int): (String) -> Int = { 0 }\n", "f", "9d99f165e367cc10"),
    ("swift", "func f(cb: (Int) -> Void, t: (x: Int, y: Int)) -> (Int) -> Int { { $0 } }\n", "f",
     "450638342205d0d1"),
    ("go", _GO + "func F(cb func(int, string) error) (n int, err error) { return 0, nil }\n", "F",
     "b4f09ad3fcaf31f0"),
    ("ts", "export function f(cb: () => void, o: { x: number }, p: Map<string, number>): string { return \"\"; }\n",
     "f", "954ce89fc268e782"),
]


@pytest.mark.parametrize(("lang", "source", "name", "digest"), UNCHANGED)
def test_a_declaration_without_inner_names_keeps_its_id(
    tmp_path: Path, lang: str, source: str, name: str, digest: str,
) -> None:
    assert _id(tmp_path / "x", lang, source, name) == f"{STABLE_ID_PREFIX}{digest}"


@pytest.mark.parametrize(("part", "hashed"), [
    ("x: number", "number"),
    ("x?: number", "?number"),
    ("...xs: number[]", "...number[]"),
    ("{ a, b }: O", "O"),
    ("[a, b]?: [number, number]", "?[number, number]"),
    ("this: W", "this: W"),
    # A part with no name to take (TypeScript reads ``(number) => void``'s
    # ``number`` as a NAME of an implicit ``any``): written as it is.
    ("number", "number"),
])
def test_one_typescript_function_type_parameter(part: str, hashed: str) -> None:
    assert _ts_function_parameter_type(part) == hashed
