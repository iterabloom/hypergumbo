# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Swift local declaration is scoped by the callable or property that holds it.

Every Swift stable_id hashes the chain of declarations enclosing it
(``_swift_scope``, INV-fabih). Until scheme v17 only types and ``func`` NAMES
were labels in that chain, so a function local to an ``init``, an accessor or
a subscript was scoped by its TYPE, and one local to a ``func`` by the bare
name its overloads share. Two same-named locals of two inits (TCA's
``IfLetStore.open``), of two overloads, of a ``static`` and an instance
``func``, of two computed properties, of a getter and a setter, or of two
subscripts tied; the run's occurrence split told them apart by order, so
deleting the first moved the second's id.

The properties pinned here: each pair of holders gives its locals two ids;
deleting the first holder's local moves no id of the second's through the
occurrence split; a local of a holder whose hashed text is ``()`` with no
``static`` (``func f()``, ``init()``) keeps the v16 formula; and what is not
the holder's identity -- a parameter's internal name, the layout of its
parameter list, ``get { }`` around a shorthand body -- moves no local's id.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import (
    make_file_stable_id,
    make_typed_stable_id,
    split_within_file_stable_id_collisions,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.swift import analyze_swift


def _symbols(root: Path, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.swift").write_text(text)
    return [s for s in analyze_swift(root).symbols if s.kind != "file"]


def _local_ids(root: Path, text: str, local: str) -> list[str]:
    """The stable ids of the symbols named ``local`` (a local function or
    type, or a local type's method), in source order."""
    found = sorted(
        (s for s in _symbols(root, text) if s.name.rsplit(".", 1)[-1] == local
         or s.name == local),
        key=lambda s: (s.span.start_line, s.span.start_col),
    )
    assert found, local
    return [s.stable_id or "" for s in found]


#: Two holders of a same-named local function ``h``: each is ``(first,
#: second)`` member text of ``struct T``; ``h`` is declared in each body.
_HOLDERS: dict[str, tuple[str, str]] = {
    "two-inits": ("init(a: Int) { func h() {} }", "init(b: String) { func h() {} }"),
    "two-inits-same-labels": ("init(_ a: Int) { func h() {} }",
                              "init(_ a: String) { func h() {} }"),
    "two-overloads-by-label": ("func f(x: Int) { func h() {} }",
                               "func f(y: Int) { func h() {} }"),
    "two-overloads-by-type": ("func f(_ x: Int) { func h() {} }",
                              "func f(_ x: String) { func h() {} }"),
    "overloads-by-return": ("func f() -> Int { func h() {}; return 1 }",
                            "func f() -> String { func h() {}; return \"\" }"),
    "static-and-instance": ("static func f() { func h() {} }", "func f() { func h() {} }"),
    "two-computed-properties": ("var p: Int { func h() {}; return 1 }",
                                "var q: Int { func h() {}; return 1 }"),
    "static-and-instance-property": ("static var p: Int { func h() {}; return 1 }",
                                     "var p: Int { func h() {}; return 1 }"),
    "getter-and-setter": (
        "var p: Int { get { func h() {}; return 1 }\n",
        "    set { func h() {} } }",
    ),
    "willset-and-didset": (
        "var p: Int = 0 { willSet { func h() {} }\n",
        "    didSet { func h() {} } }",
    ),
    "two-subscripts": ("subscript(i: Int) -> Int { func h() {}; return i }",
                       "subscript(s: String) -> Int { func h() {}; return 0 }"),
    "two-bound-closures": ("let a: () -> Void = { func h() {} }",
                           "let b: () -> Void = { func h() {} }"),
    "init-and-method": ("init() { func h() {} }", "func go() { func h() {} }"),
    "deinit-and-method": ("deinit { func h() {} }", "func go() { func h() {} }"),
}


def _holder_text(first: str, second: str) -> str:
    return "class T {\n    " + first + "\n    " + second + "\n}\n"


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_the_locals_of_two_holders_get_two_ids(tmp_path: Path, shape: str) -> None:
    ids = _local_ids(tmp_path / "r", _holder_text(*_HOLDERS[shape]), "h")
    assert len(ids) == 2
    assert ids[0] != ids[1], shape


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_a_local_type_of_two_holders_gets_two_ids(tmp_path: Path, shape: str) -> None:
    """The same for a local TYPE and its method (a table-formula id and a
    typed id): ``struct L { func go() {} }`` in place of ``func h() {}``."""
    first, second = (
        part.replace("func h() {}", "struct L { func m() {} }") for part in _HOLDERS[shape]
    )
    text = _holder_text(first, second)
    for local in ("L", "m"):
        ids = _local_ids(tmp_path / local, text, local)
        assert len(ids) == 2
        assert ids[0] != ids[1], (shape, local)


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_deleting_the_first_holders_local_moves_no_id_of_the_second(
    tmp_path: Path, shape: str,
) -> None:
    first, second = _HOLDERS[shape]
    both = _symbols(tmp_path / "both", _holder_text(first, second))
    blanked = first.replace("func h() {}; ", "").replace("func h() {}", "")
    alone = _symbols(tmp_path / "alone", _holder_text(blanked, second))
    split_within_file_stable_id_collisions(both)
    split_within_file_stable_id_collisions(alone)
    last = max((s for s in both if s.name.endswith(".h")), key=lambda s: s.span.start_line)
    survivor = [s for s in alone if s.name.endswith(".h")]
    assert len(survivor) == 1
    assert survivor[0].stable_id == last.stable_id, shape


def test_the_items_repro(tmp_path: Path) -> None:
    """TCA ``IfLetStore``: a local ``func open`` in each of two inits."""
    src = (
        "public struct IfLetStore<State, Action, Content> {\n"
        "  public init<IfContent>(_ store: Store<State?, Action>, then: (Int) -> IfContent) {\n"
        "    func open(_ core: some Core<State?, Action>) -> any Core<State?, Action> { core }\n"
        "  }\n"
        "  public init<IfContent, ElseContent>(\n"
        "    _ store: Store<State?, Action>, then: (Int) -> IfContent, else e: () -> ElseContent\n"
        "  ) {\n"
        "    func open(_ core: some Core<State?, Action>) -> any Core<State?, Action> { core }\n"
        "  }\n"
        "}\n"
    )
    ids = _local_ids(tmp_path / "r", src, "open")
    assert len(ids) == 2
    assert ids[0] != ids[1]


def test_a_local_of_an_empty_signature_holder_keeps_the_v16_formula(tmp_path: Path) -> None:
    """``func f()``, ``init()`` and ``deinit`` label the chain by their bare
    name, as before: their locals' ids do not move with scheme v17."""
    src = (
        "class T {\n"
        "    func f() { func h() {} }\n"
        "    init() { func i() {} }\n"
        "    deinit { func d() {} }\n"
        "}\n"
        "func top() { func t() {} }\n"
    )
    fsid = make_file_stable_id("swift", "a.swift")
    chains = {"h": "T.f.h", "i": "T.init.i", "d": "T.deinit.d", "t": "top.t"}
    got = {s.name.rsplit(".", 1)[-1]: s for s in _symbols(tmp_path / "r", src)}
    for local, chain in chains.items():
        sym = got[local]
        assert sym.stable_id == make_typed_stable_id(
            sym.kind, "()", "", name=local, qualified_name=chain, file_stable_id=fsid,
        ), local


#: Edits to a holder that are not a change of its identity: the local ``h``
#: keeps its id.
_SAME_HOLDER: dict[str, tuple[str, str]] = {
    "internal-parameter-name": ("init(a x: Int) { func h() {} }",
                                "init(a y: Int) { func h() {} }"),
    "parameter-list-layout": (
        "func f(a x: Int,\n        b y: [String:Int], c z: Dictionary<String,\n Int>) { func h() {} }",
        "func f(a x: Int, b y: [String: Int], c z: Dictionary<String, Int>) { func h() {} }"),
    "class-vs-static": ("class func f() { func h() {} }", "static func f() { func h() {} }"),
    "shorthand-vs-explicit-getter": ("var p: Int { func h() {}; return 1 }",
                                     "var p: Int { get { func h() {}; return 1 } }"),
    "operator-parameter-names": ("static func + (l: T, r: T) -> T { func h() {}; return l }",
                                 "static func + (a: T, b: T) -> T { func h() {}; return a }"),
}


@pytest.mark.parametrize("local", ["h", "L"])
@pytest.mark.parametrize("shape", sorted(_SAME_HOLDER))
def test_an_edit_that_is_not_the_holders_identity_moves_no_local(
    tmp_path: Path, shape: str, local: str,
) -> None:
    """For a local function (a typed id) and a local type (a table-formula
    id, whose scope slot nothing else canonicalizes)."""
    one, other = (
        part.replace("func h() {}", "struct L {}") if local == "L" else part
        for part in _SAME_HOLDER[shape]
    )
    a = _local_ids(tmp_path / "a", "class T {\n    " + one + "\n}\n", local)
    b = _local_ids(tmp_path / "b", "class T {\n    " + other + "\n}\n", local)
    assert a == b, shape


def test_a_tuple_bound_property_adds_no_label(tmp_path: Path) -> None:
    """A pattern that binds no single name is no label: a local in its
    initialiser closure is scoped by what encloses the property."""
    with_tuple = _local_ids(
        tmp_path / "a", "func f() {\n    let (a, b) = { () -> (Int, Int) in func h() {}; return (1, 2) }()\n}\n", "h")
    bare = _local_ids(tmp_path / "b", "func f() {\n    _ = { func h() {} }\n}\n", "h")
    assert with_tuple == bare
