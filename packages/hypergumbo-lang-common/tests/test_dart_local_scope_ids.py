# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/dart.py
"""A Dart local function is scoped by the member that holds it, by the name
that tells that member from its siblings.

A Dart local function hashes the chain of declarations enclosing it
(``_dart_scope``, INV-fabih), each function body labelled by its signature's
name. Until scheme v19 that name was the signature's FIRST identifier, which
every constructor of ``C`` shares (``C``, ``C.a``, ``factory C.f``) and which a
getter shares with its setter (``p``); an operator has none. Two same-named
locals of two named constructors, of a getter and its setter, or of two
operators tied (WI-finam, the defect WI-podor fixed for Swift); the run's
occurrence split told them apart by order only.

Pinned: each pair of holders gives its locals two ids; deleting the first
holder's local moves no id of the second's; a local of a method or of a
top-level function keeps the formula it had.
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
from hypergumbo_lang_common.dart import analyze_dart


def _symbols(root: Path, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.dart").write_text(text)
    return [s for s in analyze_dart(root).symbols if s.kind != "file"]


def _locals(symbols: list[Symbol]) -> list[Symbol]:
    """The local functions ``h``, in source order."""
    return sorted(
        (s for s in symbols if s.name.rsplit(".", 1)[-1] == "h"),
        key=lambda s: (s.span.start_line, s.span.start_col),
    )


#: Two members of ``class C`` that each declare a local ``void h() {}``.
_HOLDERS: dict[str, tuple[str, str]] = {
    "two-named-constructors": ("C.a() { void h() {} }", "C.b() { void h() {} }"),
    "unnamed-and-named-constructor": ("C() { void h() {} }", "C.a() { void h() {} }"),
    "factory-and-named-constructor": ("factory C.f() { void h() {} return C.a(); }",
                                      "C.a() { void h() {} }"),
    "getter-and-setter": ("int get p { void h() {} return 1; }",
                          "set p(int v) { void h() {} }"),
    "two-operators": ("C operator +(C o) { void h() {} return o; }",
                      "C operator -(C o) { void h() {} return o; }"),
}


def _holder_text(first: str, second: str) -> str:
    return "class C {\n  " + first + "\n  " + second + "\n}\n"


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_the_locals_of_two_holders_get_two_ids(tmp_path: Path, shape: str) -> None:
    found = _locals(_symbols(tmp_path / "r", _holder_text(*_HOLDERS[shape])))
    assert len(found) == 2
    assert found[0].stable_id != found[1].stable_id, shape


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_deleting_the_first_holders_local_moves_no_id_of_the_second(
    tmp_path: Path, shape: str,
) -> None:
    first, second = _HOLDERS[shape]
    both = _symbols(tmp_path / "both", _holder_text(first, second))
    alone = _symbols(
        tmp_path / "alone", _holder_text(first.replace("void h() {} ", ""), second))
    split_within_file_stable_id_collisions(both)
    split_within_file_stable_id_collisions(alone)
    survivor = _locals(alone)
    assert len(survivor) == 1
    assert survivor[0].stable_id == _locals(both)[-1].stable_id, shape


def test_a_local_of_a_method_or_function_keeps_its_formula(tmp_path: Path) -> None:
    """A method's and a top-level function's label is its name, as before."""
    src = "class C {\n  void t() { void h() {} }\n}\nvoid top() { int k(int x) => x; }\n"
    fsid = make_file_stable_id("dart", "a.dart")
    got = {s.name: s for s in _symbols(tmp_path / "r", src)}
    assert got["C.h"].stable_id == make_typed_stable_id(
        "method", "()", "", name="h", qualified_name="C.t.h", file_stable_id=fsid)
    assert got["k"].stable_id == make_typed_stable_id(
        "function", "(int)int", "", name="k", qualified_name="top.k", file_stable_id=fsid)


def test_a_function_literal_adds_no_label(tmp_path: Path) -> None:
    """A function literal has no name: a local declared in it is scoped by
    the member around it, as before."""
    src = "class C {\n  void t() { var f = () { void h() {} }; }\n}\n"
    fsid = make_file_stable_id("dart", "a.dart")
    found = _locals(_symbols(tmp_path / "r", src))
    assert len(found) == 1
    assert found[0].stable_id == make_typed_stable_id(
        "method", "()", "", name="h", qualified_name="C.t.h", file_stable_id=fsid)
