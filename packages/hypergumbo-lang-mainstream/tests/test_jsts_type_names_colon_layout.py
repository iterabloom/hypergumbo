# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
"""A TypeScript type name is written through the file's imports in every
layout of the ``:`` before or after it (stable-id v19).

The import rewrite (``FileTypeNames.canonical``, WI-palus, scheme v17) reads
a name right before a ``:`` as an object type's label (``{ a: T }``) and a
name right after a ``:`` as no type name (a ``::`` path's member), so it left
both as written. In TypeScript that decided by layout: the consequence of a
conditional type written ``A extends B ? C: D`` (no space before the colon)
was read as a label and ``C`` was not rewritten, nor was the ``T`` of
``{a:T}``, ``[a:T]``, ``m(x:T)`` or ``{ [K in T]-?:X }``, so one declaration
hashed apart in two layouts whenever the name was imported. The hashed text
(``_jsts_hashed_text``) now writes a space after every type annotation's
colon and around a conditional type's, where the source had none; the hash
is layout-free after that, so only the rewrite's reading changes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.js_ts import analyze_javascript

_IMPORT = "import { Foo, Bar } from './foo';\n"


def _id(root: Path, text: str, name: str) -> str:
    root.mkdir(parents=True)
    (root / "a.ts").write_text(text)
    found = [s for s in analyze_javascript(root).symbols if s.name == name]
    assert len(found) == 1, name
    assert found[0].stable_id is not None, name  # reach: a typed id
    return found[0].stable_id


#: (spaced, compact, the type written by its import path, symbol): one
#: declaration in two layouts, and the same type spelled as what it names.
LAYOUTS: dict[str, tuple[str, str, str, str]] = {
    "conditional-consequence": (
        "export function f<T>(x: T extends string ? Foo : Bar) {}\n",
        "export function f<T>(x: T extends string ? Foo: Bar) {}\n",
        "export function f<T>(x: T extends string ? import(\"./foo\").Foo : import(\"./foo\").Bar) {}\n", "f"),
    "conditional-alternative": (
        "export function f<T>(x: T extends string ? Foo : Bar) {}\n",
        "export function f<T>(x: T extends string ?Foo:Bar) {}\n",
        "export function f<T>(x: T extends string ? import(\"./foo\").Foo : import(\"./foo\").Bar) {}\n", "f"),
    "object-type-property": (
        "export function f(x: { a: Foo }) {}\n", "export function f(x: {a:Foo}) {}\n",
        "export function f(x: { a: import(\"./foo\").Foo }) {}\n", "f"),
    "optional-property": (
        "export function f(x: { a?: Foo }) {}\n", "export function f(x: {a?:Foo}) {}\n",
        "export function f(x: { a?: import(\"./foo\").Foo }) {}\n", "f"),
    "tuple-label": (
        "export function f(x: [a: Foo]) {}\n", "export function f(x: [a:Foo]) {}\n",
        "export function f(x: [a: import(\"./foo\").Foo]) {}\n", "f"),
    "mapped-type": (
        "export function f<T>(x: { [K in keyof T]-?: Foo }) {}\n",
        "export function f<T>(x: {[K in keyof T]-?:Foo}) {}\n",
        "export function f<T>(x: { [K in keyof T]-?: import(\"./foo\").Foo }) {}\n", "f"),
    "index-signature": (
        "export function f(x: { [k: string]: Foo }) {}\n", "export function f(x: {[k:string]:Foo}) {}\n",
        "export function f(x: { [k: string]: import(\"./foo\").Foo }) {}\n", "f"),
    "interface-method": (
        "export interface I { m(x: Foo): Bar; }\n", "export interface I { m(x:Foo):Bar; }\n",
        "export interface I { m(x: import(\"./foo\").Foo): import(\"./foo\").Bar; }\n", "I.m"),
}


@pytest.mark.parametrize("case", sorted(LAYOUTS))
def test_an_imported_name_is_rewritten_in_every_colon_layout(tmp_path: Path, case: str) -> None:
    spaced, compact, by_path, name = LAYOUTS[case]
    one = _id(tmp_path / "spaced", _IMPORT + spaced, name)
    two = _id(tmp_path / "compact", _IMPORT + compact, name)
    three = _id(tmp_path / "path", by_path, name)
    assert one == three, case  # reach: the spaced layout is written through the import
    assert one == two, case


@pytest.mark.parametrize(("first", "second"), [
    # The two branches are two types.
    ("export function f<T>(x: T extends string ? Foo: Bar) {}\n",
     "export function f<T>(x: T extends string ? Bar: Foo) {}\n"),
    # A label is a name, not a type: an import of the same name rewrites nothing.
    ("export function f(x: {Foo:number}) {}\n", "export function f(x: {Baz:number}) {}\n"),
])
def test_what_differs_still_moves_the_id(tmp_path: Path, first: str, second: str) -> None:
    assert _id(tmp_path / "x", _IMPORT + first, "f") != _id(tmp_path / "y", _IMPORT + second, "f")


def test_a_label_is_not_rewritten(tmp_path: Path) -> None:
    """``{Foo:number}`` in a file that imports ``Foo`` hashes as in one that
    does not: the space written after the colon leaves the label a label."""
    with_import = _id(tmp_path / "x", _IMPORT + "export function f(x: {Foo:number}) {}\n", "f")
    without = _id(tmp_path / "y", "export function f(x: {Foo:number}) {}\n", "f")
    assert with_import == without
