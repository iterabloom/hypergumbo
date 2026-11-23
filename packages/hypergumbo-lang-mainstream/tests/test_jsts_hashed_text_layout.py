# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
"""The text a TypeScript typed stable id hashes is read from the syntax tree.

``layout_free`` (core) canonicalizes a hashed text without knowing its
language, so two TypeScript layouts it cannot tell apart are told apart here,
in the analyzer, from the tree (``_jsts_hashed_text``):

- A multi-line intersection written one member per line opens with a ``&``
  (``x:\\n  & A\\n  & B``): the same type as ``A & B``. ``layout_free`` cannot
  drop a leading ``&`` (in Rust, ``(\\n &self`` is a reference), but the tree
  marks it: an ``intersection_type`` whose first child is the ``&``.
- A regex literal in a hashed default value keeps its whitespace: it is
  meaning (``/a b/`` is not ``/a  b/``), and ``layout_free`` reads a ``/`` as
  punctuation, so the analyzer writes each whitespace character of the
  pattern as an escape (``\\x20``) that matches the same character.

Every other hashed text is the displayed one, so no other id moves.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.js_ts import (
    _extract_field_type,
    _extract_jsts_signature,
    _method_signature_text,
    analyze_javascript,
)


def _ids(tmp_path: Path, text: str, name: str = "a.ts") -> dict[str, tuple[str | None, str | None]]:
    """Each symbol's (stable id, displayed signature), by name."""
    for old in tmp_path.iterdir():
        old.unlink()
    (tmp_path / name).write_text(text)
    return {
        sym.name: (sym.stable_id, sym.signature)
        for sym in analyze_javascript(tmp_path).symbols
        if sym.kind in ("function", "method", "field")
    }


_INTERSECTION_ONE_LINE = """\
type A = { a: number };
type B = { b: string };
export function inter(x: A & B, y: string): void {}
export const arrow = (p: A & B): B => p;
export class K {
  m(v: Array<A & B>): A & B { return null as any; }
  f: A & B;
}
export interface I {
  im(x: A & B): A & B;
  ip: A & B;
}
function local(x: A & B): void {}
export function rest(...xs: Array<A & B>): void {}
register((p: A & B): void => {});
"""

_INTERSECTION_LEADING = """\
type A = { a: number };
type B = { b: string };
export function inter(
  x:
    & A
    & B,
  y: string,
): void {}
export const arrow = (
  p:
    & A
    & B,
): B => p;
export class K {
  m(v: Array<
    & A
    & B
  >):
    & A
    & B { return null as any; }
  f:
    & A
    & B;
}
export interface I {
  im(x:
    & A
    & B):
    & A
    & B;
  ip:
    & A
    & B;
}
function local(x:
  & A
  & B): void {}
export function rest(...xs: Array<
  & A
  & B>): void {}
register((p:
  & A
  & B): void => {});
"""

_INTERSECTION_SYMBOLS = ("inter", "arrow", "K.m", "K.f", "I.im", "I.ip", "local", "rest", "_cb_register")


def test_a_leading_intersection_amp_is_layout(tmp_path: Path) -> None:
    one_line = _ids(tmp_path, _INTERSECTION_ONE_LINE)
    leading = _ids(tmp_path, _INTERSECTION_LEADING)
    for name in _INTERSECTION_SYMBOLS:
        assert one_line[name][0] is not None, name
        # Reach: the displayed signature keeps the layout, leading ``&`` included.
        assert "& A" not in (one_line[name][1] or ""), name
        assert "\n" in (leading[name][1] or ""), name
        assert one_line[name][0] == leading[name][0], name


