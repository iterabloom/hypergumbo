# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/solidity.py
"""A Solidity type imported by name and its namespace-qualified spelling hash alike.

WI-rafis (stable-id scheme v19). A Solidity function's, modifier's, event's
or constructor's stable id hashes its signature text and a state variable's
or struct member's id its type; both were hashed as written, so
``f(T memory x)`` under ``import {T} from "./Lib.sol"`` and ``f(L.T memory
x)`` under ``import "./Lib.sol" as L``, one function, got two ids. The file's
imports now bind names the way WI-palus's mechanism reads them, written as
TypeScript's import type writes them:

- ``import {T} from "p"`` binds ``T`` to ``import("p").T``, and
  ``import {S as U} from "p"`` binds ``U`` to ``import("p").S``;
- ``import "p" as L`` and ``import * as L from "p"`` bind ``L`` to
  ``import("p")``, so ``L.T`` is ``import("p").T``.

Not bound: ``import "p";`` (every name of the file, unlisted: a glob), a
name the file declares. Each case declares one symbol in two files at the same
path; ``SAME`` asserts one id, ``APART`` two.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_extended1.solidity import analyze_solidity


def _id(root: Path, text: str, name: str) -> str:
    root.mkdir(parents=True)
    (root / "C.sol").write_text(text)
    found = [s for s in analyze_solidity(root).symbols if s.name == name]
    assert len(found) == 1, [s.name for s in analyze_solidity(root).symbols]
    assert found[0].stable_id is not None, name
    return str(found[0].stable_id)


_H = ('pragma solidity ^0.8.0;\nimport {T} from "./Lib.sol";\nimport {S as U} from \'./Lib.sol\';\n'
      'import "./Lib.sol" as L;\nimport * as N from "./Lib.sol";\nimport "./Other.sol";\n')


def _c(body: str) -> str:
    return _H + "contract C {\n    " + body + "\n}\n"


#: (first body, second body, symbol): one declaration, one type spelled two ways.
SAME: dict[str, tuple[str, str, str]] = {
    "named-vs-namespace": ("function f(T memory x) public {}", "function f(L.T memory x) public {}",
                           "C.f"),
    "alias-vs-namespace": ("function f(U memory x) public {}", "function f(L.S memory x) public {}",
                           "C.f"),
    "star-namespace": ("function f(T memory x) public {}", "function f(N.T memory x) public {}", "C.f"),
    "two-namespaces": ("function f(L.T memory x) public {}", "function f(N.T memory x) public {}",
                       "C.f"),
    "returns": ("function f() public returns (T memory) {}",
                "function f() public returns (L.T memory) {}", "C.f"),
    "array": ("function f(T[] memory xs) public {}", "function f(L.T[] memory xs) public {}", "C.f"),
    "state-variable": ("T public t1;", "L.T public t1;", "C.t1"),
    "mapping": ("mapping(address => U) public m;", "mapping(address => L.S) public m;", "C.m"),
    "modifier": ("modifier m(U memory x) { _; }", "modifier m(N.S memory x) { _; }", "C.m"),
}

APART: dict[str, tuple[str, str, str]] = {
    # two imported types stay two
    "two-types": ("function f(T memory x) public {}", "function f(L.S memory x) public {}", "C.f"),
    # ``import "./Other.sol";`` lists no name: Other's types stay as written
    "glob": ("function f(O memory x) public {}", "function f(Q.O memory x) public {}", "C.f"),
}


@pytest.mark.parametrize("case", sorted(SAME))
def test_two_spellings_of_one_imported_type_hash_alike(tmp_path: Path, case: str) -> None:
    first, second, name = SAME[case]
    assert _id(tmp_path / "x", _c(first), name) == _id(tmp_path / "y", _c(second), name)


@pytest.mark.parametrize("case", sorted(APART))
def test_another_type_or_an_unbound_spelling_hashes_apart(tmp_path: Path, case: str) -> None:
    first, second, name = APART[case]
    assert _id(tmp_path / "x", _c(first), name) != _id(tmp_path / "y", _c(second), name)


def test_a_declared_type_keeps_its_name(tmp_path: Path) -> None:
    """A struct the file declares is not the imported one of the same name:
    inside ``C`` its own ``T`` shadows the imported ``T``."""
    head = ('pragma solidity ^0.8.0;\nimport {T} from "./Lib.sol";\nimport "./Lib.sol" as L;\n'
            'contract C {\n    struct T { uint a; }\n')
    local = _id(tmp_path / "x", head + "    function f(T memory x) public {}\n}\n", "C.f")
    imported = _id(tmp_path / "y", head + "    function f(L.T memory x) public {}\n}\n", "C.f")
    assert local != imported


def test_an_import_change_that_changes_no_referent_moves_no_id(tmp_path: Path) -> None:
    """Reordering the imports, adding an unrelated one, or importing what is
    already written qualified keeps the id."""
    body = "contract C {\n    function f(L.T memory x, U memory y) public {}\n}\n"
    a = 'import "./Lib.sol" as L;\n'
    b = 'import {S as U} from "./Lib.sol";\n'
    ids = {
        _id(tmp_path / "base", a + b + body, "C.f"),
        _id(tmp_path / "reordered", b + a + body, "C.f"),
        _id(tmp_path / "unrelated", a + b + 'import {Z} from "./Z.sol";\n' + body, "C.f"),
        _id(tmp_path / "added", a + b + 'import {T} from "./Lib.sol";\n' + body, "C.f"),
    }
    assert len(ids) == 1
