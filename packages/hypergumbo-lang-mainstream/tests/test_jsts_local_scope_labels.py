# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
"""Same-named locals of two JS/TS callables of one name do not share a
stable id (stable-id v19, WI-finam; the defect WI-podor fixed for Swift).

A JS/TS stable id hashes the chain of scopes enclosing the symbol
(``_jsts_scope``). Until v19 a callable was labelled by its bare name, so a
``function h`` (or a local class, an arrow const, an anonymous callback)
local to a getter and to its setter, to two redeclared ``function r``, or to
two methods ``m`` of two object literals under two keys hashed one id, split
only by occurrence order. A callable now labels the chain by its name and
what its own id hashes beside it (``_jsts_callable_hashed``), canonically
spaced: ``r(a)``, ``m(number)void``; a callable hashing ``()`` keeps its bare
name, so a getter and its setter (which takes one parameter) differ. A
static member's label starts with ``static``. A property key labels the chain when its value is an object
literal or a callable, as a variable does.

Left tied (no name to read, or nothing tells them apart): two redeclarations
with one signature (``function s(a)`` twice), methods with a computed or
string key, objects assigned to two member expressions.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.identity_schemes import STABLE_ID_PREFIX
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _locals(root: Path, name: str, text: str, local: str) -> list[str]:
    """The stable ids of every symbol named ``local``, in source order."""
    root.mkdir(parents=True)
    (root / name).write_text(text)
    found = sorted(
        (s for s in analyze_javascript(root).symbols if s.name == local),
        key=lambda s: (s.span.start_line, s.span.start_col),
    )
    assert len(found) >= 2, [s.name for s in analyze_javascript(root).symbols]  # reach
    return [s.stable_id or "" for s in found]


#: (source, local): two same-named locals of two enclosing callables that
#: share a name; their ids must differ, in a .js and a .ts file.
APART: dict[str, tuple[str, str]] = {
    "getter-setter-function": (
        "class C {\n  get p() { function h() {} return 1; }\n  set p(v) { function h() {} }\n}\n", "h"),
    "getter-setter-class": (
        "class C {\n  get p() { class K {} return 1; }\n  set p(v) { class K {} }\n}\n", "K"),
    "getter-setter-arrow-const": (
        "class C {\n  get p() { const a = () => 1; return 1; }\n  set p(v) { const a = () => 1; }\n}\n", "a"),
    "getter-setter-callback": (
        "class C {\n  get p() { run(() => 1); return 1; }\n  set p(v) { run(() => 1); }\n}\n", "_cb_run"),
    "redeclared-function": (
        "function r(a) { function h() {} }\nfunction r(a, b) { function h() {} }\n", "h"),
    "object-literal-keys": (
        "const o = { a: { m() { function h() {} } }, b: { m() { function h() {} } } };\n", "h"),
    "object-literal-methods": (
        "const o = { a: { m() {} }, b: { m() {} } };\n", "m"),
    "function-valued-keys": (
        "const o = { a: function () { function h() {} }, b: function () { function h() {} } };\n", "h"),
    "sibling-blocks": (
        "if (x) { const v = (a) => { function h() {} }; } else { const v = (a, b) => { function h() {} }; }\n",
        "h"),
}


@pytest.mark.parametrize("ext", ["js", "ts"])
@pytest.mark.parametrize("case", sorted(APART))
def test_same_named_locals_of_two_callables_do_not_tie(tmp_path: Path, case: str, ext: str) -> None:
    source, local = APART[case]
    ids = _locals(tmp_path / "x", f"a.{ext}", source, local)
    assert len(set(ids)) == len(ids), case


def test_a_typescript_setter_and_getter_with_types(tmp_path: Path) -> None:
    ids = _locals(tmp_path / "x", "a.ts",
                  "class F { get p(): number { function h() {} return 1; } set p(v: number) { function h() {} } }\n",
                  "h")
    assert len(set(ids)) == 2


def test_a_local_moves_with_its_callables_signature_not_with_its_layout(tmp_path: Path) -> None:
    """The label is the callable's hashed signature, layout-free: a reflowed
    parameter list keeps the local's id, a changed parameter type moves it."""
    one = _locals(tmp_path / "a", "a.ts", "function r(a: number) { function h() {} }\n"
                  "function q() { function h() {} }\n", "h")[0]
    reflowed = _locals(tmp_path / "b", "a.ts", "function r(\n  a: number,\n) { function h() {} }\n"
                       "function q() { function h() {} }\n", "h")[0]
    retyped = _locals(tmp_path / "c", "a.ts", "function r(a: string) { function h() {} }\n"
                      "function q() { function h() {} }\n", "h")[0]
    assert one == reflowed
    assert one != retyped


def test_a_callable_hashing_empty_parentheses_keeps_its_bare_label(tmp_path: Path) -> None:
    """``function q()`` labels its locals ``q`` as before: a static and an
    instance ``f()`` still tell their locals apart by ``static``."""
    ids = _locals(tmp_path / "x", "a.js",
                  "class C {\n  static f() { function h() {} }\n  f() { function h() {} }\n}\n", "h")
    assert len(set(ids)) == 2


#: Pins: the ids of locals whose enclosing callable hashes ``()`` and whose
#: chain gains no label, the ids they had before (scheme v18 digests).
UNCHANGED: list[tuple[str, str, str, int]] = [
    ("a.js", "function q() { function h() {} }\n", "h", 0),
]


def test_a_local_of_a_bare_callable_keeps_its_id(tmp_path: Path) -> None:
    for name, source, local, index in UNCHANGED:
        root = tmp_path / name
        root.mkdir()
        (root / name).write_text(source)
        found = [s for s in analyze_javascript(root).symbols if s.name == local]
        assert found[index].stable_id == _PINNED[(name, local, index)]


_PINNED: dict[tuple[str, str, int], str] = {
    ("a.js", "h", 0): f"{STABLE_ID_PREFIX}bd14312405a0dcd9",
}


@pytest.mark.parametrize(("first", "second", "local"), [
    # A key whose value is a call (not an object literal or a callable) is no label:
    # renaming it moves nothing.
    ("const o = { a: run(() => 1) };\n", "const o = { z: run(() => 1) };\n", "_cb_run"),
    # A string key names nothing: no label.
    ("const o = { 'a': { m() {} } };\n", "const o = { 'z': { m() {} } };\n", "m"),
])
def test_what_is_no_label_moves_nothing(tmp_path: Path, first: str, second: str, local: str) -> None:
    for index, source in enumerate((first, second)):
        (tmp_path / str(index)).mkdir()
        (tmp_path / str(index) / "a.js").write_text(source)
    one, two = (
        [s.stable_id for s in analyze_javascript(tmp_path / str(i)).symbols if s.name == local]
        for i in (0, 1)
    )
    assert one and one == two


def test_what_the_rule_leaves_tied(tmp_path: Path) -> None:
    """Methods with a computed or string key read no name and label nothing,
    and two redeclarations with one signature have one label: their locals
    still share an id (split later by occurrence)."""
    computed = _locals(tmp_path / "x", "a.js",
                       "class C {\n  [k](a) { function h() {} }\n  ['l'](a) { function h() {} }\n}\n", "h")
    same = _locals(tmp_path / "y", "a.js", "function s(a) { function h() {} }\nfunction s(a) { function h() {} }\n", "h")
    assert len(set(computed)) == 1
    assert len(set(same)) == 1
