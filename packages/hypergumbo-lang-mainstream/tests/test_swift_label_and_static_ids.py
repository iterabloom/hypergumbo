# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Swift typed stable_id hashes argument labels and ``static`` / ``class``.

Swift names a callable by its argument labels: ``f(from:)`` and ``f(to:)`` are
two functions, ``init(from:)`` and ``init(to:)`` two initialisers. Until
scheme v17 the hashed signature dropped labels with parameter names, so each
such pair hashed one text and tied; the run's within-file occurrence split
(``split_within_file_stable_id_collisions``) told them apart by order, and
deleting the first moved the second's id onto the first's. Likewise a type may
declare ``static func f()`` (or ``class func f()``) beside ``func f()``, a
``static var`` beside a ``var``, a ``static subscript`` beside a ``subscript``,
and a protocol may require both; and an operator may be declared ``prefix``
and ``postfix`` with one signature. None of these words was hashed.

The properties pinned here: each such pair gets two ids; deleting one moves no
id of the other through the occurrence split; what is NOT part of a callable's
identity still moves nothing -- an internal parameter name, an operator's
parameter names, backquotes around a label, ``class`` vs ``static``; and a
declaration with no label and no such word hashes the text it hashed before
(an unlabelled ``_`` parameter is its type alone), so its id does not move.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import (
    labelled_parameter,
    make_file_stable_id,
    make_typed_stable_id,
    split_within_file_stable_id_collisions,
    visibility_from_modifiers,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.swift import analyze_swift, normalize_swift_signature


def _symbols(root: Path, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.swift").write_text(text)
    return [s for s in analyze_swift(root).symbols if s.kind != "file"]


def _ids_at(root: Path, text: str) -> dict[int, str]:
    """``{line: stable_id}`` of the one symbol declared on each line."""
    out: dict[int, str] = {}
    for s in _symbols(root, text):
        assert s.span.start_line not in out, s.name
        assert s.stable_id is not None, s.name
        out[s.span.start_line] = s.stable_id
    return out


#: Pairs that differ ONLY in what a caller writes or in ``static`` / fixity,
#: each pair on lines 2 and 3 of its type.
_PAIRS: dict[str, tuple[str, str, str]] = {
    "function-labels": ("struct T {\n", "    func f(from x: Int) -> Int { x }\n",
                        "    func f(to x: Int) -> Int { x }\n"),
    "unlabelled-vs-labelled": ("struct T {\n", "    func g(_ x: Int) {}\n",
                               "    func g(x: Int) {}\n"),
    "label-order": ("struct T {\n", "    func h(a x: Int, b y: Int) {}\n",
                    "    func h(b x: Int, a y: Int) {}\n"),
    "init-labels": ("struct T {\n", "    init(from a: Int) {}\n", "    init(to a: Int) {}\n"),
    "failable-init-labels": ("struct T {\n", "    init?(from a: Int) { nil }\n",
                             "    init?(to a: Int) { nil }\n"),
    "generic-function-labels": ("struct T {\n", "    func k<U>(from u: U) {}\n",
                                "    func k<U>(to u: U) {}\n"),
    "static-method": ("class T {\n", "    static func m() {}\n", "    func m() {}\n"),
    "class-method": ("class T {\n", "    class func m() {}\n", "    func m() {}\n"),
    "final-class-method": ("class T {\n", "    final class func m() {}\n", "    func m() {}\n"),
    "static-computed-property": ("class T {\n", "    static var c: Int { 0 }\n",
                                 "    var c: Int { 1 }\n"),
    "class-computed-property": ("class T {\n", "    class var c: Int { 0 }\n",
                                "    var c: Int { 1 }\n"),
    "static-stored-property": ("struct T {\n", "    static let s: Int = 1\n",
                               "    let s: Int = 2\n"),
    "static-subscript": ("struct T {\n", "    static subscript(i: Int) -> Int { i }\n",
                         "    subscript(i: Int) -> Int { i }\n"),
    "protocol-static-method": ("protocol T {\n", "    static func r()\n", "    func r()\n"),
    "protocol-static-property": ("protocol T {\n", "    static var p: Int { get }\n",
                                 "    var p: Int { get }\n"),
    "protocol-init-labels": ("protocol T {\n", "    init(from a: Int)\n", "    init(to a: Int)\n"),
    "prefix-postfix-operator": ("struct T {\n", "    static prefix func ++ (v: T) -> T { v }\n",
                                "    static postfix func ++ (v: T) -> T { v }\n"),
    "extension-static-method": ("extension T {\n", "    static func e() {}\n",
                                "    func e() {}\n"),
}


@pytest.mark.parametrize("shape", sorted(_PAIRS))
def test_the_two_members_of_a_pair_get_two_ids(tmp_path: Path, shape: str) -> None:
    head, first, second = _PAIRS[shape]
    ids = _ids_at(tmp_path / "r", head + first + second + "}\n")
    assert ids[2] != ids[3], shape


@pytest.mark.parametrize("shape", sorted(_PAIRS))
def test_deleting_the_first_moves_no_id_of_the_second(tmp_path: Path, shape: str) -> None:
    """Through the run's occurrence split: the second member keeps its id when
    the first's line is blanked (a tie re-minted the survivor by order)."""
    head, first, second = _PAIRS[shape]
    both = _symbols(tmp_path / "both", head + first + second + "}\n")
    alone = _symbols(tmp_path / "alone", head + "\n" + second + "}\n")
    split_within_file_stable_id_collisions(both)
    split_within_file_stable_id_collisions(alone)
    kept = {s.span.start_line: s.stable_id for s in alone}
    assert {s.span.start_line: s.stable_id for s in both}[3] == kept[3], shape


#: Edits that are NOT a change of identity: each pair of texts must give the
#: member on line 2 one id.
_SAME: dict[str, tuple[str, str]] = {
    "internal-name": ("struct T {\n    func f(from x: Int) {}\n}\n",
                      "struct T {\n    func f(from y: Int) {}\n}\n"),
    "internal-name-of-unlabelled": ("struct T {\n    func f(_ x: Int) {}\n}\n",
                                    "struct T {\n    func f(_ y: Int) {}\n}\n"),
    "init-internal-name": ("struct T {\n    init(from x: Int) {}\n}\n",
                           "struct T {\n    init(from y: Int) {}\n}\n"),
    "label-equals-name": ("struct T {\n    func f(x: Int) {}\n}\n",
                          "struct T {\n    func f(x x: Int) {}\n}\n"),
    "backquoted-label": ("struct T {\n    func f(`default` x: Int) {}\n}\n",
                         "struct T {\n    func f(default x: Int) {}\n}\n"),
    "operator-parameter-names": (
        "struct T {\n    static func + (lhs: T, rhs: T) -> T { lhs }\n}\n",
        "struct T {\n    static func + (a: T, b: T) -> T { a }\n}\n"),
    "class-vs-static": ("class T {\n    class func m() {}\n}\n",
                        "class T {\n    static func m() {}\n}\n"),
    "class-vs-static-property": ("class T {\n    class var c: Int { 0 }\n}\n",
                                 "class T {\n    static var c: Int { 0 }\n}\n"),
    "label-layout": ("struct T {\n    func f(from  x: Int,to y: Int) {}\n}\n",
                     "struct T {\n    func f(from x: Int, to y: Int) {}\n}\n"),
}


@pytest.mark.parametrize("shape", sorted(_SAME))
def test_an_edit_that_is_not_identity_moves_no_id(tmp_path: Path, shape: str) -> None:
    one, other = _SAME[shape]
    assert _ids_at(tmp_path / "a", one)[2] == _ids_at(tmp_path / "b", other)[2], shape


def test_a_member_with_no_label_and_no_static_hashes_what_it_hashed(tmp_path: Path) -> None:
    """The v16 formula -- no labels, no decorators -- still gives the id of a
    member that has neither: an unlabelled parameter, no parameter, an
    instance property and field, an instance subscript, an operator without a
    type-member word (a global one). Their ids do not move with scheme v17."""
    src = (
        "struct T {\n"
        "    func f(_ x: Int, _ y: String) -> Int { x }\n"
        "    func g() {}\n"
        "    var c: Int { 0 }\n"
        "    let s: Int = 1\n"
        "    subscript(i: Int) -> Int { i }\n"
        "    init(_ v: Int) {}\n"
        "}\n"
        "func - (lhs: T, rhs: T) -> T { lhs }\n"
    )
    fsid = make_file_stable_id("swift", "a.swift")
    checked = 0
    for sym in _symbols(tmp_path / "r", src):
        if sym.kind in ("struct",):
            continue
        member = sym.name.rsplit(".", 1)[-1]
        if sym.kind in ("method", "function", "constructor", "subscript"):
            hashed = normalize_swift_signature(sym.signature) or ""
        else:
            hashed = sym.signature or ""
        expected = make_typed_stable_id(
            sym.kind, hashed, visibility_from_modifiers(sym.modifiers),
            name=member, qualified_name=sym.qualified_name, file_stable_id=fsid,
        )
        assert sym.stable_id == expected, sym.name
        checked += 1
    assert checked == 7


def test_a_label_is_hashed_through_the_shared_helper() -> None:
    """The hashed text: ``label:Type`` per labelled parameter, the type alone
    for ``_`` (the shared ``labelled_parameter`` spelling, which Dart's named
    parameters use too); labels are kept only when asked for."""
    sig = "(from: Int, _: String, `default`: [K: V]) -> Int"
    assert normalize_swift_signature(sig, labels=True) == (
        "(" + ",".join([
            labelled_parameter("from", "Int"), "String", labelled_parameter("default", "[K: V]"),
        ]) + ")Int"
    )
    assert normalize_swift_signature(sig, labels=True) == "(from:Int,String,default:[K: V])Int"
    assert normalize_swift_signature(sig) == "(Int,String,[K: V])Int"
    assert labelled_parameter(None, "Int") == "Int"


def test_the_items_repro(tmp_path: Path) -> None:
    """The filed shapes: ``f(from:)`` / ``f(to:)`` and ``static func f()`` /
    ``class func f()`` / ``func f()`` of one type."""
    ids = _ids_at(tmp_path / "r", (
        "class C {\n"
        "    func f(from x: Int) {}\n"
        "    func f(to x: Int) {}\n"
        "}\n"
        "class D {\n"
        "    static func f() {}\n"
        "    func f() {}\n"
        "}\n"
        "class E {\n"
        "    class func f() {}\n"
        "    func f() {}\n"
        "}\n"
    ))
    assert ids[2] != ids[3]
    assert ids[6] != ids[7]
    assert ids[10] != ids[11]
