# SPDX-License-Identifier: AGPL-3.0-or-later
"""A member hashes the constraints of the block that declares it.

Swift lets one type have several extensions, each with its own ``where``
clause, and each may declare a member of the same name and signature:
``extension Array where Element: P { func f() }`` beside the same extension
with ``Element: Q`` is legal, and the two ``f`` are different functions.
Through stable-id scheme v15 a member hashed its enclosing types by NAME
only, so the two ``Array.f`` shared one hash input and the within-file
occurrence split told them apart by ORDER: deleting the first moved its id
onto the second (WI-lonoh).

An extension's ``where`` clause (whitespace-collapsed, as written: an
extension declares no type parameters of its own, so the names in it are the
extended type's) now joins the extension's label in the scope chain every
Swift stable_id hashes. So:

- two constrained extensions' same-named members hash apart, and deleting
  one leaves the other's id where it was, for every member kind (method,
  static method, property, static field, subscript, init, a nested type and
  its members);
- a member of an extension WITHOUT a ``where`` clause hashes exactly what it
  hashed before (the same as the same member in the type's own body).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.swift import analyze_swift

_SWIFT_HEAD = "protocol P {}\nprotocol Q {}\nstruct Box<T> {}\n"

# (case, member declaration, emitted symbol name, kind)
_SWIFT_MEMBERS = [
    ("method", "func g() {}", "Box.g", "method"),
    ("static-method", "static func s() {}", "Box.s", "method"),
    ("property", "var h: Int { 1 }", "Box.h", "property"),
    ("static-field", "static let c: Int = 1", "Box.c", "field"),
    ("subscript", "subscript(i: Int) -> Int { i }", "Box.subscript(i:)", "subscript"),
    ("init", "init(z: Int) {}", "Box.init", "constructor"),
    ("nested-type", "struct Inner {}", "Inner", "struct"),
    ("nested-member", "struct Inner { func m() {} }", "Inner.m", "method"),
    ("generic-method", "func gen<U>(_ u: U) where U: Q {}", "Box.gen", "method"),
]


def _swift_ids(root: Path, blocks: list[str], name: str, kind: str) -> list[str]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "x.swift").write_text(_SWIFT_HEAD + "\n".join(blocks) + "\n")
    found = [s for s in analyze_swift(root).symbols if s.name == name and s.kind == kind]
    assert len(found) == len(blocks), [(s.name, s.kind) for s in analyze_swift(root).symbols]
    found.sort(key=lambda s: s.span.start_line if s.span else 0)
    assert all(s.stable_id for s in found)
    return [str(s.stable_id) for s in found]


@pytest.mark.parametrize(("case", "member", "name", "kind"), _SWIFT_MEMBERS,
                         ids=[m[0] for m in _SWIFT_MEMBERS])
def test_swift_members_of_two_constrained_extensions_hash_apart(
    tmp_path: Path, case: str, member: str, name: str, kind: str,
) -> None:
    on_p = f"extension Box where T: P {{ {member} }}"
    on_q = f"extension Box where T: Q {{ {member} }}"
    a, b = _swift_ids(tmp_path / "both", [on_p, on_q], name, kind)
    assert a != b, case
    [alone] = _swift_ids(tmp_path / "alone", [on_q], name, kind)
    assert b == alone, case


@pytest.mark.parametrize(("case", "member", "name", "kind"), _SWIFT_MEMBERS,
                         ids=[m[0] for m in _SWIFT_MEMBERS])
def test_swift_member_of_an_unconstrained_extension_keeps_its_hash(
    tmp_path: Path, case: str, member: str, name: str, kind: str,
) -> None:
    """No ``where`` clause: the extension's label is the type's name, as in
    v15, so the member hashes as it does in the type's own body."""
    [ext] = _swift_ids(tmp_path / "ext", [f"extension Box {{ {member} }}"], name, kind)
    [constrained] = _swift_ids(tmp_path / "c", [f"extension Box where T: P {{ {member} }}"], name, kind)
    assert ext != constrained, case
    if kind != "constructor":
        # an init in a type body is not an init in an extension (a memberwise
        # init exists only for the body), so only the other kinds compare.
        root = tmp_path / "body"
        root.mkdir()
        (root / "x.swift").write_text(
            "protocol P {}\nprotocol Q {}\nstruct Box<T> {\n" + member + "\n}\n")
        [body] = [str(s.stable_id) for s in analyze_swift(root).symbols
                  if s.name == name and s.kind == kind]
        assert ext == body, case


def test_swift_the_item_repro_splits(tmp_path: Path) -> None:
    """The item's repro: ``Array.f`` in ``Element: P`` and ``Element: Q``
    extensions, plus an ``Element == Int`` one and a multi-constraint one."""
    root = tmp_path
    (root / "x.swift").write_text(
        "protocol P {}\nprotocol Q {}\n"
        "extension Array where Element: P { func f() {} }\n"
        "extension Array where Element: Q { func f() {} }\n"
        "extension Array where Element == Int { func f() {} }\n"
        "extension Array where Element: P, Element: Q { func f() {} }\n"
    )
    ids = [s.stable_id for s in analyze_swift(root).symbols if s.name == "Array.f"]
    assert len(ids) == 4
    assert len(set(ids)) == 4


def test_swift_clause_whitespace_does_not_move_the_id(tmp_path: Path) -> None:
    [a] = _swift_ids(tmp_path / "a", ["extension Box where T: P { func g() {} }"], "Box.g", "method")
    [b] = _swift_ids(tmp_path / "b", ["extension Box\n    where T:   P\n{ func g() {} }"], "Box.g", "method")
    assert a == b
