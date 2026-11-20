# SPDX-License-Identifier: AGPL-3.0-or-later
"""An ``init`` / ``deinit`` of a function-local type is scoped as its methods are.

Every Swift stable_id hashes the chain of enclosing types AND functions
(``_swift_scope``, INV-fabih) -- except, until this fix, an initialiser's and a
deinitialiser's, which hashed the enclosing TYPE chain only. So two
``struct Inner { init(x: Int) {} }`` local to ``func f`` and ``func g`` of one
file had one ``Inner.init`` id, and a function-local ``struct Shadow`` tied with
the file's top-level ``Shadow``. The run's within-file occurrence split
(``split_within_file_stable_id_collisions``) re-mints the second of a tie with
an ordinal, so deleting the first type moved the survivor's init and deinit
ids while its struct and methods kept theirs.

The properties pinned here: same text local to two functions yields distinct
init / deinit ids; deleting the earlier copy moves no id of the later one once
the run's occurrence split has been applied; a member of a type declared
outside every function keeps the type-chain formula (its id does not move).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import (
    make_file_stable_id,
    make_typed_stable_id,
    split_within_file_stable_id_collisions,
    visibility_from_modifiers,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.swift import analyze_swift, normalize_swift_signature

#: One body, every lifecycle form of a type: a struct with a plain, a failable
#: and a nested type's init; a class with a designated, a convenience init and
#: a deinit; a method of each as the reference.
_STRUCT = (
    "    struct Inner {\n"
    "        init(x: Int) {}\n"
    "        init?(s: String) { return nil }\n"
    "        deinit {}\n"
    "        func go() {}\n"
    "        subscript(i: Int) -> Int { return i }\n"
    "        struct Deep { init(d: Int) {} }\n"
    "    }\n"
    "    class K {\n"
    "        init() {}\n"
    "        convenience init(a: Int) { self.init() }\n"
    "        deinit {}\n"
    "        func kgo() {}\n"
    "    }\n"
)

#: The positions a type can be local in. Each wraps ``_STRUCT`` twice -- the
#: EARLIER copy first, so deleting it is deleting a sibling scope.
_CONTAINERS: dict[str, tuple[str, str, str]] = {
    "function": ("func f() {\n", "}\nfunc g() {\n", "}\n"),
    "closure-in-function": (
        "func f() {\n    let c = {\n", "    }\n    c()\n}\nfunc g() {\n    let c = {\n",
        "    }\n    c()\n}\n",
    ),
    "method-of-another-type": (
        "struct Host {\n    func f() {\n", "    }\n    func g() {\n", "    }\n}\n",
    ),
    "method-of-a-local-type": (
        "func top() {\n    struct L {\n        func f() {\n",
        "        }\n        func g() {\n", "        }\n    }\n}\n",
    ),
}


def _symbols(root: Path, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.swift").write_text(text)
    symbols = [s for s in analyze_swift(root).symbols if s.kind != "file"]
    split_within_file_stable_id_collisions(symbols)
    return symbols


def _lifecycle(symbols: list[Symbol]) -> list[Symbol]:
    return [s for s in symbols if s.name.endswith((".init", ".deinit"))]


@pytest.mark.parametrize("container", sorted(_CONTAINERS))
def test_two_local_copies_have_distinct_lifecycle_ids(tmp_path: Path, container: str) -> None:
    """Before the occurrence split: every init / deinit of f's copy differs from g's."""
    open_f, between, close_g = _CONTAINERS[container]
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.swift").write_text(open_f + _STRUCT + between + _STRUCT + close_g)
    lifecycle = _lifecycle(list(analyze_swift(root).symbols))
    assert len(lifecycle) == 2 * 7, [s.name for s in lifecycle]
    ids = [s.stable_id for s in lifecycle]
    assert None not in ids
    assert len(set(ids)) == len(ids), sorted(
        (s.name, s.span.start_line, s.stable_id) for s in lifecycle
    )


@pytest.mark.parametrize("container", sorted(_CONTAINERS))
def test_deleting_the_earlier_copy_moves_no_id_of_the_later(
    tmp_path: Path, container: str,
) -> None:
    """INV-fabih's wide statement, for lifecycle members: the earlier copy's
    body is blanked (same line count), and every symbol of the later one keeps
    its id through the run's occurrence split."""
    open_f, between, close_g = _CONTAINERS[container]
    blank = "\n" * _STRUCT.count("\n")
    both = _symbols(tmp_path / "both", open_f + _STRUCT + between + _STRUCT + close_g)
    later = _symbols(tmp_path / "later", open_f + blank + between + _STRUCT + close_g)
    cut = len((open_f + _STRUCT + between).splitlines())
    before = {
        (s.kind, s.name, s.span.start_line, s.span.start_col): s.stable_id
        for s in both if s.span.start_line > cut
    }
    after = {
        (s.kind, s.name, s.span.start_line, s.span.start_col): s.stable_id
        for s in later if s.span.start_line > cut
    }
    assert set(after) == set(before)
    moved = sorted(k for k in after if after[k] != before[k])
    assert moved == []
    assert len(_lifecycle(later)) == 7


def test_the_items_repro(tmp_path: Path) -> None:
    """The filed fixture: f's and g's ``Inner.init`` and ``Inner.deinit`` tied."""
    src = (
        "func f() {\n"
        "    struct Inner { init(x: Int) {} ; deinit {} ; func go() {} }\n"
        "}\n"
        "func g() {\n"
        "    struct Inner { init(x: Int) {} ; deinit {} ; func go() {} }\n"
        "}\n"
    )
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.swift").write_text(src)
    by = {(s.name, s.span.start_line): s.stable_id for s in analyze_swift(root).symbols}
    assert by[("Inner.init", 2)] != by[("Inner.init", 5)]
    assert by[("Inner.deinit", 2)] != by[("Inner.deinit", 5)]


def test_a_local_type_does_not_tie_with_a_top_level_one_of_its_name(tmp_path: Path) -> None:
    """A function-local ``Shadow`` shared the top-level ``Shadow``'s init id."""
    src = (
        "struct Shadow { init(x: Int) {} }\n"
        "func f() {\n"
        "    struct Shadow { init(x: Int) {} }\n"
        "}\n"
    )
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.swift").write_text(src)
    inits = [s for s in analyze_swift(root).symbols if s.name == "Shadow.init"]
    assert len(inits) == 2
    assert inits[0].stable_id != inits[1].stable_id


def test_a_non_local_types_lifecycle_ids_keep_the_type_chain_formula(tmp_path: Path) -> None:
    """Outside every function the scope chain IS the type chain, so a top-level
    or nested type's init / deinit ids are the formula over ``Outer.Inner.init``
    -- the values they had before this fix."""
    src = (
        "class Top {\n"
        "    init(v: Int) {}\n"
        "    deinit {}\n"
        "    struct Nested { init(n: Int) {} }\n"
        "}\n"
        "extension Top { convenience init(w: Int) { self.init(v: w) } }\n"
    )
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.swift").write_text(src)
    fsid = make_file_stable_id("swift", "a.swift")
    checked = 0
    for sym in _lifecycle(list(analyze_swift(root).symbols)):
        kind = "constructor" if sym.name.endswith(".init") else "method"
        member = sym.name.rsplit(".", 1)[1]
        expected = make_typed_stable_id(
            kind, normalize_swift_signature(sym.signature, labels=True) or "",
            visibility_from_modifiers(sym.modifiers),
            name=member, qualified_name=sym.qualified_name, file_stable_id=fsid,
        )
        assert sym.stable_id == expected, sym.name
        checked += 1
    assert checked == 4
