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


# --- Rust: the header of the ``impl`` block a member is declared in ---------
#
# Rust's analogue: one type may have several ``impl`` blocks, and two of them
# may each define a method of the same name and signature when they differ in
# the self type's arguments (``impl Foo<A>`` / ``impl Foo<B>``), a bound
# (``impl<T: X> Foo<T>`` / ``impl<T: Y> Foo<T>``), a ``where`` clause, or the
# trait (``impl Display for Foo`` / ``impl Debug for Foo``, each with its
# ``fmt``). Through scheme v15 a member hashed only ``Owner::name``, so those
# tied. A non-plain impl's header (type parameters by position, trait, self
# type, ``where`` clause) is now a label in the hashed scope chain; a plain
# ``impl Foo`` adds no label, so its members hash what they did.

_RUST_HEAD = (
    "pub struct Foo<T>(T);\npub struct Bar;\npub struct A;\npub struct B;\n"
    "pub trait X {}\npub trait Y {}\npub trait T1 { fn k(&self); }\npub trait T2 { fn k(&self); }\n"
    "pub trait G<P> { fn k(&self); }\npub struct L<'a>(&'a u8);\npub trait H { type T; }\n"
)

# (case, impl block A, impl block B, symbol name): the two blocks differ only
# in their header and each declares the same member.
_RUST_PAIRS = [
    ("type-arguments", "impl Foo<A> { pub fn f(&self) {} }", "impl Foo<B> { pub fn f(&self) {} }", "Foo::f"),
    ("bound", "impl<T: X> Foo<T> { pub fn f(&self) {} }", "impl<T: Y> Foo<T> { pub fn f(&self) {} }", "Foo::f"),
    ("where", "impl<T> Foo<T> where T: X { pub fn f(&self) {} }",
     "impl<T> Foo<T> where T: Y { pub fn f(&self) {} }", "Foo::f"),
    ("trait", "impl T1 for Bar { fn k(&self) {} }", "impl T2 for Bar { fn k(&self) {} }", "Bar::k"),
    ("trait-arguments", "impl G<A> for Bar { fn k(&self) {} }", "impl G<B> for Bar { fn k(&self) {} }", "Bar::k"),
    ("reference-self-type", "impl T1 for Bar { fn k(&self) {} }", "impl T1 for &Bar { fn k(&self) {} }", "Bar::k"),
    ("nested-function", "impl T1 for Bar { fn k(&self) { fn helper() {} } }",
     "impl T2 for Bar { fn k(&self) { fn helper() {} } }", "Bar::helper"),
]


def _rust_ids(root: Path, blocks: list[str], name: str) -> list[str]:
    from hypergumbo_lang_mainstream.rust import analyze_rust

    root.mkdir(parents=True, exist_ok=True)
    (root / "lib.rs").write_text(_RUST_HEAD + "\n".join(blocks) + "\n")
    found = [s for s in analyze_rust(root).symbols if s.name == name and s.path.endswith("lib.rs")]
    assert len(found) == len(blocks), [(s.name, s.kind) for s in analyze_rust(root).symbols]
    found.sort(key=lambda s: s.span.start_line if s.span else 0)
    assert all(s.stable_id for s in found)
    return [str(s.stable_id) for s in found]


@pytest.mark.parametrize(("case", "block_a", "block_b", "name"), _RUST_PAIRS,
                         ids=[p[0] for p in _RUST_PAIRS])
def test_rust_members_of_two_impl_blocks_hash_apart(
    tmp_path: Path, case: str, block_a: str, block_b: str, name: str,
) -> None:
    a, b = _rust_ids(tmp_path / "both", [block_a, block_b], name)
    assert a != b, case
    [alone] = _rust_ids(tmp_path / "alone", [block_b], name)
    assert b == alone, case


@pytest.mark.parametrize(("case", "block", "renamed"), [
    ("type-parameter", "impl<T: X> Foo<T> where T: Y { pub fn f(&self) {} }",
     "impl<U: X> Foo<U> where U: Y { pub fn f(&self) {} }"),
    ("lifetime", "impl<'a> T1 for &'a Bar { fn k(&self) {} }", "impl<'b> T1 for &'b Bar { fn k(&self) {} }"),
    ("const", "impl<const N: usize> Foo<[u8; N]> { pub fn f(&self) {} }",
     "impl<const M: usize> Foo<[u8; M]> { pub fn f(&self) {} }"),
    ("whitespace", "impl<T: X> Foo<T> { pub fn f(&self) {} }",
     "impl<T: X>\n    Foo<T>\n{ pub fn f(&self) {} }"),
    ("member-after-path", "impl<T: H> Foo<T> where T::T: X { pub fn f(&self) {} }",
     "impl<S: H> Foo<S> where S::T: X { pub fn f(&self) {} }"),
    ("lifetime-bound-left-out", "impl<'a, T: X + 'a> Foo<T> where T: 'a { pub fn f(&self) {} }",
     "impl<T: X> Foo<T> { pub fn f(&self) {} }"),
    ("comment-left-out", "impl<T: X> /* c */ Foo<T> { pub fn f(&self) {} }",
     "impl<T: X> Foo<T> { pub fn f(&self) {} }"),
], ids=["type-parameter", "lifetime", "const", "whitespace", "member-after-path",
        "lifetime-bound-left-out", "comment-left-out"])
def test_rust_renaming_an_impl_parameter_moves_no_id(
    tmp_path: Path, case: str, block: str, renamed: str,
) -> None:
    name = "Foo::f" if "Foo" in block else "Bar::k"
    [a] = _rust_ids(tmp_path / "a", [block], name)
    [b] = _rust_ids(tmp_path / "b", [renamed], name)
    assert a == b, case


@pytest.mark.parametrize(("case", "block", "name"), [
    ("plain", "impl Bar { pub fn f(&self) {} }", "Bar::f"),
    ("path", "impl crate::Bar { pub fn f(&self) {} }", "crate::Bar::f"),
    ("lifetimes-only", "impl<'a> L<'a> { pub fn f(&self) {} }", "L::f"),
    ("elided-lifetime", "impl L<'_> { pub fn f(&self) {} }", "L::f"),
], ids=["plain", "path", "lifetimes-only", "elided-lifetime"])
def test_rust_a_plain_impl_adds_no_label(tmp_path: Path, case: str, block: str, name: str) -> None:
    """A plain block (once its lifetimes are left out) hashes ``Owner::f`` as
    its qualified name, exactly as before."""
    from hypergumbo_core.analyze.base import (
        make_file_stable_id, make_typed_stable_id, visibility_from_modifiers,
    )
    from hypergumbo_lang_mainstream.rust import normalize_rust_signature

    [sid] = _rust_ids(tmp_path, [block], name)
    expected = make_typed_stable_id(
        "method", normalize_rust_signature("(&self)"), visibility_from_modifiers(["pub"]), name="f",
        qualified_name=name, file_stable_id=make_file_stable_id("rust", "lib.rs"),
    )
    assert sid == expected, case
