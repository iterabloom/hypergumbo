# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Solidity ``struct`` / ``enum`` is a symbol, and holds its members.

The invariant
-------------
Every ``struct`` and ``enum`` declaration (at file level, or in a contract,
library or interface) is a symbol of kind ``struct`` / ``enum``, named the way
every contract member is named (``Vault.Deposit``; a file-level one bare).
Its fields and enum values are ``field`` symbols named one level deeper
(``Vault.Deposit.owner``, ``Vault.Mode.Fast``), and a ``contains`` edge joins
each to its struct or enum. A struct construction ``Deposit({...})`` or an
enum conversion ``Mode(x)`` is a call to that declaration.

Identity
--------
A struct field already hashed its contract and its struct
(``Vault.Deposit.owner`` in the qualified-name slot), so emitting the struct
moves no field's stable_id. ``test_field_stable_id_is_the_existing_formula``
asserts the field's id is still that formula: a fix that changed it would be
a stable-id move outside this change's scope.

Why these tests exist
---------------------
The analyzer emitted the fields and no struct: the fields were placed in the
contract (or the file) by span, a type that state variables and parameters
name had no node, and ``RoyaltyInfo(...)`` became an external stand-in. Enums
and their values had no symbol at all.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import (
    make_declaration_stable_id,
    make_file_stable_id,
    make_typed_stable_id,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_core.linkers.containment import link_containment
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_core.overload_arity import scoped_table_stable_id
from hypergumbo_lang_extended1.solidity import analyze_solidity

VAULT = """
pragma solidity ^0.8.20;

struct Free { address owner; uint256 amount; }
enum FreeMode { On, Off }

contract Vault {
    struct Deposit { address owner; uint256 amount; }
    struct Loan { address owner; Deposit collateral; mapping(address => uint256) shares; }
    enum Mode { Fast, Slow }
    address public owner;
    Deposit private _d;

    function make(address who) public returns (Mode) {
        Deposit memory d = Deposit({owner: who, amount: 1});
        Free memory f = Free(who, 2);
        _d = d;
        return Mode(f.amount);
    }
}

library Lib {
    struct Entry { address owner; bytes32 key; }
}

interface IThing {
    struct Info { address owner; }
}

contract Other {
    struct Deposit { address owner; }
    function build() public pure returns (Vault.Deposit memory) {
        Lib.Entry memory e = Lib.Entry(address(0), 0);
        return Vault.Deposit(e.owner, 0);
    }
}
"""

INHERITED = """
contract Base {
    struct Pos { uint x; }
    enum Side { L, R }
}
contract Child is Base {
    function go() public pure returns (Side) {
        Pos memory p = Pos(1);
        return Side(p.x);
    }
}
"""


def _analyze(tmp_path: Path, text: str, name: str = "V.sol") -> list[Symbol]:
    (tmp_path / name).write_text(text, encoding="utf-8")
    return list(analyze_solidity(tmp_path).symbols)


def _one(symbols: list[Symbol], name: str, kind: str) -> Symbol:
    found = [s for s in symbols if s.name == name and s.kind == kind]
    assert len(found) == 1, (name, kind, [(s.name, s.kind) for s in symbols])
    return found[0]


class TestStructAndEnumSymbols:
    def test_struct_in_each_container_and_at_file_level(self, tmp_path: Path) -> None:
        symbols = _analyze(tmp_path, VAULT)
        for name in ("Free", "Vault.Deposit", "Vault.Loan", "Lib.Entry",
                     "IThing.Info", "Other.Deposit"):
            _one(symbols, name, "struct")

    def test_enum_in_contract_and_at_file_level(self, tmp_path: Path) -> None:
        symbols = _analyze(tmp_path, VAULT)
        _one(symbols, "Vault.Mode", "enum")
        _one(symbols, "FreeMode", "enum")

    def test_enum_values_are_fields_of_their_enum(self, tmp_path: Path) -> None:
        symbols = _analyze(tmp_path, VAULT)
        for name in ("Vault.Mode.Fast", "Vault.Mode.Slow", "FreeMode.On", "FreeMode.Off"):
            value = _one(symbols, name, "field")
            assert value.stable_id is not None
            assert value.is_exported is True

    def test_struct_stable_id_is_the_scoped_table_formula(self, tmp_path: Path) -> None:
        symbols = _analyze(tmp_path, VAULT)
        nested = _one(symbols, "Vault.Deposit", "struct")
        assert nested.stable_id == scoped_table_stable_id(
            kind="struct", lang="solidity", path="V.sol", name="Deposit",
            scope=("Vault",),
        )
        # At file level the scoped formula is the backstop's own table value.
        free = _one(symbols, "Free", "struct")
        assert free.stable_id == make_declaration_stable_id(
            "struct", "solidity", "V.sol", "Free",
        )
        mode = _one(symbols, "Vault.Mode", "enum")
        assert mode.stable_id == scoped_table_stable_id(
            kind="enum", lang="solidity", path="V.sol", name="Mode", scope=("Vault",),
        )

    def test_same_struct_name_in_two_contracts_has_two_identities(
        self, tmp_path: Path,
    ) -> None:
        symbols = _analyze(tmp_path, VAULT)
        a = _one(symbols, "Vault.Deposit", "struct")
        b = _one(symbols, "Other.Deposit", "struct")
        assert a.stable_id != b.stable_id


class TestStructFields:
    def test_field_is_named_under_contract_and_struct(self, tmp_path: Path) -> None:
        symbols = _analyze(tmp_path, VAULT)
        names = {s.name for s in symbols if s.kind == "field"}
        for name in ("Vault.Deposit.owner", "Vault.Deposit.amount", "Vault.Loan.owner",
                     "Vault.Loan.collateral", "Vault.Loan.shares", "Lib.Entry.owner",
                     "IThing.Info.owner", "Other.Deposit.owner", "Free.owner"):
            assert name in names, (name, sorted(names))
        assert "Deposit.owner" not in names

    def test_field_stable_id_is_the_existing_formula(self, tmp_path: Path) -> None:
        """The struct's contract and the struct were already hashed: no field moves."""
        symbols = _analyze(tmp_path, VAULT)
        anchor = make_file_stable_id("solidity", "V.sol")
        for name, vtype, owner_chain in (
            ("Vault.Deposit.owner", "address", "Vault.Deposit"),
            ("Vault.Loan.collateral", "Deposit", "Vault.Loan"),
            ("Lib.Entry.key", "bytes32", "Lib.Entry"),
            ("Free.amount", "uint256", "Free"),
        ):
            field = _one(symbols, name, "field")
            short = name.rsplit(".", 1)[1]
            assert field.stable_id == make_typed_stable_id(
                "field", vtype, "", name=short,
                qualified_name=f"{owner_chain}.{short}", file_stable_id=anchor,
            ), name

    def test_same_field_in_two_structs_and_a_state_variable(self, tmp_path: Path) -> None:
        symbols = _analyze(tmp_path, VAULT)
        ids = [
            _one(symbols, name, "field").stable_id
            for name in ("Vault.Deposit.owner", "Vault.Loan.owner", "Vault.owner")
        ]
        assert len(set(ids)) == 3
        assert not any(":occ:" in (sid or "") for sid in ids)


class TestContainment:
    def _contains(self, symbols: list[Symbol], tmp_path: Path) -> set[tuple[str, str]]:
        by_id = {s.id: s.name for s in symbols}
        result = link_containment(
            LinkerContext(symbols=symbols, edges=[], repo_root=tmp_path),
        )
        return {
            (by_id.get(e.src, e.src), by_id.get(e.dst, e.dst))
            for e in result.edges if e.edge_type == "contains"
        }

    def test_struct_holds_its_fields_and_contract_holds_struct(
        self, tmp_path: Path,
    ) -> None:
        symbols = _analyze(tmp_path, VAULT)
        pairs = self._contains(symbols, tmp_path)
        for pair in (
            ("Vault", "Vault.Deposit"),
            ("Vault.Deposit", "Vault.Deposit.owner"),
            ("Vault.Loan", "Vault.Loan.shares"),
            ("Free", "Free.owner"),
            ("Lib.Entry", "Lib.Entry.key"),
            ("Vault.Mode", "Vault.Mode.Fast"),
            ("FreeMode", "FreeMode.On"),
        ):
            assert pair in pairs, pair
        assert ("Vault", "Vault.Deposit.owner") not in pairs


class TestConstructionCalls:
    def _calls(self, tmp_path: Path, text: str) -> dict[str, list[tuple[str, str]]]:
        (tmp_path / "V.sol").write_text(text, encoding="utf-8")
        result = analyze_solidity(tmp_path)
        by_id = {s.id: s for s in result.symbols}
        out: dict[str, list[tuple[str, str]]] = {}
        for edge in result.edges:
            if edge.edge_type != "calls" or edge.src not in by_id:
                continue
            dst = by_id.get(edge.dst)
            target = (dst.name, dst.kind) if dst else ("?", edge.dst)
            out.setdefault(by_id[edge.src].name, []).append(target)
        return out

    def test_struct_and_enum_constructions_bind_their_declaration(
        self, tmp_path: Path,
    ) -> None:
        calls = self._calls(tmp_path, VAULT)
        assert sorted(calls["Vault.make"]) == [
            ("Free", "struct"), ("Vault.Deposit", "struct"), ("Vault.Mode", "enum"),
        ]

    def test_qualified_construction_binds_the_named_contracts_struct(
        self, tmp_path: Path,
    ) -> None:
        calls = self._calls(tmp_path, VAULT)
        assert ("Vault.Deposit", "struct") in calls["Other.build"]
        assert ("Lib.Entry", "struct") in calls["Other.build"]
        assert ("Other.Deposit", "struct") not in calls["Other.build"]

    def test_inherited_struct_and_enum(self, tmp_path: Path) -> None:
        calls = self._calls(tmp_path, INHERITED)
        assert sorted(calls["Child.go"]) == [("Base.Pos", "struct"), ("Base.Side", "enum")]
