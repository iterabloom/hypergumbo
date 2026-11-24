# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/solidity.py
"""A Solidity elementary type and its alias hash alike (WI-juzom, scheme v19).

The Solidity documentation names five aliases: ``uint`` / ``int`` for
``uint256`` / ``int256``, ``ufixed`` / ``fixed`` for ``ufixed128x18`` /
``fixed128x18``, and ``byte`` for ``bytes1`` (until 0.8.0, which removed it).
A typed stable id hashed each type as written, so ``f(uint x)`` and
``f(uint256 x)``, one function, got two ids. Each alias is now hashed as the
type it names (the spelling the analysed contracts write most). Controls: two
sizes stay two (``uint8`` / ``uint256``, ``bytes1`` / ``bytes32``,
``fixed`` / ``fixed64x18``). A user-defined type cannot take an alias's name:
each is a keyword.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_extended1.solidity import analyze_solidity


def _id(root: Path, body: str, name: str) -> str:
    root.mkdir(parents=True)
    (root / "C.sol").write_text("pragma solidity ^0.7.0;\ncontract C {\n    " + body + "\n}\n")
    found = [s for s in analyze_solidity(root).symbols if s.name == name]
    assert len(found) == 1, [s.name for s in analyze_solidity(root).symbols]
    assert found[0].stable_id is not None, name
    return str(found[0].stable_id)


SAME: dict[str, tuple[str, str, str]] = {
    "uint": ("function f(uint x) public {}", "function f(uint256 x) public {}", "C.f"),
    "int": ("function f(int x) public {}", "function f(int256 x) public {}", "C.f"),
    "byte": ("function f(byte x) public {}", "function f(bytes1 x) public {}", "C.f"),
    "ufixed": ("function f(ufixed x) public {}", "function f(ufixed128x18 x) public {}", "C.f"),
    "fixed": ("function f(fixed x) public {}", "function f(fixed128x18 x) public {}", "C.f"),
    "returns-array": ("function f() public returns (uint[] memory) {}",
                      "function f() public returns (uint256[] memory) {}", "C.f"),
    "mapping-field": ("mapping(uint => int) public m;", "mapping(uint256 => int256) public m;", "C.m"),
    "field": ("uint public total;", "uint256 public total;", "C.total"),
    "modifier": ("modifier m(uint x) { _; }", "modifier m(uint256 x) { _; }", "C.m"),
}

APART: dict[str, tuple[str, str, str]] = {
    "two-sizes": ("function f(uint8 x) public {}", "function f(uint256 x) public {}", "C.f"),
    "two-signs": ("function f(uint x) public {}", "function f(int256 x) public {}", "C.f"),
    "two-bytes": ("function f(byte x) public {}", "function f(bytes32 x) public {}", "C.f"),
    "two-fixed": ("function f(fixed x) public {}", "function f(fixed64x18 x) public {}", "C.f"),
}


@pytest.mark.parametrize("case", sorted(SAME))
def test_an_alias_and_its_type_hash_alike(tmp_path: Path, case: str) -> None:
    first, second, name = SAME[case]
    assert _id(tmp_path / "x", first, name) == _id(tmp_path / "y", second, name)


@pytest.mark.parametrize("case", sorted(APART))
def test_two_types_hash_apart(tmp_path: Path, case: str) -> None:
    first, second, name = APART[case]
    assert _id(tmp_path / "x", first, name) != _id(tmp_path / "y", second, name)
