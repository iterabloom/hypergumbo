# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
"""A destructured JavaScript or TypeScript parameter is in its function's
signature and typed stable id whole (stable-id v19).

Through v18 two things lost it. The signature extractor cut a destructured
parameter that has a default at its first ``=`` (``{retries = 3} = {}`` read
``{retries = ...``, so adding ``timeout = 5`` moved nothing) and dropped a
bare ``object_pattern`` / ``array_pattern`` parameter of a JavaScript file
altogether. And the shared names-first normalizer split a pattern at its
commas and took the text after its first ``:`` as the parameter's type, so
``{a: x}`` hashed ``x}`` (the key a caller writes was lost) and a typed
``{a, b}: O`` hashed ``{ a`` and ``O`` (the first bound names in, the last
out).

The rule now: an UNTYPED pattern is the parameter's only description, as an
untyped parameter's name is, so it is hashed whole: its keys, bound names and
nesting, each default value written ``...`` at any depth (the default value
is not an identity input, its presence is; ``test_jsts_default_values_not_
hashed.py``). A TYPED pattern (``{a, b}: O``) is hashed as its type alone,
the way a typed parameter's name is left out and the way a pattern in a
function type already was (``_ts_function_parameter_type``): renaming a
destructured binding of a typed parameter moves nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _symbol(root: Path, name: str, text: str, symbol: str) -> tuple[str | None, str | None]:
    """The (stable id, displayed signature) of ``symbol``."""
    root.mkdir(parents=True)
    (root / name).write_text(text)
    found = [s for s in analyze_javascript(root).symbols if s.name == symbol]
    assert len(found) == 1, symbol
    return found[0].stable_id, found[0].signature


def _ids(tmp_path: Path, name: str, first: str, second: str, symbol: str) -> tuple[str, str]:
    one = _symbol(tmp_path / "x", name, first, symbol)[0]
    two = _symbol(tmp_path / "y", name, second, symbol)[0]
    assert one is not None and two is not None, symbol  # reach: both are typed ids
    return one, two


#: (first, second, symbol): two parameter lists that differ in a destructured
#: parameter's shape, written in a .js and in a .ts file.
APART: dict[str, tuple[str, str, str]] = {
    "default-pattern-second-key": ("export function f({retries = 3} = {}) {}\n",
                                   "export function f({retries = 3, timeout = 5} = {}) {}\n", "f"),
    "nested-default-presence": ("export function f({retries = 3} = {}) {}\n",
                                "export function f({retries} = {}) {}\n", "f"),
    "bare-object-pattern": ("export function m({ a, b }, [c, d] = []) {}\n",
                            "export function m({ a, b, e }, [c, d] = []) {}\n", "m"),
    "bare-array-pattern": ("export function m([a, b]) {}\n", "export function m([a, b, c]) {}\n", "m"),
    "object-or-array": ("export function n({ a, b }) {}\n", "export function n([a, b]) {}\n", "n"),
    "pattern-or-nothing": ("export function n({ a }) {}\n", "export function n() {}\n", "n"),
    "renamed-key": ("export function n({ a: x }) {}\n", "export function n({ b: x }) {}\n", "n"),
    "nested-key": ("export function n({ a: { b } }) {}\n", "export function n({ c: { b } }) {}\n", "n"),
    "rest-pattern": ("export function n(...[a, b]) {}\n", "export function n(...[a]) {}\n", "n"),
    "method": ("export class C { m({ a }, [b] = []) {} }\n",
               "export class C { m({ a, z }, [b] = []) {} }\n", "C.m"),
    "arrow": ("export const g = ({ a }) => a;\n", "export const g = ({ a, b }) => a;\n", "g"),
}


@pytest.mark.parametrize("ext", ["js", "ts"])
@pytest.mark.parametrize("case", sorted(APART))
def test_a_destructured_parameter_shape_is_in_the_id(tmp_path: Path, case: str, ext: str) -> None:
    one, two = _ids(tmp_path, f"a.{ext}", *APART[case])
    assert one != two, case


#: Pairs that must hash alike: a default value inside or after a pattern
#: (not an identity input), the layout of a pattern, and a typed pattern's
#: bound names (a typed parameter hashes its type).
SAME: dict[str, tuple[str, str, str, str]] = {
    "nested-default-value": ("js", "export function f({retries = 3} = {}) {}\n",
                             "export function f({retries = 4} = { retries: 1 }) {}\n", "f"),
    "nested-default-value-ts": ("ts", "export function f({retries = 3} = {}) {}\n",
                                "export function f({retries = 4} = {}) {}\n", "f"),
    "array-default-value": ("js", "export function f([a = 1, b] = []) {}\n",
                            "export function f([a = 2, b] = [3]) {}\n", "f"),
    "layout": ("js", "export function m({ a, b }, [c, d] = []) {}\n",
               "export function m(\n  {\n    a,\n    b,\n  },\n  [c, d] = [],\n) {}\n", "m"),
    "typed-pattern-bindings": ("ts", "export function f({ a, b }: O) {}\n",
                               "export function f({ a, c }: O) {}\n", "f"),
    "typed-pattern-first-binding": ("ts", "export function f({ a, b }: O) {}\n",
                                    "export function f({ z, b }: O) {}\n", "f"),
    "typed-pattern-is-its-type": ("ts", "export function f({ a, b }: O, n: number) {}\n",
                                  "export function f(o: O, n: number) {}\n", "f"),
    "typed-nested-pattern": ("ts", "export function f({ a: { b = 1 } }: O = {}) {}\n",
                             "export function f({ a: { c } }: O = {}) {}\n", "f"),
    "typed-array-pattern": ("ts", "export function f([a, b]: [number, string]) {}\n",
                            "export function f(t: [number, string]) {}\n", "f"),
    "typed-pattern-quoted-key": ("ts", "export function f({ \"]\": x }: O) {}\n",
                                 "export function f({ \"]\": y }: O) {}\n", "f"),
    "typed-optional-pattern": ("ts", "export function f({ a }?: O) {}\n",
                               "export function f({ b }?: O) {}\n", "f"),
}


@pytest.mark.parametrize("case", sorted(SAME))
def test_what_is_not_the_shape_moves_nothing(tmp_path: Path, case: str) -> None:
    ext, first, second, symbol = SAME[case]
    one, two = _ids(tmp_path, f"a.{ext}", first, second, symbol)
    assert one == two, case


@pytest.mark.parametrize(("first", "second"), [
    ("export function f({ a }: O) {}\n", "export function f({ a }: P) {}\n"),
    ("export function f({ a }: O) {}\n", "export function f({ a }: O, b: O) {}\n"),
    ("export function f({ a }: O) {}\n", "export function f({ a }: O = {}) {}\n"),
])
def test_a_typed_pattern_type_stays_in_the_id(tmp_path: Path, first: str, second: str) -> None:
    one, two = _ids(tmp_path, "a.ts", first, second, "f")
    assert one != two


@pytest.mark.parametrize(("ext", "source", "shown"), [
    ("js", "export function f({retries = 3, timeout = 5} = {}) {}\n", "({retries = 3, timeout = 5} = ...)"),
    ("js", "export function m({ a, b }, [c, d] = []) {}\n", "({ a, b }, [c, d] = ...)"),
    ("js", "export function n(x = 5, { a: y }) {}\n", "(x = ..., { a: y })"),
    ("ts", "export function f({retries = 3} = {}) {}\n", "({retries = 3} = {})"),
])
def test_the_displayed_signature_shows_the_whole_pattern(
    tmp_path: Path, ext: str, source: str, shown: str,
) -> None:
    assert _symbol(tmp_path / "x", f"a.{ext}", source, source.split("(")[0].split()[-1])[1] == shown