@pytest.mark.parametrize(("left", "right"), [
    ("A & B", "A | B"),     # an infix operator is kept
    ("A & B", "B"),         # an infix ``&`` is not a leading one
    ("A & B", "A & B & C"),
])
def test_an_intersection_member_or_operator_is_not_layout(
    tmp_path: Path, left: str, right: str,
) -> None:
    head = "type A = {}; type B = {}; type C = {};\n"
    ids_left = _ids(tmp_path, head + f"export function f(x: {left}): void {{}}\n")
    ids_right = _ids(tmp_path, head + f"export function f(x: {right}): void {{}}\n")
    assert ids_left["f"][0] is not None
    assert ids_left["f"][0] != ids_right["f"][0]


@pytest.mark.parametrize(("left", "right"), [
    ("/a b/", "/a  b/"),
    ("/a b/", "/ab/"),
    ("/a\tb/", "/a b/"),
    ("/[ ]x/g", "/[  ]x/g"),
    ("/a\\ b/", "/a\\  b/"),
    ("/a\u3000b/", "/a\u2003b/"),
])
def test_whitespace_inside_a_regex_default_is_meaning(
    tmp_path: Path, left: str, right: str,
) -> None:
    ids_left = _ids(tmp_path, f"export function f(r = {left}): void {{}}\n")
    ids_right = _ids(tmp_path, f"export function f(r = {right}): void {{}}\n")
    assert ids_left["f"][1] == f"(r = {left}): void"  # reach: the default is displayed
    assert ids_left["f"][0] != ids_right["f"][0]


@pytest.mark.parametrize(("escaped", "plain"), [
    ("/a\\ b/", "/a b/"),          # an identity escape
    ("/a\\x20b/", "/a b/"),
    ("/a\\u3000b/", "/a\u3000b/"),
])
def test_a_whitespace_escape_is_the_character_it_matches(
    tmp_path: Path, escaped: str, plain: str,
) -> None:
    """The hashed pattern writes whitespace as the escape that matches it, so
    a pattern that spells the escape itself is the same regex, one id."""
    ids_escaped = _ids(tmp_path, f"export function f(r = {escaped}): void {{}}\n")
    ids_plain = _ids(tmp_path, f"export function f(r = {plain}): void {{}}\n")
    assert ids_escaped["f"][1] == f"(r = {escaped}): void"
    assert ids_escaped["f"][0] == ids_plain["f"][0]


def test_a_regex_default_keeps_its_id_when_its_surroundings_reflow(tmp_path: Path) -> None:
    one_line = _ids(tmp_path, "export function f(r = /a b/, s: RegExp = /x y/g): void {}\n")
    reflowed = _ids(tmp_path, "export function f(\n  r = /a b/,\n  s: RegExp = /x y/g,\n): void {}\n")
    assert one_line["f"][0] is not None
    assert one_line["f"][0] == reflowed["f"][0]


_UNCHANGED = """\
type A = {}; type B = {};
export function plain(a: A & B, b: A | B, c: 'x' | 'y', r = /x\\s+y/g, s = "a  b"): A & B { return a; }
export class K { f: A & B; m(x: (y: A) => B): void {} }
export interface I { im(x: A | B): A & B; ip: A & B; }
"""


def test_the_hashed_text_is_the_displayed_text_elsewhere() -> None:
    """With no leading ``&`` and no whitespace in a regex the hashed text is
    the displayed text, byte for byte: no other id moves."""
    import tree_sitter
    import tree_sitter_typescript

    source = _UNCHANGED.encode()
    tree = tree_sitter.Parser(tree_sitter.Language(tree_sitter_typescript.language_typescript())).parse(source)
    seen = 0
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        pending.extend(node.children)
        if node.type in ("function_declaration", "method_definition"):
            pair = (_extract_jsts_signature(node, source),
                    _extract_jsts_signature(node, source, hashed=True))
        elif node.type == "method_signature":
            pair = (_method_signature_text(node, source),
                    _method_signature_text(node, source, hashed=True))
        elif node.type in ("public_field_definition", "property_signature"):
            pair = (_extract_field_type(node, source), _extract_field_type(node, source, hashed=True))
        else:
            continue
        seen += 1
        assert pair[0] and pair[0] == pair[1]
    assert seen == 5
